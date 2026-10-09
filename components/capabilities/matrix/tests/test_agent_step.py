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
        ("refused", "failed", False),
        ("failed", "failed", False),
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
