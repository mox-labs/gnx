"""Conformance suites: what every implementation of one of matrix's ports must do, as tests a
package runs against its own implementation.

Mix a suite into a test class and say how to build what it checks::

    from matrix.testing import ConstructStoreConformance

    class TestMyStore(ConstructStoreConformance):
        def store(self):
            return MyStore(...)

pytest collects the suite's tests in that class. Each test is synchronous and awaits in an
event loop of its own, so no async plugin is needed; each failure is an ``AssertionError``
saying what was promised. A protocol check proves names alone; these check behaviour: what
the port promises when it is called, and that each optional member an implementation has
keeps its protocol. What one implementation adds beyond the port (the JSONL store's files) is
for that implementation's own tests.

There is a suite for every port but the model runtime's.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any, ClassVar, get_args

from matrix.domain.agent import AgentDefinition, AgentResponse, BoundAgent
from matrix.domain.component import Component, Context, wire
from matrix.domain.construct import Construct
from matrix.domain.errors import (
    AgentRuntimeError,
    ContractError,
    NotFoundError,
    RunError,
    RuntimeReason,
    StoreError,
)
from matrix.domain.executor import Executor, Run
from matrix.domain.observer import (
    RUN_END,
    RUN_START,
    Event,
    Observer,
    SupportsAround,
    SupportsClose,
)
from matrix.domain.optional import SupportsAclose, SupportsResolved, SupportsWarnings
from matrix.domain.ports._out.agent_runtime import AgentRuntime
from matrix.domain.ports._out.construct_store import (
    ConstructStore,
    RunWriter,
    SupportsDamaged,
    SupportsEvents,
    SupportsLocation,
)
from matrix.domain.ports._out.document_source import Document, DocumentSource
from matrix.domain.problems import Problem
from matrix.domain.session import SESSION

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Mapping

__all__ = [
    "AgentRuntimeConformance",
    "ComponentConformance",
    "ConstructStoreConformance",
    "ContextConformance",
    "DocumentSourceConformance",
    "ObserverConformance",
]

#: The kind of the rows a suite's own runs record.
ROW = "conformance.v1.row"


def _expect(holds: bool, promise: str) -> None:
    if not holds:
        raise AssertionError(promise)


def _raises(error: type[BaseException], promise: str, call: Callable[[], object]) -> None:
    try:
        call()
    except error:
        return
    raise AssertionError(promise)


class AgentRuntimeConformance:
    """What every agent runtime does. Define :meth:`runtime`; :meth:`definition` and
    :attr:`task` say what its session is, when the defaults do not suit it."""

    #: The task each session is given.
    task: ClassVar[str] = "Say hello."

    def runtime(self) -> AgentRuntime:
        """A new runtime, as its builder would make it."""
        raise NotImplementedError("define runtime() to return the agent runtime to check")

    def definition(self) -> AgentDefinition:
        """A definition the runtime plays."""
        return AgentDefinition(name="conformance", system_prompt="Answer in one word.")

    def test_it_keeps_the_agent_runtime_port(self) -> None:
        runtime = self.runtime()
        _expect(isinstance(runtime, AgentRuntime), "it has check and run")
        _expect(inspect.iscoroutinefunction(runtime.run), "run is a coroutine function")
        _expect(not inspect.iscoroutinefunction(runtime.check), "check is synchronous")

    def test_it_checks_a_definition_it_plays_without_refusing_it(self) -> None:
        self.runtime().check(self.definition())

    def test_a_session_ends_in_a_response_saying_why_or_in_a_typed_failure(self) -> None:
        runtime, definition = self.runtime(), self.definition()
        runtime.check(definition)
        try:
            response = asyncio.run(_closing(runtime, runtime.run(definition, self.task)))
        except AgentRuntimeError as error:
            _expect(
                error.reason in get_args(RuntimeReason),
                f"a failed session's reason is one of {get_args(RuntimeReason)}",
            )
            return
        _expect(isinstance(response, AgentResponse), "a session returns an AgentResponse")
        _expect(bool(response.stop), "a response says why its session ended (stop)")

    def test_each_optional_member_it_has_keeps_its_protocol(self) -> None:
        runtime = self.runtime()
        _optional_warnings_and_resolved(runtime)
        if isinstance(runtime, SupportsAclose):
            _expect(inspect.iscoroutinefunction(runtime.aclose), "aclose is a coroutine function")
            asyncio.run(runtime.aclose())


async def _closing(runtime: object, work: Awaitable[Any]) -> Any:
    """``work``, then what ``runtime`` opened for it closed in the same loop."""
    try:
        return await work
    finally:
        if isinstance(runtime, SupportsAclose):
            await runtime.aclose()


def _optional_warnings_and_resolved(built: object) -> None:
    if isinstance(built, SupportsWarnings):
        warnings = built.warnings
        _expect(
            isinstance(warnings, tuple) and all(isinstance(w, Problem) for w in warnings),
            "warnings is a tuple of Problem",
        )
    if isinstance(built, SupportsResolved):
        resolved = built.resolved
        _expect(isinstance(resolved, dict), "resolved is a mapping")
        json.dumps(resolved)


class _Rows:
    """Hands one row over while running and returns two more."""

    requires: frozenset[str] = frozenset()
    provides = frozenset({ROW})

    async def run(self, inputs: object, ctx: Context) -> dict[str, Any]:
        ctx.provide(ROW, {"n": 0})
        return {ROW: [{"n": 1}, {"n": 2}]}


class ConstructStoreConformance:
    """What every store does. Define :meth:`store`, returning a store with no run in it."""

    def store(self) -> ConstructStore:
        """A store with no run recorded in it yet."""
        raise NotImplementedError("define store() to return the store to check")

    @staticmethod
    def _recorded(store: ConstructStore, run_id: str) -> Run:
        return asyncio.run(Executor().run(wire({"rows": _Rows()}), store=store, run_id=run_id))

    def test_it_keeps_the_store_port(self) -> None:
        store = self.store()
        _expect(isinstance(store, ConstructStore), "it has open, load, describe and runs")
        run = Run(run_id="conformance-port", construct=Construct([ROW]))
        writer = store.open(run)
        _expect(isinstance(writer, RunWriter), "open gives a writer with append and finish")
        _expect(isinstance(writer.finish(run), str), "finish says where the record is")

    def test_a_run_reads_back_as_it_was_recorded(self) -> None:
        store = self.store()
        self._recorded(store, "conformance-read")
        construct = store.load("conformance-read")
        _expect(
            [dict(row) for row in construct.rows(ROW)] == [{"n": 0}, {"n": 1}, {"n": 2}],
            "every row comes back, in the order recorded",
        )
        entries = construct.provenance(ROW)
        _expect([e.component for e in entries] == ["rows"] * 3, "each row keeps its ledger entry")
        _expect(construct.torn == (), "no entry is torn in a finished run")
        _expect(store.describe("conformance-read")["status"] == "completed", "it keeps the status")
        _expect("conformance-read" in store.runs(), "it lists the run")

    def test_a_record_is_never_reopened(self) -> None:
        store = self.store()
        self._recorded(store, "conformance-once")
        run = Run(run_id="conformance-once", construct=Construct([ROW]))
        _raises(StoreError, "opening a run id that exists is a StoreError", lambda: store.open(run))

    def test_a_run_reads_as_running_while_it_is_recorded(self) -> None:
        store = self.store()
        run = Run(run_id="conformance-live", construct=Construct([ROW]))
        writer = store.open(run)
        try:
            writer.append(ROW, {"n": 0})
            _expect(store.describe("conformance-live")["status"] == "running", "it reads running")
        finally:
            writer.finish(run)

    def test_a_missing_run_is_not_found(self) -> None:
        store = self.store()
        missing = "conformance-missing"
        _raises(NotFoundError, "load of a missing run", lambda: store.load(missing))
        _raises(NotFoundError, "describe of a missing run", lambda: store.describe(missing))

    def test_runs_are_listed_oldest_first(self) -> None:
        store = self.store()
        self._recorded(store, "conformance-first")
        self._recorded(store, "conformance-second")
        listed = [run for run in store.runs() if run in {"conformance-first", "conformance-second"}]
        _expect(listed == ["conformance-first", "conformance-second"], "oldest first")

    def test_each_optional_member_it_has_keeps_its_protocol(self) -> None:
        store = self.store()
        if isinstance(store, SupportsLocation):
            _expect(isinstance(store.location("conformance-x"), str), "location is a string")
        if isinstance(store, SupportsDamaged):
            damaged = store.damaged()
            _expect(
                isinstance(damaged, list)
                and all(isinstance(w, str) and isinstance(y, str) for w, y in damaged),
                "damaged is a list of (where, why) strings",
            )
        run = Run(run_id="conformance-events", construct=Construct([ROW]))
        writer = store.open(run)
        try:
            if isinstance(writer, SupportsEvents):
                sink = writer.events()
                _expect(sink is None or isinstance(sink, Observer), "events gives an observer")
                if isinstance(sink, SupportsClose):
                    sink.close()
        finally:
            writer.finish(run)


class _Echo:
    """An agent runtime that answers with the task: a session for an observer to see."""

    def check(self, definition: AgentDefinition) -> None:
        return None

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        return AgentResponse(content=task, stop="completed", family="conformance")


class _Busy:
    """Uses every part of its context an observer sees, and runs an agent session."""

    requires: frozenset[str] = frozenset()
    provides = frozenset({ROW})

    async def run(self, inputs: object, ctx: Context) -> dict[str, Any]:
        ctx.emit("conformance.event", n=1)
        ctx.measure("conformance.count", 1, kind="counter")
        async with ctx.span("conformance.block", n=2):
            reply = await BoundAgent(AgentDefinition(name="echo"), _Echo()).run("hello")
        ctx.defer(lambda: None)
        return {ROW: {"reply": reply.content}}


class _Fails:
    requires: frozenset[str] = frozenset()
    provides = frozenset({"conformance.v1.never"})

    async def run(self, inputs: object, ctx: Context) -> None:
        raise ValueError("this component fails, so the observer sees a failure")


class _Heard(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def _failures_of(logger: str) -> Iterator[list[logging.LogRecord]]:
    """The records ``logger`` logs at ERROR inside the block."""
    heard = _Heard()
    target = logging.getLogger(logger)
    target.addHandler(heard)
    try:
        yield heard.records
    finally:
        target.removeHandler(heard)


class ObserverConformance:
    """What every observer does. Define :meth:`observer`."""

    def observer(self) -> Observer:
        """A new observer, as its builder would make it."""
        raise NotImplementedError("define observer() to return the observer to check")

    def test_it_keeps_the_observer_port(self) -> None:
        _expect(isinstance(self.observer(), Observer), "it has on_event")

    def test_it_sees_a_run_that_completes_and_one_that_fails_without_failing(self) -> None:
        observer = self.observer()
        with _failures_of("matrix.observer") as failures:
            run = asyncio.run(Executor([observer]).run(wire({"busy": _Busy()})))
            with suppress(RunError):  # it fails by design: the observer sees a failure
                asyncio.run(Executor([observer]).run(wire({"fails": _Fails()})))
        _expect(run.status == "completed", "the run it watched completed")
        _expect(
            not failures,
            "on_event, and around when it has one, take every event matrix sends: "
            + "; ".join(record.getMessage() for record in failures),
        )

    def test_each_optional_member_it_has_keeps_its_protocol(self) -> None:
        observer = self.observer()
        if isinstance(observer, SupportsAround):
            start = Event(RUN_START, span="conformance", run_id="conformance")
            manager = observer.around(start)
            if manager is not None:
                with manager as end:
                    if callable(end):
                        end(Event(RUN_END, span="conformance", run_id="conformance"))
        if isinstance(observer, SupportsClose):
            observer.close()


class DocumentSourceConformance:
    """What every document source does. Define :meth:`source`, :meth:`present` (a location
    holding at least one readable document) and :meth:`absent` (one with nothing there)."""

    #: The suffixes asked for when a location is read as a folder.
    suffixes: ClassVar[tuple[str, ...]] = (".yaml", ".md")

    def source(self) -> DocumentSource:
        raise NotImplementedError("define source() to return the document source to check")

    def present(self) -> Any:
        raise NotImplementedError("define present() to return a location holding a document")

    def absent(self) -> Any:
        raise NotImplementedError("define absent() to return a location with nothing there")

    def test_it_keeps_the_document_source_port(self) -> None:
        _expect(isinstance(self.source(), DocumentSource), "it has document, documents, beside")

    def test_nothing_there_is_none(self) -> None:
        source, absent = self.source(), self.absent()
        _expect(source.document(absent) is None, "document of nothing is None")
        _expect(source.documents(absent, self.suffixes) is None, "documents of nothing is None")

    def test_a_document_is_plain_data_and_where_it_came_from(self) -> None:
        found = self.source().document(self.present())
        _expect(isinstance(found, Document), "a location with a document gives a Document")
        assert isinstance(found, Document)
        _expect(isinstance(found.source, str) and bool(found.source), "it says where it is from")
        _expect(isinstance(found.stem, str), "its stem is a string")
        _expect(
            isinstance(found.data, dict) and found.problem is None,
            "a readable document is its mapping, with no problem",
        )

    def test_the_documents_at_a_location_are_each_a_document(self) -> None:
        found = self.source().documents(self.present(), self.suffixes)
        _expect(isinstance(found, tuple) and bool(found), "a location gives its documents")
        assert found is not None
        _expect(all(isinstance(d, Document) for d in found), "each is a Document")
        _expect(
            all((d.data is None) == isinstance(d.problem, Problem) for d in found),
            "each is its data, or None with the problem that kept it from being read",
        )

    def test_a_path_beside_a_document_is_a_string(self) -> None:
        source = self.source()
        found = source.document(self.present())
        assert found is not None
        _expect(isinstance(source.beside(found, "other.yaml"), str), "beside gives a string")


class ContextConformance:
    """What every component context does. Define :meth:`within`, which awaits a check with a
    context of the host's, as a component would hold it."""

    async def within(self, check: Callable[[Context], Awaitable[None]]) -> None:
        raise NotImplementedError("define within() to await check(ctx) with a context")

    def _checked(self, check: Callable[[Context], Awaitable[None]]) -> None:
        asyncio.run(self.within(check))

    def test_it_keeps_the_context_protocol(self) -> None:
        async def check(ctx: Context) -> None:
            _expect(isinstance(ctx, Context), "it has every member of Context")
            _expect(isinstance(ctx.run_id, str) and isinstance(ctx.component, str), "it names")
            _expect(ctx.deadline is None or isinstance(ctx.deadline, float), "a deadline")
            _expect(isinstance(ctx.stopping, asyncio.Event), "stopping is an asyncio.Event")

        self._checked(check)

    def test_it_takes_telemetry_and_releases_under_the_components_own_names(self) -> None:
        async def check(ctx: Context) -> None:
            ctx.emit("conformance.event", n=1)
            ctx.measure("conformance.count", 1, kind="counter", unit="{item}")
            async with ctx.span("conformance.block", n=2):
                pass
            ctx.defer(lambda: None)

        self._checked(check)

    def test_it_refuses_what_a_run_refuses(self) -> None:
        async def check(ctx: Context) -> None:
            _raises(ContractError, "an unnamespaced event", lambda: ctx.emit("unnamespaced"))
            _raises(ContractError, "an event under matrix.*", lambda: ctx.emit("matrix.mine.x"))
            _raises(ContractError, "a measure kind", lambda: ctx.measure("a.b", 1, kind="median"))
            _raises(ContractError, "a value that is no number", lambda: ctx.measure("a.b", "x"))  # type: ignore[arg-type]
            try:
                async with ctx.span("unnamespaced"):
                    pass
            except ContractError:
                return
            raise AssertionError("an unnamespaced span is a ContractError")

        self._checked(check)

    def test_a_scope_names_the_sessions_of_its_block(self) -> None:
        async def check(ctx: Context) -> None:
            async with ctx.attributed() as scope:
                pass
            _expect(scope.sessions() == (), "a block that made no session names none")

        self._checked(check)


class ComponentConformance:
    """What every component does. Define :meth:`component`, and :meth:`inputs` when it
    requires kinds: rows for each, which a run is given."""

    def component(self) -> Component:
        raise NotImplementedError("define component() to return the component to check")

    def inputs(self) -> Mapping[str, Any]:
        """Rows of each kind the component requires."""
        return {}

    def test_it_keeps_the_component_protocol_and_wires(self) -> None:
        component = self.component()
        _expect(isinstance(component, Component), "it has requires, provides and run")
        _expect(inspect.iscoroutinefunction(component.run), "run is a coroutine function")
        wire({"it": component}, inputs=component.requires)

    def test_it_runs_to_completion_handing_over_only_what_it_provides(self) -> None:
        component = self.component()
        wiring = wire({"it": component}, inputs=component.requires)
        try:
            run = asyncio.run(Executor().run(wiring, dict(self.inputs())))
        except RunError as error:
            raise AssertionError(f"a run of it with its inputs completes: {error}") from error
        _expect(
            {e.kind for e in run.construct.ledger if e.component == "it"}
            <= set(component.provides) | {SESSION},
            "it hands over rows only of the kinds it provides",
        )
