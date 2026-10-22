"""The session table: one row per agent session or model call, behind the rows it produced.

``matrix.v1.session`` is matrix's own table, like the ledger. A reply row keeps the reply and
points at its session row; the ledger entry of every row a component hands over names the
sessions it ran since its previous hand-over. A reader without matrix joins the three with
DuckDB, as ``docs/explanation/data-model.md`` shows (W3.5).
"""

from __future__ import annotations

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
    BoundAgent,
    CompilationError,
    Executor,
    MatrixConfig,
    RunError,
    Session,
    compose,
    default_registry,
    digest,
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
    assert session.sent == digest(
        {"prompt": "rate it", "system": "Be fair.", "model": None, "output": None}
    )
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
