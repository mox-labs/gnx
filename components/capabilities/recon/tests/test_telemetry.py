"""Telemetry: the spans recon opens, the event log every survey keeps, the HTTP client spans
and their redaction, OpenTelemetry's spans and metrics, and export set up from ``OTEL_*``.

The HTTP tests talk to a server on 127.0.0.1 started by the test; nothing leaves the machine.
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

import recon
from recon.adapters._in.cli import main
from recon.adapters._out import otel_telemetry
from recon.adapters._out.http_requester import HttpxRequester
from recon.adapters._out.otel_telemetry import OtelEvents, export_from_env
from recon.domain.errors import CollectionError
from recon.domain.models import SourceEntry
from recon.domain.ratelimit import RateLimiter
from recon.domain.telemetry import Telemetry
from tests.recon_fakes import FixedClock


class Recorded:
    """An event sink that keeps every event."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def on_event(self, event: Any) -> None:
        self.events.append(event)

    def ends(self, name: str) -> list[Any]:
        return [e for e in self.events if e.name == f"{name}.end"]


# --- spans ---------------------------------------------------------------------------------


def test_spans_nest_and_count_up_and_their_end_says_how_they_went() -> None:
    sink = Recorded()
    telemetry = Telemetry([sink], FixedClock())
    with telemetry.span("recon.survey", {"recon.mission": "m"}) as outer:
        with telemetry.span("recon.collect", {"recon.source": None}) as inner:
            inner.set("recon.records", 3)
        outer.fail("run_failed", "1 of 1 table(s) failed\nmore detail")

    names = [(e.name, e.span, e.parent) for e in sink.events]
    assert names == [
        ("recon.survey.start", "1", None),
        ("recon.collect.start", "2", "1"),
        ("recon.collect.end", "2", "1"),
        ("recon.survey.end", "1", None),
    ]
    collect_end, survey_end = sink.events[2], sink.events[3]
    assert collect_end.status == "ok" and collect_end.attributes == {"recon.records": 3}
    assert survey_end.status == "error" and survey_end.message == "1 of 1 table(s) failed"
    assert survey_end.attributes == {"recon.mission": "m", "error.type": "run_failed"}
    assert survey_end.duration_ms == 0.0
    assert survey_end.document()["schema"] == "recon.v1.event"


@pytest.mark.parametrize(
    ("raised", "error_type"),
    [
        (CollectionError("x", kind="transient"), "transient"),
        (KeyboardInterrupt(), "interrupted"),
        (RuntimeError("bug"), "RuntimeError"),
    ],
)
def test_an_exception_fails_its_span_and_goes_on(raised: BaseException, error_type: str) -> None:
    sink = Recorded()
    telemetry = Telemetry([sink], FixedClock())
    with pytest.raises(type(raised)), telemetry.span("recon.query"):
        raise raised
    [end] = sink.ends("recon.query")
    assert end.status == "error" and end.attributes["error.type"] == error_type


def test_a_sink_that_raises_is_dropped_and_named_and_the_work_goes_on() -> None:
    class Broken:
        def on_event(self, event: Any) -> None:
            raise OSError("disk full")

    sink = Recorded()
    telemetry = Telemetry([Broken(), sink], FixedClock())
    with telemetry.span("recon.survey"):
        pass
    assert len(sink.events) == 2
    assert telemetry.failed == ["Broken: OSError: disk full"]


# --- the event log -------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    for name in otel_telemetry.EXPORT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


def test_every_survey_keeps_its_events_in_its_archive(project: Path) -> None:
    config = project / ".recon" / "m" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        yaml.safe_dump(
            {
                "collectors": [
                    {"name": "good", "type": "cli", "run": "echo '{\"a\": 1}'; echo '{}'"},
                    {"name": "bad", "type": "cli", "run": "exit 7"},
                ]
            }
        )
    )
    survey = json.loads(_invoke("survey", "m", "--json").stdout)
    archive = Path(survey["archive"])
    events = [json.loads(line) for line in (archive / "telemetry.jsonl").read_text().splitlines()]

    assert [e["name"] for e in events] == [
        "recon.survey.start",
        "recon.collect.start",
        "recon.collect.end",
        "recon.collect.start",
        "recon.collect.end",
        "recon.survey.end",
    ]
    ends = {e["attributes"].get("recon.table", "survey"): e for e in events if e["status"]}
    assert ends["good"]["status"] == "ok" and ends["good"]["attributes"]["recon.records"] == 2
    assert ends["bad"]["status"] == "error"
    assert ends["bad"]["attributes"]["error.type"] == "run_failed"
    assert ends["survey"]["attributes"] == {
        "recon.mission": "m",
        "recon.archive": archive.name,
        "recon.tables": 2,
        "recon.tables.failed": 1,
        "recon.records": 2,
        "error.type": "run_failed",
    }
    assert {e["parent"] for e in events if e["name"].startswith("recon.collect")} == {"1"}


def test_the_event_log_is_not_a_table(project: Path) -> None:
    config = project / ".recon" / "m" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump({"collectors": [{"name": "t", "type": "cli", "run": "true"}]}))
    _invoke("survey", "m")
    status = json.loads(_invoke("status", "--json").stdout)
    assert [t["name"] for t in status["missions"][0]["latest"]["tables"]] == ["t"]
    result = _invoke("query", "m", "SELECT * FROM telemetry", "--json")
    assert result.exit_code == recon.EXIT_CODES["query"]


# --- HTTP client spans ---------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        code = 503 if self.path.startswith("/busy") else 200
        body = b'{"ok": true}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def server() -> Any:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()


def _requester(sink: Recorded) -> HttpxRequester:
    clock = FixedClock()
    return HttpxRequester(RateLimiter(clock), clock, Telemetry([sink], clock))


def test_a_request_is_a_client_span_with_the_required_attributes(server: Any) -> None:
    sink = Recorded()
    port = server.server_address[1]
    source = SourceEntry(name="s", url=f"http://127.0.0.1:{port}", auth={"param": "api_key"})
    _requester(sink).request(source, "GET", f"{source.url}/ok", params={"api_key": "s3cret"})

    [start] = [e for e in sink.events if e.name == "recon.http.request.start"]
    [end] = sink.ends("recon.http.request")
    assert start.kind == "client"
    assert end.attributes == {
        "http.request.method": "GET",
        "server.address": "127.0.0.1",
        "server.port": port,
        "url.full": f"http://127.0.0.1:{port}/ok?api_key=REDACTED",
        "http.response.status_code": 200,
    }
    assert "s3cret" not in json.dumps([e.document() for e in sink.events])


def test_each_retry_is_its_own_span_with_its_resend_count(server: Any) -> None:
    sink = Recorded()
    source = SourceEntry(name="s", url=f"http://127.0.0.1:{server.server_address[1]}")
    with pytest.raises(CollectionError):
        _requester(sink).request(source, "GET", f"{source.url}/busy")
    ends = sink.ends("recon.http.request")
    assert [e.attributes.get("http.request.resend_count") for e in ends] == [None, 1, 2]
    assert {e.attributes["error.type"] for e in ends} == {"503"}
    assert all(e.status == "error" and e.attributes["http.response.status_code"] == 503
               for e in ends)  # fmt: skip


def test_a_transport_failure_names_its_exception_and_credentials_in_the_url_are_redacted() -> None:
    sink = Recorded()
    source = SourceEntry(name="s", url="http://user:pw@127.0.0.1:9", timeout=2)
    with pytest.raises(CollectionError):
        _requester(sink).request(source, "GET", f"{source.url}/x?sig=abc&q=1")
    end = sink.ends("recon.http.request")[0]
    assert end.attributes["error.type"] == "httpx.ConnectError"
    assert end.attributes["url.full"] == ("http://REDACTED:REDACTED@127.0.0.1:9/x?sig=REDACTED&q=1")
    assert "http.response.status_code" not in end.attributes


# --- OpenTelemetry -------------------------------------------------------------------------


def test_events_become_otel_spans_and_metrics() -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import SpanKind, StatusCode

    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    sink = OtelEvents(
        "9.9", tracer_provider=tracer_provider, meter_provider=MeterProvider([reader])
    )
    telemetry = Telemetry([sink], FixedClock())
    with telemetry.span("recon.survey", {"recon.mission": "m"}):
        for table, outcome in (("ok", None), ("bad", "auth")):
            about = {"recon.table": table, "recon.collector.type": "api"}
            with telemetry.span("recon.collect", about) as collect:
                with telemetry.span(
                    "recon.http.request", {"http.request.method": "POST"}, kind="client"
                ):
                    pass
                if outcome:
                    collect.fail(outcome, "HTTP 401")
                else:
                    collect.set("recon.records", 5)

    spans = {s.name + "/" + str(s.attributes.get("recon.table", "")): s
             for s in exporter.get_finished_spans()}  # fmt: skip
    survey = spans["recon.survey/"]
    assert survey.instrumentation_scope.name == "recon"
    assert survey.instrumentation_scope.schema_url == "https://opentelemetry.io/schemas/1.44.0"
    assert spans["recon.collect/ok"].parent.span_id == survey.context.span_id
    assert spans["recon.collect/bad"].status.status_code == StatusCode.ERROR
    posts = [s for s in exporter.get_finished_spans() if s.name == "POST"]
    assert len(posts) == 2 and all(s.kind == SpanKind.CLIENT for s in posts)

    data = reader.get_metrics_data()
    assert data is not None
    points = {
        metric.name: [(dict(p.attributes), p.value) for p in metric.data.data_points]
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert points["recon.collect.records"] == [({"recon.collector.type": "api"}, 5)]
    assert points["recon.collect.failures"] == [
        ({"recon.collector.type": "api", "error.type": "auth"}, 1)
    ]


def test_without_the_variables_nothing_is_installed_and_nothing_is_warned() -> None:
    assert export_from_env({}, "9.9") == []
    assert export_from_env({"OTEL_TRACES_EXPORTER": "otlp", "OTEL_SDK_DISABLED": "true"},
                           "9.9") == []  # fmt: skip


def test_the_variables_without_the_sdk_are_a_warning_in_the_json_document(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(env: Any, version: str) -> Any:
        raise ImportError("No module named 'opentelemetry.sdk'", name="opentelemetry.sdk")

    monkeypatch.setattr(otel_telemetry, "_provider_installed", lambda: False)
    monkeypatch.setattr(otel_telemetry, "_install", missing)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    config = project / ".recon" / "m" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump({"collectors": [{"name": "t", "type": "cli", "run": "true"}]}))

    for args in (["survey", "m", "-n", "--json"], ["survey", "m", "--json"]):
        result = _invoke(*args)
        assert result.exit_code == 0, result.stderr
        [warning] = json.loads(result.stdout)["warnings"]
        assert warning["kind"] == "otel_unavailable"
        assert warning["got"] == "OTEL_EXPORTER_OTLP_ENDPOINT"
        assert "recon[otel]" in warning["fix"]
    query = json.loads(_invoke("query", "m", "SELECT 1 AS one", "--json").stdout)
    assert [w["kind"] for w in query["warnings"]] == ["otel_unavailable"]


def _spy_on_install(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every SDK install ``export_from_env`` would make, and make none."""
    installed: list[str] = []
    monkeypatch.setattr(otel_telemetry, "_provider_installed", lambda: False)

    def install(env: Any, version: str) -> list[Any]:
        installed.extend(name for name in otel_telemetry.EXPORT_VARIABLES if env.get(name))
        return []

    monkeypatch.setattr(otel_telemetry, "_install", install)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    return installed


def test_the_library_installs_no_provider_whatever_otel_says(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenTelemetry's guidance: a library uses the API, the application configures the SDK.
    A program importing recon with OTEL_* set keeps whatever it set up, or nothing."""
    installed = _spy_on_install(monkeypatch)
    cfg = project / "mission.yaml"
    cfg.write_text(yaml.safe_dump({"collectors": [{"name": "t", "type": "cli", "run": "true"}]}))
    recon.init("m", config=cfg)
    assert recon.plan("m").warnings == []
    survey = recon.survey("m")
    assert survey.status == "ok" and survey.warnings == []
    assert recon.query("m", "SELECT 1 AS one").warnings == []
    assert installed == []
    assert recon.configure_telemetry() == [] and installed == ["OTEL_EXPORTER_OTLP_ENDPOINT"]


def test_the_command_line_installs_the_sdk_from_otel(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _spy_on_install(monkeypatch)
    config = project / ".recon" / "m" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump({"collectors": [{"name": "t", "type": "cli", "run": "true"}]}))
    assert _invoke("survey", "m", "--json").exit_code == 0
    assert installed == ["OTEL_EXPORTER_OTLP_ENDPOINT"]


class _Collector(http.server.BaseHTTPRequestHandler):
    """An OTLP/HTTP receiver that keeps what it is sent."""

    received: list[tuple[str, bytes]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Collector.received.append((self.path, body))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


def test_setting_the_standard_variables_is_enough_to_export(project: Path) -> None:
    """In a process of its own, as a person runs it: the endpoint variable and nothing else."""
    collector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=collector.serve_forever, daemon=True).start()
    config = project / ".recon" / "m" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump({"collectors": [{"name": "t", "type": "cli", "run": "true"}]}))
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"http://127.0.0.1:{collector.server_address[1]}"
    try:
        result = subprocess.run(
            [str(Path(sys.executable).with_name("recon")), "survey", "m", "--json"],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        collector.shutdown()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["warnings"] == []
    traces = b"".join(body for path, body in _Collector.received if path == "/v1/traces")
    assert b"recon.survey" in traces and b"recon.collect" in traces
    metrics = b"".join(body for path, body in _Collector.received if path == "/v1/metrics")
    assert b"recon.collect.records" in metrics


def test_an_unsupported_exporter_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[str] = []
    from opentelemetry import metrics, trace

    monkeypatch.setattr(otel_telemetry, "_provider_installed", lambda: False)
    monkeypatch.setattr(trace, "set_tracer_provider", lambda p: installed.append("traces"))
    monkeypatch.setattr(metrics, "set_meter_provider", lambda p: installed.append("metrics"))
    env = {"OTEL_TRACES_EXPORTER": "zipkin", "OTEL_METRICS_EXPORTER": "none"}
    [warning] = export_from_env(env, "9.9")
    assert warning.kind == "otel_unavailable" and warning.got == "zipkin"
    assert warning.allowed == ("otlp", "none")
    assert installed == ["traces"]


def test_the_otel_extra_is_declared() -> None:
    """``pip install 'recon[otel]'`` is what the warning tells a person to run."""
    from importlib.metadata import requires

    extras = [r for r in requires("recon") or [] if "extra == 'otel'" in r]
    assert any(r.startswith("opentelemetry-sdk") for r in extras)
    assert any(r.startswith("opentelemetry-exporter-otlp") for r in extras)
