"""Agents as flow members, and agent failures classified by whose problem they are."""

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
    ConfigError,
    Flow,
    MatrixConfig,
    Member,
    NotFoundError,
    RecordingObserver,
    RunError,
    compose,
)
from matrix.adapters._out.runtime.claude_sdk import _http_reason
from matrix.adapters._out.runtime.mock import MockRuntime, MockRuntimeConfig

DIFF = "acme.v1.diff"
REVIEW = "acme.v1.review"


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


async def test_an_agent_runs_as_a_flow_member_with_its_inputs_rendered() -> None:
    box = container()
    box.registry.register_payload(REVIEW, Review)
    flow = Flow(
        "review",
        (
            Member(
                "rev",
                AGENT_STEP,
                {"diff": "change", "result": "verdicts"},
                config={
                    "agent": "reviewer",
                    "inputs": {"diff": DIFF},
                    "task": "Review:\n{diff}",
                    "output": REVIEW,
                },
            ),
        ),
        inputs={"change": DIFF},
    )
    run = await box.executor().run(box.compile(flow), {"change": "+ added line"})
    assert run.outputs("verdicts") == ({"verdict": "ship"},)
    runtime = box.runtime("m")
    assert runtime.calls == [("reviewer", "Review:\n+ added line")]


async def test_a_reply_without_json_breaks_the_declared_output() -> None:
    box = container(reply="looks fine to me")
    flow = Flow(
        "r",
        (
            Member(
                "rev",
                AGENT_STEP,
                {"task": "t", "result": "out"},
                config={"agent": "reviewer", "output": REVIEW},
            ),
        ),
        inputs={"t": "matrix.v1.task"},
    )
    with pytest.raises(RunError) as e:
        await box.executor().run(box.compile(flow), {"t": "go"})
    assert e.value.kind == "contract" and "holds no JSON" in str(e.value)


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

    out = await step.run(Inputs({"task": "x"}, RunContext("r", "s")))
    assert out["result"] == {"verdict": "hold"}
    assert isinstance(out["response"], AgentResponse)
    assert step.provides == {"response": AGENT_RESPONSE, "result": REVIEW}


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
    assert recorder.names() == ["agent.start", "agent.end"]
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
        ("refused", "unknown", False),
        ("failed", "unknown", False),
    ],
)
def test_runtime_failures_carry_whose_problem_they_are(
    reason: Any, kind: str, retryable: bool
) -> None:
    error = AgentRuntimeError("x", reason=reason)
    assert (error.kind, error.retryable) == (kind, retryable)
    assert error.payload()["error"]["reason"] == reason


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


async def test_an_agent_called_by_a_member_reports_as_its_child() -> None:
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
    from matrix import Executor, compile_flow

    flow = Flow("f", (Member("s", step, {"task": "t"}),), inputs={"t": "matrix.v1.task"})
    run = await Executor([recorder]).run(compile_flow(flow), {"t": "go"})
    member_start = next(e for e in recorder.events if e.name == "member.start")
    agent_start = next(e for e in recorder.events if e.name == "agent.start")
    assert (agent_start.run_id, agent_start.parent) == (run.run_id, member_start.span)


def test_a_compiled_flow_carries_its_members_effects() -> None:
    box = container()
    flow = Flow(
        "e",
        (Member("rev", AGENT_STEP, {"task": "t"}, config={"agent": "reviewer"}),),
        inputs={"t": "matrix.v1.task"},
    )
    compiled = box.compile(flow)
    assert compiled.effects == {"rev": frozenset()}  # the mock runtime touches nothing
    assert compiled.declared_effects() == frozenset()


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
    assert (error.kind, error.exit_code, error.retryable) == ("unknown", 1, False)
