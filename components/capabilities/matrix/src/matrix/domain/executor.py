"""The executor: run wired components with inputs, and record what happened as a :class:`Run`.

**Scheduling is by readiness.** A component starts as soon as the providers of every kind it
requires have finished; components that do not depend on each other run side by side, up to
``Limits.concurrency`` when one is set. Nothing else orders the work.

**The executor is the Construct's only writer.** A component gets
:class:`~matrix.domain.component.Inputs`, the rows of the kinds it requires, each its own
copy. Rows of the kinds it provides reach the record two ways: handed over while it runs
(``ctx.provide``), and returned when it ends. Either way a row must be of a kind the
component declared, and must validate against the kind's schema when one is registered;
otherwise the component broke its contract. A row is recorded, and durable in the run's
store, before the call that handed it over returns.

**Every component ends in one state**: ``completed``, ``failed``, ``cancelled`` or ``skipped``
(``Run.components``). **Every run ends in one of three**: ``completed``, ``failed`` (some
component failed, or the record could not be kept; :class:`RunError` carries the partial Run)
or ``cancelled`` (the caller, Ctrl-C or SIGTERM cancelled it; :class:`RunCancelled` carries
the partial Run). Nothing recorded before the end is lost in either case.

**One stop path.** A component is stopped the same way whatever the cause (its deadline
passed, another component failed, the run was cancelled):

1. *stop request*: ``ctx.stopping`` is set. A component that returns now keeps what it
   returns;
2. *grace*: the executor waits ``grace_s`` for that return;
3. *cancel*: the component's task is cancelled, and its own ``finally`` blocks run. A
   component that swallows the cancellation is **abandoned** after ``cleanup_s`` and
   reported; matrix cannot stop in-process code that will not stop;
4. *deferred release*: when the run ends, however it ends, everything components handed to
   ``ctx.defer`` is called, last registered first, each bounded by ``cleanup_s``. A release
   that raises or overruns is recorded in ``Run.releases``; it never stops the others.

What this cannot bound: a component that blocks the event loop (synchronous work) stalls
every deadline, sibling and release until it yields.

Everything above is narrated as events on the run's channel (:mod:`matrix.domain.observer`),
which components feed too, through ``ctx.emit``, ``ctx.measure`` and ``ctx.span``.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from matrix.domain.component import Component, Inputs, Release, RunContext, Wiring
from matrix.domain.construct import INPUT, Construct
from matrix.domain.errors import (
    ComponentError,
    ConfigError,
    ContractError,
    MatrixError,
    RunCancelled,
    RunError,
    StoreError,
)
from matrix.domain.observer import (
    COMPONENT_END,
    COMPONENT_LATE,
    COMPONENT_START,
    COMPONENT_STOPPING,
    CURRENT_SPAN,
    MEASURE,
    MEASURE_KINDS,
    RELEASE_END,
    RELEASE_START,
    ROW_APPEND,
    RUN_END,
    RUN_START,
    Channel,
    Event,
    EventPolicy,
    Observer,
    SpanScope,
    check_event_name,
)
from matrix.domain.signals import cancel_on_signals

if TYPE_CHECKING:
    from matrix.domain.ports._out.construct_store import ConstructStore, RunWriter

logger = logging.getLogger("matrix.executor")

RUN_SCHEMA = "matrix.v1.run"

#: How a component ended. Open: a consumer must tolerate a state it does not know.
ComponentStatus = Literal["completed", "failed", "cancelled", "skipped"]
#: Why a component was asked to stop.
StopReason = Literal["deadline", "run_failed", "cancelled"]

#: The name a failure carries when the record could not be finished, rather than a component.
STORE = "$store"


@dataclass(frozen=True)
class ComponentLimits:
    """Bounds for one component, overriding the run's. ``None`` inherits the run's value."""

    deadline_s: float | None = None
    grace_s: float | None = None
    cleanup_s: float | None = None

    def __post_init__(self) -> None:
        _check_bounds(self.deadline_s, self.grace_s, self.cleanup_s)


@dataclass(frozen=True)
class Limits:
    """Bounds on one run.

    ``concurrency`` caps how many components run at once; ``None`` (the default) leaves it to
    the wiring. ``deadline_s`` applies to each component separately, from the moment it
    starts; ``components`` overrides it (and ``grace_s``, ``cleanup_s``) for the names it
    lists. ``grace_s`` is how long a component has to return after a stop request before it
    is cancelled; ``cleanup_s`` how long a cancelled component, or one deferred release, has
    to finish before it is abandoned.

    ``on_failure``: ``fast`` (the default) stops every running component and skips the rest
    as soon as one fails; ``isolate`` skips only the components that depend on the failed one
    and lets the others finish. The run ends ``failed`` either way.
    """

    concurrency: int | None = None
    deadline_s: float | None = None
    grace_s: float = 5.0
    cleanup_s: float = 30.0
    on_failure: Literal["fast", "isolate"] = "fast"
    components: Mapping[str, ComponentLimits] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.concurrency is not None and self.concurrency < 1:
            raise ConfigError(f"concurrency must be >= 1, got {self.concurrency}")
        if self.on_failure not in ("fast", "isolate"):
            raise ConfigError(f"on_failure must be 'fast' or 'isolate', got {self.on_failure!r}")
        _check_bounds(self.deadline_s, self.grace_s, self.cleanup_s)

    def of(self, name: str) -> tuple[float | None, float, float]:
        """``(deadline_s, grace_s, cleanup_s)`` for the component ``name``."""
        own = self.components.get(name, ComponentLimits())
        return (
            own.deadline_s if own.deadline_s is not None else self.deadline_s,
            own.grace_s if own.grace_s is not None else self.grace_s,
            own.cleanup_s if own.cleanup_s is not None else self.cleanup_s,
        )


def _check_bounds(deadline_s: float | None, grace_s: float | None, cleanup_s: float | None) -> None:
    if deadline_s is not None and deadline_s <= 0:
        raise ConfigError(f"deadline_s must be > 0, got {deadline_s}")
    if grace_s is not None and grace_s < 0:
        raise ConfigError(f"grace_s must be >= 0, got {grace_s}")
    if cleanup_s is not None and cleanup_s <= 0:
        raise ConfigError(f"cleanup_s must be > 0, got {cleanup_s}")


@dataclass
class Run:
    """Observed state of one execution. Separate from the wiring, which a run never changes."""

    run_id: str
    construct: Construct
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended: datetime | None = None
    #: ``{component, kind, message}`` for each component that failed
    failures: list[dict[str, Any]] = field(default_factory=list)
    #: name -> ``{status, reason?, duration_ms?, abandoned?}``: how each component ended.
    #: ``abandoned`` marks one that ignored cancellation and may still be running.
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: ``{component, status, error}`` per deferred release, in the order they were called.
    #: ``status`` is ``released``, ``failed`` (it raised) or ``abandoned`` (it overran).
    releases: list[dict[str, Any]] = field(default_factory=list)
    #: Events emitted during the run that no observer received (telemetry is lossy).
    events_dropped: int = 0

    def rows(self, kind: str) -> tuple[Any, ...]:
        """The rows of ``kind`` recorded in this run."""
        return self.construct.rows(kind)

    def document(self) -> dict[str, Any]:
        """The run as a JSON-ready document (``matrix.v1.run``), without its tables."""
        return {
            "schema": RUN_SCHEMA,
            "run_id": self.run_id,
            "status": self.status,
            "started": self.started.isoformat(),
            "ended": self.ended.isoformat() if self.ended else None,
            "components": self.components,
            "failures": self.failures,
            "releases": self.releases,
        }


class Executor:
    """Runs wired components. Stateless between runs; safe to share.

    ``events`` bounds the telemetry a run may hold for its observers. ``signals=False``
    leaves SIGTERM and SIGINT alone; the caller then owns turning them into cancellation
    (see :mod:`matrix.domain.signals`).
    """

    def __init__(
        self,
        observers: Sequence[Observer] = (),
        *,
        events: EventPolicy | None = None,
        signals: bool = True,
    ) -> None:
        self._observers = tuple(observers)
        self._events = events or EventPolicy()
        self._signals = signals

    async def run(
        self,
        wiring: Wiring,
        inputs: Mapping[str, Any] | None = None,
        *,
        limits: Limits | None = None,
        store: ConstructStore | None = None,
        run_id: str | None = None,
    ) -> Run:
        """Run ``wiring`` to a terminal state and return the Run.

        ``inputs`` maps each run-input kind to one row, or to a list or tuple of rows. With a
        ``store``, the run's record is opened there before anything starts and every row is
        durable in it as it lands.

        Raises :class:`RunError` when a component failed or the record could not be kept, and
        :class:`RunCancelled` when the calling task was cancelled; both carry the partial
        Run, and both are raised only after every component stopped and every deferred
        release was called. Raises StoreError, before anything runs, when the store cannot
        open the run (its id is taken).
        """
        limits = limits or Limits()
        supplied = {kind: _rows(value) for kind, value in (inputs or {}).items()}
        missing = sorted(wiring.inputs - set(supplied))
        unknown = sorted(set(supplied) - wiring.inputs)
        if missing or unknown:
            raise ConfigError(
                (f"missing inputs {missing}" if missing else "")
                + ("; " if missing and unknown else "")
                + (f"unknown inputs {unknown}" if unknown else "")
                + f". Run inputs: {sorted(wiring.inputs) or '(none)'}",
                fix="supply exactly the kinds declared as run inputs",
            )
        strangers = sorted(set(limits.components) - set(wiring.components))
        if strangers:
            raise ConfigError(
                f"limits name components {strangers} that are not in the run. "
                f"Components: {sorted(wiring.components)}",
                fix="key Limits.components by component name",
            )
        for kind, rows in supplied.items():
            for row in rows:
                _validate(wiring, kind, row, where=f"input {kind!r}")
        run = Run(run_id=run_id or uuid.uuid4().hex, construct=Construct(wiring.kinds))
        writer = _open(store, run) if store is not None else None
        channel = Channel(self._observers, self._events, run.run_id)
        session = _Session(wiring, run, limits, channel, writer)
        # The run lives in its own task, shielded from the caller's cancellation: cancelling
        # the caller asks the run to stop, and the run answers once it has cleaned up.
        main = asyncio.ensure_future(session.execute(supplied))
        interrupted = False
        with cancel_on_signals(self._signals):
            while not main.done():
                try:
                    await asyncio.shield(main)
                except asyncio.CancelledError:
                    if main.cancelled():
                        raise  # the loop is tearing every task down; nothing left to await
                    interrupted = True
                    session.cancel()
        main.result()
        if interrupted:
            raise RunCancelled(run)
        if session.first is not None:
            name, cause = session.first
            raise RunError(
                f"run {run.run_id} failed at {name!r}: {cause}",
                run=run,
                component=name,
                cause_kind=cause.kind,
            ) from cause
        return run


def _open(store: ConstructStore, run: Run) -> RunWriter:
    try:
        writer = store.open(run)
    except MatrixError:
        raise
    except Exception as e:
        raise StoreError(
            f"the store could not open run {run.run_id!r}: {type(e).__name__}: {e}"
        ) from e
    run.construct.write_through(writer.append)
    return writer


@dataclass
class _Step:
    """One component's passage through a run."""

    name: str
    component: Component
    provides: frozenset[str]
    deadline_s: float | None
    grace_s: float
    cleanup_s: float
    context: RunContext
    hooks: _Hooks
    #: The executor's own stop flag; ``context.stopping`` mirrors it for the component.
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    reason: StopReason | None = None
    running: bool = False
    #: The store failure met while recording one of this component's rows, if any.
    broke: StoreError | None = None
    span: str = field(default_factory=lambda: uuid.uuid4().hex)


class _Hooks:
    """What one component's :class:`RunContext` may ask of the run."""

    def __init__(self, session: _Session, name: str, cleanup_s: float) -> None:
        self._session = session
        self._name = name
        self._cleanup_s = cleanup_s
        self._warned = False
        self.closed = False

    def _late(self, call: str) -> bool:
        """Whether the component has ended; the first late call is reported, the rest quiet."""
        if self.closed and not self._warned:
            self._warned = True
            logger.warning(
                "component %r used its context (%s) after it ended; ignored", self._name, call
            )
            self._session.emit(COMPONENT_LATE, uuid.uuid4().hex, {"call": call}, self._name)
        return self.closed

    def _parent(self) -> str:
        scope = CURRENT_SPAN.get()
        if scope is not None and scope.run_id == self._session.run_id:
            return scope.span
        return self._session.run_id

    def provide(self, kind: str, row: Any) -> None:
        if not self._late("provide"):
            self._session.provide(self._name, kind, row)

    def defer(self, release: Release) -> None:
        if not callable(release):
            raise TypeError(f"defer() takes a callable, got {type(release).__name__}")
        if not self._late("defer"):
            self._session.releases.append((self._name, release, self._cleanup_s))

    def emit(self, name: str, fields: dict[str, Any]) -> None:
        check_event_name(name)
        if not self._late("emit"):
            self._session.emit(name, uuid.uuid4().hex, fields, self._name, self._parent())

    def measure(self, name: str, value: float, unit: str, kind: str, attrs: dict[str, Any]) -> None:
        check_event_name(name, what="measure")
        if kind not in MEASURE_KINDS:
            raise ContractError(
                f"measure {name!r}: kind {kind!r} is not one of {', '.join(MEASURE_KINDS)}"
            )
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ContractError(
                f"measure {name!r}: value must be a number, got {type(value).__name__}"
            )
        if not self._late("measure"):
            fields = {"name": name, "value": value, "unit": unit, "kind": kind, "attrs": attrs}
            self._session.emit(MEASURE, uuid.uuid4().hex, fields, self._name, self._parent())

    @asynccontextmanager
    async def span(self, name: str, fields: dict[str, Any]) -> AsyncIterator[None]:
        check_event_name(name, what="span")
        scope = CURRENT_SPAN.get()
        if self._late("span") or scope is None:
            yield
            return
        span = uuid.uuid4().hex
        self._session.emit(f"{name}.start", span, fields, self._name, scope.span)
        token = CURRENT_SPAN.set(replace(scope, span=span))
        started = time.monotonic()
        status = "ok"
        try:
            yield
        except BaseException:
            status = "error"
            raise
        finally:
            CURRENT_SPAN.reset(token)
            ended = {"status": status, "duration_ms": int((time.monotonic() - started) * 1000)}
            self._session.emit(f"{name}.end", span, ended, self._name, scope.span)


class _Session:
    """One run in flight: what is ready, the stop path, and the releases owed at the end."""

    def __init__(
        self,
        wiring: Wiring,
        run: Run,
        limits: Limits,
        channel: Channel,
        writer: RunWriter | None,
    ) -> None:
        self._wiring = wiring
        self._run = run
        self._limits = limits
        self._channel = channel
        self._writer = writer
        self.run_id = run.run_id
        #: Every component that has started, running or ended.
        self._steps: dict[str, _Step] = {}
        self._drivers: set[asyncio.Task[None]] = set()
        self._wake = asyncio.Event()
        #: Why nothing more may start (a skip reason), once something stopped the run.
        self._halted: str | None = None
        self._cancelled = False
        self.releases: list[tuple[str, Release, float]] = []
        #: The first failure, which :class:`RunError` reports as the cause.
        self.first: tuple[str, MatrixError] | None = None

    def cancel(self) -> None:
        """Ask the run to stop: every running component is stop-requested, the rest skipped."""
        self._cancelled = True
        self._halt("cancelled", "cancelled")
        self._wake.set()

    async def execute(self, supplied: Mapping[str, tuple[Any, ...]]) -> None:
        run = self._run
        started = time.monotonic()
        self._channel.start()
        self.emit(RUN_START, run.run_id, {"components": list(self._wiring.components)}, parent=None)
        try:
            try:
                for kind, rows in supplied.items():
                    for row in rows:
                        self._record(INPUT, kind, row)
            except StoreError as e:
                self._failed(INPUT, e)
            await self._schedule()
        finally:
            # Settled before the releases run: a cancellation that arrives while they do
            # still reaches the caller, but it did not stop any component.
            cancelled = self._cancelled
            await self._release()
            run.ended = datetime.now(UTC)
            run.status = "cancelled" if cancelled else "failed" if run.failures else "completed"
            self._finish()
            self.emit(
                RUN_END,
                run.run_id,
                {
                    "status": run.status,
                    "rows": len(run.construct),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
                parent=None,
            )
            run.events_dropped = await self._channel.close()
            if run.events_dropped:
                logger.warning(
                    "run %s: %d events were dropped before any observer received them",
                    run.run_id,
                    run.events_dropped,
                )

    def _finish(self) -> None:
        """Record how the run ended. A record that cannot be finished is a failed run."""
        if self._writer is None:
            return
        run = self._run
        try:
            try:
                self._writer.finish(run)
            except MatrixError:
                raise
            except Exception as e:
                raise StoreError(f"the store could not finish the run: {e}") from e
        except MatrixError as e:
            self._failed(STORE, e)
            if run.status == "completed":
                run.status = "failed"

    async def _schedule(self) -> None:
        self._advance()
        while self._drivers:
            await self._wake.wait()
            self._wake.clear()
            for driver in [d for d in self._drivers if d.done()]:
                self._drivers.discard(driver)
                driver.result()  # a driver records every ending; if it raised, matrix is wrong
            self._advance()

    def _advance(self) -> None:
        """Start whatever is ready, and settle whatever can no longer run."""
        ended = self._run.components
        cap = self._limits.concurrency
        changed = True
        while changed:
            changed = False
            for name in self._wiring.components:
                if name in ended or name in self._steps:
                    continue
                waits = self._wiring.depends[name]
                broken = [d for d in waits if d in ended and ended[d]["status"] != "completed"]
                if self._halted is not None:
                    self._skip(name, self._halted)
                elif broken:
                    self._skip(name, f"upstream: {broken[0]}")
                elif all(d in ended for d in waits) and (cap is None or len(self._drivers) < cap):
                    self._start(name)
                else:
                    continue  # still waiting, on a provider or on room to run
                changed = True

    def _start(self, name: str) -> None:
        deadline_s, grace_s, cleanup_s = self._limits.of(name)
        component = self._wiring.components[name]
        hooks = _Hooks(self, name, cleanup_s)
        step = _Step(
            name,
            component,
            frozenset(component.provides),
            deadline_s,
            grace_s,
            cleanup_s,
            RunContext(self.run_id, name, hooks=hooks),
            hooks,
        )
        self._steps[name] = step
        self._drivers.add(asyncio.ensure_future(self._drive(step)))

    async def _drive(self, step: _Step) -> None:
        """Run one component to a terminal state. Never raises: every ending is recorded."""
        name = step.name
        if self._halted is not None:  # the run was stopped before this one got to start
            self._skip(name, self._halted)
            self._wake.set()
            return
        self.emit(COMPONENT_START, step.span, {}, name)
        started = time.monotonic()
        abandoned = False
        error: MatrixError | None
        try:
            status, error, abandoned = await self._attempt(step, started)
        except MatrixError as e:
            status, error = "failed", e
        finally:
            step.running = False
            step.hooks.closed = True
        if step.broke is not None:
            status, error = "failed", step.broke
        record: dict[str, Any] = {
            "status": status,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        if step.reason is not None:
            record["reason"] = step.reason
        if abandoned:
            record["abandoned"] = True
            logger.warning(
                "component %r ignored cancellation for %ss and was abandoned; it may still be "
                "running",
                name,
                step.cleanup_s,
            )
        self._run.components[name] = record
        fields = dict(record)
        if error is not None:
            fields["kind"] = error.kind
        self.emit(COMPONENT_END, step.span, fields, name)
        if error is not None:
            self._failed(name, error)
        self._wake.set()

    def _failed(self, name: str, error: MatrixError) -> None:
        self._run.failures.append({"component": name, "kind": error.kind, "message": str(error)})
        self.first = self.first or (name, error)
        if isinstance(error, StoreError):
            self._halt("store failed", "run_failed")  # no record, no run: whatever the policy
        elif self._limits.on_failure == "fast":
            self._halt("policy: fast", "run_failed")

    async def _attempt(
        self, step: _Step, started: float
    ) -> tuple[ComponentStatus, MatrixError | None, bool]:
        """Inputs in, the stop path around the component, and how it ended."""
        name = step.name
        inputs = Inputs(
            {
                kind: _private(self._run.construct.rows(kind), kind)
                for kind in step.component.requires
            }
        )
        if step.deadline_s is not None:
            step.context.deadline = started + step.deadline_s
        scope = SpanScope(self.run_id, step.span, name, self._channel)
        task = asyncio.ensure_future(_call(step.component, inputs, step.context, scope))
        stopped = asyncio.ensure_future(step.stop.wait())
        step.running = True
        sent = False
        try:
            await asyncio.wait(
                {task, stopped}, timeout=step.deadline_s, return_when=asyncio.FIRST_COMPLETED
            )
            if not task.done():
                if step.reason is None:
                    self._request_stop(step, "deadline")
                await asyncio.wait({task}, timeout=step.grace_s)
            if not task.done():
                sent = task.cancel()
                await asyncio.wait({task}, timeout=step.cleanup_s)
        finally:
            stopped.cancel()

        overran = ComponentError(
            f"component {name!r} timed out after {step.deadline_s}s",
            component=name,
            reason="timeout",
        )
        if not task.done():
            # It swallowed the cancellation. Nothing in-process can end it; say so and move on.
            task.add_done_callback(_retrieve)
            if step.reason == "deadline":
                return "failed", overran, True
            return "cancelled", None, True
        if sent or task.cancelled():
            if not task.cancelled():
                _retrieve(task)  # it returned or raised after the cancel: too late to count
            if step.reason == "deadline":
                return "failed", overran, False
            if step.reason is not None:
                return "cancelled", None, False
            return (
                "failed",
                ComponentError(
                    f"component {name!r} raised CancelledError without being cancelled",
                    component=name,
                ),
                False,
            )
        raised = task.exception()
        if isinstance(raised, MatrixError):
            return "failed", raised, False
        if raised is not None:
            failure = ComponentError(
                f"component {name!r} failed: {type(raised).__name__}: {raised}", component=name
            )
            failure.__cause__ = raised
            return "failed", failure, False
        # Returned in time, or within the grace of a stop request: what it returned is kept.
        self._commit(step, task.result())
        if step.reason == "deadline":
            return "failed", overran, False
        return "completed", None, False

    def _commit(self, step: _Step, result: Any) -> None:
        """Record what a component returned: every row checked first, then all appended."""
        name = step.name
        if result is None:
            return
        if not isinstance(result, Mapping):
            raise ContractError(
                f"component {name!r} returned {type(result).__name__}; components return a "
                f"mapping of kind to rows, or nothing (provides: {sorted(step.provides)})",
                component=name,
            )
        undeclared = sorted(set(result) - step.provides)
        if undeclared:
            raise ContractError(
                f"component {name!r} returned rows of {undeclared}, which it does not provide "
                f"(provides: {sorted(step.provides)})",
                component=name,
                declared=sorted(step.provides),
            )
        returned = [(kind, row) for kind, value in result.items() for row in _rows(value)]
        for kind, row in returned:
            _validate(self._wiring, kind, row, where=f"component {name!r} kind {kind!r}")
        for kind, row in returned:
            self._record(name, kind, row, checked=True)

    def provide(self, name: str, kind: str, row: Any) -> None:
        """One row handed over mid-run: checked, recorded and durable before this returns."""
        step = self._steps[name]
        if kind not in step.provides:
            raise ContractError(
                f"component {name!r} handed over a row of {kind!r}, which it does not provide "
                f"(provides: {sorted(step.provides)})",
                component=name,
                declared=sorted(step.provides),
            )
        try:
            self._record(name, kind, row)
        except StoreError as e:
            step.broke = step.broke or e
            self._halt("store failed", "run_failed")
            raise

    def _record(self, name: str, kind: str, row: Any, *, checked: bool = False) -> None:
        if not checked:
            _validate(self._wiring, kind, row, where=f"component {name!r} kind {kind!r}")
        # The record keeps its own copy: a component that goes on to change the object it
        # handed over cannot change what was recorded.
        kept = _private(row, kind)
        try:
            entry = self._run.construct.append(kind, kept, component=name)
        except MatrixError:
            raise
        except Exception as e:
            raise StoreError(
                f"the store could not append a row of {kind!r}: {type(e).__name__}: {e}",
                table=kind,
            ) from e
        self.emit(
            ROW_APPEND,
            uuid.uuid4().hex,
            {"kind": kind, "row": entry.row, "seq": entry.seq},
            None if name == INPUT else name,
        )

    def _request_stop(self, step: _Step, reason: StopReason) -> None:
        if step.reason is not None:
            return
        step.reason = reason
        step.context.stopping.set()
        step.stop.set()
        self.emit(COMPONENT_STOPPING, step.span, {"reason": reason}, step.name)

    def _halt(self, why: str, reason: StopReason) -> None:
        """Nothing more starts; everything running is asked to stop. The first cause stands."""
        if self._halted is None:
            self._halted = why
        for step in self._steps.values():
            if step.running:
                self._request_stop(step, reason)

    def _skip(self, name: str, why: str) -> None:
        self._run.components[name] = {"status": "skipped", "reason": why}
        self.emit(COMPONENT_END, uuid.uuid4().hex, {"status": "skipped", "reason": why}, name)

    async def _release(self) -> None:
        """Call every deferred release, last registered first, each under its own bound."""
        while self.releases:
            name, release, bound = self.releases.pop()
            span = uuid.uuid4().hex
            self.emit(RELEASE_START, span, {}, name)
            status, error = "released", None
            try:
                pending = release()
                if inspect.isawaitable(pending):
                    task = asyncio.ensure_future(pending)
                    await asyncio.wait({task}, timeout=bound)
                    if not task.done():
                        task.cancel()
                        task.add_done_callback(_retrieve)
                        status, error = "abandoned", f"still running after {bound}s"
                    elif task.cancelled():
                        status, error = "failed", "CancelledError"
                    elif (raised := task.exception()) is not None:
                        status, error = "failed", f"{type(raised).__name__}: {raised}"
            except Exception as e:
                status, error = "failed", f"{type(e).__name__}: {e}"
            if error is not None:
                logger.warning("a release deferred by %r %s: %s", name, status, error)
            self._run.releases.append({"component": name, "status": status, "error": error})
            self.emit(RELEASE_END, span, {"status": status, "error": error}, name)

    def emit(
        self,
        name: str,
        span: str,
        fields: dict[str, Any],
        component: str | None = None,
        parent: str | None = "",
    ) -> None:
        """Put one event on the run's channel. ``parent`` defaults to the run's own span."""
        under = self.run_id if parent == "" else parent
        self._channel.emit(Event(name, span, self.run_id, under, fields, component))


async def _call(component: Component, inputs: Inputs, ctx: RunContext, scope: SpanScope) -> Any:
    CURRENT_SPAN.set(scope)  # this task's own context: an agent it calls reports as its child
    return await component.run(inputs, ctx)


def _retrieve(task: asyncio.Future[Any]) -> None:
    """Consume a late task's outcome, so asyncio does not log it as never retrieved."""
    if not task.cancelled():
        task.exception()


def _rows(value: Any) -> tuple[Any, ...]:
    """A list or tuple is rows; anything else is one row."""
    return tuple(value) if isinstance(value, list | tuple) else (value,)


def _private(value: Any, kind: str) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception as e:
        raise ContractError(
            f"a row of {kind!r} cannot be copied ({type(e).__name__}: {e}); rows must be "
            "data, not live handles",
            table=kind,
        ) from e


def _validate(wiring: Wiring, kind: str, row: Any, *, where: str) -> None:
    schema: type[BaseModel] | None = wiring.schemas.get(kind)
    if schema is None or isinstance(row, schema):
        return
    try:
        schema.model_validate(row)
    except ValidationError as e:
        issues = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
            for err in e.errors()
        )
        raise ContractError(f"{where}: row is not a valid {kind}: {issues}", table=kind) from None
