"""The session table: one row per agent session or model call, behind the rows it produced.

``matrix.v1.session`` is matrix's own table, like the ledger. A reply row keeps the reply and
points at its session row; the ledger entry of every row a component hands over names the
sessions it ran since its previous hand-over. A reader without matrix joins the three with
DuckDB, as ``docs/explanation/data-model.md`` shows.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from matrix import (
    AGENT_RESPONSE,
    LEDGER,
    MODEL_CALL,
    MODEL_STEP,
    PROMPT,
    SESSION,
    TASK,
    AgentDefinition,
    AgentResponse,
    AgentRuntimeError,
    BoundAgent,
    CompilationError,
    Executor,
    Limits,
    MatrixConfig,
    ModelRequest,
    RunContext,
    RunError,
    Session,
    call_sent,
    compose,
    default_registry,
    digest,
    record_session,
    wire,
)
from matrix.adapters._out.runtime.mock import MockAgentRuntime, MockAgentRuntimeConfig

DATA_MODEL = Path(__file__).parents[1] / "docs" / "explanation" / "data-model.md"
RATING = "acme.v1.rating"


def _box(**more: Any) -> Any:
    return compose(
        MatrixConfig.model_validate(
            {
                "model_runtimes": {"m": {"type": "mock", "default": '{"score": 4}'}},
                "agent_runtimes": {"a": {"type": "mock", "default": "reviewed"}},
                "agents": {"reviewer": {"runtime": "a", "system_prompt": "Review.", "model": "x"}},
                **more,
            }
        ),
        registry=default_registry(discover=False),
    )


def _model_step(**config: Any) -> list[dict[str, Any]]:
    return [{"name": "rate", "type": MODEL_STEP, "config": {"runtime": "m", **config}}]


async def test_a_model_call_is_a_session_row_its_reply_points_at_and_its_rows_name() -> None:
    box = _box()
    wiring = box.wire(_model_step(output=RATING, system="Be fair."), inputs=[PROMPT])
    run = await box.executor().run(wiring, {PROMPT: "rate it"})
    (session,) = run.rows(SESSION)
    assert isinstance(session, Session)
    assert (session.port, session.runtime) == ("model-runtime", "matrix.v1.model-runtime.mock")
    assert (session.family, session.backend, session.finish_reason) == ("mock", "mock", "stop")
    assert (session.attempts, session.retries, session.turns) == (1, 0, None)
    assert (session.status, session.reason, session.error) == ("completed", None, None)
    assert session.sent == digest(
        {"prompt": "rate it", "system": "Be fair.", "model": None, "output": None}
    )
    assert session.sent == call_sent(ModelRequest(prompt="rate it", system="Be fair."))
    assert (session.trace_id, session.span_id) == (None, None)  # no OTel span was current
    (reply,) = run.rows(MODEL_CALL)
    assert (reply.session, reply.text, reply.finish_reason) == (0, '{"score": 4}', "stop")
    # What the session row holds is not repeated on the reply.
    assert (reply.usage, reply.attempts, reply.latency_ms, reply.backend) == (None,) * 4
    behind = {e.kind: e.sessions for e in run.construct.ledger if e.kind != SESSION}
    assert behind == {PROMPT: (), MODEL_CALL: (0,), RATING: (0,)}


async def test_an_agent_session_is_a_session_row_its_response_points_at() -> None:
    box = _box()
    wiring = box.wire(
        [{"name": "review", "type": "matrix.v1.agent-step", "config": {"agent": "reviewer"}}],
        inputs=[TASK],
    )
    run = await box.executor().run(wiring, {TASK: "look"})
    (session,) = run.rows(SESSION)
    assert (session.port, session.runtime, session.agent) == (
        "agent-runtime",
        "matrix.v1.agent-runtime.mock",
        "reviewer",
    )
    assert (session.stop, session.turns, session.family) == ("completed", 1, "mock")
    definition = box.agent("reviewer").definition
    assert session.sent == digest(
        {"definition": definition.model_dump(mode="json"), "task": "look"}
    )
    (response,) = run.rows(AGENT_RESPONSE)
    assert (response.session, response.content, response.task) == (0, "reviewed", "look")
    assert (response.family, response.stop) == ("mock", "completed")  # what consumers act on
    assert (response.num_turns, response.model) == (None, None)  # in the session row
    (entry,) = run.construct.provenance(AGENT_RESPONSE)
    assert entry.sessions == (0,)


async def test_the_session_row_records_the_trace_and_span_the_host_is_in() -> None:
    """Ids come from the OpenTelemetry context current when the session ends; matrix mints
    none."""
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    box = _box()
    wiring = box.wire(_model_step(), inputs=[PROMPT])
    tracer = sdk_trace.TracerProvider().get_tracer("host")
    with tracer.start_as_current_span("host") as span:
        run = await box.executor().run(wiring, {PROMPT: "rate it"})
        context = span.get_span_context()
    (session,) = run.rows(SESSION)
    assert session.trace_id == f"{context.trace_id:032x}"
    assert session.span_id == f"{context.span_id:016x}"


async def test_outside_a_run_nothing_is_recorded_and_the_response_keeps_its_facts() -> None:
    runtime = MockAgentRuntime(MockAgentRuntimeConfig(default="ok"))
    response = await BoundAgent(AgentDefinition(name="a"), runtime).run("hello")
    assert (response.session, response.content, response.num_turns) == (None, "ok", 1)


class _TwoAgents:
    """Runs one agent, hands a row over, runs two more, and returns one row."""

    requires = frozenset({TASK})
    provides = frozenset({"acme.v1.first", "acme.v1.rest"})

    def __init__(self, agent: BoundAgent) -> None:
        self._agent = agent

    async def run(self, inputs: Any, ctx: Any) -> dict[str, Any]:
        first = await self._agent.run("one")
        ctx.provide("acme.v1.first", first.content)
        second, third = await self._agent.run("two"), await self._agent.run("three")
        return {"acme.v1.rest": [second.content, third.content]}


async def test_a_row_names_the_sessions_its_component_ran_since_its_last_hand_over() -> None:
    runtime = MockAgentRuntime(MockAgentRuntimeConfig(default="ok"))
    agent = BoundAgent(AgentDefinition(name="a"), runtime)
    run = await Executor().run(wire({"two": _TwoAgents(agent)}, inputs=[TASK]), {TASK: "go"})
    assert len(run.rows(SESSION)) == 3
    assert [e.sessions for e in run.construct.provenance("acme.v1.first")] == [(0,)]
    assert [e.sessions for e in run.construct.provenance("acme.v1.rest")] == [(1, 2), (1, 2)]
    assert {e.component for e in run.construct.provenance(SESSION)} == {"two"}


async def test_a_component_can_ask_which_sessions_its_next_row_will_name() -> None:
    """``ctx.sessions()`` is what the ledger entry of the component's next row will name, so
    a component can put the sessions behind a row in the row itself."""
    runtime = MockAgentRuntime(MockAgentRuntimeConfig(default="ok"))
    agent = BoundAgent(AgentDefinition(name="a"), runtime)
    seen: list[tuple[int, ...]] = []

    class _Asks:
        requires = frozenset({TASK})
        provides = frozenset({"acme.v1.out"})

        async def run(self, inputs: Any, ctx: Any) -> None:
            await agent.run("one")
            await agent.run("two")
            seen.append(ctx.sessions())
            ctx.provide("acme.v1.out", "first")
            seen.append(ctx.sessions())
            await agent.run("three")
            seen.append(ctx.sessions())
            ctx.provide("acme.v1.out", "second")

    run = await Executor().run(wire({"asks": _Asks()}, inputs=[TASK]), {TASK: "go"})
    assert seen == [(0, 1), (), (2,)]
    assert [e.sessions for e in run.construct.provenance("acme.v1.out")] == [(0, 1), (2,)]
    assert RunContext("r", "alone").sessions() == ()  # outside a run, none


OUT = "acme.v1.out"


def _fake(label: str) -> int | None:
    """Record a session as a runtime would, labelled by its agent."""
    return record_session(Session(port="agent-runtime", agent=label, sent=digest(label)))


def _labels(run: Any, rows: tuple[int, ...]) -> list[str]:
    return [run.rows(SESSION)[row].agent for row in rows]


async def test_concurrent_tasks_under_one_component_each_see_exactly_their_own_sessions() -> None:
    """Two trials in flight in one component, their sessions interleaved: the scope each
    opens (``ctx.attributed()``) names exactly the sessions made in its own task, and so does
    the ledger entry of the row each hands over with them. Neither claims the other's."""
    seen: dict[str, tuple[int, ...]] = {}
    left: list[tuple[int, ...]] = []

    class _Trials:
        requires = frozenset({TASK})
        provides = frozenset({OUT})

        async def run(self, inputs: Any, ctx: RunContext) -> None:
            turn = {"a": asyncio.Event(), "b": asyncio.Event()}

            async def trial(me: str, other: str) -> None:
                async with ctx.attributed() as attribution:
                    for step in range(3):
                        await turn[me].wait()
                        turn[me].clear()
                        _fake(f"{me}{step}")
                        turn[other].set()
                    seen[me] = attribution.sessions()
                    ctx.provide(OUT, me, sessions=attribution.sessions())

            turn["a"].set()
            async with asyncio.TaskGroup() as group:
                group.create_task(trial("a", "b"))
                group.create_task(trial("b", "a"))
            left.append(ctx.sessions())

    run = await Executor().run(wire({"trials": _Trials()}, inputs=[TASK]), {TASK: "go"})
    assert _labels(run, tuple(range(6))) == ["a0", "b0", "a1", "b1", "a2", "b2"]
    assert seen == {"a": (0, 2, 4), "b": (1, 3, 5)}
    named = {run.rows(OUT)[e.row]: e.sessions for e in run.construct.provenance(OUT)}
    assert named == seen
    assert left == [()]  # every session was named by the row of its own trial


async def test_a_scope_holds_its_task_s_sessions_and_its_child_tasks_and_nests() -> None:
    """A scope names the sessions made in its task and in every task started inside it, an
    inner scope's among them. It names none from a task started before it opened, and none
    made after it closed, so what it names is fixed once the block is left."""
    scopes: dict[str, Any] = {}

    class _Nests:
        requires = frozenset({TASK})
        provides = frozenset({OUT})

        async def run(self, inputs: Any, ctx: RunContext) -> None:
            go, late = asyncio.Event(), asyncio.Event()

            async def sibling() -> None:
                await go.wait()
                _fake("sibling")

            async def grandchild() -> None:
                _fake("grandchild")

            async def child() -> None:
                _fake("child")
                await asyncio.create_task(grandchild())

            async def lingers() -> None:
                await late.wait()
                _fake("late")

            before = asyncio.create_task(sibling())
            async with ctx.attributed() as outer:
                _fake("outer")
                async with ctx.attributed() as inner:
                    _fake("inner")
                    await asyncio.create_task(child())
                go.set()
                await before
                lingering = asyncio.create_task(lingers())
            late.set()
            await lingering
            scopes.update(outer=outer, inner=inner)
            ctx.provide(OUT, "all")

    run = await Executor().run(wire({"nests": _Nests()}, inputs=[TASK]), {TASK: "go"})
    outer, inner = scopes["outer"].sessions(), scopes["inner"].sessions()
    assert _labels(run, outer) == ["outer", "inner", "child", "grandchild"]
    assert _labels(run, inner) == ["inner", "child", "grandchild"]
    assert _labels(run, (4, 5)) == ["sibling", "late"]
    # Every one ran in the component: its next plain hand-over names them all.
    assert [e.sessions for e in run.construct.provenance(OUT)] == [(0, 1, 2, 3, 4, 5)]


async def test_a_row_handed_over_with_its_sessions_leaves_the_rest_to_the_next_row() -> None:
    """``provide(kind, row, sessions=...)`` names exactly those sessions. The ones it did
    not name are still the component's unnamed sessions (``ctx.sessions()``), and its next
    plain hand-over names them, as it would have without a scope."""
    seen: list[tuple[int, ...]] = []

    class _Mixed:
        requires = frozenset({TASK})
        provides = frozenset({OUT})

        async def run(self, inputs: Any, ctx: RunContext) -> None:
            _fake("loose")
            async with ctx.attributed() as trial:
                _fake("trial")
            seen.append(ctx.sessions())
            ctx.provide(OUT, "trial", sessions=trial.sessions())
            seen.append(ctx.sessions())
            ctx.provide(OUT, "rest")
            ctx.provide(OUT, "none", sessions=())

    run = await Executor().run(wire({"mixed": _Mixed()}, inputs=[TASK]), {TASK: "go"})
    assert seen == [(0, 1), (0,)]
    assert [e.sessions for e in run.construct.provenance(OUT)] == [(1,), (0,), ()]


async def test_a_row_may_name_only_sessions_its_own_component_ran() -> None:
    """The ledger is the record of what produced a row: naming a session another component
    ran, or one that does not exist, is refused as a contract error, and nothing is
    recorded for that row."""

    class _Runs:
        requires = frozenset({TASK})
        provides = frozenset({"acme.v1.first"})

        async def run(self, inputs: Any, ctx: RunContext) -> Any:
            _fake("theirs")
            return {"acme.v1.first": "done"}

    class _Claims:
        requires = frozenset({"acme.v1.first"})
        provides = frozenset({OUT})

        def __init__(self, claimed: tuple[int, ...]) -> None:
            self._claimed = claimed

        async def run(self, inputs: Any, ctx: RunContext) -> None:
            _fake("mine")
            ctx.provide(OUT, "claimed", sessions=self._claimed)

    for claimed in [(0,), (1, 7)]:
        wiring = wire({"runs": _Runs(), "claims": _Claims(claimed)}, inputs=[TASK])
        with pytest.raises(RunError) as e:
            await Executor().run(wiring, {TASK: "go"})
        failure = e.value.run.failures[0]
        assert (failure.component, failure.kind) == ("claims", "contract")
        assert e.value.fix is not None and "ctx.attributed()" in e.value.fix
        assert e.value.run.rows(OUT) == ()


async def test_a_scope_does_not_take_the_sessions_of_a_run_started_inside_it() -> None:
    """A run started inside a scope has its own session table; its rows are not this run's,
    so the scope names none of them."""

    class _Inner:
        requires = frozenset({TASK})
        provides = frozenset({OUT})

        async def run(self, inputs: Any, ctx: RunContext) -> None:
            _fake("inner")

    seen: list[tuple[int, ...]] = []

    class _Outer:
        requires = frozenset({TASK})
        provides = frozenset({"acme.v1.first"})

        async def run(self, inputs: Any, ctx: RunContext) -> None:
            async with ctx.attributed() as scope:
                await Executor().run(wire({"inner": _Inner()}, inputs=[TASK]), {TASK: "x"})
                _fake("outer")
            seen.append(scope.sessions())

    run = await Executor().run(wire({"outer": _Outer()}, inputs=[TASK]), {TASK: "go"})
    assert seen == [(0,)] and _labels(run, (0,)) == ["outer"]


async def test_a_trial_cancelled_mid_session_names_its_own_sessions_the_cancelled_one_too() -> None:
    """A trial cancelled while its agent session is in flight, with other trials' scopes
    open beside it: the cancelled session's row is recorded while the cancellation unwinds,
    inside the trial's scope, so that scope names it, and no other scope does."""
    started, after = asyncio.Event(), asyncio.Event()

    class _Holds:
        def check(self, _: Any) -> None:
            return None

        async def run(self, definition: Any, task: str) -> Any:
            started.set()
            await asyncio.Event().wait()

    agent = BoundAgent(AgentDefinition(name="held"), _Holds())
    scopes: dict[str, Any] = {}

    class _Trials:
        requires = frozenset({TASK})
        provides = frozenset({OUT})

        async def run(self, inputs: Any, ctx: RunContext) -> None:
            async def held() -> None:
                async with ctx.attributed() as scopes["held"]:
                    _fake("held0")
                    await agent.run("wait")

            async def other(me: str, last: asyncio.Event) -> None:
                async with ctx.attributed() as scopes[me]:
                    _fake(f"{me}0")
                    await started.wait()
                    _fake(f"{me}1")
                    await last.wait()  # still open while the held trial is cancelled
                    _fake(f"{me}2")

            trials = [asyncio.create_task(other(me, after)) for me in ("a", "b")]
            holding = asyncio.create_task(held())
            await started.wait()
            holding.cancel()
            with pytest.raises(asyncio.CancelledError):
                await holding
            after.set()
            await asyncio.gather(*trials)

    run = await Executor().run(wire({"trials": _Trials()}, inputs=[TASK]), {TASK: "go"})
    held = scopes["held"].sessions()
    assert _labels(run, held) == ["held0", "held"]
    assert run.rows(SESSION)[held[-1]].status == "cancelled"
    assert _labels(run, scopes["a"].sessions()) == ["a0", "a1", "a2"]
    assert _labels(run, scopes["b"].sessions()) == ["b0", "b1", "b2"]


async def test_a_scope_left_by_an_exception_is_final_and_names_only_its_own() -> None:
    """A trial that raises inside its scope leaves it as surely as one that returns: what it
    names then is its own sessions, and stays so whatever the component records next."""
    seen: list[tuple[int, ...]] = []

    class _Raises:
        requires = frozenset({TASK})
        provides = frozenset({OUT})

        async def run(self, inputs: Any, ctx: RunContext) -> None:
            _fake("before")
            try:
                async with ctx.attributed() as trial:
                    _fake("mine")
                    raise RuntimeError("the trial broke")
            except RuntimeError:
                seen.append(trial.sessions())
            _fake("after")
            async with ctx.attributed():
                _fake("next")
            seen.append(trial.sessions())

    run = await Executor().run(wire({"raises": _Raises()}, inputs=[TASK]), {TASK: "go"})
    assert seen == [(1,), (1,)] and _labels(run, (1,)) == ["mine"]


async def test_outside_a_run_a_scope_names_nothing() -> None:
    async with RunContext("r", "alone").attributed() as scope:
        assert _fake("unrecorded") is None
    assert scope.sessions() == ()


async def test_a_call_whose_reply_is_refused_is_still_on_the_record() -> None:
    """The step fails and provides nothing, but the call happened: its session row says how
    it ended, for whoever reads why the run failed."""
    box = _box(model_runtimes={"m": {"type": "mock", "default": ""}})
    wiring = box.wire(_model_step(), inputs=[PROMPT])
    with pytest.raises(RunError) as e:
        await box.executor().run(wiring, {PROMPT: "rate it"})
    run = e.value.run
    (session,) = run.rows(SESSION)
    assert session.finish_reason == "stop" and run.rows(MODEL_CALL) == ()
    assert run.construct.provenance(SESSION)[0].component == "rate"


class _Rating(BaseModel):
    score: int


async def test_a_failed_model_call_is_still_a_session_row_with_what_it_cost() -> None:
    """A call that failed (here: its repair was rate limited, with no retries left) spent
    tokens and made attempts. Its row says it failed, why, and what each attempt cost."""
    hardline = pytest.importorskip("hardline")
    from hardline.adapters._out.mock import MockBackend

    from matrix import ModelStep, ModelStepConfig
    from matrix.adapters._out.runtime.model import HardlineModelRuntime

    row = {
        "backend": "mock",
        "model": "q-1",
        "family": "q",
        "retries": 0,
        "options": {"script": ["not a rating", {"error": "rate_limit"}]},
    }
    runtime = HardlineModelRuntime(
        hardline.build_runtime(
            config={"default": "q", "models": {"q": row}}, backends={"mock": MockBackend()}
        )
    )
    step = ModelStep(
        ModelStepConfig(runtime="m", output=RATING),
        model_runtimes={"m": runtime},
        schemas={RATING: _Rating},
    )
    with pytest.raises(RunError) as e:
        await Executor().run(wire({"rate": step}, inputs=[PROMPT]), {PROMPT: "rate it"})
    run = e.value.run
    (session,) = run.rows(SESSION)
    assert (session.port, session.status, session.reason) == (
        "model-runtime",
        "failed",
        "rate_limited",
    )
    assert session.error is not None and "rate_limit" in session.error
    assert session.attempt_log is not None
    assert [(a.name, a.reason) for a in session.attempt_log] == [("q", None), ("q", "rate_limit")]
    assert session.usage is not None and session.usage == session.attempt_log[0].usage
    assert session.usage.input_tokens > 0
    assert session.sent == digest(
        {"prompt": "rate it", "system": None, "model": None, "output": _Rating.model_json_schema()}
    )
    # Linkage is unchanged: the row is the failed component's, and no entry names it.
    assert run.construct.provenance(SESSION)[0].component == "rate"
    assert all(entry.sessions == () for entry in run.construct.ledger)


class _Failing:
    """An agent runtime whose sessions fail, as ``error`` says."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def check(self, definition: AgentDefinition) -> None:
        pass

    async def run(self, definition: AgentDefinition, task: str) -> Any:
        raise self._error


@pytest.mark.parametrize(
    ("error", "reason", "facts"),
    [
        (
            AgentRuntimeError(
                "agent 'a': session failed (HTTP 529)",
                reason="unavailable",
                response=AgentResponse(
                    session_id="s-1", tokens_input=90, tokens_output=3, cost_usd=0.02, num_turns=2
                ),
            ),
            "unavailable",
            ("s-1", 90, 0.02, 2),
        ),
        (RuntimeError("the runtime broke its port"), None, (None, None, None, None)),
    ],
)
async def test_a_failed_agent_session_is_still_a_session_row(
    error: Exception, reason: str | None, facts: tuple[Any, ...]
) -> None:
    """A session that failed is on the record with what its runtime knew of it: its ids and
    usage when the runtime's error carried them, and always that it failed and why."""
    agent = BoundAgent(AgentDefinition(name="a"), _Failing(error), runtime_url="acme.v1.rt")

    class _Once:
        requires = frozenset({TASK})
        provides = frozenset({"acme.v1.out"})

        async def run(self, inputs: Any, ctx: Any) -> Any:
            await agent.run("go")

    with pytest.raises(RunError) as e:
        await Executor().run(wire({"once": _Once()}, inputs=[TASK]), {TASK: "go"})
    run = e.value.run
    (session,) = run.rows(SESSION)
    assert (session.port, session.agent, session.runtime) == ("agent-runtime", "a", "acme.v1.rt")
    assert (session.status, session.reason, session.stop) == ("failed", reason, None)
    assert session.error is not None and str(error) in session.error
    usage = session.usage.input_tokens if session.usage is not None else None
    assert (session.session_id, usage, session.cost_usd, session.turns) == facts
    assert session.sent == digest(
        {
            "definition": AgentDefinition(name="a").model_dump(mode="json"),
            "task": "go",
        }
    )
    assert run.construct.provenance(SESSION)[0].component == "once"


class _Hangs:
    """An agent runtime and a model runtime whose session or call never returns."""

    def check(self, _: Any) -> None:
        return None

    async def run(self, definition: Any, task: str) -> Any:
        await asyncio.Event().wait()

    async def call(self, request: Any) -> Any:
        await asyncio.Event().wait()


async def test_a_cancelled_agent_session_is_a_session_row_that_says_so() -> None:
    """A session cancelled mid-flight (its component overran its deadline) was asked for and
    may have spent tokens: it is a row, ``cancelled``, with the ids it ran under and a digest
    of what was sent."""
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    from matrix.composition.telemetry import current_trace

    agent = BoundAgent(AgentDefinition(name="a"), _Hangs(), runtime_url="acme.v1.rt")

    class _Asks:
        requires = frozenset({TASK})
        provides = frozenset({"acme.v1.out"})

        async def run(self, inputs: Any, ctx: Any) -> Any:
            await agent.run("go")

    limits = Limits(deadline_s=0.05, grace_s=0, cleanup_s=1)
    tracer = sdk_trace.TracerProvider().get_tracer("host")
    with tracer.start_as_current_span("host"), pytest.raises(RunError) as e:
        await Executor(trace=current_trace).run(
            wire({"asks": _Asks()}, inputs=[TASK]), {TASK: "go"}, limits=limits
        )
    (session,) = e.value.run.rows(SESSION)
    assert (session.port, session.agent, session.runtime) == ("agent-runtime", "a", "acme.v1.rt")
    assert (session.status, session.reason, session.error) == ("cancelled", "cancelled", None)
    assert session.sent == digest(
        {
            "definition": AgentDefinition(name="a").model_dump(mode="json"),
            "task": "go",
        }
    )
    assert session.trace_id is not None and session.span_id is not None
    assert e.value.run.construct.provenance(SESSION)[0].component == "asks"


async def test_a_cancelled_model_call_is_a_session_row_that_says_so() -> None:
    """A model step's call cancelled with its run is a row, ``cancelled``, with a digest of
    what was sent."""
    from matrix import ModelStep, ModelStepConfig, RunCancelled

    step = ModelStep(ModelStepConfig(runtime="r"), model_runtimes={"r": _Hangs()})
    started = asyncio.Event()

    class _Watch:
        def on_event(self, event: Any) -> None:
            if event.name == "matrix.model.start":
                started.set()

    run = asyncio.ensure_future(
        Executor([_Watch()]).run(
            wire({"rate": step}, inputs=[PROMPT]), {PROMPT: "rate it"}, limits=Limits(grace_s=0)
        )
    )
    await started.wait()
    run.cancel()
    with pytest.raises(RunCancelled) as e:
        await run
    (session,) = e.value.run.rows(SESSION)
    assert (session.port, session.status, session.reason) == (
        "model-runtime",
        "cancelled",
        "cancelled",
    )
    assert session.sent == call_sent(ModelRequest(prompt="rate it"))


class _Down:
    """A model runtime whose calls fail, classified."""

    def check(self, _: Any) -> None:
        return None

    async def call(self, request: Any) -> Any:
        from matrix import ModelRuntimeError

        raise ModelRuntimeError("the provider is down", reason="unavailable")


async def test_a_failed_or_cancelled_agent_session_keeps_the_task_as_sent() -> None:
    """Owner ruling 2026-10-22: what a failed step was asked is in the record in full, not
    only as a digest. A completed session's reply row keeps it (``AgentResponse.task``), so
    its session row does not repeat it."""

    def asks(agent: BoundAgent, task: str) -> Any:
        class _Asks:
            requires = frozenset({TASK})
            provides = frozenset({"acme.v1.out"})

            async def run(self, inputs: Any, ctx: Any) -> Any:
                await agent.run(task)

        return _Asks()

    failing = BoundAgent(AgentDefinition(name="a"), _Failing(AgentRuntimeError("down")))
    with pytest.raises(RunError) as e:
        await Executor().run(wire({"a": asks(failing, "look at x")}, inputs=[TASK]), {TASK: "-"})
    (session,) = e.value.run.rows(SESSION)
    assert (session.status, session.task, session.prompt) == ("failed", "look at x", None)

    hangs = BoundAgent(AgentDefinition(name="a"), _Hangs())
    limits = Limits(deadline_s=0.05, grace_s=0, cleanup_s=1)
    with pytest.raises(RunError) as e:
        await Executor().run(
            wire({"a": asks(hangs, "look at y")}, inputs=[TASK]), {TASK: "-"}, limits=limits
        )
    (session,) = e.value.run.rows(SESSION)
    assert (session.status, session.task) == ("cancelled", "look at y")

    box = _box()
    wiring = box.wire(
        [{"name": "review", "type": "matrix.v1.agent-step", "config": {"agent": "reviewer"}}],
        inputs=[TASK],
    )
    run = await box.executor().run(wiring, {TASK: "look"})
    (session,) = run.rows(SESSION)
    assert (session.status, session.task) == ("completed", None)
    assert run.rows(AGENT_RESPONSE)[0].task == "look"


async def test_a_failed_or_cancelled_model_call_keeps_the_prompt_as_sent() -> None:
    """Owner ruling 2026-10-22, as for an agent session: the prompt as sent, on the row of a
    call that failed or was cancelled; a completed call's reply row keeps it
    (``ModelCall.prompt``)."""
    from matrix import ModelStep, ModelStepConfig, RunCancelled

    step = ModelStep(ModelStepConfig(runtime="r"), model_runtimes={"r": _Down()})
    with pytest.raises(RunError) as e:
        await Executor().run(wire({"rate": step}, inputs=[PROMPT]), {PROMPT: "rate it"})
    (session,) = e.value.run.rows(SESSION)
    assert (session.status, session.reason) == ("failed", "unavailable")
    assert (session.prompt, session.task) == ("rate it", None)

    step = ModelStep(ModelStepConfig(runtime="r"), model_runtimes={"r": _Hangs()})
    started = asyncio.Event()

    class _Watch:
        def on_event(self, event: Any) -> None:
            if event.name == "matrix.model.start":
                started.set()

    running = asyncio.ensure_future(
        Executor([_Watch()]).run(
            wire({"rate": step}, inputs=[PROMPT]), {PROMPT: "rate that"}, limits=Limits(grace_s=0)
        )
    )
    await started.wait()
    running.cancel()
    with pytest.raises(RunCancelled) as cancelled:
        await running
    (session,) = cancelled.value.run.rows(SESSION)
    assert (session.status, session.prompt) == ("cancelled", "rate that")

    box = _box()
    run = await box.executor().run(box.wire(_model_step(), inputs=[PROMPT]), {PROMPT: "rate"})
    (session,) = run.rows(SESSION)
    assert (session.status, session.prompt) == ("completed", None)
    assert run.rows(MODEL_CALL)[0].prompt == "rate"


def _recorded(root: Path, **step: Any) -> Any:
    """A box that runs one model step and records it in a JSONL store under ``root``."""
    return _box(
        store={"type": "jsonl", "root": str(root)},
        components=_model_step(**step),
        inputs=[PROMPT],
    )


async def test_a_reused_reply_keeps_its_pointer_and_names_no_session_of_its_own(
    tmp_path: Path,
) -> None:
    box = _recorded(tmp_path)
    first = await box.run({PROMPT: "rate it"}, run_id="first")
    again = await box.run(run_id="again", reuse_from="first")
    assert (len(first.rows(SESSION)), again.rows(SESSION)) == (1, ())
    (reply,) = again.rows(MODEL_CALL)
    assert reply.session == 0  # a row of `first`'s session table: again.reused_from
    assert again.reused_from == "first"
    assert [e.sessions for e in again.construct.provenance(MODEL_CALL)] == [()]


def test_the_session_table_is_matrix_s_own() -> None:
    class Claims:
        requires = frozenset({SESSION})
        provides = frozenset({"acme.v1.out"})

        async def run(self, inputs: Any, ctx: Any) -> None:
            return None

    with pytest.raises(CompilationError, match="session"):
        wire({"c": Claims()})


async def test_the_session_table_round_trips_through_the_jsonl_store(tmp_path: Path) -> None:
    box = _recorded(tmp_path)
    await box.run({PROMPT: "rate it"}, run_id="r1")
    construct = box.store.load("r1")
    (session,) = construct.rows(SESSION)
    assert session["runtime"] == "matrix.v1.model-runtime.mock"
    entry = next(e for e in construct.rows(LEDGER) if e.kind == MODEL_CALL)
    assert entry.sessions == (0,)
    assert (tmp_path / "r1" / f"{SESSION}.jsonl").is_file()


def _documented_sql() -> str:
    """The join ``data-model.md`` shows a reader without matrix."""
    text = DATA_MODEL.read_text()
    found = re.search(r"<!-- session-join -->\s*```sql\n(.*?)```", text, re.S)
    assert found, "data-model.md shows the board-to-session join"
    return found.group(1)


async def test_the_documented_duckdb_join_reads_a_reply_s_sessions_without_matrix(
    tmp_path: Path,
) -> None:
    duckdb = pytest.importorskip("duckdb")

    class Rating(BaseModel):
        score: int

    box = _recorded(tmp_path, output=RATING)
    box.registry.register_payload(RATING, Rating)
    await box.run({PROMPT: "rate it"}, run_id="r1")
    sql = _documented_sql().replace("RUN", str(tmp_path / "r1")).replace("KIND", RATING)
    rows = duckdb.sql(sql).fetchall()
    assert rows == [(0, '{"score": 4}', "matrix.v1.model-runtime.mock", "mock", "stop", 1)]


async def test_every_run_has_a_session_table_as_it_has_a_ledger() -> None:
    """A reader finds the table in every run, with no rows when no session ran."""
    run = await Executor().run(wire({}, inputs=[PROMPT]), {PROMPT: "x"})
    assert SESSION in run.construct.kinds() and run.rows(SESSION) == ()
