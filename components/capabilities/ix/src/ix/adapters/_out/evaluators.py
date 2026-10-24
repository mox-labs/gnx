"""The ``evaluator`` sensor: one of pydantic-evals' evaluators reads a trial.

ix keeps the loop. pydantic-evals' ``Dataset.evaluate`` is not used: ix's run path carries
the trials out, and this sensor hands each one to an evaluator as pydantic-evals'
``EvaluatorContext``, built from what ix recorded of it:

* ``inputs``: the prompt the probe put; ``output``: the reply's text;
* ``expected_output`` and ``metadata``: the probe's truth keys of those names;
* ``name``: the probe's id;
* ``duration``: how long the probe took, in seconds, timed while the sensor watched it;
* the span tree: every span that ended in the trial while the probe ran, captured in this
  process. pydantic-evals attaches its in-memory exporter to the installed OpenTelemetry SDK
  tracer provider, and keeps only the spans that end in the task it captures for, so the spans
  are the trial's own: matrix's ``invoke_agent`` and ``execute_tool`` spans for an agent
  session, and whatever a model runtime or a library reports inside them. They are kept in
  the interaction (``watched``), so a run read back later is read the same way.

**The spans need no export.** The sensor is a :class:`~ix.domain.ports.SpanReader`: it says
whether its evaluator reads spans (every evaluator but those known to read none, so one
pydantic-evals adds is taken to). A run whose sensors read spans keeps them in the process: the
composition root uses the tracer provider installed (the command's, when the ``OTEL_*``
variables ask for export), installs one with no exporter when there is none, and puts matrix's
``otel`` observer on the run. Without the OpenTelemetry SDK nothing can be installed, and with
spans captured and not one of them from the trial there is nothing to read either: a reading
of the span tree then fails, with that reason. pydantic-evals' own span evaluators answer
``False`` or ``0.0`` then, which would read as an agent that called no tool; the sensor does
not let that through. An evaluator that reads no span (``Equals``) reads as usual.

**Which metric.** An evaluator gives assertions (true or false), scores (numbers) and labels
(text), each under a name. ``eval_passed`` is whether every assertion held, and
``eval_score`` the one score; ``result`` names the one to read when there are more.
Labels are kept in the readings' ``detail`` with every result and its reason. An evaluator
that calls a model (one with a ``model`` field: ``LLMJudge``, ``GEval``) gives the
``judged_`` metrics instead, which never decide a verdict.

**The judge's model is the composition root's.** It comes from the matrix model runtime the
options name (``model_runtime``, ``model``, ``runtime_options``), never from the evaluator's
arguments and never from pydantic-evals' default (``openai:gpt-5.2``), which would resolve a
provider from the environment: validate refuses a judge evaluator that names no runtime, and
one whose arguments name a model. A runtime that is a stand-in (``mock``) gives pydantic-ai's
``TestModel``, which calls no model; any other is asked of the root's :class:`JudgeModels`,
which gives the pydantic-ai ``Model`` that runtime offers (``hardline``'s makes each request a
hardline call). The connections its calls opened are closed when the reading ends, in the
loop that opened them. Its calls are hardline's ``chat`` spans and counters, made while the
sensor reads, after the trial's spans were kept: they never join the tree an evaluator reads.
Each is also a row of the run's session table, which the runtime's ``Model`` records, so the
reading names the calls behind it (``sessions``), as a judge sensor's does.

pydantic-evals is imported here and nowhere else, and only when a reading, a review or a
watch needs it, so ix starts as fast without it.
"""

from __future__ import annotations

import dataclasses
import inspect
import math
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from functools import cache
from typing import TYPE_CHECKING, Any, Protocol

from ix.adapters._out.matrix_agents import MODEL_RUNTIME, STAND_IN, runtime_requirement
from ix.domain.kind import placeholders
from ix.domain.ports import Requirement, Sensed, StoodIn
from ix.domain.problem import Problem, problem

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence
    from contextlib import AbstractAsyncContextManager

    from pydantic_evals.evaluators import Evaluator
    from pydantic_evals.otel import SpanTree, SpanTreeRecordingError

    from ix.adapters._out.matrix_agents import Runtimes
    from ix.domain.ports import Surroundings
    from ix.domain.run import Interaction, Target

#: The kind this sensor carries out, and the key its watch is kept under in an interaction.
KIND = "ix.v1.sensor.evaluator"

#: The metrics, by whether the evaluator calls a model.
PASSED, SCORE = "eval_passed", "eval_score"
JUDGED = "judged_"

#: The metric of an evaluator that reads how long the probe took, in place of ``eval_passed``:
#: a time, which the kind declares contended, so it never decides a run with trials in flight.
DURATION_PASSED = "eval_duration_passed"

#: pydantic-evals' evaluators that read how long the probe took.
READS_DURATION = frozenset({"MaxDuration"})

#: The options that say where a judge evaluator's model comes from.
JUDGE_OPTIONS = ("model_runtime", "model", "runtime_options")

#: pydantic-evals' evaluators that read no span: the reply, the probe's truth, its duration,
#: or a model's judgement of them. Any other is taken to read the trial's spans.
READS_NO_SPANS = frozenset(
    {"Equals", "EqualsExpected", "Contains", "IsInstance", "MaxDuration", "LLMJudge", "GEval"}
)

#: Why a trial's spans are not there, when the watch kept none and said nothing.
UNWATCHED = "no spans were kept for this trial: the evaluator sensor did not watch it"


class JudgeModels(Protocol):
    """Where a judge evaluator's model comes from, as the composition root hands it over:
    a pydantic-ai ``Model`` that makes its calls through a matrix model runtime."""

    def model(self, runtime: str, model: str | None, config: Mapping[str, Any]) -> Any:
        """The pydantic-ai ``Model`` for ``model`` on the model runtime named ``runtime``,
        built with ``config``. Raises ``IxError`` (with a ``fix``), ``matrix.MatrixError`` or
        ``ValueError`` when there is none to give."""
        ...


class NoSpansError(ValueError):
    """An evaluator read the trial's span tree, and there was none to read."""


@cache
def evaluator_classes() -> dict[str, type[Evaluator[Any, Any, Any]]]:
    """pydantic-evals' evaluators, by the name a spec gives them: every concrete
    ``Evaluator`` its ``evaluators`` package exports (report evaluators are not)."""
    import pydantic_evals.evaluators as evaluators
    from pydantic_evals.evaluators import Evaluator

    found: dict[str, type[Evaluator[Any, Any, Any]]] = {}
    for name in evaluators.__all__:
        cls = getattr(evaluators, name)
        if isinstance(cls, type) and issubclass(cls, Evaluator) and not inspect.isabstract(cls):
            found[cls.get_serialization_name()] = cls
    return found


def _calls_a_model(cls: type) -> bool:
    """Whether evaluators of ``cls`` call a model: they take one, as a ``model`` field."""
    return dataclasses.is_dataclass(cls) and any(
        field.name == "model" for field in dataclasses.fields(cls)
    )


def _evaluator_class(options: Mapping[str, Any]) -> type[Evaluator[Any, Any, Any]]:
    """The evaluator class ``options`` names. Raises ValueError for a name pydantic-evals does
    not export."""
    name = str(options.get("evaluator", ""))
    known = evaluator_classes()
    if name not in known:
        raise ValueError(
            f"pydantic-evals has no evaluator named {name!r}; it has {', '.join(sorted(known))}"
        )
    return known[name]


def _arguments(options: Mapping[str, Any]) -> dict[str, Any]:
    given = options.get("arguments") or {}
    if not isinstance(given, Mapping):
        raise ValueError(f"arguments is a mapping of the evaluator's arguments; got {given!r}")
    return {str(key): value for key, value in given.items()}


@cache
def _context_class() -> type:
    """pydantic-evals' ``EvaluatorContext``, saying whether the evaluator read its span tree:
    what tells an answer about spans from one about none."""
    from pydantic_evals.evaluators import EvaluatorContext

    class Observed(EvaluatorContext[Any, Any, Any]):
        read_spans = False

        @property
        def span_tree(self) -> SpanTree:
            self.read_spans = True
            return super().span_tree

    return Observed


def _capture() -> Any:
    """pydantic-evals' span capture: a context manager that yields a ``SpanTree``, filled
    with the spans that ended inside it once it exits, or a ``SpanTreeRecordingError`` that
    says why there can be none. It lives in a module pydantic-evals keeps private, so a
    release that moves it is said, not silently read as no spans."""
    try:
        from pydantic_evals.otel._context_subtree import context_subtree
    except ImportError as error:
        from pydantic_evals.otel import SpanTreeRecordingError

        message = (
            "this pydantic-evals keeps its span capture somewhere ix does not know "
            f"(pydantic_evals.otel._context_subtree: {error}); install the release ix is "
            "built against"
        )

        @contextmanager
        def unavailable() -> Iterator[SpanTreeRecordingError]:
            yield SpanTreeRecordingError(message)

        return unavailable
    return context_subtree


def _hex(number: int | None, width: int) -> str | None:
    return None if number is None else f"{number:0{width}x}"


def _ns(at: datetime) -> int:
    return round(at.timestamp() * 1_000_000_000)


def _stored(node: Any) -> dict[str, Any]:
    """A captured span as the interaction keeps it: ids as hex, times as nanoseconds since
    the epoch, its attributes as they were and its status."""
    return {
        "name": node.name,
        "trace_id": _hex(node.trace_id, 32),
        "span_id": _hex(node.span_id, 16),
        "parent_span_id": _hex(node.parent_span_id, 16),
        "start_ns": _ns(node.start_timestamp),
        "end_ns": _ns(node.end_timestamp),
        "attributes": {key: _plain(value) for key, value in node.attributes.items()},
        "status": node.status,
    }


def _plain(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def _tree(seen: Mapping[str, Any]) -> SpanTree | SpanTreeRecordingError:
    """The span tree a watch kept, rebuilt; or the ``SpanTreeRecordingError`` that says why
    there is none."""
    from pydantic_evals.otel import SpanNode, SpanTree, SpanTreeRecordingError

    stored = seen.get("spans")
    if not isinstance(stored, list):
        return SpanTreeRecordingError(str(seen.get("spans_unavailable") or UNWATCHED))

    def when(ns: int) -> datetime:
        return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC)

    tree = SpanTree()
    tree.add_spans(
        [
            SpanNode(
                name=span["name"],
                trace_id=int(span["trace_id"], 16),
                span_id=int(span["span_id"], 16),
                parent_span_id=None
                if span["parent_span_id"] is None
                else int(span["parent_span_id"], 16),
                start_timestamp=when(span["start_ns"]),
                end_timestamp=when(span["end_ns"]),
                attributes=dict(span["attributes"]),
                status=span["status"],
            )
            for span in stored
        ]
    )
    return tree


def _results(output: Any, name: str) -> dict[str, dict[str, Any]]:
    """What an evaluator gave, as ``{name: {value, reason}}``: a value alone, an
    ``EvaluationReason``, or a mapping of names to either. Raises ValueError for anything
    else, and for a number that is not finite."""
    from pydantic_evals.evaluators import EvaluationReason

    given = output.items() if isinstance(output, Mapping) else [(name, output)]
    found: dict[str, dict[str, Any]] = {}
    for key, item in given:
        value, reason = item, None
        if isinstance(item, EvaluationReason):
            value, reason = item.value, item.reason
        if not isinstance(value, bool | int | float | str):
            raise ValueError(
                f"the evaluator gave {type(value).__name__} for {key!r}: an evaluator gives a "
                "boolean, a number or a label"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"the evaluator gave {value!r} for {key!r}, which is not finite")
        found[str(key)] = {"value": value, "reason": reason}
    return found


def _values(
    results: Mapping[str, Mapping[str, Any]], *, judged: bool, result: str | None
) -> dict[str, bool | float]:
    """The metrics the results give: whether every assertion held, and the one score."""
    if result is not None:
        if result not in results:
            raise ValueError(
                f"the evaluator gave no result named {result!r}; it gave "
                f"{', '.join(results) or 'none'}"
            )
        results = {result: results[result]}
    passes = [r["value"] for r in results.values() if isinstance(r["value"], bool)]
    scores = {
        name: r["value"]
        for name, r in results.items()
        if isinstance(r["value"], int | float) and not isinstance(r["value"], bool)
    }
    if len(scores) > 1:
        raise ValueError(
            f"the evaluator gave {len(scores)} scores ({', '.join(scores)}): name the one to "
            "read with the sensor's result option"
        )
    prefix = JUDGED if judged else ""
    values: dict[str, bool | float] = {}
    if passes:
        values[prefix + PASSED] = all(passes)
    for score in scores.values():
        values[prefix + SCORE] = float(score)
    return values


class EvaluatorSensor:
    """One pydantic-evals evaluator over each trial: see the module docstring.

    Options: ``evaluator`` (required), ``arguments``, ``result``; for an evaluator that
    calls a model, ``model_runtime`` (required), ``model`` and ``runtime_options``. A
    :class:`~ix.domain.ports.Review`, a :class:`~ix.domain.ports.Preflight` (a judge
    evaluator's model can be had) and a :class:`~ix.domain.ports.StandIn`."""

    def __init__(self, runtimes: Runtimes, judge_models: JudgeModels) -> None:
        self._runtimes = runtimes
        self._judge_models = judge_models

    # --- reading the experiment ---

    def review(self, options: Mapping[str, Any], around: Surroundings) -> Sequence[Problem]:
        """An evaluator pydantic-evals does not have, arguments it would refuse, a model in
        its arguments, a judge evaluator with no model runtime, model options for one that
        calls no model, and a metric the evaluator cannot give."""
        if any(placeholders(options.get(key)) for key in ("evaluator", "arguments")):
            return []
        at = around.path
        try:
            cls = _evaluator_class(options)
        except ValueError as error:
            return [
                problem(
                    f"{at}.evaluator",
                    kind="option_invalid",
                    message=str(error),
                    fix="name one of pydantic-evals' evaluators",
                    allowed=sorted(evaluator_classes()),
                    got=options.get("evaluator"),
                )
            ]
        name = cls.get_serialization_name()
        judge = _calls_a_model(cls)
        found: list[Problem] = []
        if "model" in (options.get("arguments") or {}):
            found.append(
                problem(
                    f"{at}.arguments.model",
                    kind="option_invalid",
                    message=f"{name}'s model is set by the sensor's model_runtime and model, "
                    "never in its arguments: a model named there would be resolved by "
                    "pydantic-ai from the environment",
                    fix="remove arguments.model; give model_runtime (and model) beside evaluator",
                )
            )
        else:
            found += self._instantiable(cls, options, at)
        if judge and not options.get("model_runtime"):
            found.append(
                problem(
                    f"{at}.model_runtime",
                    kind="option_missing",
                    message=f"{name} calls a model, and names no model runtime: there is no "
                    "default, and pydantic-evals' own default model is never used",
                    fix="add model_runtime: a matrix model runtime (hardline: calls a model "
                    "provider; mock: calls no model), and model",
                )
            )
        if not judge:
            found += [
                problem(
                    f"{at}.{key}",
                    kind="option_inapplicable",
                    message=f"{name} calls no model, so {key} has nothing to set",
                    fix=f"remove {key}",
                )
                for key in JUDGE_OPTIONS
                if key in options
            ]
        found += _unprovided(name, judge, around, at)
        return found

    def _instantiable(
        self, cls: type[Evaluator[Any, Any, Any]], options: Mapping[str, Any], at: str
    ) -> list[Problem]:
        """The arguments refused by the evaluator they are for; a judge's model stands in as
        None, as the root gives it only when the trial is read."""
        try:
            arguments = _arguments(options)
            cls(**arguments)
        except Exception as error:
            return [
                problem(
                    f"{at}.arguments",
                    kind="option_invalid",
                    message=f"{cls.__name__} refuses these arguments: {error}",
                    fix=f"give {cls.__name__} the arguments pydantic-evals documents for it",
                )
            ]
        return []

    def requirements(self, options: Mapping[str, Any], *, base: str) -> list[Requirement]:
        """That a judge evaluator's model can be had from the model runtime it names. Nothing
        for an evaluator that calls no model, one the review refuses, or options that hold a
        placeholder."""
        if any(placeholders(value) for value in options.values()):
            return []
        try:
            judge = _calls_a_model(_evaluator_class(options))
        except ValueError:
            return []
        name = options.get("model_runtime")
        if not judge or not name:
            return []
        return [
            runtime_requirement(
                f"the {options['evaluator']} evaluator's model runtime {name!r} gives it a model",
                lambda: self._judge_model(options),
            )
        ]

    def stand_in(self, options: Mapping[str, Any]) -> StoodIn:
        """A judge evaluator on the stand-in model runtime, whose model is pydantic-ai's
        ``TestModel``: its readings say nothing. An evaluator that calls no model stays as it
        is, reading the stand-in's replies as it would a model's."""
        try:
            judge = _calls_a_model(_evaluator_class(options))
        except ValueError:
            judge = False
        if not judge:
            return StoodIn(options=options)
        kept = {key: value for key, value in options.items() if key != "runtime_options"}
        return StoodIn(
            options={**kept, "model_runtime": STAND_IN},
            notes=(
                f"{options['evaluator']} judged by pydantic-ai's TestModel, which calls no "
                "model: the readings say nothing",
            ),
        )

    def reads_spans(self, options: Mapping[str, Any]) -> bool:
        """Whether the evaluator ``options`` name reads the trial's spans: every one but those
        known to read none; a name still to be filled in is taken to."""
        return str(options.get("evaluator", "")) not in READS_NO_SPANS

    # --- reading a trial ---

    def watch(
        self, target: Target, options: Mapping[str, Any]
    ) -> AbstractAsyncContextManager[dict[str, Any]]:
        """While the probe runs: how long it takes, and every span that ends in the trial."""
        return self._watching()

    @asynccontextmanager
    async def _watching(self) -> AsyncIterator[dict[str, Any]]:
        from pydantic_evals.otel import SpanTree

        seen: dict[str, Any] = {}
        began = time.perf_counter()
        with _capture()() as tree:
            try:
                yield seen
            finally:
                seen["duration_s"] = time.perf_counter() - began
        # The capture fills the tree as its block exits.
        if isinstance(tree, SpanTree):
            seen["spans"] = [_stored(node) for node in tree]
        else:
            seen["spans_unavailable"] = tree.message

    def _judge_model(self, options: Mapping[str, Any]) -> Any:
        """A judge evaluator's model, from the model runtime its options name: pydantic-ai's
        ``TestModel`` on a stand-in, the root's otherwise."""
        name = options.get("model_runtime")
        if not name:
            raise ValueError(
                f"{options.get('evaluator')} calls a model, and the sensor names no "
                "model_runtime: there is no default"
            )
        if self._runtimes.stand_in(MODEL_RUNTIME, str(name)):
            from pydantic_ai.models.test import TestModel

            return TestModel()
        model = options.get("model")
        return self._judge_models.model(
            str(name),
            None if model is None else str(model),
            dict(options.get("runtime_options") or {}),
        )

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        cls = _evaluator_class(options)
        judge = _calls_a_model(cls)
        arguments = _arguments(options)
        model = self._judge_model(options) if judge else None
        if judge:
            arguments["model"] = model
        try:
            return await self._read(cls(**arguments), interaction, truth, options, judged=judge)
        finally:
            close = getattr(model, "aclose", None)
            if callable(close):  # the connections its calls opened, in this loop
                await close()

    async def _read(
        self,
        evaluator: Evaluator[Any, Any, Any],
        interaction: Interaction,
        truth: Mapping[str, Any],
        options: Mapping[str, Any],
        *,
        judged: bool,
    ) -> Sensed:
        response = interaction.observations["response"]
        seen = interaction.watched.get(KIND, {})
        spans = _tree(seen)
        context = _context_class()(
            name=interaction.probe,
            inputs=str(response.get("prompt", "")),
            metadata=truth.get("metadata"),
            expected_output=truth.get("expected_output"),
            output=str(response.get("text", "")),
            duration=float(seen.get("duration_s", interaction.duration_ns / 1_000_000_000)),
            _span_tree=spans,
            attributes={},
            metrics={},
        )
        try:
            output = await evaluator.evaluate_async(context)
        except Exception:
            _spans_read(context, spans)
            raise
        read = _spans_read(context, spans)
        name = evaluator.get_default_evaluation_name()
        results = _results(output, name)
        if not results:
            raise ValueError(f"{name} gave no result for probe {interaction.probe}")
        result = options.get("result")
        values = _values(results, judged=judged, result=None if result is None else str(result))
        if evaluator.get_serialization_name() in READS_DURATION:
            values = {DURATION_PASSED: passed} if (passed := values.get(PASSED)) is not None else {}
        detail: dict[str, Any] = {
            "evaluator": evaluator.get_serialization_name(),
            "results": results,
        }
        if read is not None:
            detail["spans"] = read
        return Sensed(values=values, detail=detail)


def _spans_read(context: Any, spans: SpanTree | SpanTreeRecordingError) -> int | None:
    """How many spans the evaluator had to read, or None when it read none. Raises
    :class:`NoSpansError` when it read the span tree and there was none, or one without a
    span of the trial: an answer from nothing is not a reading."""
    from pydantic_evals.otel import SpanTreeRecordingError

    if not context.read_spans:
        return None
    if isinstance(spans, SpanTreeRecordingError):
        raise NoSpansError(
            f"the evaluator reads the trial's spans, and none were captured: {spans.message} "
            "A run whose sensors read spans keeps them in this process, on the tracer provider "
            "installed or one it installs, which needs the OpenTelemetry SDK (opentelemetry-sdk)"
        )
    if not spans.nodes_by_id:
        raise NoSpansError(
            "the evaluator reads the trial's spans, and the trial left none: nothing reported "
            "the session as spans, though matrix's otel observer is on every run whose sensors "
            "read them"
        )
    return len(spans.nodes_by_id)


def _unprovided(name: str, judge: bool, around: Surroundings, at: str) -> list[Problem]:
    """A metric named of this sensor that an evaluator of this sort never gives: a judged
    one from an evaluator that calls no model, or the other way round; and from one that
    reads how long the probe took, any but ``eval_duration_passed``, and that one from any
    other."""
    given = _given(name, judge)
    why = (
        "reads how long the probe took"
        if name in READS_DURATION
        else f"{'calls a' if judge else 'calls no'} model"
    )
    named = [
        metric for sensor in around.sensors if sensor.kind == KIND for metric in sensor.metrics
    ]
    return [
        problem(
            f"{at}.evaluator",
            kind="metric_unprovided",
            message=f"{name} {why}, so it gives {' and '.join(given)}, not {metric}",
            fix=f"name {' and '.join(given)} instead of {metric}",
            allowed=given,
            got=metric,
        )
        for metric in named
        if metric not in given
    ]


def _given(name: str, judge: bool) -> tuple[str, ...]:
    """The metrics an evaluator of this name gives."""
    if judge:
        return (f"{JUDGED}{PASSED}", f"{JUDGED}{SCORE}")
    if name in READS_DURATION:
        return (DURATION_PASSED,)
    return (PASSED, SCORE)
