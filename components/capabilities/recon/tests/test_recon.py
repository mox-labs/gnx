"""Integration tests for the Recon dispatch loop."""

import inspect
import json
import tempfile
from pathlib import Path

import pytest
import yaml

from recon.adapters._out.api_collector import ApiCollector
from recon.adapters._out.cli_collector import CliCollector
from recon.adapters._out.http_requester import HttpxRequester
from recon.adapters._out.web_collector import WebCollector
from recon.application.recon import failure_kind, run
from recon.application.utilization import RateLimiter
from recon.domain.converters import ConversionResult
from recon.domain.exceptions import ReconError
from recon.domain.models import ReconConfig
from recon.domain.substitution import substitute


class _NoOpConverter:
    """Test-only converter that returns an empty ConversionResult.

    `run()` tests focus on dispatch/sink/error paths; they never exercise
    the web collector, so the converter never gets called.
    """

    def convert(self, content, content_type, url):
        return ConversionResult(title="", text="")


def _collectors():
    req = HttpxRequester(RateLimiter())
    return {
        "cli": CliCollector(),
        "api": ApiCollector(req),
        "web": WebCollector(req, _NoOpConverter()),
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
        assert tables["bad"]["error"]["kind"] == "collection"
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


class TestCollisionDetection:
    def test_pinned_and_fanout_collide(self):
        """Fan-out of `foo` over source `arxiv` produces `foo-arxiv`,
        same name as a pinned collector `foo-arxiv`. Pre-flight must catch it."""
        config = ReconConfig.model_validate(
            {
                "catalog": [{"name": "arxiv", "type": "local", "url": "/tmp"}],
                "collectors": [
                    {"name": "foo", "type": "cli", "run": "echo hi"},
                    {"name": "foo-arxiv", "type": "cli", "source": "arxiv", "run": "echo hi"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            with pytest.raises(ReconError, match="Output name collision"):
                run(config, _collectors(), mission)
            # No archive dir should have been created
            assert not (mission / "archive").exists()


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
            statuses = {r["name"]: r["status"] for r in results}
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
        assert capture["request"]["commands"] == ["printf '{\"a\": 1}\\n{\"a\": 2}\\n'"]

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
        from recon.application.query import available_tables

        config = ReconConfig.model_validate(
            {"preserve_raw": True, "collectors": [{"name": "t", "type": "cli", "run": "true"}]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            mission = Path(tmp) / "m"
            mission.mkdir()
            archive, _ = run(config, _collectors(), mission)
            assert available_tables(archive) == ["t"]


class TestFailureKind:
    """The survey exit code is chosen from the failed tables' kinds."""

    @staticmethod
    def _results(*kinds):
        return [
            {"name": f"t{i}", "status": "error", "error": {"kind": k, "message": ""}}
            if k
            else {"name": f"t{i}", "status": "ok"}
            for i, k in enumerate(kinds)
        ]

    def test_all_ok_is_none(self):
        assert failure_kind(self._results(None, None)) is None

    def test_all_transient_is_transient(self):
        assert failure_kind(self._results(None, "transient", "transient")) == "transient"

    def test_any_auth_wins(self):
        assert failure_kind(self._results("transient", "auth", "collection")) == "auth"

    def test_mixed_is_collection(self):
        assert failure_kind(self._results("transient", "collection")) == "collection"
        assert failure_kind(self._results("unknown")) == "collection"


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


class TestSinkClosesTheGenerator:
    """`_sink` must close a SUSPENDED generator when the consumer fails.

    Regression test, and the discriminating case took two tries to find. A typing pass
    removed `contextlib.closing(records)` to silence a mypy type-var error, dropping the
    cleanup the docstring promises — subprocess kill, httpx close, rate-limit release.

    Why the obvious tests do not catch it:
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
        from recon.application.recon import _sink

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
            _sink(gen, tmp_path / "out.jsonl")
        assert closed == [True], (
            "the generator was suspended when the consumer raised; closing() must run its "
            "finally before _sink returns, or the upstream collector never releases its "
            "subprocess / httpx client / rate-limit token"
        )
        assert inspect.getgeneratorstate(gen) == inspect.GEN_CLOSED

    def test_generator_closed_on_normal_completion(self, tmp_path):
        from recon.application.recon import _sink

        closed = []

        def make():
            try:
                yield {"a": 1}
                yield {"a": 2}
            finally:
                closed.append(True)

        gen = make()
        assert _sink(gen, tmp_path / "out.jsonl") == 2
        assert closed == [True]
        assert inspect.getgeneratorstate(gen) == inspect.GEN_CLOSED

    def test_partial_tmp_is_removed_on_failure(self, tmp_path):
        from recon.application.recon import _sink

        def make():
            yield {"a": 1}
            raise RuntimeError("boom")

        out = tmp_path / "out.jsonl"
        with pytest.raises(RuntimeError):
            _sink(make(), out)
        assert not out.exists()
        assert not out.with_suffix(out.suffix + ".tmp").exists(), "partial .tmp must be cleaned"
