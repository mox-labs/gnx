"""The ``evaluator`` sensor: pydantic-evals' evaluators over ix's trials.

The deterministic evaluators read a reply on a stand-in. The span evaluators read the spans a
scripted agent run left: a runtime that reports timed tool calls, run through matrix's
``BoundAgent`` with matrix's ``otel`` observer on a tracer provider of the test's own, which
pydantic-evals' capture is pointed at. Nothing here installs a global provider, and nothing
calls a model: a judge evaluator judges on pydantic-ai's ``TestModel``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix
import pytest
import yaml
from matrix.adapters._out.observers.otel import OtelObserver
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_evals.evaluators import llm_as_a_judge
from pydantic_evals.otel import _context_in_memory_span_exporter as capture

import ix
from ix.adapters._out.evaluators import KIND, EvaluatorSensor, NoSpansError
from ix.composition import Root
from ix.composition.extension import register
from ix.composition.settings import IxSettings
from ix.domain.run import Interaction, Target

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ix.domain.ports import Sensed

SCRIPTED = "acme.v1.agent-runtime.scripted"
ONESHOT = "acme.v1.model-runtime.oneshot"
SPANS = "acme.v1.observer.spans"


class _Runtimes:
    """matrix's runtimes as the sensor sees them: ``mock`` is the one stand-in."""

    def stand_in(self, point: str, name: str) -> bool:
        return name in ("mock", "matrix.v1.model-runtime.mock")


class _Refused:
    """A root with no judge model to give: the test fails if asked."""

    def model(self, runtime: str, model: str | None, config: Mapping[str, Any]) -> Any:
        pytest.fail(f"the root was asked for a judge model on {runtime!r}")


class _Gives:
    """A root that gives the model it holds, and remembers what it was asked."""

    def __init__(self, model: Any) -> None:
        self.given = model
        self.asked: list[tuple[str, str | None, dict[str, Any]]] = []

    def model(self, runtime: str, model: str | None, config: Mapping[str, Any]) -> Any:
        self.asked.append((runtime, model, dict(config)))
        return self.given


def _sensor(judge_models: Any = None) -> EvaluatorSensor:
    return EvaluatorSensor(_Runtimes(), judge_models or _Refused())  # type: ignore[arg-type]


def _trial(text: str, *, prompt: str = "What is 2 + 2?", watched: Any = None) -> Interaction:
    return Interaction(
        seq=0,
        subject="agent",
        role="variant",
        probe="sum",
        started_ns=0,
        duration_ns=3_000_000_000,
        observations={"response": {"prompt": prompt, "text": text}},
        watched={} if watched is None else {KIND: watched},
    )


async def _read(
    options: Mapping[str, Any],
    interaction: Interaction,
    truth: Mapping[str, Any] | None = None,
    *,
    judge_models: Any = None,
) -> Sensed:
    sensed = await _sensor(judge_models).sense(interaction, truth=truth or {}, options=options)
    assert not isinstance(sensed, dict)
    return sensed


# --- a scripted agent run, and the spans it leaves -----------------------------------------


def _call(
    name: str, second: int, arguments: dict[str, Any], *, error: bool = False
) -> dict[str, Any]:
    return {
        "name": name,
        "id": f"toolu_{second}",
        "input": arguments,
        "started": f"2026-10-22T10:00:{second:02d}+00:00",
        "ended": f"2026-10-22T10:00:{second:02d}.500000+00:00",
        "error": error,
    }


#: Read, then Grep, then Read again; and a Bash call that failed.
CALLS = (
    _call("Read", 1, {"file_path": "app.py"}),
    _call("Grep", 2, {"pattern": "def main"}),
    _call("Read", 3, {"file_path": "test_app.py"}),
    _call("Bash", 4, {"command": "pytest"}, error=True),
)


class Scripted:
    """An agent runtime whose session makes the calls it was given, timed as the Claude
    runtime times them."""

    def __init__(self, calls: tuple[dict[str, Any], ...] = CALLS) -> None:
        self.calls = calls

    def check(self, definition: matrix.AgentDefinition) -> None:
        return None

    async def run(self, definition: matrix.AgentDefinition, task: str) -> matrix.AgentResponse:
        return matrix.AgentResponse(
            content="Fixed: main now returns 0.", tool_calls=self.calls, num_turns=2,
            stop="completed", family="scripted", model="s-1",
        )  # fmt: skip


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> TracerProvider:
    """An SDK tracer provider of the test's own, which pydantic-evals' capture reads as the
    installed one."""
    installed = TracerProvider()
    monkeypatch.setattr(capture, "get_tracer_provider", lambda: installed)
    return installed


async def _watched_run(
    provider: TracerProvider, *, capture_content: bool = False, run: bool = True
) -> dict[str, Any]:
    """What the sensor's watch keeps of a scripted session, run inside it as a probe is."""
    observer = OtelObserver(
        tracer_provider=provider, meter_provider=MeterProvider(), capture_content=capture_content
    )
    agent = matrix.BoundAgent(
        matrix.AgentDefinition(name="coder"), Scripted(), observers=[observer]
    )
    async with _sensor().watch(Target(subject="agent"), {}) as seen:
        if run:
            await agent.run("Fix main.")
    return seen


def _agentic(evaluator: str, **arguments: Any) -> dict[str, Any]:
    return {"evaluator": evaluator, "arguments": arguments}


class TestEvaluatorsThatReadTheReply:
    @pytest.mark.parametrize(
        ("evaluator", "arguments", "passed"),
        [
            ("Equals", {"value": "4"}, True),
            ("Equals", {"value": "four"}, False),
            ("Contains", {"value": "4"}, True),
            ("Contains", {"value": "5"}, False),
            ("IsInstance", {"type_name": "str"}, True),
            ("IsInstance", {"type_name": "int"}, False),
        ],
    )
    async def test_an_assertion_is_eval_passed(
        self, evaluator: str, arguments: dict[str, Any], passed: bool
    ) -> None:
        sensed = await _read(_agentic(evaluator, **arguments), _trial("4"))
        assert sensed.values == {"eval_passed": passed}
        assert sensed.detail["evaluator"] == evaluator
        (result,) = sensed.detail["results"].values()
        assert result["value"] is passed
        assert "spans" not in sensed.detail  # it read none

    async def test_the_probes_expected_output_is_the_evaluators(self) -> None:
        options = {"evaluator": "EqualsExpected"}
        assert (await _read(options, _trial("4"), {"expected_output": "4"})).values == {
            "eval_passed": True
        }
        assert (await _read(options, _trial("5"), {"expected_output": "4"})).values == {
            "eval_passed": False
        }

    async def test_an_evaluator_that_gives_nothing_fails_the_reading(self) -> None:
        """EqualsExpected compares only when there is an expected output: with none it gives
        no result, and a reading of nothing is no reading."""
        with pytest.raises(ValueError, match="gave no result for probe sum"):
            await _read({"evaluator": "EqualsExpected"}, _trial("4"))

    async def test_max_duration_reads_how_long_the_probe_took(self) -> None:
        trial = _trial("4", watched={"duration_s": 1.5, "spans": []})
        assert (await _read(_agentic("MaxDuration", seconds=2), trial)).values == {
            "eval_passed": True
        }
        assert (await _read(_agentic("MaxDuration", seconds=1), trial)).values == {
            "eval_passed": False
        }

    async def test_an_evaluator_that_reads_no_span_needs_no_sdk(self) -> None:
        trial = _trial("4", watched={"duration_s": 0.1, "spans_unavailable": "no provider"})
        assert (await _read(_agentic("Equals", value="4"), trial)).values == {"eval_passed": True}


class TestEvaluatorsThatReadTheSpans:
    """The agentic evaluators, on the spans a scripted session left in the trial (W4.11:
    matrix's ``execute_tool`` spans carry what they read)."""

    async def test_tool_correctness_reads_the_sessions_tool_calls(
        self, provider: TracerProvider
    ) -> None:
        trial = _trial("Fixed.", watched=await _watched_run(provider))
        right = await _read(_agentic("ToolCorrectness", expected_tools=["Read", "Grep", "Read"]),
                            trial)  # fmt: skip
        assert right.values == {"eval_passed": True}
        assert right.detail["spans"] == 5  # the session and its four tool calls, Bash's failed
        wrong = await _read(_agentic("ToolCorrectness", expected_tools=["Read"]), trial)
        assert wrong.values == {"eval_passed": False}
        (result,) = wrong.detail["results"].values()
        assert "unexpected tools: 'Grep' (x1), 'Read' (x1)" in result["reason"]

    async def test_a_failed_tool_call_counts_as_pydantic_evals_says(
        self, provider: TracerProvider
    ) -> None:
        """Bash failed: its span is an error, so ToolCorrectness leaves it out by default and
        MaxToolCalls counts it, as pydantic-evals documents."""
        trial = _trial("Fixed.", watched=await _watched_run(provider))
        with_bash = _agentic("ToolCorrectness", expected_tools=["Read", "Grep", "Read", "Bash"])
        assert (await _read(with_bash, trial)).values == {"eval_passed": False}
        counted = {**with_bash, "arguments": {**with_bash["arguments"], "include_failed": True}}
        assert (await _read(counted, trial)).values == {"eval_passed": True}
        assert (await _read(_agentic("MaxToolCalls", max_calls=3), trial)).values == {
            "eval_passed": False
        }
        assert (await _read(_agentic("MaxToolCalls", max_calls=4), trial)).values == {
            "eval_passed": True
        }

    async def test_trajectory_match_is_eval_score(self, provider: TracerProvider) -> None:
        trial = _trial("Fixed.", watched=await _watched_run(provider))
        exact = _agentic("TrajectoryMatch", expected_trajectory=["Read", "Grep", "Read"],
                         order="exact")  # fmt: skip
        assert (await _read(exact, trial)).values == {"eval_score": 1.0}
        partial = _agentic("TrajectoryMatch", expected_trajectory=["Read", "Read"])
        sensed = await _read(partial, trial)
        assert sensed.values == {"eval_score": pytest.approx(0.8)}  # LCS 2 of 3 and of 2

    async def test_argument_correctness_reads_arguments_only_when_content_is_captured(
        self, provider: TracerProvider
    ) -> None:
        """A tool call's arguments are content: on the span only with capture_content."""
        options = _agentic("ArgumentCorrectness", tool_name="Grep",
                           expected_arguments={"pattern": "def main"})  # fmt: skip
        without = _trial("Fixed.", watched=await _watched_run(provider))
        sensed = await _read(options, without)
        assert sensed.values == {"eval_passed": False}
        (result,) = sensed.detail["results"].values()
        assert "arguments not available" in result["reason"]
        with_content = _trial("Fixed.", watched=await _watched_run(provider, capture_content=True))
        assert (await _read(options, with_content)).values == {"eval_passed": True}

    async def test_the_spans_are_kept_as_json_and_read_back_the_same(
        self, provider: TracerProvider
    ) -> None:
        """The interaction keeps what the watch saw, so a run read back later is read the
        same way, with no SDK then."""
        seen = await _watched_run(provider)
        kept = json.loads(json.dumps(seen))
        names = sorted(span["name"] for span in kept["spans"])
        assert names == ["execute_tool Bash", "execute_tool Grep", "execute_tool Read",
                         "execute_tool Read", "invoke_agent coder"]  # fmt: skip
        tool = next(span for span in kept["spans"] if span["name"] == "execute_tool Grep")
        assert tool["attributes"]["gen_ai.tool.name"] == "Grep"
        assert tool["parent_span_id"] is not None and len(tool["trace_id"]) == 32
        assert "gen_ai.tool.call.arguments" not in tool["attributes"]
        options = _agentic("ToolCorrectness", expected_tools=["Read", "Grep", "Read"])
        assert (await _read(options, _trial("Fixed.", watched=kept))).values == {
            "eval_passed": True
        }


class TestWithoutTheTrialsSpans:
    """An evaluator that reads spans and has none is no reading: pydantic-evals' own answer
    (False, 0.0) would read as an agent that called no tool."""

    async def test_no_sdk_provider_fails_the_reading_and_says_how_to_have_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The API's own provider, as a process that installed no SDK has.
        monkeypatch.setattr(capture, "get_tracer_provider", trace.ProxyTracerProvider)
        seen = await _watched_run(TracerProvider())
        assert "spans" not in seen and "set_tracer_provider" in seen["spans_unavailable"]
        trial = _trial("Fixed.", watched=seen)
        options = _agentic("ToolCorrectness", expected_tools=["Read"])
        with pytest.raises(NoSpansError, match="none were captured") as raised:
            await _read(options, trial)
        # Changed requirement (W11.2b): a run keeps its spans in process, so the way to have
        # them is the SDK, not export.
        assert "opentelemetry-sdk" in str(raised.value)
        # HasMatchingSpan raises where ToolCorrectness answers False: the same reason either way
        query = _agentic("HasMatchingSpan", query={"name_contains": "execute_tool"})
        with pytest.raises(NoSpansError, match="none were captured"):
            await _read(query, trial)

    async def test_spans_with_none_from_the_trial_fail_the_reading(
        self, provider: TracerProvider
    ) -> None:
        """An SDK, and nothing reported the session as spans (here, the session never ran
        inside the watch)."""
        seen = await _watched_run(provider, run=False)
        assert seen["spans"] == []
        with pytest.raises(NoSpansError, match="the trial left none"):
            await _read(_agentic("MaxToolCalls", max_calls=5), _trial("x", watched=seen))

    async def test_a_trial_the_sensor_did_not_watch_says_so(self) -> None:
        with pytest.raises(NoSpansError, match="did not watch it"):
            await _read(_agentic("MaxToolCalls", max_calls=5), _trial("x"))


class TestTheJudgesModel:
    """An LLMJudge or GEval judges on the model the composition root gives, from the matrix
    model runtime the experiment names: never pydantic-evals' default."""

    @pytest.fixture(autouse=True)
    def _no_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(*args: Any, **kwargs: Any) -> Any:
            pytest.fail("pydantic-evals' default judge model was used")

        monkeypatch.setattr(llm_as_a_judge, "_default_model", FunctionModel(refuse))

    async def test_the_judge_judges_on_the_roots_model(self) -> None:
        model = TestModel()
        root = _Gives(model)
        options = {
            "evaluator": "LLMJudge", "arguments": {"rubric": "Says four."},
            "model_runtime": "oneshot", "model": "one-1", "runtime_options": {"retries": 0},
        }  # fmt: skip
        sensed = await _read(options, _trial("4"), judge_models=root)
        assert root.asked == [("oneshot", "one-1", {"retries": 0})]
        assert model.last_model_request_parameters is not None  # it was called
        assert set(sensed.values) == {"judged_eval_passed"}

    async def test_a_score_and_a_pass_are_both_judged(self) -> None:
        options = {
            "evaluator": "LLMJudge",
            "arguments": {"rubric": "Says four.", "score": {"include_reason": True}},
            "model_runtime": "oneshot",
        }
        sensed = await _read(options, _trial("4"), judge_models=_Gives(TestModel()))
        assert set(sensed.values) == {"judged_eval_passed", "judged_eval_score"}
        assert set(sensed.detail["results"]) == {"LLMJudge_pass", "LLMJudge_score"}
        only = {**options, "result": "LLMJudge_score"}
        assert set((await _read(only, _trial("4"), judge_models=_Gives(TestModel()))).values) == {
            "judged_eval_score"
        }

    async def test_a_stand_in_runtime_judges_on_test_model_and_asks_the_root_nothing(
        self,
    ) -> None:
        options = {"evaluator": "LLMJudge", "arguments": {"rubric": "Says four."},
                   "model_runtime": "mock"}  # fmt: skip
        sensed = await _read(options, _trial("4"))  # _Refused fails the test if asked
        assert set(sensed.values) == {"judged_eval_passed"}

    async def test_a_judge_with_no_model_runtime_is_not_read(self) -> None:
        with pytest.raises(ValueError, match="names no model_runtime"):
            steps = {"criteria": "Correct.", "evaluation_steps": ["Check the sum."]}
            await _read({"evaluator": "GEval", "arguments": steps}, _trial("4"))

    def test_the_stand_in_puts_a_judge_on_mock_and_leaves_the_rest(self) -> None:
        sensor = _sensor()
        judge = {"evaluator": "LLMJudge", "arguments": {"rubric": "r"},
                 "model_runtime": "hardline", "runtime_options": {"x": 1}}  # fmt: skip
        stood = sensor.stand_in(judge)
        assert stood.options == {"evaluator": "LLMJudge", "arguments": {"rubric": "r"},
                                 "model_runtime": "mock"}  # fmt: skip
        assert "TestModel" in stood.notes[0]
        plain = _agentic("Equals", value="4")
        assert sensor.stand_in(plain).options == plain and sensor.stand_in(plain).notes == ()


# --- through validate, plan and run -------------------------------------------------------


def _settings(observers: tuple[str, ...] = ()) -> matrix.Config[IxSettings]:
    return matrix.Config[IxSettings](
        client=IxSettings(), matrix=matrix.MatrixConfig(observers=observers)
    )


class Oneshot:
    """A model runtime a third party registers, which is no stand-in."""

    def check(self, model: str | None) -> None:
        return None

    async def call(self, request: matrix.ModelRequest) -> matrix.ModelCall:
        return matrix.ModelCall(text="4", name="one-1", model="one-1")


def _registry(provider: TracerProvider | None = None) -> matrix.Registry:
    """ix's adapters, matrix's built-ins, the scripted agent runtime, a model runtime that
    is no stand-in, and an ``otel`` observer on the test's provider, with content."""
    registry = matrix.default_registry(discover=False)
    register(registry)
    registry.register("agent-runtime", SCRIPTED, Scripted)
    registry.register("model-runtime", ONESHOT, Oneshot)
    if provider is not None:
        registry.register(
            "observer",
            SPANS,
            lambda: OtelObserver(
                tracer_provider=provider, meter_provider=MeterProvider(), capture_content=True
            ),
        )
    return registry


def _experiment(tmp_path: Path, **changes: Any) -> Path:
    data: dict[str, Any] = {
        "name": "fixes-main",
        "question": "Does the agent read the code before it fixes it?",
        "genre": "evaluate",
        "subjects": {"agent": {"kind": "agent-session", "runtime": SCRIPTED}},
        "probes": [
            {
                "id": "fix",
                "kind": "prompt",
                "prompt": "Fix main.",
                "truth": {"expected_output": "Fixed: main now returns 0."},
            },
        ],  # fmt: skip
        "sensors": [
            {
                "kind": "evaluator",
                "evaluator": "ToolCorrectness",
                "arguments": {"expected_tools": ["Read", "Grep", "Read"]},
            },
        ],  # fmt: skip
        "primary": {"metric": "eval_passed"},
        "threshold": {"op": ">=", "value": 0.8},
        "repeats": 5,
        **changes,
    }
    folder = tmp_path / "fixes-main"
    folder.mkdir(exist_ok=True)
    (folder / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return folder


def _rows(report: ix.Report, table: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(report.tables[table]).read_text().splitlines()]


def _readings(report: ix.Report) -> list[dict[str, Any]]:
    return _rows(report, "ix.v1.reading")


def _problems(root: Root, lab: Path) -> list[tuple[str, str]]:
    return [(found.path, found.kind) for found in root.validate(lab).problems]


class TestValidate:
    def test_the_experiment_validates(self, tmp_path: Path) -> None:
        root = Root(registry=_registry(), settings=_settings())
        assert _problems(root, _experiment(tmp_path)) == []

    @pytest.mark.parametrize(
        ("sensor", "found"),
        [
            ({"evaluator": "Pythonic"}, [("sensors.0.evaluator", "option_invalid")]),
            ({"evaluator": "Contains", "arguments": {"needle": "x"}},
             [("sensors.0.arguments", "option_invalid")]),
            ({"evaluator": "Equals", "arguments": {"value": 4}, "model_runtime": "hardline"},
             [("sensors.0.model_runtime", "option_inapplicable")]),
            ({"evaluator": "LLMJudge", "arguments": {"rubric": "r"}},
             [("sensors.0.model_runtime", "option_missing"),
              ("sensors.0.evaluator", "metric_unprovided")]),
            ({"evaluator": "LLMJudge", "arguments": {"rubric": "r", "model": "openai:gpt-5.2"},
              "model_runtime": "hardline"},
             [("sensors.0.arguments.model", "option_invalid"),
              ("sensors.0.evaluator", "metric_unprovided")]),
        ],
    )  # fmt: skip
    def test_what_the_evaluator_would_refuse_is_a_problem(
        self, sensor: dict[str, Any], found: list[tuple[str, str]], tmp_path: Path
    ) -> None:
        lab = _experiment(tmp_path, sensors=[{"kind": "evaluator", **sensor}])
        assert sorted(_problems(Root(registry=_registry(), settings=_settings()), lab)) == sorted(
            found
        )

    def test_a_judged_metric_from_an_evaluator_that_calls_no_model_is_a_problem(
        self, tmp_path: Path
    ) -> None:
        lab = _experiment(tmp_path, primary={"metric": "text_matched"},
                          metrics=["judged_eval_passed"],
                          sensors=[{"kind": "evaluator", "evaluator": "Equals",
                                    "arguments": {"value": "4"}},
                                   {"kind": "text-match", "contains": "Fixed"}])  # fmt: skip
        problems = Root(registry=_registry(), settings=_settings()).validate(lab).problems
        (wrong,) = [p for p in problems if p.kind == "metric_unprovided"]
        assert wrong.got == "judged_eval_passed" and "eval_passed" in wrong.fix


class TestPlanAndRun:
    def test_a_judge_judges_on_a_hardline_row_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """W11.2b. Before, the root had no pydantic-ai Model over a model runtime to give, so a
        judge evaluator on a real runtime was ``Runnable: no``. Now it judges on the matrix
        model runtime it names, here ``hardline`` with a mock row, configured as every model
        is (``matrix.models``): the plan says it can run, and each reading is the row's grade,
        given as pydantic-ai's output tool call, through hardline. Offline: the row is
        hardline's mock, and pydantic-evals' own default model fails the test if asked."""

        def refuse(*args: Any, **kwargs: Any) -> Any:
            pytest.fail("pydantic-evals' default judge model was used")

        monkeypatch.setattr(llm_as_a_judge, "_default_model", FunctionModel(refuse))
        grade = {"reason": "It says it fixed main.", "pass": True, "score": 1.0}
        row = {
            "backend": "mock", "model": "grader-1", "family": "mockjudge",
            "options": {"script": [{"tool_calls": [
                {"name": "final_result", "arguments": json.dumps(grade)}
            ]}]},
        }  # fmt: skip
        settings = matrix.Config[IxSettings](
            client=IxSettings(),
            matrix=matrix.MatrixConfig(models={"models": {"grader": row}}),
        )
        lab = _experiment(
            tmp_path,
            primary={"metric": "text_matched"},
            metrics=["judged_eval_passed"],
            sensors=[{"kind": "evaluator", "evaluator": "LLMJudge",
                      "arguments": {"rubric": "Says it fixed main."},
                      "model_runtime": "hardline", "model": "grader"},
                     {"kind": "text-match", "contains": "Fixed"}],
        )  # fmt: skip
        root = Root(registry=_registry(), settings=settings)
        plan = root.plan(lab)
        (judge,) = [r for r in plan.requirements if "LLMJudge" in r.need]
        assert judge.holds, judge.why
        report = root.run(lab, exploratory=True)
        judged = [
            json.loads(line)
            for line in Path(report.tables["ix.v1.reading"]).read_text().splitlines()
            if '"judged_eval_passed"' in line
        ]
        assert len(judged) == 5
        assert all(r["failure"] is None and r["value"] is True for r in judged)
        (result,) = judged[0]["detail"]["results"].values()
        assert result["reason"] == "It says it fixed main."

    def test_a_model_runtime_that_gives_no_pydantic_ai_model_cannot_judge(
        self, tmp_path: Path
    ) -> None:
        """A judge evaluator needs a pydantic-ai Model over its model runtime; a runtime that
        offers none (``pydantic_ai_model``) is ``Runnable: no``, saying so, and nothing is
        called."""
        lab = _experiment(
            tmp_path,
            primary={"metric": "text_matched"},
            metrics=["judged_eval_passed"],
            sensors=[{"kind": "evaluator", "evaluator": "LLMJudge",
                      "arguments": {"rubric": "Says it fixed main."},
                      "model_runtime": ONESHOT},
                     {"kind": "text-match", "contains": "Fixed"}],
        )  # fmt: skip
        plan = Root(registry=_registry(), settings=_settings()).plan(lab)
        (judge,) = [r for r in plan.requirements if "LLMJudge" in r.need]
        assert not judge.holds and "gives no pydantic-ai Model" in judge.why
        assert "pydantic_ai_model" in judge.fix
        assert judge.needed_by == ("sensors.0",)

    def test_a_run_reads_tool_calls_from_each_trials_spans(
        self, tmp_path: Path, provider: TracerProvider
    ) -> None:
        """The whole path: each trial is watched while its probe runs, its spans kept in the
        interaction, and ToolCorrectness reads them."""
        root = Root(registry=_registry(provider), settings=_settings(observers=(SPANS,)))
        report = root.run(_experiment(tmp_path), exploratory=True)
        assert report.status == "completed" and report.simulated == "none"
        readings = [
            json.loads(line)
            for line in Path(report.tables["ix.v1.reading"]).read_text().splitlines()
        ]
        assert len(readings) == 5
        assert all(r["metric"] == "eval_passed" and r["value"] is True for r in readings)
        assert all(r["detail"]["spans"] == 5 for r in readings)
        interactions = [
            json.loads(line)
            for line in Path(report.tables["ix.v1.interaction"]).read_text().splitlines()
        ]
        watched = interactions[0]["watched"][KIND]
        assert watched["duration_s"] > 0
        # Each trial's spans are its own: one session, not every session so far.
        assert sum(s["name"].startswith("invoke_agent") for s in watched["spans"]) == 1

    def test_with_no_export_the_spans_are_kept_in_process_and_none_leaves(
        self, tmp_path: Path
    ) -> None:
        """W11.2b. Before, a span evaluator had spans only when the ``OTEL_*`` variables asked
        for export (the command installs the SDK, and matrix attaches its ``otel`` observer,
        only then): with nothing configured each reading failed. Now a run whose sensors read
        spans installs a tracer provider with no exporter and attaches matrix's observer, so
        the evaluator reads the trial's spans and nothing leaves the process."""
        report = Root(registry=_registry(), settings=_settings()).run(
            _experiment(tmp_path), exploratory=True
        )
        readings = _readings(report)
        assert len(readings) == 5
        assert all(r["value"] is True and r["detail"]["spans"] == 5 for r in readings)
        installed = trace.get_tracer_provider()
        assert isinstance(installed, TracerProvider)
        # Its one processor is pydantic-evals' capture, which keeps a trial's spans in memory
        # for the sensor: no exporter.
        exporters = [
            type(getattr(processor, "span_exporter", None)).__name__
            for processor in installed._active_span_processor._span_processors
        ]
        assert exporters == ["_ContextInMemorySpanExporter"]

    def test_with_export_configured_the_installed_provider_is_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The command installed the SDK from the ``OTEL_*`` variables: the run's spans go to
        that provider's exporter, as asked, and the evaluator reads them there; no second
        provider is installed."""
        monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
        exported = InMemorySpanExporter()
        configured = TracerProvider()
        configured.add_span_processor(SimpleSpanProcessor(exported))
        trace.set_tracer_provider(configured)
        report = Root(registry=_registry(), settings=_settings()).run(
            _experiment(tmp_path), exploratory=True
        )
        assert trace.get_tracer_provider() is configured
        assert all(r["value"] is True for r in _readings(report))
        names = [span.name for span in exported.get_finished_spans()]
        assert names.count("invoke_agent agent") == 5 and names.count("execute_tool Grep") == 5

    def test_a_judges_spans_never_join_the_tree_an_evaluator_reads(self, tmp_path: Path) -> None:
        """The subject is one model call per trial, on a hardline row; a judge reads each reply
        on another hardline row. The judge's model calls are spans too (``matrix.model``,
        hardline's ``chat``), made while the sensors read, after the trial's spans were
        kept: MaxModelRequests counts the subject's one request, never the judge's."""
        rows = {
            "answerer": {"backend": "mock", "model": "a-1", "family": "answering",
                         "options": {"default": "Fixed: main now returns 0."}},
            "grader": {"backend": "mock", "model": "g-1", "family": "grading",
                       "options": {"default": '{"score": 0.9, "reason": "fixed"}'}},
        }  # fmt: skip
        settings = matrix.Config[IxSettings](
            client=IxSettings(), matrix=matrix.MatrixConfig(models={"models": rows})
        )
        lab = _experiment(
            tmp_path,
            subjects={"agent": {"kind": "model-call", "runtime": "hardline",
                                "model": "answerer"}},
            sensors=[{"kind": "evaluator", "evaluator": "MaxModelRequests",
                      "arguments": {"max_requests": 1}},
                     {"kind": "judge", "rubric": "Says it fixed main.",
                      "model_runtime": "hardline", "model": "grader"}],
            metrics=["judged_score"],
        )  # fmt: skip
        report = Root(registry=_registry(), settings=settings).run(lab, exploratory=True)
        readings = _readings(report)
        counted = [r for r in readings if r["metric"] == "eval_passed"]
        assert len(counted) == 5 and all(r["value"] is True for r in counted)
        assert all(
            "1 model request(s)" in r["detail"]["results"]["MaxModelRequests"]["reason"]
            for r in counted
        )
        judged = [r for r in readings if r["metric"] == "judged_score"]
        assert len(judged) == 5 and all(r["value"] == 0.9 for r in judged)
        for interaction in _rows(report, "ix.v1.interaction"):
            spans = interaction["watched"][KIND]["spans"]
            rows_called = {s["attributes"].get("hardline.row") for s in spans} - {None}
            assert rows_called == {"answerer"}
            (call,) = [s for s in spans if s["name"] == "matrix.model"]
            (chat,) = [s for s in spans if s["name"].startswith("chat ")]
            assert chat["parent_span_id"] == call["span_id"]  # under the call that made it

    def test_with_no_sdk_to_install_each_reading_fails_with_the_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the OpenTelemetry SDK no provider can be installed: every reading is a
        sensor failure saying why, and none reads as an agent that called no tool."""
        monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)
        report = Root(registry=_registry(), settings=_settings()).run(
            _experiment(tmp_path), exploratory=True
        )
        readings = _readings(report)
        assert readings and all(r["value"] is None for r in readings)
        assert all("none were captured" in r["failure"]["message"] for r in readings)
        assert all("opentelemetry-sdk" in r["failure"]["message"] for r in readings)

    def test_a_dry_run_of_a_judge_evaluator_calls_no_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*args: Any, **kwargs: Any) -> Any:
            pytest.fail("pydantic-evals' default judge model was used")

        monkeypatch.setattr(llm_as_a_judge, "_default_model", FunctionModel(refuse))
        lab = _experiment(
            tmp_path,
            subjects={"agent": {"kind": "agent-session", "runtime": "claude-sdk"}},
            primary={"metric": "text_matched"},
            metrics=["judged_eval_passed"],
            sensors=[{"kind": "evaluator", "evaluator": "LLMJudge",
                      "arguments": {"rubric": "Says it fixed main."},
                      "model_runtime": "hardline", "model": "judge"},
                     {"kind": "text-match", "contains": "Fixed"}],
        )  # fmt: skip
        report = Root(settings=_settings()).run(lab, dry_run=True)
        assert report.dry_run and report.exit_code == 0
        assert any("TestModel" in note for note in report.stood_in)
        judged = [
            json.loads(line)
            for line in Path(report.tables["ix.v1.reading"]).read_text().splitlines()
            if "judged_eval_passed" in line
        ]
        assert judged and all(r["failure"] is None for r in judged)


def test_ix_starts_and_lists_its_kinds_without_pydantic_evals() -> None:
    """pydantic-evals and pydantic-ai are imported inside the adapter, when a reading needs
    them: building every adapter, as ``ix kinds`` does, imports neither."""
    code = (
        "import sys, ix\n"
        "ix.kinds()\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'pydantic_evals', 'pydantic_ai'}))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_the_kind_says_what_a_judge_sends() -> None:
    """The kind's own words: the judge's model is never the library's default."""
    kind = ix.kinds().get("sensor", "evaluator")
    assert kind is not None
    assert (
        "pydantic-evals' own default model is never used"
        in kind.options["model_runtime"].description
    )


def test_the_evaluators_said_to_read_no_span_never_reach_for_one() -> None:
    """Which of pydantic-evals' evaluators read the trial's spans is ix's word
    (``READS_NO_SPANS``), checked here against the release installed: each it names never
    touches the span tree in its code, and each other one does. One that starts or stops
    reading spans in a later release fails here, not at a run."""
    import inspect

    from ix.adapters._out.evaluators import READS_NO_SPANS, _registry

    for name, cls in _registry().items():
        assert ("span_tree" in inspect.getsource(cls)) == (name not in READS_NO_SPANS), name
    assert _sensor().reads_spans({"evaluator": "ToolCorrectness"})
    assert not _sensor().reads_spans({"evaluator": "LLMJudge"})
