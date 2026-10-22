"""Telemetry: OpenTelemetry, configured the standard way, and the record's own event log.

matrix emits events; the ``otel`` observer turns them into spans and metrics through the
OpenTelemetry API, which does nothing until an SDK is configured. The SDK and the OTLP
exporters are dependencies too, imported only when export is asked for.

**A library uses the API; an application installs the SDK** (OpenTelemetry's own rule).
Composing, wiring and running never install a tracer or meter provider. When the environment
asks for export (an ``OTEL_EXPORTER_OTLP_*ENDPOINT`` is set, or ``OTEL_TRACES_EXPORTER`` /
``OTEL_METRICS_EXPORTER`` names an exporter other than ``none``) and ``OTEL_SDK_DISABLED`` is
not ``true``:

* composition attaches the ``otel`` observer to every run, whether or not the config names
  it; through the API alone it goes wherever the application's provider sends it;
* an application entry point calls :func:`configure_telemetry` to install the SDK from those
  variables: ``matrix run`` does, and so may a tool built on matrix. It installs the
  exporters the two ``*_EXPORTER`` variables name (``otlp``, the default, or ``console``),
  OTLP over ``OTEL_EXPORTER_OTLP_PROTOCOL`` (``http/protobuf``, the default, or ``grpc``),
  and a resource from ``OTEL_SERVICE_NAME`` and ``OTEL_RESOURCE_ATTRIBUTES``
  (``service.name`` is ``matrix`` when neither sets it). A program that configures
  OpenTelemetry itself does not call it.

When the environment asks for export and an install lacks the SDK or the exporters, nothing
is exported, and :func:`warnings` says so as a problem every ``matrix`` document carries; it
is never silent.

**With nothing configured**, a run given a store that keeps events (the JSONL store) still
writes them into its record, ``events.jsonl``: the local sink.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any

from matrix.domain.errors import missing_dependency, reinstall
from matrix.domain.problems import Problem, problem

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Variables that, set, say where to export.
_ENDPOINTS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
)
_EXPORTERS = {"traces": "OTEL_TRACES_EXPORTER", "metrics": "OTEL_METRICS_EXPORTER"}
#: The exporters matrix installs; the spec's others (zipkin, prometheus) are reported.
_SUPPORTED = frozenset({"otlp", "console", "none"})
_PROTOCOLS = frozenset({"http/protobuf", "grpc"})


def _true(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _names(value: str | None) -> list[str]:
    return [name.strip().lower() for name in (value or "").split(",") if name.strip()]


def requested(environ: Mapping[str, str] | None = None) -> str | None:
    """The variable that asks for export, or None when the environment asks for none."""
    env = os.environ if environ is None else environ
    if _true(env.get("OTEL_SDK_DISABLED")):
        return None
    for variable in _ENDPOINTS:
        if env.get(variable):
            return variable
    for variable in _EXPORTERS.values():
        if any(name != "none" for name in _names(env.get(variable))):
            return variable
    return None


#: What export needs beyond the API: the SDK and the OTLP exporters, imported only then.
_SDK = "opentelemetry.sdk"
_OTLP = "opentelemetry.exporter.otlp.proto.http"


def _installed(module: str = _SDK) -> bool:
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):  # a parent package that is missing, or stubbed out
        return False


def _missing(environ: Mapping[str, str]) -> str | None:
    """The module export needs and lacks, for what the environment asks."""
    if not _installed(_SDK):
        return _SDK
    wants_otlp = any(
        "otlp" in (_names(environ.get(variable)) or ["otlp"]) for variable in _EXPORTERS.values()
    )
    if wants_otlp and not _installed(_OTLP):
        return _OTLP
    return None


def warnings(environ: Mapping[str, str] | None = None) -> tuple[Problem, ...]:
    """What a reader should know about telemetry before a run: export asked for in an install
    that lacks what does it, or a variable naming what matrix does not install."""
    env = os.environ if environ is None else environ
    variable = requested(env)
    if variable is None:
        return ()
    lacking = _missing(env)
    if lacking is not None:
        return (
            problem(
                variable,
                "dependency_missing",
                f"{variable} asks for OpenTelemetry export, but {lacking} is not installed: "
                "nothing will be exported",
                fix=reinstall(),
                got=env.get(variable),
            ),
        )
    found: list[Problem] = []
    for name in _EXPORTERS.values():
        for exporter in _names(env.get(name)):
            if exporter not in _SUPPORTED:
                found.append(
                    problem(
                        name,
                        "invalid_value",
                        f"{name} names {exporter!r}, which matrix does not install; it is skipped",
                        allowed=sorted(_SUPPORTED),
                        got=exporter,
                    )
                )
    for name in ("OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"):
        protocol = env.get(name)
        if protocol and protocol not in _PROTOCOLS:
            found.append(
                problem(
                    name,
                    "invalid_value",
                    f"{name} is {protocol!r}; matrix exports OTLP over http/protobuf or grpc, "
                    "and uses http/protobuf",
                    allowed=sorted(_PROTOCOLS),
                    got=protocol,
                )
            )
    return tuple(found)


@dataclass
class Telemetry:
    """What :func:`configure_telemetry` installed. ``shutdown()`` flushes and stops it: call
    it once the work is done, or the last spans and metrics may never leave the process."""

    #: Whether an SDK was installed by this call.
    enabled: bool = False
    #: The exporters installed, as ``<signal>:<exporter>`` (``traces:otlp``).
    exporters: tuple[str, ...] = ()
    warnings: tuple[Problem, ...] = ()
    _providers: list[Any] = field(default_factory=list)

    def shutdown(self) -> None:
        for provider in self._providers:
            provider.shutdown()
        self._providers.clear()


def current_trace() -> tuple[str, str] | None:
    """The trace and span ids, as hex, of the OpenTelemetry span current in the calling task,
    or None when there is none (or no SDK made one): what a session row records. matrix mints
    no ids; with the API alone every span is invalid, and this is None."""
    from opentelemetry import trace

    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return trace.format_trace_id(context.trace_id), trace.format_span_id(context.span_id)


def trace_environment() -> dict[str, str]:
    """The OpenTelemetry context current in the calling task, as the environment variables a
    child process reads it from (``TRACEPARENT``, ``TRACESTATE``; OpenTelemetry's
    environment-variable carrier, the W3C header names upper-cased), through the configured
    propagators. Empty when no span is current, or no SDK made one, so a child is handed
    nothing then: what the ``claude-sdk`` runtime passes the CLI it starts."""
    from opentelemetry import propagate

    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    if "traceparent" not in carrier:
        return {}
    return {key.upper(): value for key, value in carrier.items()}


def configure_telemetry(
    *,
    service_name: str | None = None,
    endpoint: str | None = None,
    console: bool = False,
) -> Telemetry:
    """Install the OpenTelemetry SDK so matrix's spans and metrics are exported: for an
    application's entry point, never for a library, since the providers it installs are the
    process's own. ``matrix run`` calls it.

    With no arguments, the standard ``OTEL_*`` variables decide, and when they ask for
    nothing this does nothing. ``endpoint`` exports OTLP there, and ``console`` prints to
    stderr, whatever the variables say. ``OTEL_SDK_DISABLED=true`` turns all of it off.

    Raises ConfigError in an install that lacks the SDK or the exporters when ``endpoint`` or
    ``console`` asked for export; when only the environment did, the returned
    :class:`Telemetry` carries the warning instead.
    """
    if _true(os.environ.get("OTEL_SDK_DISABLED")):
        return Telemetry()
    by_environment = requested() is not None
    if not (endpoint or console or by_environment):
        return Telemetry()
    if (endpoint or console) and not _installed(_SDK):
        raise missing_dependency(needed_by="telemetry", module=_SDK)
    if endpoint and not _installed(_OTLP):
        raise missing_dependency(needed_by="OTLP export", module=_OTLP)
    if not _installed(_SDK):  # only the environment asked: say so, and go on
        return Telemetry(warnings=warnings())
    plan = _plan(endpoint=endpoint, console=console, by_environment=by_environment)
    if not any(plan.values()):
        return Telemetry(warnings=warnings())
    return _install(plan, service_name=service_name, endpoint=endpoint, notes=warnings())


def _plan(*, endpoint: str | None, console: bool, by_environment: bool) -> dict[str, set[str]]:
    """The exporters for each signal."""
    plan: dict[str, set[str]] = {"traces": set(), "metrics": set()}
    usable = _SUPPORTED - {"none"} - (set() if _installed(_OTLP) else {"otlp"})
    for signal, variable in _EXPORTERS.items():
        if by_environment:  # what cannot be installed is in the warnings
            plan[signal] |= set(_names(os.environ.get(variable)) or ["otlp"]) & usable
        if endpoint:
            plan[signal].add("otlp")
        if console:
            plan[signal].add("console")
    return plan


def _install(
    plan: dict[str, set[str]],
    *,
    service_name: str | None,
    endpoint: str | None,
    notes: tuple[Problem, ...],
) -> Telemetry:
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    attributes: dict[str, str] = {}
    named = os.environ.get("OTEL_SERVICE_NAME") or "service.name=" in os.environ.get(
        "OTEL_RESOURCE_ATTRIBUTES", ""
    )
    if service_name or not named:
        attributes["service.name"] = service_name or "matrix"
    # Resource.create reads OTEL_RESOURCE_ATTRIBUTES and OTEL_SERVICE_NAME itself.
    resource = Resource.create(attributes)
    installed: list[str] = []
    providers: list[Any] = []

    if plan["traces"]:
        tracer_provider = TracerProvider(resource=resource)
        for name in sorted(plan["traces"]):
            if name == "console":
                # stderr: stdout is the command's own document
                console = ConsoleSpanExporter(out=sys.stderr)
                tracer_provider.add_span_processor(SimpleSpanProcessor(console))
            else:
                exporter = _otlp("traces", endpoint)
                tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
            installed.append(f"traces:{name}")
        trace.set_tracer_provider(tracer_provider)
        providers.append(tracer_provider)
    if plan["metrics"]:
        readers = [
            PeriodicExportingMetricReader(
                ConsoleMetricExporter(out=sys.stderr)
                if name == "console"
                else _otlp("metrics", endpoint)
            )
            for name in sorted(plan["metrics"])
        ]
        installed += [f"metrics:{name}" for name in sorted(plan["metrics"])]
        meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        metrics.set_meter_provider(meter_provider)
        providers.append(meter_provider)
    return Telemetry(enabled=True, exporters=tuple(installed), warnings=notes, _providers=providers)


def _otlp(signal: str, endpoint: str | None) -> Any:
    """An OTLP exporter for ``signal`` over the protocol the environment names. Without an
    ``endpoint`` the exporter reads the ``OTEL_EXPORTER_OTLP_*`` variables itself."""
    protocol = (
        os.environ.get(f"OTEL_EXPORTER_OTLP_{signal.upper()}_PROTOCOL")
        or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        or "http/protobuf"
    )
    grpc = protocol == "grpc"
    try:
        if signal == "traces" and grpc:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            return OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()
        if signal == "traces":
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as HttpSpanExporter,
            )

            url = f"{endpoint.rstrip('/')}/v1/traces" if endpoint else None
            return HttpSpanExporter(endpoint=url) if url else HttpSpanExporter()
        if grpc:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

            return OTLPMetricExporter(endpoint=endpoint) if endpoint else OTLPMetricExporter()
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter as HttpMetricExporter,
        )

        url = f"{endpoint.rstrip('/')}/v1/metrics" if endpoint else None
        return HttpMetricExporter(endpoint=url) if url else HttpMetricExporter()
    except ImportError as e:
        raise missing_dependency(
            needed_by=f"OTLP export of {signal}", module="opentelemetry.exporter.otlp"
        ) from e
