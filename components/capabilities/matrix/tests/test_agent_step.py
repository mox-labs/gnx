"""Agents as components, and agent failures classified by whose problem they are."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from matrix import (
    AGENT_RESPONSE,
    AGENT_STEP,
    AgentDefinition,
    AgentResponse,
    AgentRuntimeError,
    AgentStep,
    AgentStepConfig,
    BoundAgent,
    CompilationError,
    ConfigError,
    MatrixConfig,
    NotFoundError,
    RecordingObserver,
    RunError,
    compose,
)
from matrix.adapters._out.runtime.claude_sdk import _http_reason
from matrix.adapters._out.runtime.mock import MockRuntime, MockRuntimeConfig

DIFF = "acme.v1.diff"
REVIEW = "acme.v1.review"
TASK = "matrix.v1.task"


class Review(BaseModel):
    verdict: str


def container(reply: str = '{"verdict": "ship"}') -> Any:
    return compose(
        MatrixConfig.model_validate(
            {
                "runtimes": {"m": {"type": "mock", "default": reply}},
                "agents": {"reviewer": {"runtime": "m", "system_prompt": "Review."}},
            }
        )
    )


async def test_an_agent_runs_as_a_component_with_the_rows_it_requires_rendered() -> None:
    box = container()
    box.registry.register_payload(REVIEW, Review)
    wiring = box.wire(
        [
            {
                "name": "rev",
                "type": AGENT_STEP,
                "config": {
                    "agent": "reviewer",
                    "inputs": {"diff": DIFF},
                    "task": "Review:\n{diff}",
                    "output": REVIEW,
                },
            }
        ],
        inputs=[DIFF],
    )
    assert wiring.components["rev"].requires == {DIFF}
    assert wiring.components["rev"].provides == {AGENT_RESPONSE, REVIEW}
    run = await box.executor().run(wiring, {DIFF: "+ added line"})
    assert run.rows(REVIEW) == (Review(verdict="ship"),)
    assert isinstance(run.rows(AGENT_RESPONSE)[0], AgentResponse)
    runtime = box.runtime("m")
    assert runtime.calls == [("reviewer", "Review:\n+ added line")]


async def test_a_reply_without_json_breaks_the_declared_output() -> None:
    box = container(reply="looks fine to me")
    wiring = box.wire(
        [{"name": "rev", "type": AGENT_STEP, "config": {"agent": "reviewer", "output": REVIEW}}],
        inputs=[TASK],
    )
    with pytest.raises(RunError) as e:
        await box.executor().run(wiring, {TASK: "go"})
    assert (e.value.kind, e.value.cause_kind) == ("run_failed", "contract")
    assert "holds no JSON" in str(e.value) and e.value.exit_code == 8


async def test_a_reply_without_json_says_what_the_agent_said_and_how_to_get_json() -> None:
    """Regression: the error named neither the reply nor the remedy, and the run's error
    dropped the cause's fix, so an agent driving matrix could not tell that ``output``
    requires the reply to be JSON."""
    box = container(reply="looks fine to me")
    wiring = box.wire(
        [{"name": "rev", "type": AGENT_STEP, "config": {"agent": "reviewer", "output": REVIEW}}],
        inputs=[TASK],
    )
    with pytest.raises(RunError) as e:
        await box.executor().run(wiring, {TASK: "go"})
    assert "'looks fine to me'" in str(e.value) and "`output`" in str(e.value)
    fix = e.value.payload()["error"]["fix"]
    assert "one JSON document" in fix and "by_agent" in fix and "drop `output`" in fix


async def test_a_reply_that_is_not_a_valid_row_of_the_output_kind_breaks_the_contract() -> None:
    box = container(reply='{"verdict": 3}')
    box.registry.register_payload(REVIEW, Review)
    wiring = box.wire(
        [{"name": "rev", "type": AGENT_STEP, "config": {"agent": "reviewer", "output": REVIEW}}],
        inputs=[TASK],
    )
    with pytest.raises(RunError, match="not a valid acme.v1.review") as e:
        await box.executor().run(wiring, {TASK: "go"})
    assert (e.value.kind, e.value.cause_kind) == ("run_failed", "contract")


def test_two_agent_steps_each_name_the_kind_of_their_reply() -> None:
    """Regression: the clash named the kind and ``provides``, not the key that fixes it."""
    box = container()
    step = {"type": AGENT_STEP, "config": {"agent": "reviewer"}}
    with pytest.raises(CompilationError, match="a kind has one provider") as e:
        box.wire([{"name": "one", **step}, {"name": "two", **step}], inputs=[TASK])
    (clash,) = e.value.problems
    assert (clash.path, clash.kind, clash.got) == (
        "components.two.config.response",
        "name_taken",
        AGENT_RESPONSE,
    )
    assert "one, two" in clash.message and "`response:`" in clash.fix
    second = {"type": AGENT_STEP, "config": {"agent": "reviewer", "response": "acme.v1.reply"}}
    wiring = box.wire([{"name": "one", **step}, {"name": "two", **second}], inputs=[TASK])
    assert wiring.providers == {AGENT_RESPONSE: "one", "acme.v1.reply": "two"}


async def test_the_task_as_sent_is_on_the_response_row_and_the_agent_start_event() -> None:
    """Regression: the rendered task was invisible after a run."""
    box = container()
    seen = RecordingObserver()
    config = {"agent": "reviewer", "task": "R: {t}", "inputs": {"t": TASK}}
    wiring = box.wire([{"name": "rev", "type": AGENT_STEP, "config": config}], inputs=[TASK])
    run = await box.executor([seen]).run(wiring, {TASK: "x"})
    assert run.rows(AGENT_RESPONSE)[0].task == "R: x"
    (start,) = [e for e in seen.events if e.name == "matrix.agent.start"]
    assert start.fields["task"] == "R: x"


async def test_the_mock_replies_by_task_then_by_agent_then_by_default() -> None:
    """Regression: the mock's ``responses`` keys were undocumented, and two agent steps on
    one mock could not each get their own reply without matching the whole task text."""
    mock = MockRuntime(
        MockRuntimeConfig(
            responses={"exact task": "by task"}, by_agent={"a": "by agent"}, default="default"
        )
    )
    a, b = AgentDefinition(name="a"), AgentDefinition(name="b")
    assert (await mock.run(a, "exact task")).content == "by task"
    assert (await mock.run(a, "other")).content == "by agent"
    assert (await mock.run(b, "other")).content == "default"
    assert (await MockRuntime().run(b, "t")).content == "mock:b:t"


async def test_several_rows_of_a_required_kind_are_rendered_as_a_list() -> None:
    box = container()
    wiring = box.wire(
        [{"name": "rev", "type": AGENT_STEP, "config": {"agent": "reviewer"}}], inputs=[TASK]
    )
    await box.executor().run(wiring, {TASK: ["a", "b"]})
    assert box.runtime("m").calls == [("reviewer", '[\n  "a",\n  "b"\n]')]
    with pytest.raises(RunError, match="handed over no rows"):
        await box.executor().run(wiring, {TASK: []})


async def test_a_fenced_json_block_is_accepted() -> None:
    step = AgentStep(
        AgentStepConfig(agent="a", output=REVIEW),
        agents={
            "a": BoundAgent(
                AgentDefinition(name="a"),
                MockRuntime(MockRuntimeConfig(default='ok\n```json\n{"verdict": "hold"}\n```')),
            )
        },
    )
    from matrix import Inputs, RunContext

    out = await step.run(Inputs({TASK: ["x"]}), RunContext("r", "s"))
    assert out[REVIEW] == ({"verdict": "hold"},)
    assert isinstance(out[AGENT_RESPONSE], AgentResponse)
    assert step.requires == {TASK} and step.provides == {AGENT_RESPONSE, REVIEW}


def test_an_unknown_agent_names_the_composed_ones() -> None:
    with pytest.raises(NotFoundError, match="Composed: a"):
        AgentStep(AgentStepConfig(agent="ghost"), agents={"a": object()})  # type: ignore[dict-item]


def test_several_inputs_need_a_template() -> None:
    with pytest.raises(ConfigError, match="no task template"):
        AgentStep(
            AgentStepConfig(agent="a", inputs={"x": DIFF, "y": DIFF}),
            agents={"a": object()},  # type: ignore[dict-item]
        )


async def test_bound_agents_report_to_observers() -> None:
    recorder = RecordingObserver()
    agent = BoundAgent(AgentDefinition(name="a"), MockRuntime(), observers=[recorder])
    await agent.run("hi")
    assert recorder.names() == ["matrix.agent.start", "matrix.agent.end"]
    assert recorder.events[-1].fields["status"] == "completed"


def test_a_runtime_refuses_a_definition_at_binding() -> None:
    class NoTools(MockRuntime):
        def check(self, definition: AgentDefinition) -> None:
            if definition.tools:
                raise ConfigError("no tools here")

    with pytest.raises(ConfigError, match="agents.a: no tools here"):
        compose(
            MatrixConfig.model_validate(
                {
                    "runtimes": {"m": {"type": "mock"}},
                    "agents": {"a": {"runtime": "m", "system_prompt": "x", "tools": ["Read"]}},
                }
            ),
            registry=_registry_with(NoTools),
        )


def _registry_with(cls: type) -> Any:
    from matrix import default_registry
    from matrix.domain.registry import Registry

    base = default_registry(discover=False)
    registry = Registry()
    for e in base.entries():
        build = (lambda c, _cls=cls: _cls(c)) if e.type_url.endswith("runtime.mock") else e.build
        registry.register(
            e.point, e.type_url, build, config=e.config, needs=e.needs, effects=e.effects
        )
    return registry


@pytest.mark.parametrize(
    ("reason", "kind", "retryable"),
    [
        ("rate_limited", "transient", True),
        ("unavailable", "transient", True),
        ("timeout", "transient", True),
        ("auth", "auth", False),
        ("incapable", "config", False),
        ("refused", "component", False),
        ("failed", "component", False),
    ],
)
def test_runtime_failures_carry_whose_problem_they_are(
    reason: Any, kind: str, retryable: bool
) -> None:
    error = AgentRuntimeError("x", reason=reason)
    assert (error.kind, error.retryable) == (kind, retryable)
    assert error.payload()["error"]["details"]["reason"] == reason


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (429, "rate_limited"),
        (401, "auth"),
        (403, "auth"),
        (529, "unavailable"),
        (504, "timeout"),
        (400, "failed"),
        (None, "failed"),
    ],
)
def test_sdk_http_statuses_map_to_reasons(status: Any, reason: str) -> None:
    assert _http_reason(status) == reason


def test_model_runtime_maps_hardline_failures() -> None:
    pytest.importorskip("hardline")
    from hardline import BackendError, SecretError

    from matrix.adapters._out.runtime.model import _reason

    assert _reason(BackendError("x", reason="rate_limit")) == "rate_limited"
    assert _reason(BackendError("x", reason="auth")) == "auth"
    assert _reason(BackendError("x", reason="bad_request")) == "incapable"
    assert _reason(SecretError("x")) == "auth"
    assert _reason(RuntimeError("x")) == "failed"


async def test_an_agent_called_by_a_component_reports_as_its_child() -> None:
    recorder = RecordingObserver()
    box = compose(
        MatrixConfig.model_validate(
            {
                "runtimes": {"m": {"type": "mock"}},
                "agents": {"a": {"runtime": "m", "system_prompt": "x"}},
            }
        )
    )
    agent = BoundAgent(box.agent("a").definition, box.runtime("m"), observers=[recorder])
    step = AgentStep(AgentStepConfig(agent="a"), agents={"a": agent})
    from matrix import Executor, wire

    run = await Executor([recorder]).run(wire({"s": step}, inputs=[TASK]), {TASK: "go"})
    started = next(e for e in recorder.events if e.name == "matrix.component.start")
    agent_start = next(e for e in recorder.events if e.name == "matrix.agent.start")
    assert (agent_start.run_id, agent_start.parent) == (run.run_id, started.span)
    assert agent_start.component == "s"


def test_a_wiring_carries_its_components_effects() -> None:
    box = container()
    wiring = box.wire(
        [{"name": "rev", "type": AGENT_STEP, "config": {"agent": "reviewer"}}], inputs=[TASK]
    )
    assert wiring.effects == {"rev": frozenset()}  # the mock runtime touches nothing
    assert wiring.declared_effects() == frozenset()


def test_the_container_reports_unbuildable_entries_with_the_wiring_problems() -> None:
    box = container()
    with pytest.raises(CompilationError) as e:
        box.wire(
            [
                {"name": "rev", "type": AGENT_STEP, "config": {"agent": "nobody"}},
                {"name": "rev", "type": AGENT_STEP, "config": {"agent": "reviewer"}},
                {"name": "typo", "type": AGENT_STEP, "config": {"agnet": "reviewer"}},
                {"name": "lost", "type": "acme.v1.unregistered"},
                {"name": "fine", "type": AGENT_STEP, "config": {"agent": "reviewer"}},
            ]
        )
    found = [(p.path, p.kind) for p in e.value.problems]
    assert found == [
        ("components.rev.config", "name_unknown"),  # no agent named 'nobody'
        ("components.rev", "name_taken"),
        ("components.typo.config.agent", "missing_key"),
        ("components.typo.config.agnet", "unknown_key"),
        ("components.lost.type", "name_unknown"),
        # ... and what is wrong with the wiring of the ones that could be built, in one report
        ("components.fine.requires", "unprovided"),
    ]
    assert "no agent named 'nobody'" in e.value.problems[0].message
    assert e.value.problems[4].got == "acme.v1.unregistered"


def test_the_container_wires_the_components_its_config_lists() -> None:
    box = compose(
        MatrixConfig.model_validate(
            {
                "runtimes": {"m": {"type": "mock"}},
                "agents": {"reviewer": {"runtime": "m", "system_prompt": "Review."}},
                "inputs": [DIFF],
                "components": [
                    {
                        "name": "rev",
                        "type": AGENT_STEP,
                        "config": {"agent": "reviewer", "inputs": {"diff": DIFF}},
                    }
                ],
            }
        )
    )
    wiring = box.wire()
    assert (wiring.inputs, wiring.order()) == ({DIFF}, ("rev",))


async def test_every_offline_runtime_keeps_the_port_promise() -> None:
    """A response always says why the session ended (agent_runtime.py: Guarantees)."""
    pytest.importorskip("hardline")
    box = compose(
        MatrixConfig.model_validate(
            {
                "models": {"models": {"q": {"backend": "mock", "model": "q", "family": "qwen"}}},
                "runtimes": {"mock": {"type": "mock"}, "model": {"type": "model"}},
            }
        )
    )
    for name in ("mock", "model"):
        definition = AgentDefinition(name="a", model="q" if name == "model" else None, tools=())
        response = await box.runtime(name).run(definition, "hi")
        assert isinstance(response, AgentResponse) and response.stop, name


def test_an_unknown_reason_is_a_failure_not_a_crash() -> None:
    error = AgentRuntimeError("quota exhausted", reason="quota")  # type: ignore[arg-type]
    assert (error.kind, error.exit_code, error.retryable) == ("component", 8, False)


@pytest.mark.parametrize(
    ("task", "inputs", "said"),
    [
        ("Review:\n{dif}", {"diff": DIFF}, "'dif'"),  # a typo: no input of that name
        ("Review the change", {"diff": DIFF}, "'diff'"),  # an input the task never uses
        ("Review:\n{}", {"diff": DIFF}, "positional"),
        ("Review:\n{diff", {"diff": DIFF}, "invalid"),
    ],
)
def test_a_task_template_is_checked_against_its_inputs_when_the_step_is_built(
    task: str, inputs: dict[str, str], said: str
) -> None:
    """Regression (mx-src F13): a typo composed, wired and planned, and failed only when the
    run reached the step, after its upstream work."""
    with pytest.raises(ConfigError, match=said) as e:
        AgentStep(AgentStepConfig(agent="a", inputs=inputs, task=task), agents={"a": object()})  # type: ignore[dict-item]
    assert e.value.fix


def test_a_template_may_index_an_input_and_escape_braces() -> None:
    step = AgentStep(
        AgentStepConfig(agent="a", inputs={"diff": DIFF}, task="{{literal}} {diff[0]}"),
        agents={"a": object()},  # type: ignore[dict-item]
    )
    assert step.render({DIFF: ("xyz",)}) == "{literal} x"
