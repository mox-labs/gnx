"""Integration tests for the Recon dispatch loop."""

import inspect
import json
import tempfile
from pathlib import Path

import pytest
import yaml

import recon
from recon.adapters._out.api_collector import ApiCollector
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.cli_collector import CliCollector
from recon.adapters._out.http_requester import HttpxRequester
from recon.adapters._out.system_clock import SystemClock
from recon.adapters._out.web_collector import WebCollector
from recon.application.survey import Survey
from recon.composition import DEFAULT_USER_AGENT
from recon.domain.converters import ConversionResult
from recon.domain.documents import TableError, TableOutcome, failure_kind
from recon.domain.errors import CollectionError
from recon.domain.models import ReconConfig
from recon.domain.ratelimit import RateLimiter
from recon.domain.substitution import substitute
from recon.domain.telemetry import Telemetry
from tests.recon_fakes import FixedClock, ScriptedRequester
from tests.recon_fakes import run_survey as run


class _NoOpConverter:
    """Test-only converter that returns an empty ConversionResult.

    `run()` tests focus on dispatch/sink/error paths; they never exercise
    the web collector, so the converter never gets called.
    """

    def convert(self, content, content_type, url):
        return ConversionResult(title="", text="")


def _collectors():
    clock = SystemClock()
    req = HttpxRequester(RateLimiter(clock), clock)
    return {
        "cli": CliCollector(),
        "api": ApiCollector(req, {}, DEFAULT_USER_AGENT),
        "web": WebCollector(req, _NoOpConverter(), DEFAULT_USER_AGENT),
    }


class TestSubstitute:
    def test_basic(self):
        assert substitute("/search/{query}", {"query": "test"}) == "/search/test"

    def test_missing_key(self):
        assert substitute("{missing}", {}) == "{missing}"

    def test_multiple(self):
        assert substitute("{a}/{b}", {"a": "1", "b": "2"}) == "1/2"


class TestReconRun:
    def test_single_cli_collector(self):
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "test", "type": "cli", "run": 'echo \'{"k": "v"}\''},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert archive.is_dir()
            assert (archive / "test.jsonl").exists()
            assert (archive / "meta.yaml").exists()

    def test_jsonl_content(self):
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "data", "type": "cli", "run": 'printf \'{"k": "v"}\\n\''},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            lines = (archive / "data.jsonl").read_text().strip().splitlines()
            records = [json.loads(line) for line in lines]
            assert len(records) == 1
            assert records[0]["k"] == "v"

    def test_multiple_collectors(self):
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "first", "type": "cli", "run": "echo one"},
                    {"name": "second", "type": "cli", "run": "echo two"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert (archive / "first.jsonl").exists()
            assert (archive / "second.jsonl").exists()

    def test_fan_out_across_catalog(self):
        """No source on collector → runs against all catalog entries."""
        config = ReconConfig.model_validate(
            {
                "catalog": [
                    {"name": "a", "type": "local", "url": "/tmp"},
                    {"name": "b", "type": "local", "url": "/tmp"},
                ],
                "collectors": [
                    {"name": "scan", "type": "cli", "run": "echo hello"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert (archive / "scan-a.jsonl").exists()
            assert (archive / "scan-b.jsonl").exists()
            assert not (archive / "scan.jsonl").exists()

    def test_pinned_source_no_fan_out(self):
        """Collector with source= runs against that source only."""
        config = ReconConfig.model_validate(
            {
                "catalog": [
                    {"name": "s2", "url": "https://api.semanticscholar.org"},
                    {"name": "arxiv", "url": "https://export.arxiv.org"},
                ],
                "collectors": [
                    {"name": "test", "type": "cli", "run": "echo hello", "source": "s2"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert (archive / "test.jsonl").exists()
            assert not (archive / "test-arxiv.jsonl").exists()

    def test_no_catalog_no_fan_out(self):
        """No catalog → collector runs once with no source."""
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "solo", "type": "cli", "run": "echo hello"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert (archive / "solo.jsonl").exists()

    def test_source_ref_error_recorded(self):
        """Missing source reference records error, doesn't crash."""
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "bad", "type": "api", "source": "nonexistent", "endpoint": "/x"},
                    {"name": "ok", "type": "cli", "run": "echo hi"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            meta = yaml.safe_load((archive / "meta.yaml").read_text())
            assert meta["tables"][0]["status"] == "error"
            assert "nonexistent" in meta["tables"][0]["error"]["message"]
            # Second collector still ran
            assert meta["tables"][1]["status"] == "ok"

    def test_meta_yaml_content(self):
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "meta-test", "type": "cli", "run": "echo hi"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            meta = yaml.safe_load((archive / "meta.yaml").read_text())
            assert "timestamp" in meta
            assert meta["tables"][0]["name"] == "meta-test"
            assert meta["tables"][0]["status"] == "ok"
            assert "records" in meta["tables"][0]

    def test_meta_format_2_declares_types_and_provenance(self):
        """meta.yaml v2: recon version, config sha256, and per table its type_url."""
        from recon import __version__

        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "plain", "type": "cli", "run": "echo '{\"a\": 1}'"},
                    {
                        "name": "typed",
                        "type": "cli",
                        "run": "echo '{\"a\": 1}'",
                        "type_url": "acme.papers.v1.hit",
                    },
                    {"name": "bad", "type": "cli", "run": "exit 2"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission, config_sha256="ab" * 32)
            meta = yaml.safe_load((archive / "meta.yaml").read_text())
            records = [json.loads(x) for x in (archive / "typed.jsonl").read_text().splitlines()]

        assert meta["format_version"] == 2
        assert meta["recon_version"] == __version__
        assert meta["config_sha256"] == "ab" * 32
        tables = {t["name"]: t for t in meta["tables"]}
        assert tables["plain"]["type_url"] == "recon.v1.records"
        assert tables["typed"]["type_url"] == "acme.papers.v1.hit"
        assert tables["typed"]["records"] == 1 and "seconds" in tables["typed"]
        assert tables["bad"]["error"]["kind"] == "run_failed"
        assert "exit 2" in tables["bad"]["error"]["message"]
        assert records == [{"a": 1}], "records stay as normalized: no injected type fields"

    def test_collector_error_recorded(self):
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "fail", "type": "cli", "run": "exit 2"},
                    {"name": "ok", "type": "cli", "run": "echo hi"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            meta = yaml.safe_load((archive / "meta.yaml").read_text())
            assert meta["tables"][0]["status"] == "error"
            assert meta["tables"][1]["status"] == "ok"
            assert (archive / "ok.jsonl").exists()

    def test_archive_directory_structure(self):
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "t", "type": "cli", "run": "echo hi"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "my-mission"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert archive.parent.name == "archive"
            assert archive.parent.parent == mission

    def test_unknown_collector_type_recorded(self):
        """Unregistered type gets error in meta, doesn't crash."""
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "ok", "type": "cli", "run": "echo hi"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, {}, mission)  # empty registry
            meta = yaml.safe_load((archive / "meta.yaml").read_text())
            assert meta["tables"][0]["status"] == "error"
            assert "No collector registered" in meta["tables"][0]["error"]["message"]

    def test_empty_collector_output(self):
        """Empty records get _empty sentinel."""
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "empty", "type": "cli", "run": "true"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            lines = (archive / "empty.jsonl").read_text().strip().splitlines()
            records = [json.loads(line) for line in lines]
            assert records == [{"_empty": True}]

    def test_an_empty_table_reports_zero_records(self):
        """The sentinel keeps the file readable; it is not a record. Counting it made an
        empty table indistinguishable from a table with one row."""
        events: list[dict] = []
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "empty", "type": "cli", "run": "true"},
                    {"name": "one", "type": "cli", "run": "echo hi"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, results = run(config, _collectors(), mission, on_event=events.append)
            meta = yaml.safe_load((archive / "meta.yaml").read_text())

        assert {r.name: r.records for r in results} == {"empty": 0, "one": 1}
        assert {t["name"]: t["records"] for t in meta["tables"]} == {"empty": 0, "one": 1}
        assert {e["name"]: e["records"] for e in events} == {"empty": 0, "one": 1}


class TestAtomicJsonlWrite:
    def test_tmp_is_cleaned_up_on_success(self):
        config = ReconConfig.model_validate(
            {"collectors": [{"name": "t", "type": "cli", "run": "echo '{\"a\": 1}'"}]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            # final file exists, no lingering .tmp
            assert (archive / "t.jsonl").exists()
            assert not (archive / "t.jsonl.tmp").exists()


class TestIncompleteSentinel:
    def test_sentinel_on_collector_error(self):
        """A failed collector should produce a .incomplete sentinel so
        downstream readers know the archive isn't fully trustworthy."""
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "ok", "type": "cli", "run": "echo '{\"a\": 1}'"},
                    {"name": "bad", "type": "cli", "run": "exit 2"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, results = run(config, _collectors(), mission)
            statuses = {r.name: r.status for r in results}
            assert statuses == {"ok": "ok", "bad": "error"}
            assert (archive / ".incomplete").exists()
            assert (archive / "meta.yaml").exists()

    def test_no_sentinel_when_all_ok(self):
        config = ReconConfig.model_validate(
            {"collectors": [{"name": "ok", "type": "cli", "run": "echo '{\"a\": 1}'"}]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert not (archive / ".incomplete").exists()


class TestPreserveRaw:
    def test_disabled_by_default(self):
        """preserve_raw: false → no capture log and no raw/ in the archive."""
        config = ReconConfig.model_validate(
            {"collectors": [{"name": "t", "type": "cli", "run": "echo '{\"a\": 1}'"}]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert not (archive / "raw").exists()
            assert not (archive / "captures.jsonl").exists()

    def test_cli_stdout_is_captured_content_addressed(self):
        """preserve_raw: true → one recon.v1.capture line; body under raw/<sha256>."""
        import hashlib

        config = ReconConfig.model_validate(
            {
                "preserve_raw": True,
                "collectors": [
                    {"name": "echoer", "type": "cli", "run": 'printf \'{"a": 1}\\n{"a": 2}\\n\''},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)

            lines = (archive / "captures.jsonl").read_text().splitlines()
            assert len(lines) == 1
            capture = json.loads(lines[0])
            body_bytes = (archive / capture["body"]).read_bytes()

        assert body_bytes == b'{"a": 1}\n{"a": 2}\n'
        sha = hashlib.sha256(body_bytes).hexdigest()
        assert capture["schema"] == "recon.v1.capture"
        assert capture["kind"] == "cli"
        assert capture["collector"] == "echoer"
        assert capture["status"] == 0
        assert capture["body"] == f"raw/{sha}"
        assert capture["sha256"] == sha
        assert capture["bytes"] == len(body_bytes)
        assert capture["request"]["commands"] == ['printf \'{"a": 1}\\n{"a": 2}\\n\'']

    def test_jsonl_still_produced_alongside_raw(self):
        """Raw doesn't replace processed output — both exist."""
        config = ReconConfig.model_validate(
            {
                "preserve_raw": True,
                "collectors": [
                    {"name": "t", "type": "cli", "run": "echo '{\"a\": 1}'"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert (archive / "t.jsonl").exists()
            assert (archive / "captures.jsonl").exists()

    def test_capture_log_is_not_a_query_table(self):
        from recon.adapters._out.duckdb_query import DuckdbQuery

        config = ReconConfig.model_validate(
            {"preserve_raw": True, "collectors": [{"name": "t", "type": "cli", "run": "true"}]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert DuckdbQuery().tables(archive) == ["t"]


class TestFailureKind:
    """The survey exit code is chosen from the failed tables' kinds."""

    @staticmethod
    def _results(*kinds):
        def table(i, kind):
            ok = TableOutcome(name=f"t{i}", file=f"t{i}.jsonl", collector="c", type="cli",
                              source=None, type_url="recon.v1.records")  # fmt: skip
            if kind is None:
                return ok
            error = TableError(kind=kind, message="")
            return ok.model_copy(update={"status": "error", "error": error})

        return [table(i, k) for i, k in enumerate(kinds)]

    def test_all_ok_is_none(self):
        assert failure_kind(self._results(None, None)) is None

    def test_all_transient_is_transient(self):
        assert failure_kind(self._results(None, "transient", "transient")) == "transient"

    def test_any_auth_wins(self):
        assert failure_kind(self._results("transient", "auth", "run_failed")) == "auth"

    def test_mixed_is_run_failed(self):
        """A survey whose failures are neither auth nor all transient exits ``run_failed``
        (8), the kind an unclassified table failure carries too."""
        assert failure_kind(self._results("transient", "run_failed")) == "run_failed"
        assert failure_kind(self._results("unknown")) == "run_failed"


class TestEventCallback:
    def test_emits_ok_and_error_events(self):
        events: list[dict] = []
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "ok", "type": "cli", "run": "echo '{\"a\": 1}'"},
                    {"name": "bad", "type": "cli", "run": "exit 2"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            run(config, _collectors(), mission, on_event=events.append)
        kinds = [e["kind"] for e in events]
        assert "ok" in kinds and "error" in kinds
        ok_event = next(e for e in events if e["kind"] == "ok")
        assert ok_event["name"] == "ok"
        assert ok_event["records"] == 1


class TestTableErrorCarriesItsFix:
    """A collector that knows the remedy says so; the table's error keeps it."""

    class _Refuses:
        def __init__(self, fix):
            self._fix = fix

        def collect(self, entry, source, *, captures=None):
            raise CollectionError("limit reached", kind="run_failed", fix=self._fix)
            yield {}

    def _error(self, tmp_path, fix):
        config = ReconConfig.model_validate({"collectors": [{"name": "t", "type": "x"}]})
        events: list[dict] = []
        archive, results = run(config, {"x": self._Refuses(fix)}, tmp_path, on_event=events.append)
        meta = yaml.safe_load((archive / "meta.yaml").read_text())
        error = results[0].document()["error"]
        assert meta["tables"][0]["error"] == error
        return error, events[0]

    def test_fix_reaches_the_results_meta_and_event(self, tmp_path):
        error, event = self._error(tmp_path, "raise the limit")
        assert error == {
            "kind": "run_failed",
            "message": "[t] limit reached",
            "fix": "raise the limit",
        }
        assert event["fix"] == "raise the limit"

    def test_an_error_without_a_remedy_has_a_null_fix(self, tmp_path):
        """Changed requirement: a table error is one record, so ``fix`` is always present,
        null when the collector named no remedy (as in the error envelope)."""
        error, _event = self._error(tmp_path, None)
        assert error == {"kind": "run_failed", "message": "[t] limit reached", "fix": None}


class TestSinkClosesTheGenerator:
    """`write_table` must close a SUSPENDED generator when the consumer fails, so the
    collector's cleanup runs: subprocess kill, httpx close, rate-limit release.

    Why the obvious tests do not catch a missing `contextlib.closing(records)`:
      - normal completion — the generator exhausts, so it is already closed;
      - the generator itself raises — it terminated, so it is already closed;
      - refcount GC closes an abandoned generator once its last reference drops.

    closing() changes observable behaviour in exactly one situation: the generator is
    SUSPENDED mid-stream and the CONSUMER raises. Then, without closing(), the generator
    is left suspended and its finally does not run until GC decides to. That is the case
    below, produced by making json.dumps fail on the second record via a circular
    reference (which `default=str` cannot rescue), while a reference to the generator is
    held so GC cannot stand in for the fix.
    """

    @staticmethod
    def _circular():
        d: dict = {}
        d["self"] = d
        return d

    def test_suspended_generator_is_closed_when_the_consumer_fails(self, tmp_path):
        closed = []

        def make():
            try:
                yield {"a": 1}
                yield {"bad": TestSinkClosesTheGenerator._circular()}
                yield {"never": "reached"}
            finally:
                closed.append(True)

        gen = make()  # reference held: GC must not be what closes it
        with pytest.raises(ValueError):
            ArchiveFiles(SystemClock()).write_table(tmp_path, "out", gen)
        assert closed == [True], (
            "the generator was suspended when the consumer raised; closing() must run its "
            "finally before write_table returns, or the upstream collector never releases its "
            "subprocess / httpx client / rate-limit token"
        )
        assert inspect.getgeneratorstate(gen) == inspect.GEN_CLOSED

    def test_generator_closed_on_normal_completion(self, tmp_path):
        closed = []

        def make():
            try:
                yield {"a": 1}
                yield {"a": 2}
            finally:
                closed.append(True)

        gen = make()
        assert ArchiveFiles(SystemClock()).write_table(tmp_path, "out", gen) == 2
        assert closed == [True]
        assert inspect.getgeneratorstate(gen) == inspect.GEN_CLOSED

    def test_partial_tmp_is_removed_on_failure(self, tmp_path):
        def make():
            yield {"a": 1}
            raise RuntimeError("boom")

        out = tmp_path / "out.jsonl"
        with pytest.raises(RuntimeError):
            ArchiveFiles(SystemClock()).write_table(tmp_path, "out", make())
        assert not out.exists()
        assert not out.with_suffix(out.suffix + ".tmp").exists(), "partial .tmp must be cleaned"


class TestAnUnfinishedArchiveReadsIncomplete:
    """The marker is written when the archive is made and removed only when the survey
    finishes, so any way of not finishing leaves it."""

    def test_the_marker_exists_while_the_survey_runs(self, tmp_path):
        seen: list[bool] = []

        class Looks:
            def collect(self, entry, source, *, captures=None):
                [archive] = (tmp_path / "archive").iterdir()
                seen.append((archive / ".incomplete").exists())
                yield {"a": 1}

        config = ReconConfig.model_validate({"collectors": [{"name": "t", "type": "x"}]})
        archive, _ = run(config, {"x": Looks()}, tmp_path)
        assert seen == [True]
        assert not (archive / ".incomplete").exists(), "removed once every table succeeded"

    def test_a_meta_yaml_that_cannot_be_written_leaves_the_marker(self, tmp_path):
        class NoMeta(ArchiveFiles):
            def write_meta(self, archive, meta):
                raise OSError("disk full")

        events: list[dict] = []
        clock = SystemClock()
        config = ReconConfig.model_validate(
            {"collectors": [{"name": "t", "type": "cli", "run": "echo '{\"a\": 1}'"}]}
        )
        archives = NoMeta(clock)
        started = clock.now()
        archive = archives.create(tmp_path, "a")
        survey = Survey(archives, clock, recon.__version__, Telemetry([], clock), events.append)
        [table] = survey.run(config, _collectors(), archive, started=started)
        assert table.status == "ok"
        assert (archive / ".incomplete").exists()
        assert [e["kind"] for e in events if e["kind"] == "archive_warning"] == ["archive_warning"]
        # The survey says so itself, not only to a callback a caller may not pass.
        assert not survey.finished
        [warning] = survey.warnings
        assert warning.kind == "archive_unfinished" and "disk full" in warning.message

    def test_a_finished_survey_says_so(self, tmp_path):
        clock = SystemClock()
        archives = ArchiveFiles(clock)
        config = ReconConfig.model_validate(
            {"collectors": [{"name": "t", "type": "cli", "run": "echo '{\"a\": 1}'"}]}
        )
        archive = archives.create(tmp_path, "a")
        survey = Survey(archives, clock, recon.__version__, Telemetry([], clock))
        survey.run(config, _collectors(), archive, started=clock.now())
        assert survey.finished and survey.warnings == []


class TestOneRecordHoldingTheRecords:
    """Regression: an api source read without the ``extract:`` its records sit under (arXiv
    without ``extract: feed.entry``) gave one record, the whole feed, and "ok"."""

    BODY = json.dumps({"feed": {"title": "q", "entry": [{"id": "1"}, {"id": "2"}]}})

    #: The probe the skill recommends, one paper, read with the right extract: the one
    #: record is a paper, and its authors are its own list.
    ONE_PAPER = json.dumps({"feed": {"title": "q", "entry": [
        {"id": "1", "author": [{"name": "a"}, {"name": "b"}, {"name": "c"}]},
    ]}})  # fmt: skip
    ONE_WORK = json.dumps({"meta": {"count": 1}, "results": [
        {"id": "W1", "concepts": [{"id": f"C{i}", "score": 0.5} for i in range(18)]},
    ]})  # fmt: skip

    def _survey(self, tmp_path, entry, body=BODY):
        config = ReconConfig.model_validate({
            "catalog": [{"name": "arxiv", "url": "https://export.arxiv.test"}],
            "collectors": [{"name": "papers", "type": "api", "source": "arxiv",
                            "endpoint": "/api/query", **entry}],
        })  # fmt: skip
        requester = ScriptedRequester({"https://export.arxiv.test/api/query": body})
        clock = FixedClock()
        archives = ArchiveFiles(clock)
        archive = archives.create(tmp_path, "a")
        survey = Survey(archives, clock, "0", Telemetry([], clock), extracts={"api"})
        collectors = {"api": ApiCollector(requester, {}, DEFAULT_USER_AGENT)}
        tables = survey.run(config, collectors, archive, started=clock.now())
        return tables, survey.warnings

    def test_one_record_holding_a_list_of_objects_is_warned_with_the_extract(self, tmp_path):
        tables, [warning] = self._survey(tmp_path, {})
        assert tables[0].status == "ok" and tables[0].records == 1
        assert warning.kind == "extract_missing" and warning.path == "collectors.0.extract"
        assert warning.fix == "set extract: feed.entry on collector 'papers', then survey again"

    @pytest.mark.parametrize(
        ("body", "extract"),
        [(ONE_PAPER, "feed.entry"), (ONE_WORK, "results")],
        ids=["arxiv", "openalex"],
    )
    def test_a_probe_that_extracts_one_record_is_not_warned_about_its_own_lists(
        self, tmp_path, body, extract
    ):
        """Regression (changed requirement): a correct extract and a one-record probe were
        told to set ``extract: feed.entry.author`` (arXiv) or ``results.concepts`` (OpenAlex),
        a fix that would have made a table of authors. A collector with an extract is never
        warned."""
        tables, warnings = self._survey(tmp_path, {"extract": extract}, body)
        assert tables[0].records == 1 and warnings == []

    def test_the_right_extract_is_not_warned(self, tmp_path):
        tables, warnings = self._survey(tmp_path, {"extract": "feed.entry"})
        assert tables[0].records == 2 and warnings == []
