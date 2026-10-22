"""OpenTelemetry for hardline, on the GenAI semantic conventions: the one file that names it.

The conventions followed are those of ``open-telemetry/semantic-conventions-genai`` at
``9ffc7e0`` (2026-10-21, status Development): ``docs/gen-ai/client-inference.md`` for the span
and ``gen_ai.client.inference.duration``, ``docs/gen-ai/gen-ai-token-metrics.md`` for the token
counters. They may change; the names here are pinned to that reading.

Two adapters, both chosen by the composition root, so the domain names no telemetry:

* ``TracedBackend`` wraps any ``ModelBackend``. Each backend call is one span, ``chat
  {gen_ai.request.model}``, of kind CLIENT, under whatever span is current (the caller's, so the
  trace is the caller's): a retry or a fallback is a sibling span. It records
  ``gen_ai.client.inference.duration`` for every call, failed ones included, and the
  ``gen_ai.client.inference.usage.*`` counters for every strictly positive count reported.
  hardline owns these counters: a host counts its agent sessions, not the model calls inside.
* ``OtelCallObserver`` hears the logical call (``CallObserver``) and counts what only the
  runtime knows: ``hardline.retries`` and ``hardline.fallbacks``, by row, with ``error.type``.

**No content, and no opt-in.** No prompt, system prompt, reply, refusal or error message is an
attribute, an event or a span status: an exception is not recorded on the span (its message
and traceback are content), and a failed span's status has no description. ``error.type`` is
the ``BackendError`` reason, or the exception's class name. Row names, model ids, locations,
counts and reasons are not content.

Values hardline chooses where the conventions leave the choice: ``gen_ai.provider.name`` is the
backend's entry-point name (``openai-compat``, ``anthropic``), since an OpenAI-compatible
backend cannot know which provider serves its ``base_url``; ``gen_ai.token.modality`` is
``text``, since hardline sends text turns and reads text replies only; ``hardline.row`` names
the registry row, which two rows of one model tell apart.

Everything goes through the OpenTelemetry API, which does nothing until an application installs
an SDK; hardline never installs one. A caller may hand its own providers to ``build_runtime``.
"""

from __future__ import annotations

import time
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from opentelemetry import metrics, trace
from opentelemetry.metrics import MeterProvider
from opentelemetry.trace import SpanKind, Status, StatusCode, TracerProvider

from hardline.domain.errors import BackendError
from hardline.domain.types import AttemptFailed, FellBack, RawCompletion

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hardline.domain.types import CallEvent, ModelSpec, Request

SCOPE = "hardline"

#: The providers a host may hand ``build_runtime``, by these names, so OpenTelemetry is named in
#: this file alone.
__all__ = ["MeterProvider", "OtelCallObserver", "Telemetry", "TracedBackend", "TracerProvider"]

#: The conventions' advised buckets for ``gen_ai.client.inference.duration``, in seconds.
_DURATION_BUCKETS = (
    0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92,
)  # fmt: skip

#: The ``gen_ai.client.inference.usage.*`` counters, by the ``Usage`` field each counts.
_TOKEN_COUNTERS = {
    "input_tokens": "gen_ai.client.inference.usage.input_tokens",
    "output_tokens": "gen_ai.client.inference.usage.output_tokens",
    "cache_read_tokens": "gen_ai.client.inference.usage.cache_read.input_tokens",
    "cache_write_tokens": "gen_ai.client.inference.usage.cache_write.input_tokens",
}

#: Span attributes for a ``Usage`` field, set when the count is reported (cache ones when > 0).
_USAGE_ATTRIBUTES = {
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "cache_read_tokens": "gen_ai.usage.cache_read.input_tokens",
    "cache_write_tokens": "gen_ai.usage.cache_write.input_tokens",
}


def _version() -> str:
    try:
        return version("hardline")
    except PackageNotFoundError:
        return "0+unknown"


class Telemetry:
    """A tracer and the inference instruments, made once per runtime and shared by its
    backends. ``None`` providers are the global ones."""

    def __init__(
        self,
        tracer_provider: TracerProvider | None = None,
        meter_provider: MeterProvider | None = None,
    ) -> None:
        self.tracer = trace.get_tracer(SCOPE, _version(), tracer_provider=tracer_provider)
        meter = metrics.get_meter(SCOPE, _version(), meter_provider=meter_provider)
        self.duration = meter.create_histogram(
            "gen_ai.client.inference.duration",
            unit="s",
            description="GenAI client inference operation duration.",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self.tokens = {
            field: meter.create_counter(name, unit="{token}")
            for field, name in _TOKEN_COUNTERS.items()
        }
        self.retries = meter.create_counter(
            "hardline.retries", unit="{retry}", description="A row asked again after a failure"
        )
        self.fallbacks = meter.create_counter(
            "hardline.fallbacks",
            unit="{fallback}",
            description="A row's retries spent, and the next row of its chain asked",
        )


class TracedBackend:
    """``inner``, with one ``chat`` span and the inference metrics per call. Every other member
    of the port, optional ones included, is ``inner``'s."""

    def __init__(self, inner: Any, name: str, telemetry: Telemetry) -> None:
        self._inner = inner
        self._name = name
        self._telemetry = telemetry

    @property
    def structured_modes(self) -> frozenset[str]:
        modes: frozenset[str] = self._inner.structured_modes
        return modes

    @property
    def default_base_url(self) -> str | None:
        url = getattr(self._inner, "default_base_url", None)
        return url if isinstance(url, str) else None

    def check(self, spec: ModelSpec) -> Sequence[str]:
        check = getattr(self._inner, "check", None)
        return list(check(spec)) if callable(check) else []

    async def aclose(self) -> None:
        aclose = getattr(self._inner, "aclose", None)
        if callable(aclose):
            await aclose()

    async def complete(
        self, spec: ModelSpec, request: Request, api_key: str | None
    ) -> RawCompletion:
        common = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": self._name,
            "gen_ai.request.model": spec.model,
            **_server(spec.base_url),
        }
        attributes: dict[str, Any] = {
            **common,
            "gen_ai.request.max_tokens": request.max_tokens,
            "hardline.row": spec.name,
        }
        if request.temperature is not None:
            attributes["gen_ai.request.temperature"] = request.temperature
        if request.json_schema is not None and spec.structured != "prompt":
            attributes["gen_ai.output.type"] = "json"
        started = time.perf_counter()
        with self._telemetry.tracer.start_as_current_span(
            f"chat {spec.model}",
            kind=SpanKind.CLIENT,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                raw = await self._inner.complete(spec, request, api_key)
            except BaseException as e:
                error_type = e.reason if isinstance(e, BackendError) else type(e).__qualname__
                span.set_attribute("error.type", error_type)
                span.set_status(Status(StatusCode.ERROR))
                self._telemetry.duration.record(
                    time.perf_counter() - started, {**common, "error.type": error_type}
                )
                raise
            if not isinstance(raw, RawCompletion):
                return raw  # type: ignore[no-any-return]  # the runtime's contract check names it
            response = {**common, "gen_ai.response.model": raw.model}
            span.set_attributes(_response(raw))
            self._telemetry.duration.record(time.perf_counter() - started, response)
            for field, counter in self._telemetry.tokens.items():
                count = getattr(raw.usage, field)
                if count > 0:  # the conventions take strictly positive contributions only
                    counter.add(count, {**response, "gen_ai.token.modality": "text"})
            return raw


class OtelCallObserver:
    """Counts the retries and fallbacks of each logical call, by row and reason."""

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry

    def observe(self, event: CallEvent) -> None:
        if isinstance(event, AttemptFailed) and event.retry_in_s is not None:
            self._telemetry.retries.add(
                1, {"hardline.row": event.attempt.name, "error.type": str(event.attempt.reason)}
            )
        elif isinstance(event, FellBack):
            self._telemetry.fallbacks.add(
                1,
                {
                    "hardline.row": event.name,
                    "hardline.fallback": event.to,
                    "error.type": event.reason,
                },
            )


def _response(raw: RawCompletion) -> dict[str, Any]:
    """What the reply says about itself: never its text."""
    found: dict[str, Any] = {"gen_ai.response.model": raw.model}
    if raw.request_id:
        found["gen_ai.response.id"] = raw.request_id
    if raw.native_finish_reason:
        found["gen_ai.response.finish_reasons"] = (raw.native_finish_reason,)
    for field, name in _USAGE_ATTRIBUTES.items():
        count = getattr(raw.usage, field)
        if count > 0 or field in ("input_tokens", "output_tokens"):
            found[name] = count
    return found


def _server(base_url: str | None) -> dict[str, Any]:
    """``server.address`` and ``server.port`` of a row's location; none for a row with none."""
    if not base_url:
        return {}
    url = urlsplit(base_url)
    if not url.hostname:
        return {}
    port = url.port or (443 if url.scheme == "https" else 80)
    return {"server.address": url.hostname, "server.port": port}
