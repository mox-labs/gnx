"""recon's events as OpenTelemetry spans and metrics, and the SDK set up from ``OTEL_*``.

:class:`OtelEvents` is a sink. It writes through the OpenTelemetry API's global providers
(instrumentation scope ``recon``, schema URL of semconv v1.44.0), which do nothing until an
SDK is installed, so it costs next to nothing when nobody is listening. Each span carries
``recon.event.span``, the id its events have in the survey's event log, so a line of
``telemetry.jsonl`` is found in the exported trace as the span of that survey's trace (the
one whose root carries its ``recon.archive``) with that id. While recon's work runs, its span
is the current one (``around``), so a span another library opens meanwhile nests under it;
an HTTP request carries its attempt's span as ``traceparent`` (``trace_headers``). When the
sink is dropped (it raised), ``close`` ends the spans it holds open, as failed, so they are
exported rather than lost. Its metrics are derived from the ``recon.collect`` end events:

* ``recon.collect.records`` (counter, ``{record}``): records written, by
  ``recon.collector.type``;
* ``recon.collect.failures`` (counter, ``{table}``): tables that failed, by
  ``recon.collector.type`` and ``error.type`` (the table's error kind).

:func:`export_from_env` makes the standard variables enough to export, signal by signal.
A signal is asked for by the shared endpoint (``OTEL_EXPORTER_OTLP_ENDPOINT``), its own
(``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT``) or its exporter (``OTEL_TRACES_EXPORTER``); for each
signal asked, unless ``OTEL_SDK_DISABLED`` is ``true``, it installs the SDK's provider with
an OTLP exporter when the program has installed none for that signal. What they send, where
and how (``OTEL_EXPORTER_OTLP_*``, ``OTEL_SERVICE_NAME``, ``OTEL_RESOURCE_ATTRIBUTES``, ...)
the SDK reads from the process environment itself. The SDK and the exporters are recon's
dependencies; an install without them gets a warning instead of an export. A program that
installs its own provider keeps it: recon's spans or metrics go there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from recon.domain.errors import Problem
from recon.domain.telemetry import COLLECT, HTTP_REQUEST

if TYPE_CHECKING:
    from collections.abc import Mapping
    from contextlib import AbstractContextManager
    from datetime import datetime

    from recon.domain.telemetry import Event

#: The semantic conventions recon's attributes follow.
SCHEMA_URL = "https://opentelemetry.io/schemas/1.44.0"

#: The attribute that holds a span's id in the survey's event log.
EVENT_SPAN = "recon.event.span"

#: The signals recon exports.
SIGNALS = ("TRACES", "METRICS")

#: Any of these set means "export": the endpoints, and the exporter selections.
EXPORT_VARIABLES = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_TRACES_EXPORTER",
    "OTEL_METRICS_EXPORTER",
)

#: The tag of this release, which the install fix names: recon is installed from the
#: repository at its tag, never from a branch whose head may be another release.
RELEASE_TAG = "recon-v0.12.0"

_INSTALL_FIX = (
    "reinstall recon, which depends on the OpenTelemetry SDK and its OTLP exporter: uv tool "
    'install --reinstall "recon @ '
    f'git+https://github.com/mox-labs/gnx@{RELEASE_TAG}#subdirectory=components/capabilities/recon"'
    "; or unset the OTEL_* variables"
)


class OtelEvents:
    """``tracer_provider`` and ``meter_provider`` default to the global ones, as for any
    OpenTelemetry instrumentation."""

    def __init__(
        self, version: str, *, tracer_provider: Any = None, meter_provider: Any = None
    ) -> None:
        from opentelemetry import metrics, propagate, trace

        self._trace = trace
        self._propagate = propagate
        self._tracer = trace.get_tracer(
            "recon", version, tracer_provider=tracer_provider, schema_url=SCHEMA_URL
        )
        meter = metrics.get_meter(
            "recon", version, meter_provider=meter_provider, schema_url=SCHEMA_URL
        )
        self._records = meter.create_counter(
            "recon.collect.records", unit="{record}", description="Records written to tables"
        )
        self._failures = meter.create_counter(
            "recon.collect.failures", unit="{table}", description="Tables that failed"
        )
        self._open: dict[str, Any] = {}

    def on_event(self, event: Event) -> None:
        if event.name.endswith(".start"):
            parent = self._open.get(event.parent or "")
            context = self._trace.set_span_in_context(parent) if parent is not None else None
            kinds = self._trace.SpanKind
            self._open[event.span] = self._tracer.start_span(
                _span_name(event),
                context=context,
                kind=kinds.CLIENT if event.kind == "client" else kinds.INTERNAL,
                attributes={**event.attributes, EVENT_SPAN: event.span},
                start_time=_nanoseconds(event.time),
            )
            return
        if event.name.endswith(".progress"):
            open_span = self._open.get(event.span)
            if open_span is not None:
                open_span.add_event(
                    event.name, event.attributes, timestamp=_nanoseconds(event.time)
                )
            return
        span = self._open.pop(event.span, None)
        if span is None:
            return
        span.set_attributes(event.attributes)
        if event.status == "error":
            span.set_status(self._trace.StatusCode.ERROR, event.message)
        span.end(end_time=_nanoseconds(event.time))
        if event.name == f"{COLLECT}.end":
            self._measure(event)

    def around(self, event: Event) -> AbstractContextManager[Any] | None:
        """The span ``event`` started, current for the work inside it."""
        span = self._open.get(event.span)
        if span is None:
            return None
        manager: AbstractContextManager[Any] = self._trace.use_span(
            span, end_on_exit=False, record_exception=False, set_status_on_exception=False
        )
        return manager

    def trace_headers(self, span_id: str) -> dict[str, str]:
        """``traceparent`` (and ``tracestate``) naming the open span ``span_id``; none when
        nothing records it (no SDK installed)."""
        span = self._open.get(span_id)
        if span is None or not span.get_span_context().is_valid:
            return {}
        carrier: dict[str, str] = {}
        self._propagate.inject(carrier, context=self._trace.set_span_in_context(span))
        return carrier

    def close(self) -> None:
        """End every span still open, as failed: recon sends this sink nothing more."""
        for span in reversed(list(self._open.values())):
            span.set_status(
                self._trace.StatusCode.ERROR, "recon stopped sending telemetry to this sink"
            )
            span.end()
        self._open.clear()

    def _measure(self, event: Event) -> None:
        about = {"recon.collector.type": str(event.attributes.get("recon.collector.type"))}
        if event.status == "error":
            error_type = str(event.attributes.get("error.type"))
            self._failures.add(1, {**about, "error.type": error_type})
        else:
            records = event.attributes.get("recon.records", 0)
            self._records.add(records if isinstance(records, int) else 0, about)


def _span_name(event: Event) -> str:
    """An HTTP client span is named by its method, as the conventions say; any other span by
    its event name."""
    name = event.name.removesuffix(".start")
    if name == HTTP_REQUEST:
        return str(event.attributes.get("http.request.method", "HTTP"))
    return name


def _nanoseconds(time: datetime) -> int:
    return int(time.timestamp() * 1_000_000_000)


def export_from_env(env: Mapping[str, str], version: str) -> list[Problem]:
    """Install the SDK's provider for each signal ``env`` asks to export that has none yet;
    return warnings for what it asked and cannot get."""
    if env.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return []
    asked = {signal: _asked(signal, env) for signal in SIGNALS}
    # A signal whose provider the program installed is the program's: recon's data goes there.
    wanted = [signal for signal in SIGNALS if asked[signal] and not _installed(signal)]
    if not wanted:
        return []
    try:
        return _install(env, version, wanted)
    except ImportError as exc:
        first = asked[wanted[0]][0]
        return [
            Problem(
                "",
                f"{first} is set, but {exc.name or 'the OpenTelemetry SDK'} is not "
                "installed, so recon exports nothing; the survey's event log is still written",
                kind="otel_unavailable",
                fix=_INSTALL_FIX,
                got=first,
            )
        ]


def _asked(signal: str, env: Mapping[str, str]) -> list[str]:
    """The variables in ``env`` that ask for ``signal`` to be exported."""
    names = (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT",
        f"OTEL_{signal}_EXPORTER",
    )
    return [name for name in names if env.get(name)]


def _installed(signal: str) -> bool:
    """Whether the program has installed a provider for ``signal``: the global one is no
    longer the API's default, which forwards to whatever is installed later."""
    from opentelemetry import metrics, trace

    # The API names no public type for its default meter provider.
    from opentelemetry.metrics._internal import _ProxyMeterProvider

    if signal == "TRACES":
        return not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider)
    return not isinstance(metrics.get_meter_provider(), _ProxyMeterProvider)


def _install(env: Mapping[str, str], version: str, signals: list[str]) -> list[Problem]:
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    warnings: list[Problem] = []
    named = env.get("OTEL_SERVICE_NAME") or "service.name=" in env.get(
        "OTEL_RESOURCE_ATTRIBUTES", ""
    )
    defaults = {"service.version": version} | ({} if named else {"service.name": "recon"})
    resource = Resource.create(defaults)
    if "TRACES" in signals and _selected("TRACES", env, warnings):
        span_exporter = _exporter("TRACES", env, warnings)
        if span_exporter is not None:
            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            trace.set_tracer_provider(tracer_provider)
    if "METRICS" in signals and _selected("METRICS", env, warnings):
        metric_exporter = _exporter("METRICS", env, warnings)
        if metric_exporter is not None:
            reader = PeriodicExportingMetricReader(metric_exporter)
            metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    return list(dict.fromkeys(warnings))  # a protocol both signals read is warned about once


def _selected(signal: str, env: Mapping[str, str], warnings: list[Problem]) -> bool:
    """Whether ``OTEL_<signal>_EXPORTER`` selects OTLP (its default) rather than ``none``."""
    variable = f"OTEL_{signal}_EXPORTER"
    chosen = env.get(variable, "otlp").strip() or "otlp"
    if chosen not in ("otlp", "none"):
        warnings.append(
            Problem(
                "",
                f"{variable}={chosen}: recon exports otlp or nothing; exporting otlp",
                kind="otel_unavailable",
                fix=f"{variable}=otlp, or {variable}=none",
                got=chosen,
                allowed=("otlp", "none"),
            )
        )
    return chosen != "none"


def _exporter(signal: str, env: Mapping[str, str], warnings: list[Problem]) -> Any:
    """The OTLP exporter for ``signal`` over http/protobuf, the specification's default and the
    one protocol recon exports over (owner ruling 2026-10-22: no gRPC). None, with a warning,
    when the environment asks for gRPC: sent as http/protobuf, the signal would go to an
    endpoint that speaks gRPC and be lost."""
    variable = f"OTEL_EXPORTER_OTLP_{signal}_PROTOCOL"
    if not env.get(variable):
        variable = "OTEL_EXPORTER_OTLP_PROTOCOL"
    protocol = env.get(variable) or "http/protobuf"
    if protocol == "grpc":
        warnings.append(
            Problem(
                "",
                f"{variable}=grpc: recon exports OTLP over http/protobuf only, so it exports "
                "nothing that variable governs",
                kind="otel_unavailable",
                fix=f"{variable}=http/protobuf, with the endpoint at the collector's OTLP/HTTP "
                "port (4318 by default)",
                got=protocol,
                allowed=("http/protobuf",),
            )
        )
        return None
    if protocol != "http/protobuf":
        warnings.append(
            Problem(
                "",
                f"OTLP protocol {protocol} is not one recon exports over; exporting http/protobuf",
                kind="otel_unavailable",
                fix=f"{variable}=http/protobuf",
                got=protocol,
                allowed=("http/protobuf",),
            )
        )
    if signal == "TRACES":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter()
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    return OTLPMetricExporter()
