"""ix's own spans, through the OpenTelemetry API, under the instrumentation scope ``ix``.

ix is a library: it creates spans through the API and never installs a provider, so they go
wherever the application's provider sends them, and nowhere when there is none. The ``ix``
command installs one from the standard ``OTEL_*`` variables through matrix
(``matrix.configure_telemetry``); a program embedding ix configures OpenTelemetry itself.

The spans are ix's work: ``ix.plan``, ``ix.run``, ``ix.trial`` and ``ix.sensor``. The agent
sessions inside a trial are matrix's (``invoke_agent``, ``execute_tool``), and matrix's
``invoke_workflow`` for the run sits under ``ix.run``.

**The conventions are named here and nowhere else.** A metric a sensor read
(:meth:`_Span.evaluated`) is a ``gen_ai.evaluation.result`` event with the GenAI
conventions' ``gen_ai.evaluation.name``, ``gen_ai.evaluation.score.value`` and
``gen_ai.evaluation.score.label``, as read from ``opentelemetry-semantic-conventions``
0.65b0 on 2026-10-19 (moved since to the GenAI conventions' own repository, status
Development); a failure's reason (:meth:`_Span.failed`, and a metric that could not be read)
is the general conventions' ``error.type``. When the conventions move, this file does.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from collections.abc import Iterator

SCOPE = "ix"

#: The GenAI conventions' event for one evaluation, and its attributes.
EVALUATION = "gen_ai.evaluation.result"
EVALUATION_NAME = "gen_ai.evaluation.name"
SCORE_VALUE = "gen_ai.evaluation.score.value"
SCORE_LABEL = "gen_ai.evaluation.score.label"
#: The general conventions' attribute for the kind of a failure.
ERROR_TYPE = "error.type"


def _plain(attributes: dict[str, Any]) -> dict[str, Any]:
    """The attributes OpenTelemetry can carry: scalars, and lists of strings; the rest as
    text."""
    kept: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, str | bool | int | float):
            kept[key] = value
        elif isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):
            kept[key] = list(value)
        else:
            kept[key] = str(value)
    return kept


class _Span:
    def __init__(self, span: trace.Span) -> None:
        self._span = span

    def set(self, **attributes: Any) -> None:
        self._span.set_attributes(_plain(attributes))

    def failed(self, reason: str) -> None:
        self._span.set_attribute(ERROR_TYPE, reason)

    def evaluated(
        self,
        metric: str,
        *,
        score: float | None = None,
        label: str | None = None,
        failure: str | None = None,
    ) -> None:
        attributes = {
            EVALUATION_NAME: metric,
            SCORE_VALUE: score,
            SCORE_LABEL: label,
            ERROR_TYPE: failure,
        }
        self._span.add_event(EVALUATION, _plain(attributes))


class OtelTracer:
    """Spans of ix's work, under the scope ``ix``, through whatever provider is installed,
    or through ``tracer_provider``."""

    def __init__(
        self, version: str, *, tracer_provider: trace.TracerProvider | None = None
    ) -> None:
        self._tracer = trace.get_tracer(SCOPE, version, tracer_provider=tracer_provider)

    @contextmanager
    def trace(self, name: str, /, **attributes: Any) -> Iterator[_Span]:
        """A span of ``name``, current while it is open, so a span begun inside it is its
        child. An exception that leaves it marks it as an error."""
        with self._tracer.start_as_current_span(name, attributes=_plain(attributes)) as span:
            yield _Span(span)
