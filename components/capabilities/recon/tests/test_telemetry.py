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
from recon.adapters._out.http_requester import HttpRequester
from recon.adapters._out.otel_telemetry import OtelEvents, export_from_env
from recon.domain.errors import CollectionError
from recon.domain.models import AuthConfig, SourceEntry
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


def test_an_exception_s_span_message_is_its_class_and_quotes_nothing_of_it() -> None:
    """An exception no caller described (a plugin's own, a defect) fails its span with its
    class name alone: its text may hold a command or a URL's credentials, and the span's
    message goes to the OTLP endpoint and the event log."""
    sink = Recorded()
    telemetry = Telemetry([sink], FixedClock())
    with pytest.raises(RuntimeError), telemetry.span("recon.collect"):
        raise RuntimeError("curl https://user:hunter2@api.example.test/x failed")
    [end] = sink.ends("recon.collect")
    assert end.message == "RuntimeError"


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
        code = 503 if self.path.startswith("/busy") else 404 if "missing" in self.path else 200
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


def _requester(sink: Recorded) -> HttpRequester:
    clock = FixedClock()
    return HttpRequester(RateLimiter(clock), clock, Telemetry([sink], clock))


def test_a_request_is_a_client_span_with_the_required_attributes(server: Any) -> None:
    sink = Recorded()
    port = server.server_address[1]
    source = SourceEntry(name="s", url=f"http://127.0.0.1:{port}", auth=AuthConfig(param="api_key"))
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
    assert end.attributes["error.type"] == "httpx2.ConnectError"
    assert end.attributes["url.full"] == ("http://REDACTED:REDACTED@127.0.0.1:9/x?sig=REDACTED&q=1")
    assert "http.response.status_code" not in end.attributes


# --- OpenTelemetry -------------------------------------------------------------------------


def test_events_become_otel_spans_and_metrics() -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
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

    spans = {s.name + "/" + str((s.attributes or {}).get("recon.table", "")): s
             for s in exporter.get_finished_spans()}  # fmt: skip
    survey = spans["recon.survey/"]
    scope = survey.instrumentation_scope
    assert scope is not None
    assert scope.name == "recon"
    assert scope.schema_url == "https://opentelemetry.io/schemas/1.44.0"
    parent, context = spans["recon.collect/ok"].parent, survey.context
    assert parent is not None and context is not None
    assert parent.span_id == context.span_id
    assert spans["recon.collect/bad"].status.status_code == StatusCode.ERROR
    posts = [s for s in exporter.get_finished_spans() if s.name == "POST"]
    assert len(posts) == 2 and all(s.kind == SpanKind.CLIENT for s in posts)

    data = reader.get_metrics_data()
    assert data is not None
    points = {
        metric.name: [
            (dict(p.attributes or {}), p.value)
            for p in metric.data.data_points
            if isinstance(p, NumberDataPoint)
        ]
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert points["recon.collect.records"] == [({"recon.collector.type": "api"}, 5)]
    assert points["recon.collect.failures"] == [
        ({"recon.collector.type": "api", "error.type": "auth"}, 1)
    ]


def _sdk() -> tuple[Any, Any]:
    """An SDK tracer provider of the test's own, and the exporter its finished spans go to."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_a_span_a_library_opens_inside_recons_work_nests_under_it() -> None:
    """Regression: recon built its spans from events and never made one current, so a span
    another library opened inside a table (an instrumented client, a plugin's own) landed
    under whatever the caller had made current, or in a trace of its own."""
    from opentelemetry import trace

    provider, exporter = _sdk()
    library = provider.get_tracer("some.library")
    telemetry = Telemetry([OtelEvents("9.9", tracer_provider=provider)], FixedClock())
    callers = trace.get_current_span()
    with (
        telemetry.span("recon.survey"),
        telemetry.span("recon.collect"),
        library.start_as_current_span("library.call"),
    ):
        pass
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert spans["library.call"].parent is not None
    assert spans["library.call"].parent.span_id == spans["recon.collect"].context.span_id
    assert spans["recon.collect"].context.trace_id == spans["library.call"].context.trace_id
    assert trace.get_current_span() is callers, "the caller's context is back afterwards"


def test_an_event_log_span_is_the_exported_span_that_carries_its_id() -> None:
    """Regression: the event log's span ids count from 1 and OpenTelemetry's are random, and
    nothing tied one to the other, so a line of ``telemetry.jsonl`` could not be found in
    the exported trace."""
    provider, exporter = _sdk()
    recorded = Recorded()
    telemetry = Telemetry([recorded, OtelEvents("9.9", tracer_provider=provider)], FixedClock())
    with telemetry.span("recon.survey", {"recon.archive": "a1"}):
        for _ in range(2):
            with telemetry.span("recon.collect"):
                pass
    logged = {(e.name.removesuffix(".end"), e.span) for e in recorded.events
              if e.name.endswith(".end")}  # fmt: skip
    exported = {(s.name, s.attributes["recon.event.span"]) for s in exporter.get_finished_spans()}
    assert exported == logged


def test_a_sink_whose_around_raises_is_dropped_and_cannot_change_the_works_outcome() -> None:
    """``around`` runs inside the work, so it is held to what any sink is: it cannot fail the
    work or swallow its exception, and one that raises is dropped and named."""
    from contextlib import contextmanager, suppress

    class Meddles:
        def on_event(self, event: Any) -> None:
            pass

        @contextmanager
        def around(self, event: Any) -> Any:
            with suppress(RuntimeError):  # a sink trying to swallow the work's exception
                yield

    class Breaks(Meddles):
        def around(self, event: Any) -> Any:
            raise OSError("cannot")

    sink = Recorded()
    telemetry = Telemetry([Meddles(), Breaks(), sink], FixedClock())
    with pytest.raises(RuntimeError), telemetry.span("recon.collect"):
        raise RuntimeError("the work failed")
    assert telemetry.failed == ["Breaks: OSError: cannot"]
    [end] = sink.ends("recon.collect")
    assert end.status == "error" and end.attributes["error.type"] == "RuntimeError"


def test_a_dropped_sink_ends_the_spans_it_held_open() -> None:
    """Regression: a sink that raised was dropped with its OpenTelemetry spans started and
    never ended, so they were never exported: the trace lost every span open at the time."""
    provider, exporter = _sdk()

    class FailsOnItsThirdEvent(OtelEvents):
        seen = 0

        def on_event(self, event: Any) -> None:
            self.seen += 1
            if self.seen == 3:
                raise OSError("exporter hiccup")
            super().on_event(event)

    from opentelemetry.trace import StatusCode

    telemetry = Telemetry([FailsOnItsThirdEvent("9.9", tracer_provider=provider)], FixedClock())
    with telemetry.span("recon.survey"):
        with telemetry.span("recon.collect"):
            pass
        with telemetry.span("recon.collect"):
            pass
    assert telemetry.failed == ["FailsOnItsThirdEvent: OSError: exporter hiccup"]
    ended = exporter.get_finished_spans()
    assert sorted(s.name for s in ended) == ["recon.collect", "recon.survey"]
    assert all(s.status.status_code == StatusCode.ERROR for s in ended)
    assert all("telemetry" in (s.status.description or "") for s in ended)


def test_a_request_carries_the_trace_context_of_its_attempt() -> None:
    """Regression: no ``traceparent`` went out, so a server's spans could not join recon's
    trace. Each send carries its own attempt's span as the parent."""
    import httpx2

    provider, exporter = _sdk()
    sent: list[str] = []

    def busy(request: Any) -> Any:
        sent.append(request.headers.get("traceparent", ""))
        return httpx2.Response(503)

    clock = FixedClock()
    telemetry = Telemetry([OtelEvents("9.9", tracer_provider=provider)], clock)
    requester = HttpRequester(
        RateLimiter(clock), clock, telemetry, transport=httpx2.MockTransport(busy)
    )
    source = SourceEntry(name="s", url="https://api.example.test")
    with pytest.raises(CollectionError):
        requester.request(source, "GET", "https://api.example.test/x")
    attempts = [s for s in exporter.get_finished_spans() if s.name == "GET"]
    assert len(sent) == len(attempts) == 3
    for header, attempt in zip(sent, attempts, strict=True):
        context = attempt.get_span_context()
        _version, trace_id, parent_id, _flags = header.split("-")
        assert (trace_id, parent_id) == (f"{context.trace_id:032x}", f"{context.span_id:016x}")


def test_a_request_carries_no_trace_context_when_nothing_traces() -> None:
    import httpx2

    sent: list[dict[str, str]] = []

    def answer(request: Any) -> Any:
        sent.append(dict(request.headers))
        return httpx2.Response(200, json={})

    clock = FixedClock()
    requester = HttpRequester(
        RateLimiter(clock), clock, Telemetry([Recorded()], clock),
        transport=httpx2.MockTransport(answer),
    )  # fmt: skip
    requester.request(SourceEntry(name="s", url="https://a.test"), "GET", "https://a.test/x")
    assert "traceparent" not in sent[0]


def test_without_the_variables_nothing_is_installed_and_nothing_is_warned() -> None:
    assert export_from_env({}, "9.9") == []
    assert export_from_env({"OTEL_TRACES_EXPORTER": "otlp", "OTEL_SDK_DISABLED": "true"},
                           "9.9") == []  # fmt: skip


def test_the_variables_without_the_sdk_are_a_warning_in_the_json_document(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(env: Any, version: str, signals: Any) -> Any:
        raise ImportError("No module named 'opentelemetry.sdk'", name="opentelemetry.sdk")

    _installs(monkeypatch)
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
        assert "reinstall recon" in warning["fix"]
        # Regression: the fix installed from main, not from the release that warned.
        tag = f"gnx@recon-v{recon.__version__}#subdirectory=components/capabilities/recon"
        assert tag in warning["fix"]
    query = json.loads(_invoke("query", "m", "SELECT 1 AS one", "--json").stdout)
    assert [w["kind"] for w in query["warnings"]] == ["otel_unavailable"]


def _spy_on_install(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every SDK install ``export_from_env`` would make, and make none."""
    installed: list[str] = []
    _installs(monkeypatch)

    def install(env: Any, version: str, signals: Any) -> list[Any]:
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


def test_a_failure_exports_no_command_and_no_credential_in_a_url(
    project: Path, server: Any
) -> None:
    """Regression: a failed span's status was the first line of its error, so a failed
    ``cli`` command's whole text, and a source URL's user information, went to the OTLP
    endpoint. A failed span says what kind of failure it was and quotes nothing from it.

    In a process of its own, exporting to a receiver started by the test."""
    collector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=collector.serve_forever, daemon=True).start()
    _Collector.received.clear()
    keyed = f"http://user:PASSWORD-TOKEN@127.0.0.1:{server.server_address[1]}"
    config = project / ".recon" / "m" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        yaml.safe_dump(
            {
                "catalog": [
                    {"name": "keyed", "url": keyed},
                    {"name": "small", "url": keyed, "max_bytes": 5},
                ],
                "collectors": [
                    {"name": "cmd", "type": "cli", "run": "echo COMMAND-TOKEN >&2; exit 3"},
                    {"name": "gone", "type": "api", "source": "keyed", "endpoint": "/missing"},
                    {"name": "big", "type": "api", "source": "small", "endpoint": "/ok"},
                ],
            }
        )
    )
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
    survey = json.loads(result.stdout)
    tables = survey["tables"]
    assert {t["name"] for t in tables} >= {"gone", "big"} and len(tables) > 2
    assert {t["status"] for t in tables} == {"error"}
    traces = b"".join(body for path, body in _Collector.received if path == "/v1/traces")
    events = (Path(survey["archive"]) / "telemetry.jsonl").read_text()
    assert b"recon.collect" in traces
    for exported in (traces, events.encode()):
        assert b"COMMAND-TOKEN" not in exported and b"echo" not in exported
        assert b"PASSWORD-TOKEN" not in exported and b"user:" not in exported
    assert b"meta.yaml" in traces, "a failed table's span says where its error is"
    meta = (Path(survey["archive"]) / "meta.yaml").read_text()
    assert "PASSWORD-TOKEN" not in meta and "REDACTED:REDACTED@127.0.0.1" in meta
    assert "COMMAND-TOKEN" in meta, "the archive keeps the error, for whoever reads it"


def test_an_unsupported_exporter_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    installed = _installs(monkeypatch)
    env = {"OTEL_TRACES_EXPORTER": "zipkin", "OTEL_METRICS_EXPORTER": "none"}
    [warning] = export_from_env(env, "9.9")
    assert warning.kind == "otel_unavailable" and warning.got == "zipkin"
    assert warning.allowed == ("otlp", "none")
    assert installed == ["traces"]


def test_the_sdk_and_the_exporters_are_installed_with_recon() -> None:
    """The command line is an application, and an application carries the SDK and OTLP: the
    ``OTEL_*`` variables are enough, with no extra to install first (owner's rulings Q4,
    Q13). The library still writes through the API alone."""
    from importlib.metadata import requires

    plain = [r for r in requires("recon") or [] if "extra ==" not in r]
    assert any(r.startswith("opentelemetry-api") for r in plain)
    assert any(r.startswith("opentelemetry-sdk") for r in plain)
    assert any(r.startswith("opentelemetry-exporter-otlp") for r in plain)


def _installs(
    monkeypatch: pytest.MonkeyPatch, *, tracer: Any = None, meter: Any = None
) -> list[str]:
    """Spy on the providers ``export_from_env`` sets, by signal, and set none. ``tracer`` and
    ``meter`` are the ones the program installed already; None is none, the API's default."""
    from opentelemetry import metrics, trace
    from opentelemetry.metrics._internal import _ProxyMeterProvider

    installed: list[str] = []
    default_tracer, default_meter = trace.ProxyTracerProvider(), _ProxyMeterProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: tracer or default_tracer)
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: meter or default_meter)
    monkeypatch.setattr(trace, "set_tracer_provider", lambda p: installed.append("traces"))
    monkeypatch.setattr(metrics, "set_meter_provider", lambda p: installed.append("metrics"))
    return installed


def test_a_signal_with_its_own_endpoint_alone_exports_that_signal_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: any export variable installed both providers, so a traces endpoint alone
    also sent metrics to the default endpoint, which nothing listened on."""
    installed = _installs(monkeypatch)
    env = {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:4318"}
    assert export_from_env(env, "9.9") == []
    assert installed == ["traces"]


def test_a_provider_the_program_installed_is_kept_signal_by_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: only the tracer provider was looked at, so a program that installed its
    own tracer provider got no metrics from recon, and one that installed its own meter
    provider had recon try to replace it."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
    installed = _installs(monkeypatch, tracer=TracerProvider())
    assert export_from_env(env, "9.9") == []
    assert installed == ["metrics"]

    installed = _installs(monkeypatch, meter=MeterProvider())
    assert export_from_env(env, "9.9") == []
    assert installed == ["traces"]


# --- attributes a consumer reads -------------------------------------------------------------


def test_a_body_over_max_bytes_fails_its_span_as_body_too_large(server: Any) -> None:
    """``error.type`` on a request span is the status, the httpx2 class, or
    ``body_too_large``: a consumer counts refusals by it."""
    sink = Recorded()
    port = server.server_address[1]
    source = SourceEntry(name="s", url=f"http://127.0.0.1:{port}", max_bytes=1)
    with pytest.raises(CollectionError):
        _requester(sink).get(source, f"http://127.0.0.1:{port}/ok")
    [end] = sink.ends("recon.http.request")
    assert end.status == "error" and end.attributes["error.type"] == "body_too_large"


def test_a_query_span_ends_with_the_count_of_its_rows(tmp_path: Path) -> None:
    """``recon.query`` carries ``recon.rows``, the number of rows the query returned."""
    from recon.adapters._out.archive_files import ArchiveFiles
    from recon.adapters._out.duckdb_query import DuckdbQuery
    from recon.adapters._out.mission_files import MissionFiles
    from recon.application.use_cases import Recon
    from recon.composition import load_plugins
    from tests.recon_fakes import run_survey

    run_survey(
        {"collectors": [{"name": "t", "type": "cli", "run": 'printf \'{"a": 1}\\n{"a": 2}\\n\''}]},
        tmp_path,
    )
    sink, clock = Recorded(), FixedClock()
    archives = ArchiveFiles(clock)

    def no_survey(*args: Any) -> Any:
        raise AssertionError("a query builds no collector")

    querying = Recon(
        missions=MissionFiles(tmp_path, clock),
        archives=archives,
        query=DuckdbQuery(archives),
        clock=clock,
        plugins=load_plugins(),
        build_collectors=no_survey,
        telemetry=lambda archive: Telemetry([sink], clock),
        env={},
        version="0",
    )
    querying.query("m", "SELECT a FROM t")
    [end] = sink.ends("recon.query")
    assert end.attributes["recon.rows"] == 2


def test_a_log_written_inside_recons_work_reads_recons_span_as_current() -> None:
    """A log written while recon works (a plugin's own ``logging`` call) is joined to the
    span recon has open: a log bridge stamps each record with the current span's context,
    which inside recon's work is recon's span. Read here as a bridge reads it, through
    ``trace.get_current_span()``, since the SDK's own logging handler is deprecated."""
    import logging

    from opentelemetry import trace

    provider, exporter = _sdk()
    stamped: list[tuple[int, int]] = []

    class Bridge(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            context = trace.get_current_span().get_span_context()
            stamped.append((context.trace_id, context.span_id))

    logger = logging.getLogger("some.plugin")
    bridge = Bridge()
    logger.addHandler(bridge)
    try:
        telemetry = Telemetry([OtelEvents("9.9", tracer_provider=provider)], FixedClock())
        with telemetry.span("recon.collect"):
            logger.warning("a plugin speaks")
    finally:
        logger.removeHandler(bridge)
    [span] = exporter.get_finished_spans()
    assert stamped == [(span.get_span_context().trace_id, span.get_span_context().span_id)]
