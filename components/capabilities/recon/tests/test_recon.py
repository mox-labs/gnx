"""A survey end to end, through recon's root: tables, the archive, meta.yaml and events."""

import hashlib
import inspect
import json
import math
import tempfile
import time
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any, ClassVar, NoReturn

import pytest
import yaml

import recon
from recon.adapters._out.api_collector import ApiCollector
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.cli_collector import CliCollector
from recon.adapters._out.http_requester import HttpRequester
from recon.adapters._out.system_clock import SystemClock
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.adapters._out.web_collector import WebCollector
from recon.application.survey import Survey
from recon.domain.capture import CaptureLog
from recon.domain.collector import Collector, CollectorContext, CollectorType
from recon.domain.converters import ConversionResult
from recon.domain.documents import TableError, TableOutcome, failure_kind
from recon.domain.errors import CollectionError, NoRecordsError, Problem
from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
from recon.domain.ratelimit import RateLimiter
from recon.domain.substitution import substitute
from recon.domain.telemetry import Telemetry
from tests.recon_fakes import FixedClock, ScriptedRequester, plugins_with
from tests.recon_fakes import run_survey as run


class _NoOpConverter:
    """Test-only converter that returns an empty ConversionResult.

    `run()` tests focus on dispatch/sink/error paths; they never exercise
    the web collector, so the converter never gets called.
    """

    def convert(self, content: bytes, content_type: str, url: str) -> ConversionResult:
        return ConversionResult(title="", text="")


def _collectors() -> dict[str, Collector]:
    clock = SystemClock()
    req = HttpRequester(RateLimiter(clock), clock)
    return {
        "cli": CliCollector(),
        "api": ApiCollector(req, {}, DEFAULT_USER_AGENT),
        "web": WebCollector(req, _NoOpConverter(), DEFAULT_USER_AGENT),
    }


class TestSubstitute:
    def test_basic(self) -> None:
        assert substitute("/search/{query}", {"query": "test"}) == "/search/test"

    def test_missing_key(self) -> None:
        assert substitute("{missing}", {}) == "{missing}"

    def test_multiple(self) -> None:
        assert substitute("{a}/{b}", {"a": "1", "b": "2"}) == "1/2"


class TestReconRun:
    def test_single_cli_collector(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())
            assert archive.is_dir()
            assert (archive / "test.jsonl").exists()
            assert (archive / "meta.yaml").exists()

    def test_jsonl_content(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())
            lines = (archive / "data.jsonl").read_text().strip().splitlines()
            records = [json.loads(line) for line in lines]
            assert len(records) == 1
            assert records[0]["k"] == "v"

    def test_multiple_collectors(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())
            assert (archive / "first.jsonl").exists()
            assert (archive / "second.jsonl").exists()

    def test_fan_out_across_catalog(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())
            assert (archive / "scan-a.jsonl").exists()
            assert (archive / "scan-b.jsonl").exists()
            assert not (archive / "scan.jsonl").exists()

    def test_pinned_source_no_fan_out(self) -> None:
        """Collector with source= runs against that source only."""
        with tempfile.TemporaryDirectory() as tmp:
            config = ReconConfig.model_validate(
                {
                    "catalog": [
                        {"name": "s2", "type": "local", "url": tmp},
                        {"name": "arxiv", "type": "local", "url": tmp},
                    ],
                    "collectors": [
                        {"name": "test", "type": "cli", "run": "echo hello", "source": "s2"},
                    ],
                }
            )
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, mission, converter=_NoOpConverter())
            assert (archive / "test.jsonl").exists()
            assert not (archive / "test-arxiv.jsonl").exists()

    def test_no_catalog_no_fan_out(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())
            assert (archive / "solo.jsonl").exists()

    def test_a_source_not_in_the_catalog_stops_the_survey_before_it_starts(
        self, tmp_path: Path
    ) -> None:
        """Changed requirement: the survey used to record a missing source as a failed
        table, a check the plan already made; the plan refuses it, so nothing runs."""
        config = {
            "collectors": [
                {"name": "bad", "type": "api", "source": "nonexistent", "endpoint": "/x"},
                {"name": "ok", "type": "cli", "run": "echo hi"},
            ],
        }
        with pytest.raises(recon.ConfigError) as excinfo:
            run(config, tmp_path)
        [problem] = excinfo.value.problems
        assert problem.path == "collectors.0.source" and "nonexistent" in problem.message
        assert not (tmp_path / ".recon" / "m" / "archive").exists()

    def test_meta_yaml_content(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())
            meta = yaml.safe_load((archive / "meta.yaml").read_text())
            assert "timestamp" in meta
            assert meta["tables"][0]["name"] == "meta-test"
            assert meta["tables"][0]["status"] == "ok"
            assert "records" in meta["tables"][0]

    def test_meta_format_2_declares_types_and_provenance(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())
            config_text = (mission / ".recon" / "m" / "config.yaml").read_bytes()
            meta = yaml.safe_load((archive / "meta.yaml").read_text())
            records = [json.loads(x) for x in (archive / "typed.jsonl").read_text().splitlines()]

        assert meta["format_version"] == 2
        assert meta["recon_version"] == __version__
        assert meta["config_sha256"] == hashlib.sha256(config_text).hexdigest()
        tables = {t["name"]: t for t in meta["tables"]}
        assert tables["plain"]["type_url"] == "recon.v1.records"
        assert tables["typed"]["type_url"] == "acme.papers.v1.hit"
        assert tables["typed"]["records"] == 1 and "seconds" in tables["typed"]
        assert tables["bad"]["error"]["kind"] == "run_failed"
        assert "exit 2" in tables["bad"]["error"]["message"]
        assert records == [{"a": 1}], "records stay as normalized: no injected type fields"

    def test_collector_error_recorded(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())
            meta = yaml.safe_load((archive / "meta.yaml").read_text())
            assert meta["tables"][0]["status"] == "error"
            assert meta["tables"][1]["status"] == "ok"
            assert (archive / "ok.jsonl").exists()

    def test_archive_directory_structure(self) -> None:
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "t", "type": "cli", "run": "echo hi"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            archive, _ = run(config, project, name="my-mission")
            assert archive.parent.name == "archive"
            assert archive.parent.parent == project / ".recon" / "my-mission"

    def test_a_type_that_could_not_be_built_fails_its_tables_and_the_rest_run(
        self, tmp_path: Path
    ) -> None:
        """A type unknown to the plan never reaches a survey (the plan refuses it); a type
        whose factory raised does, and its tables fail as config, naming it."""

        def explode(ctx: CollectorContext) -> NoReturn:
            raise ValueError("no credentials file")

        plugins = plugins_with()
        plugins.collector_types["fragile"] = CollectorType(create=explode, effects=None)
        config = {
            "collectors": [
                {"name": "f", "type": "fragile"},
                {"name": "ok", "type": "cli", "run": "echo hi"},
            ]
        }
        archive, tables = run(config, tmp_path, plugins=plugins)
        assert [(t.name, t.status) for t in tables] == [("f", "error"), ("ok", "ok")]
        error = tables[0].error
        assert error is not None
        assert error.kind == "config"
        assert "'fragile' could not be built" in error.message

    def test_empty_collector_output(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())
            lines = (archive / "empty.jsonl").read_text().strip().splitlines()
            records = [json.loads(line) for line in lines]
            assert records == [{"_empty": True}]

    def test_an_empty_table_reports_zero_records(self) -> None:
        """The sentinel keeps the file readable; it is not a record. Counting it made an
        empty table indistinguishable from a table with one row."""
        events: list[dict[str, Any]] = []
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
            archive, results = run(
                config, mission, converter=_NoOpConverter(), on_event=events.append
            )
            meta = yaml.safe_load((archive / "meta.yaml").read_text())

        assert {r.name: r.records for r in results} == {"empty": 0, "one": 1}
        assert {t["name"]: t["records"] for t in meta["tables"]} == {"empty": 0, "one": 1}
        assert {e["name"]: e["records"] for e in events if e["kind"] == "ok"} == {
            "empty": 0,
            "one": 1,
        }


class TestAtomicJsonlWrite:
    def test_tmp_is_cleaned_up_on_success(self) -> None:
        config = ReconConfig.model_validate(
            {"collectors": [{"name": "t", "type": "cli", "run": "echo '{\"a\": 1}'"}]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, mission, converter=_NoOpConverter())
            # final file exists, no lingering .tmp
            assert (archive / "t.jsonl").exists()
            assert not (archive / "t.jsonl.tmp").exists()


class TestIncompleteSentinel:
    def test_sentinel_on_collector_error(self) -> None:
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
            archive, results = run(config, mission, converter=_NoOpConverter())
            statuses = {r.name: r.status for r in results}
            assert statuses == {"ok": "ok", "bad": "error"}
            assert (archive / ".incomplete").exists()
            assert (archive / "meta.yaml").exists()

    def test_no_sentinel_when_all_ok(self) -> None:
        config = ReconConfig.model_validate(
            {"collectors": [{"name": "ok", "type": "cli", "run": "echo '{\"a\": 1}'"}]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, mission, converter=_NoOpConverter())
            assert not (archive / ".incomplete").exists()


class TestPreserveRaw:
    def test_disabled_by_default(self) -> None:
        """preserve_raw: false → no capture log and no raw/ in the archive."""
        config = ReconConfig.model_validate(
            {"collectors": [{"name": "t", "type": "cli", "run": "echo '{\"a\": 1}'"}]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, mission, converter=_NoOpConverter())
            assert not (archive / "raw").exists()
            assert not (archive / "captures.jsonl").exists()

    def test_cli_stdout_is_captured_content_addressed(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())

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

    def test_jsonl_still_produced_alongside_raw(self) -> None:
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
            archive, _ = run(config, mission, converter=_NoOpConverter())
            assert (archive / "t.jsonl").exists()
            assert (archive / "captures.jsonl").exists()

    def test_capture_log_is_not_a_table(self) -> None:
        config = ReconConfig.model_validate(
            {"preserve_raw": True, "collectors": [{"name": "t", "type": "cli", "run": "true"}]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, mission, converter=_NoOpConverter())
            assert [t.output for t in ArchiveFiles(FixedClock()).tables(archive)] == ["t"]


class TestFailureKind:
    """The survey exit code is chosen from the failed tables' kinds."""

    @staticmethod
    def _results(*kinds: str | None) -> list[TableOutcome]:
        def table(i: int, kind: str | None) -> TableOutcome:
            ok = TableOutcome(name=f"t{i}", file=f"t{i}.jsonl", collector="c", type="cli",
                              source=None, type_url="recon.v1.records")  # fmt: skip
            if kind is None:
                return ok
            error = TableError(kind=kind, message="")
            return ok.model_copy(update={"status": "error", "error": error})

        return [table(i, k) for i, k in enumerate(kinds)]

    def test_all_ok_is_none(self) -> None:
        assert failure_kind(self._results(None, None)) is None

    def test_all_transient_is_transient(self) -> None:
        assert failure_kind(self._results(None, "transient", "transient")) == "transient"

    def test_any_auth_wins(self) -> None:
        assert failure_kind(self._results("transient", "auth", "run_failed")) == "auth"

    def test_mixed_is_run_failed(self) -> None:
        """A survey whose failures are neither auth nor all transient exits ``run_failed``
        (8), the kind an unclassified table failure carries too."""
        assert failure_kind(self._results("transient", "run_failed")) == "run_failed"
        assert failure_kind(self._results("unknown")) == "run_failed"


class TestEventCallback:
    def test_emits_ok_and_error_events(self) -> None:
        events: list[dict[str, Any]] = []
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
            run(config, mission, converter=_NoOpConverter(), on_event=events.append)
        kinds = [e["kind"] for e in events]
        assert "ok" in kinds and "error" in kinds
        ok_event = next(e for e in events if e["kind"] == "ok")
        assert ok_event["name"] == "ok"
        assert ok_event["records"] == 1


class TestAnUnclassifiedTableError:
    def test_has_the_shared_kind_run_failed(self, tmp_path: Path) -> None:
        """Regression: a collector's exception that was not a recon error gave the table the
        kind ``unknown``, whose next step is "report it as a bug in recon", where the
        CHANGELOG and README said ``run_failed``."""

        class Breaks:
            def collect(
                self,
                entry: CollectorEntry,
                source: SourceEntry | None,
                *,
                captures: CaptureLog | None = None,
            ) -> Iterator[dict[str, Any]]:
                raise AttributeError("'list' object has no attribute 'items'")
                yield {}

        config = ReconConfig.model_validate({"collectors": [{"name": "t", "type": "x"}]})
        archive, [table] = run(config, tmp_path, plugins=plugins_with(x=Breaks()))
        assert table.error is not None and table.error.kind == "run_failed"
        assert "'list' object has no attribute 'items'" in table.error.message
        meta = yaml.safe_load((archive / "meta.yaml").read_text())
        assert meta["tables"][0]["error"]["kind"] == "run_failed"


class TestTableErrorCarriesItsFix:
    """A collector that knows the remedy says so; the table's error keeps it."""

    class _Refuses:
        def __init__(self, fix: str | None) -> None:
            self._fix = fix

        def collect(
            self,
            entry: CollectorEntry,
            source: SourceEntry | None,
            *,
            captures: CaptureLog | None = None,
        ) -> Iterator[dict[str, Any]]:
            raise CollectionError("limit reached", kind="run_failed", fix=self._fix)
            yield {}

    def _error(self, tmp_path: Path, fix: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        config = ReconConfig.model_validate({"collectors": [{"name": "t", "type": "x"}]})
        events: list[dict[str, Any]] = []
        archive, results = run(
            config, tmp_path, plugins=plugins_with(x=self._Refuses(fix)), on_event=events.append
        )
        meta = yaml.safe_load((archive / "meta.yaml").read_text())
        error = results[0].document()["error"]
        assert meta["tables"][0]["error"] == error
        [event] = [e for e in events if e["kind"] == "error"]
        return error, event

    def test_fix_reaches_the_results_meta_and_event(self, tmp_path: Path) -> None:
        error, event = self._error(tmp_path, "raise the limit")
        assert error == {
            "kind": "run_failed",
            "message": "[t] limit reached",
            "fix": "raise the limit",
        }
        assert event["fix"] == "raise the limit"

    def test_an_error_without_a_remedy_has_a_null_fix(self, tmp_path: Path) -> None:
        """Changed requirement: a table error is one record, so ``fix`` is always present,
        null when the collector named no remedy (as in the error envelope)."""
        error, _event = self._error(tmp_path, None)
        assert error == {"kind": "run_failed", "message": "[t] limit reached", "fix": None}


class TestSinkClosesTheGenerator:
    """`write_table` must close a SUSPENDED generator when the consumer fails, so the
    collector's cleanup runs: subprocess kill, httpx2 close, rate-limit release.

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
    def _circular() -> dict[str, Any]:
        d: dict[str, Any] = {}
        d["self"] = d
        return d

    def test_suspended_generator_is_closed_when_the_consumer_fails(self, tmp_path: Path) -> None:
        closed: list[bool] = []

        def make() -> Generator[dict[str, Any], None, None]:
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
            "subprocess / httpx2 client / rate-limit token"
        )
        assert inspect.getgeneratorstate(gen) == inspect.GEN_CLOSED

    def test_generator_closed_on_normal_completion(self, tmp_path: Path) -> None:
        closed: list[bool] = []

        def make() -> Generator[dict[str, Any], None, None]:
            try:
                yield {"a": 1}
                yield {"a": 2}
            finally:
                closed.append(True)

        gen = make()
        assert ArchiveFiles(SystemClock()).write_table(tmp_path, "out", gen) == 2
        assert closed == [True]
        assert inspect.getgeneratorstate(gen) == inspect.GEN_CLOSED

    def test_partial_tmp_is_removed_on_failure(self, tmp_path: Path) -> None:
        def make() -> Generator[dict[str, Any], None, None]:
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

    def test_the_marker_exists_while_the_survey_runs(self, tmp_path: Path) -> None:
        seen: list[bool] = []

        class Looks:
            def collect(
                self,
                entry: CollectorEntry,
                source: SourceEntry | None,
                *,
                captures: CaptureLog | None = None,
            ) -> Iterator[dict[str, Any]]:
                [archive] = (tmp_path / ".recon" / "m" / "archive").iterdir()
                seen.append((archive / ".incomplete").exists())
                yield {"a": 1}

        config = ReconConfig.model_validate({"collectors": [{"name": "t", "type": "x"}]})
        archive, _ = run(config, tmp_path, plugins=plugins_with(x=Looks()))
        assert seen == [True]
        assert not (archive / ".incomplete").exists(), "removed once every table succeeded"

    def test_a_meta_yaml_that_cannot_be_written_leaves_the_marker(self, tmp_path: Path) -> None:
        class NoMeta(ArchiveFiles):
            def write_meta(self, archive: Path, meta: dict[str, Any]) -> NoReturn:
                raise OSError("disk full")

        events: list[dict[str, Any]] = []
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

    def test_a_finished_survey_says_so(self, tmp_path: Path) -> None:
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

    def _survey(
        self, tmp_path: Path, entry: dict[str, Any], body: str = BODY
    ) -> tuple[list[TableOutcome], list[Problem]]:
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

    def test_one_record_holding_a_list_of_objects_is_warned_with_the_extract(
        self, tmp_path: Path
    ) -> None:
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
        self, tmp_path: Path, body: str, extract: str
    ) -> None:
        """Regression (changed requirement): a correct extract and a one-record probe were
        told to set ``extract: feed.entry.author`` (arXiv) or ``results.concepts`` (OpenAlex),
        a fix that would have made a table of authors. A collector with an extract is not
        warned about the lists inside its one record."""
        tables, warnings = self._survey(tmp_path, {"extract": extract}, body)
        assert tables[0].records == 1 and warnings == []

    def test_the_right_extract_is_not_warned(self, tmp_path: Path) -> None:
        tables, warnings = self._survey(tmp_path, {"extract": "feed.entry"})
        assert tables[0].records == 2 and warnings == []


class TestNoPlausibleEmptyTable:
    """Regression: a response whose records recon could not find or keep wrote an empty
    table and "ok", which reads as a source that had nothing: a 200 carrying an error object
    under ``extract: data``, or a list of ids. The table is kept, empty, and warned."""

    def _survey(
        self, tmp_path: Path, body: Any, **entry: Any
    ) -> tuple[list[TableOutcome], list[Problem], list[dict[str, Any]], Path]:
        config = ReconConfig.model_validate({
            "catalog": [{"name": "s", "url": "https://api.example.test"}],
            "collectors": [{"name": "hits", "type": "api", "source": "s", "endpoint": "/q",
                            **entry}],
        })  # fmt: skip
        requester = ScriptedRequester({"https://api.example.test/q": json.dumps(body)})
        events: list[dict[str, Any]] = []
        clock = FixedClock()
        archives = ArchiveFiles(clock)
        archive = archives.create(tmp_path, "a")
        survey = Survey(archives, clock, "0", Telemetry([], clock), events.append)
        collectors = {"api": ApiCollector(requester, {}, DEFAULT_USER_AGENT)}
        tables = survey.run(config, collectors, archive, started=clock.now())
        return tables, survey.warnings, events, archive

    @pytest.mark.parametrize(
        ("body", "entry", "kind"),
        [
            ({"error": {"message": "quota exceeded"}}, {"extract": "data"}, "extract_unresolved"),
            ({"ids": ["W1", "W2"]}, {"extract": "ids"}, "records_not_objects"),
            (["W1", "W2"], {}, "records_not_objects"),
        ],
        ids=["error-object", "ids-at-extract", "ids-as-body"],
    )
    def test_a_table_recon_could_not_fill_is_kept_empty_and_warned(
        self, tmp_path: Path, body: dict[str, Any] | list[Any], entry: dict[str, Any], kind: str
    ) -> None:
        tables, [warning], events, archive = self._survey(tmp_path, body, **entry)
        assert (tables[0].status, tables[0].records) == ("ok", 0)
        assert (archive / "hits.jsonl").read_text() == '{"_empty": true}\n'
        assert warning.kind == kind and warning.path == "collectors.0.extract"
        assert warning.kind in recon.PROBLEM_KINDS
        assert warning.message.startswith("hits: ") and warning.fix
        assert ("table_warning", "hits") in [(e["kind"], e.get("name")) for e in events]

    def test_a_source_that_had_nothing_is_not_warned(self, tmp_path: Path) -> None:
        tables, warnings, _, _ = self._survey(tmp_path, {"data": []}, extract="data")
        assert tables[0].records == 0 and warnings == []

    def test_a_signal_after_records_fails_the_table_rather_than_emptying_it(
        self, tmp_path: Path
    ) -> None:
        """A collector type that says ``NoRecordsError`` after yielding records has not found
        nothing: keeping an empty table would drop what it gave."""

        class Late:
            def collect(
                self,
                entry: CollectorEntry,
                source: SourceEntry | None,
                *,
                captures: CaptureLog | None = None,
            ) -> Iterator[dict[str, Any]]:
                yield {"a": 1}
                raise NoRecordsError("too late", problem_kind="x", field="extract", fix="f")

        config = ReconConfig.model_validate({"collectors": [{"name": "t", "type": "late"}]})
        _archive, [table] = run(config, tmp_path, plugins=plugins_with(late=Late()))
        assert table.status == "error"


class TestDeadlines:
    """Regression: nothing bounded a survey or a table in time, so one slow source held up
    every table after it, for as long as it liked."""

    def test_a_survey_s_deadline_fails_the_table_it_cuts_and_starts_no_other(
        self, tmp_path: Path
    ) -> None:
        config = {
            "deadline": 1,
            "collectors": [
                {"name": "first", "type": "cli", "run": "sleep 5"},
                {"name": "second", "type": "cli", "run": "echo never"},
            ],
        }
        started = time.monotonic()
        archive, tables = run(config, tmp_path)
        assert time.monotonic() - started < 3
        assert [(t.name, t.status) for t in tables] == [("first", "error"), ("second", "error")]
        for table in tables:
            assert table.error is not None
            assert table.error.kind == "transient" and "deadline" in (table.error.fix or "")
        assert tables[1].error is not None
        assert "survey's deadline" in tables[1].error.message
        assert "not started" in tables[1].error.message
        assert (archive / ".incomplete").exists()

    def test_a_table_s_deadline_fails_that_table_alone(self, tmp_path: Path) -> None:
        config = {
            "collectors": [
                {"name": "slow", "type": "cli", "run": "sleep 5", "deadline": 0.5},
                {"name": "quick", "type": "cli", "run": "echo hi"},
            ],
        }
        started = time.monotonic()
        _archive, tables = run(config, tmp_path)
        assert time.monotonic() - started < 3
        assert [(t.name, t.status) for t in tables] == [("slow", "error"), ("quick", "ok")]
        assert tables[0].error is not None
        assert "slow's deadline" in tables[0].error.message
        assert (tables[0].error.fix or "").startswith("raise deadline:")

    def test_the_next_step_after_a_transient_exit_tells_a_deadline_from_a_retry(self) -> None:
        """Regression: exit 5's next step was "survey again later", but a deadline too short
        for the work fails the same way on every retry. Then it led with "raise the
        deadline" for every transient exit, where for a 429 or a 5xx a later retry is what
        helps. It says which step goes with which error: the deadline a table's error names,
        else a later retry. Then it read as a garden path ("raise a `deadline:` a table's
        error names"), and a source's ``timeout:`` that is too short, which also fails on
        every retry, was sent to the later retry: it is raised too."""
        from recon.domain.errors import EXIT_NEXT

        said = EXIT_NEXT["transient"]
        assert said.startswith(
            "if a table's error names a `deadline:` or `timeout:`, raise it (a retry fails too)"
        )
        assert said.endswith("; else survey again later")

    @pytest.mark.parametrize("value", [0, -1, math.nan, math.inf])
    def test_a_deadline_must_be_a_positive_number_of_seconds(
        self, tmp_path: Path, value: float
    ) -> None:
        """Regression: ``deadline: .nan`` passed the plan's ``<= 0`` check and then failed
        building the config, as a raw ValidationError (exit 1, ``unknown``); ``.inf`` was
        taken as a bound."""
        with pytest.raises(recon.ConfigError) as excinfo:
            run({"deadline": value, "collectors": [{"name": "c", "type": "cli", "run": "true"}]},
                tmp_path)  # fmt: skip
        assert [p.path for p in excinfo.value.problems] == ["deadline"]

    @pytest.mark.parametrize("value", [math.nan, math.inf])
    def test_a_table_s_deadline_must_be_finite(self, tmp_path: Path, value: float) -> None:
        collector = {"name": "c", "type": "cli", "run": "true", "deadline": value}
        with pytest.raises(recon.ConfigError) as excinfo:
            run({"collectors": [collector]}, tmp_path)
        assert [p.path for p in excinfo.value.problems] == ["collectors.0.deadline"]

    @pytest.mark.parametrize("value", [math.nan, math.inf])
    @pytest.mark.parametrize(
        ("setting", "path"),
        [
            ({"timeout": None}, "catalog.0.timeout"),
            ({"rate_limit": {"rps": None}}, "catalog.0.rate_limit.rps"),
        ],
    )
    def test_a_source_s_timeout_and_rate_must_be_finite(
        self, tmp_path: Path, value: float, setting: dict[str, Any], path: str
    ) -> None:
        """Regression: ``timeout: .inf`` planned valid and every request then failed
        (``OverflowError``, the table ``run_failed`` with no fix); ``rps: .inf`` planned valid
        too. Each is a plan problem at the key written."""
        key, inner = next(iter(setting.items()))
        written = value if inner is None else {next(iter(inner)): value}
        source = {"name": "s", "url": "https://api.example.test", key: written}
        collector = {"name": "q", "type": "api", "source": "s", "endpoint": "/q"}
        config = {"catalog": [source], "collectors": [collector]}
        with pytest.raises(recon.ConfigError) as excinfo:
            run(config, tmp_path)
        assert [p.path for p in excinfo.value.problems] == [path]

    @pytest.mark.parametrize(
        ("setting", "path"),
        [
            ({"timeout": 1e12}, "catalog.0.timeout"),
            ({"rate_limit": {"rps": 1e-300}}, "catalog.0.rate_limit.rps"),
        ],
    )
    def test_a_source_s_timeout_and_rate_must_be_waits_the_platform_can_time(
        self, tmp_path: Path, setting: dict[str, Any], path: str
    ) -> None:
        """Regression: ``timeout: 1e12`` and ``rps: 1e-300`` are finite and planned valid,
        and with no deadline every request then failed (``OverflowError: timestamp out of
        range for platform time_t``, the table ``run_failed`` with no fix): a socket's
        timeout of 1e12 s, and the rate limit's wait for a token, ``1 / rps``, are longer
        than the platform's timers take. Each is a plan problem at the key written."""
        source = {"name": "s", "url": "https://api.example.test", **setting}
        collector = {"name": "q", "type": "api", "source": "s", "endpoint": "/q"}
        config = {"catalog": [source], "collectors": [collector]}
        with pytest.raises(recon.ConfigError) as excinfo:
            run(config, tmp_path)
        assert [p.path for p in excinfo.value.problems] == [path]


class TestWhatASurveyResolved:
    """Each survey's ``meta.yaml`` records where what it resolved came from: how the project
    root was found, and which variables the mission declares were set, by name. A value is
    never written."""

    CONFIG: ClassVar[dict[str, Any]] = {
        "catalog": [
            {"name": "a", "url": "https://api.example.test", "auth": {"env": "A_KEY"}},
            {"name": "b", "url": "https://api.example.test", "auth": {"env": "B_KEY"}},
        ],
        "collectors": [{"name": "q", "type": "api", "endpoint": "/q"}],
    }

    def _meta(self, tmp_path: Path, **kwargs: Any) -> tuple[dict[str, Any], str]:
        requester = ScriptedRequester({"https://api.example.test/q": "[]"})
        archive, _ = run(
            self.CONFIG,
            tmp_path,
            requester=lambda telemetry, deadline, rate_limiter: requester,
            **kwargs,
        )
        text = (archive / "meta.yaml").read_text()
        meta: dict[str, Any] = yaml.safe_load(text)
        return meta, text

    def test_the_variables_declared_are_named_set_or_unset_and_never_given(
        self, tmp_path: Path
    ) -> None:
        meta, text = self._meta(tmp_path, env={"A_KEY": "s3cret-value", "OTHER": "x"})
        assert meta["resolved"]["env"] == {"A_KEY": "set", "B_KEY": "unset"}
        assert "s3cret-value" not in text and "OTHER" not in text

    def test_a_root_the_caller_named_is_recorded_as_given(self, tmp_path: Path) -> None:
        meta, _text = self._meta(tmp_path)
        assert meta["resolved"]["root"] == "argument"

    @pytest.mark.parametrize(
        ("git", "found_by"), [(True, "repository"), (False, "working_directory")]
    )
    def test_a_root_recon_found_says_how(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git: bool, found_by: str
    ) -> None:
        from recon.composition import root

        project = tmp_path / "project"
        (project / "src").mkdir(parents=True)
        if git:
            (project / ".git").mkdir()
        monkeypatch.chdir(project / "src" if git else project)
        mission = project / ".recon" / "m"
        mission.mkdir(parents=True)
        (mission / "config.yaml").write_text("collectors: [{name: c, type: cli, run: 'true'}]\n")
        document = root({}).survey("m")
        meta = yaml.safe_load((Path(document.archive) / "meta.yaml").read_text())
        assert meta["resolved"]["root"] == found_by
