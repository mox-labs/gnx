"""recon's component face (step M6, ruling R4): three matrix flow members over the two
layers' application functions, ``plan`` (``recon.v1.span``) → ``collect`` (``recon.v1.act``) →
``changes`` (``recon.v1.change``), wired by the kinds they exchange and run by matrix's
executor, each act handed over as it seals."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from matrix import Executor, MemoryConstructStore, Registry, RunCancelled, wire
from pydantic import BaseModel, ConfigDict

from recon._matrix import (
    ACT,
    CHANGE,
    CHANGES,
    COLLECT,
    PLAN,
    SPAN,
    ChangesMember,
    CollectConfig,
    CollectMember,
    MemberConfig,
    PlanMember,
    register,
)
from recon.adapters._out.system_clock import SystemClock
from recon.composition import load_plugins, root
from recon.core.domain.collector import CollectorType
from recon.core.domain.documents import Act, Change, Span
from tests.recon_fakes import VirtualClock

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from recon.composition import Recon
    from recon.core.domain.capture import CaptureLog
    from recon.core.domain.models import CollectorEntry, SourceEntry

SPANS = "acme.v1.collector.spans"
QUIET = "acme.v1.collector.quiet"


class _Asked(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    since: str
    until: str


class _Spans:
    """A source that answers each request with the window it was asked for."""

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        asked = _Asked.model_validate(entry.settings)
        yield {"id": asked.since, "since": asked.since, "until": asked.until}


class _Quiet:
    """A stream's source that sends nothing until it is closed."""

    def __init__(self) -> None:
        self.closed = threading.Event()

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
        after: Any = None,
    ) -> Iterator[dict[str, Any]]:
        self.closed.wait()
        yield from ()

    def close(self) -> None:
        self.closed.set()


def _extensions(registry: Any) -> None:
    spans = CollectorType(create=lambda ctx: _Spans(), effects=frozenset(), settings=_Asked)
    quiet = CollectorType(create=lambda ctx: _Quiet(), effects=frozenset(), unbounded=True)
    registry.register("recon.collector", SPANS, lambda: spans)
    registry.register("recon.collector", QUIET, lambda: quiet)


RANGED = {"name": "hours", "type": SPANS, "since": "{range.start}", "until": "{range.end}",
          "range": {"step": 3600, "settle": 0}, "changes": {"key": ["id"]}}  # fmt: skip


def _mission(project: Path, *collectors: dict[str, Any]) -> None:
    (project / ".recon" / "m").mkdir(parents=True, exist_ok=True)
    config = {"collectors": list(collectors)}
    (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(config))


def _session(project: Path, clock: Any) -> Recon:
    return root({}, project=project, clock=clock, plugins=load_plugins([_extensions]))


def _members(session: Recon) -> dict[str, Any]:
    return {
        "plan": PlanMember(session, MemberConfig(mission="m")),
        "collect": CollectMember(session, CollectConfig(mission="m"), record_kinds=()),
        "changes": ChangesMember(session, MemberConfig(mission="m")),
    }


def _run(session: Recon, store: MemoryConstructStore, run_id: str) -> Any:
    wiring = wire(_members(session))
    asyncio.run(Executor().run(wiring, store=store, run_id=run_id))
    return store.load(run_id)


def test_two_runs_through_the_executor_go_on_from_what_the_first_committed(
    tmp_path: Path,
) -> None:
    """The gate of M6: wired with matrix's ``wire`` and run twice through the executor with
    ``MemoryConstructStore``, the second run's range begins where the first's ended."""
    _mission(tmp_path, RANGED)
    clock = VirtualClock(datetime(2026, 11, 6, 12, 0, tzinfo=UTC))
    store = MemoryConstructStore()

    first = _run(_session(tmp_path, clock), store, "first")
    [span] = [Span.model_validate(row) for row in first.rows(SPAN)]
    assert (span.kind, span.start, span.end) == ("continuing", "2026-11-06T11:00:00Z",
                                                 "2026-11-06T12:00:00Z")  # fmt: skip
    [act] = [Act.model_validate(row) for row in first.rows(ACT)]
    [table] = act.tables
    assert table.records == 1 and table.range is not None
    assert (table.range.start, table.range.end) == (span.start, span.end), "the act committed it"
    changes = [Change.model_validate(row) for row in first.rows(CHANGE)]
    assert [(c.op, c.output, c.archive) for c in changes] == [("insert", "hours", act.archive)]

    clock.advance(3600)
    second = _run(_session(tmp_path, clock), store, "second")
    [later] = [Span.model_validate(row) for row in second.rows(SPAN)]
    assert later.start == span.end, "the second run's range continues the first's"
    assert later.end == "2026-11-06T13:00:00Z"
    [again] = [Act.model_validate(row) for row in second.rows(ACT)]
    assert again.archive != act.archive and again.tables[0].range is not None
    assert again.tables[0].range.start == span.end


def test_a_run_killed_mid_stream_keeps_every_act_it_sealed(tmp_path: Path) -> None:
    """ix's property, for recon: an act's row is handed over as the act seals, so cancelling
    the run while a stream's window is open keeps the snapshot act sealed before it, and the
    window, asked to stop, seals too."""
    _mission(tmp_path, {"name": "now", "type": "cli", "run": "printf '{\"a\": 1}\\n'"},
             {"name": "feed", "type": QUIET})  # fmt: skip
    session = _session(tmp_path, SystemClock())
    store = MemoryConstructStore()
    members = _members(session)
    del members["changes"]

    async def run_then_kill() -> None:
        task = asyncio.ensure_future(Executor().run(wire(members), store=store, run_id="killed"))
        while True:
            await asyncio.sleep(0.01)
            running = store.load("killed")
            if running.rows(ACT):
                break
        await asyncio.sleep(0.2)  # the stream's window is open now, its source quiet
        task.cancel()
        with pytest.raises(RunCancelled):
            await task

    asyncio.run(run_then_kill())
    acts = [Act.model_validate(row) for row in store.load("killed").rows(ACT)]
    assert [act.stream for act in acts] == [None, "feed"]
    assert acts[0].tables[0].records == 1 and acts[0].tables[0].status == "ok"
    assert store.describe("killed")["status"] == "cancelled"
    history = session.observation.status("m").missions[0].history
    assert history is not None and [a.id for a in history] == [acts[0].archive]


def test_a_harness_that_schedules_recon_itself_gives_the_spans(tmp_path: Path) -> None:
    """A kind with no provider is a run input: a harness leaves ``plan`` out and gives
    ``recon.v1.span`` rows of its own (the owner's "something using recon, could write an
    adapter, to produce the actual collection, and then schedule it")."""
    _mission(tmp_path, RANGED)
    session = _session(tmp_path, VirtualClock(datetime(2026, 11, 6, 12, 0, tzinfo=UTC)))
    members = _members(session)
    del members["plan"]
    asked = Span.model_validate({
        "mission": "m", "output": "hours", "kind": "fixed", "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-01T01:00:00Z", "from": "override", "request": "",
        "steps": [{"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T01:00:00Z"}],
    })  # fmt: skip
    store = MemoryConstructStore()
    wiring = wire(members, inputs=[SPAN])
    asyncio.run(Executor().run(wiring, {SPAN: [asked.document()]}, store=store, run_id="given"))
    [act] = [Act.model_validate(row) for row in store.load("given").rows(ACT)]
    assert act.tables[0].range is not None and act.tables[0].range.start == asked.start


def test_the_members_and_their_payloads_are_registered_at_matrix_s_points(
    tmp_path: Path,
) -> None:
    """Built through the registry, from config, as a flow builds them; a mission its config
    cannot plan is refused when the member is built (matrix's ConfigError)."""
    from matrix import ConfigError

    registry = Registry()
    register(registry)
    assert {PLAN, COLLECT, CHANGES} <= {e.type_url for e in registry.entries("component")}
    assert {SPAN, ACT, CHANGE} <= set(registry.payload_schemas())
    _mission(tmp_path, {"name": "now", "type": "cli", "run": "true"})
    built = registry.create("component", PLAN, {"mission": "m", "root": str(tmp_path)},
                            needs={"cwd": None})  # fmt: skip
    assert built.provides == (SPAN,)
    with pytest.raises(ConfigError):
        registry.create("component", PLAN, {"mission": "none", "root": str(tmp_path)},
                        needs={"cwd": None})  # fmt: skip
