"""The executor: run wired components with inputs, and record what happened as a :class:`Run`.

A component starts as soon as the providers of every kind it requires have finished; nothing
else orders the work. Two rules the code cannot show at a glance:

**The executor is the Construct's only writer.** A row a component hands over
(``ctx.provide``) or returns is checked against its declared kinds and the kind's schema,
recorded, and durable in the run's store before the call that handed it over returns. So is
each agent session or model call the component runs (``matrix.v1.session``); the ledger
entries of the rows it hands over next name those sessions.

**One stop path.** Whatever stops a component (its deadline, another's failure, the run's
cancellation), it is asked to stop (``ctx.stopping``), given ``grace_s`` to return, then
cancelled and given ``cleanup_s``, then abandoned. When the run ends, however it ends, every
deferred release is called, last first: an async one bounded by ``cleanup_s``, a synchronous
one run to its end, which nothing in the event loop can bound. A release that raises anything,
``SystemExit`` included, is recorded as failed and the rest are still called; what is not an
``Exception`` is raised again once the record is finished.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractContextManager, asynccontextmanager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from matrix.domain.component import Component, Inputs, Release, RunContext, Wiring
from matrix.domain.construct import INPUT, Construct, LedgerEntry
from matrix.domain.errors import (
    ComponentError,
    ConfigError,
    ContractError,
    MatrixError,
    RunCancelled,
    RunError,
    StoreError,
    invalid,
)
from matrix.domain.observer import (
    COMPONENT_END,
    COMPONENT_LATE,
    COMPONENT_RETRY,
    COMPONENT_START,
    COMPONENT_STOPPING,
    CURRENT_SPAN,
    MEASURE,
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
    around,
    check_event_name,
    check_measure,
)
from matrix.domain.ports._out.construct_store import events_of
from matrix.domain.problems import problem
from matrix.domain.session import CURRENT_SESSIONS, SESSION, Session

if TYPE_CHECKING:
    from matrix.domain.ports._out.construct_store import ConstructStore, RunWriter

logger = logging.getLogger("matrix.executor")

RUN_SCHEMA = "matrix.v1.run"

#: How a component ended. ``stopped``: it returned after it was asked to stop (another
#: component failed, or the run was cancelled), so what it returned is recorded but may be
#: partial, and it is never reused. ``reused``: its rows were carried over from an earlier
#: run. Open: a consumer must tolerate a state it does not know.
ComponentStatus = Literal["completed", "stopped", "failed", "cancelled", "skipped", "reused"]
#: Why a component was asked to stop.
StopReason = Literal["deadline", "run_failed", "cancelled"]
#: What a retry policy may name: any ``transient`` failure, or one of its reasons.
RetryOn = Literal["transient", "unavailable", "rate_limited", "timeout"]

#: The name a failure carries when the record could not be finished, rather than a component.
STORE = "$store"

#: The statuses after which a component's dependents may start.
_DONE = frozenset({"completed", "reused"})


class Retry(BaseModel):
    """When a component that failed is run again, and how long to wait first.

    A failure is retried when it matches ``errors`` (its kind, ``transient``, or its reason:
    ``unavailable``, ``rate_limited``, ``timeout``), fewer than ``attempts`` attempts have
    been made, the run has not been asked to stop, and the failed attempt handed over no
    rows: rows are part of the record, and a second attempt would add to them, not replace
    them. The wait before attempt ``n + 1`` is ``backoff_s * multiplier ** (n - 1)``, or the
    ``retry_after_s`` the failure reported, never more than ``max_wait_s``. Each attempt has
    the component's whole deadline.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Attempts in all, the first included: 1 runs a component once.
    attempts: int = Field(default=1, ge=1)
    backoff_s: float = Field(default=1.0, ge=0)
    multiplier: float = Field(default=2.0, ge=1)
    max_wait_s: float = Field(default=60.0, ge=0)
    #: The failures retried. (Not ``on``: YAML reads a bare ``on`` key as ``true``.)
    errors: tuple[RetryOn, ...] = ("transient",)

    def matches(self, error: MatrixError) -> bool:
        """Whether this policy retries ``error``, attempts aside."""
        reason = getattr(error, "reason", None) or error.details.get("reason")
        return error.kind in self.errors or (error.kind == "transient" and reason in self.errors)

    def wait_s(self, attempt: int, error: MatrixError) -> float:
        """Seconds to wait after failed attempt ``attempt`` (from 1) before the next."""
        hinted = getattr(error, "retry_after_s", None)
        if hinted is None:  # 0 is a hint too: "retry now"
            hinted = error.details.get("retry_after_s")
        if isinstance(hinted, int | float) and not isinstance(hinted, bool) and hinted >= 0:
            return min(float(hinted), self.max_wait_s)
        return min(self.backoff_s * self.multiplier ** (attempt - 1), self.max_wait_s)


class ComponentLimits(BaseModel):
    """Bounds for one component, overriding the run's. ``None`` inherits the run's value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deadline_s: float | None = Field(default=None, gt=0)
    grace_s: float | None = Field(default=None, ge=0)
    cleanup_s: float | None = Field(default=None, gt=0)
    retry: Retry | None = None


class Limits(BaseModel):
    """Bounds on one run: ``matrix.limits`` in config.

    ``concurrency`` caps how many components run at once; ``None`` (the default) leaves it to
    the wiring. ``deadline_s`` applies to each component separately, from the moment it
    starts; ``components`` overrides it (and ``grace_s``, ``cleanup_s``, ``retry``) for the
    names it lists. ``grace_s`` is how long a component has to return after a stop request
    before it is cancelled; ``cleanup_s`` how long a cancelled component, or one deferred
    release, has to finish before it is abandoned. ``retry`` is the :class:`Retry` policy of
    every component; by default nothing is retried.

    ``on_failure``: ``fast`` (the default) stops every running component and skips the rest
    as soon as one fails; ``isolate`` skips only the components that depend on the failed one
    and lets the others finish. The run ends ``failed`` either way.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    concurrency: int | None = Field(default=None, ge=1)
    deadline_s: float | None = Field(default=None, gt=0)
    grace_s: float = Field(default=5.0, ge=0)
    cleanup_s: float = Field(default=30.0, gt=0)
    on_failure: Literal["fast", "isolate"] = "fast"
    retry: Retry = Retry()
    components: dict[str, ComponentLimits] = {}

    def of(self, name: str) -> tuple[float | None, float, float]:
        """``(deadline_s, grace_s, cleanup_s)`` for the component ``name``."""
        own = self.components.get(name, ComponentLimits())
        return (
            own.deadline_s if own.deadline_s is not None else self.deadline_s,
            own.grace_s if own.grace_s is not None else self.grace_s,
            own.cleanup_s if own.cleanup_s is not None else self.cleanup_s,
        )

    def retry_of(self, name: str) -> Retry:
        """The retry policy of the component ``name``."""
        own = self.components.get(name)
        return own.retry if own is not None and own.retry is not None else self.retry


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ComponentFailure(_Record):
    """A component that failed, or ``$input``/``$store`` when the record could not be kept."""

    component: str
    #: The error's kind: ``contract``, ``component``, ``transient``, ``store``, ...
    kind: str
    message: str

    def document(self) -> dict[str, Any]:
        data: dict[str, Any] = self.model_dump(mode="json")
        return data


class ComponentEnding(_Record):
    """How one component ended. ``reason`` says why it was stopped, skipped or reused;
    ``duration_ms`` is None for one that never started; ``abandoned`` marks one that ignored
    cancellation and may still be running; ``attempts`` counts the times it was run."""

    status: ComponentStatus
    reason: str | None = None
    duration_ms: int | None = None
    abandoned: bool = False
    attempts: int = 1

    def document(self) -> dict[str, Any]:
        """``{status, reason?, duration_ms?, abandoned?, attempts?}``: a key is absent when
        it says nothing."""
        found: dict[str, Any] = {"status": self.status}
        if self.duration_ms is not None:
            found["duration_ms"] = self.duration_ms
        if self.reason is not None:
            found["reason"] = self.reason
        if self.abandoned:
            found["abandoned"] = True
        if self.attempts > 1:
            found["attempts"] = self.attempts
        return found


class ReleaseOutcome(_Record):
    """What became of one deferred release. ``error`` is None when it was released."""

    component: str
    status: Literal["released", "failed", "abandoned"]
    error: str | None = None

    def document(self) -> dict[str, Any]:
        data: dict[str, Any] = self.model_dump(mode="json")
        return data


class Reuse(_Record):
    """Rows carried over from an earlier run: each listed component is not run, and its rows
    are recorded as they were. A component may be reused only when every component it
    depends on is reused too, so what it was given then is what it would be given now."""

    #: The run the rows come from.
    run_id: str
    #: component name -> kind -> its rows, in the order they were recorded.
    rows: dict[str, dict[str, tuple[Any, ...]]]


class Run(BaseModel):
    """Observed state of one execution. Separate from the wiring, which a run never changes.

    The executor fills it in as the run goes; a caller reads it. ``construct`` is the run's
    record, its tables; ``document()`` is the run as the store keeps it, without them.
    """

    run_id: str
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    started: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended: datetime | None = None
    failures: list[ComponentFailure] = []
    #: How each component ended, by name.
    components: dict[str, ComponentEnding] = {}
    #: Each deferred release, in the order they were called.
    releases: list[ReleaseOutcome] = []
    #: Events emitted during the run that no observer received (telemetry is lossy).
    events_dropped: int = 0
    #: The run whose rows this one reused, if any.
    reused_from: str | None = None
    #: name -> what the component was built from, as a digest, when the wiring knows it: what
    #: a later run compares before it reuses the component's rows.
    fingerprints: dict[str, str] = {}
    _construct: Construct = PrivateAttr()

    def __init__(self, *, construct: Construct, **fields: Any) -> None:
        super().__init__(**fields)
        self._construct = construct

    @property  # BaseModel.construct is pydantic v1's deprecated constructor, never used here
    def construct(self) -> Construct:  # type: ignore[override]
        """The run's tables, one per kind, and its ledger."""
        return self._construct

    def rows(self, kind: str) -> tuple[Any, ...]:
        """The rows of ``kind`` recorded in this run."""
        return self.construct.rows(kind)

    def document(self) -> dict[str, Any]:
        """The run as a JSON-ready document (``matrix.v1.run``), without its tables."""
        found: dict[str, Any] = {
            "schema": RUN_SCHEMA,
            "run_id": self.run_id,
            "status": self.status,
            "started": self.started.isoformat(),
            "ended": self.ended.isoformat() if self.ended else None,
            "components": {name: ending.document() for name, ending in self.components.items()},
            "failures": [failure.document() for failure in self.failures],
            "releases": [release.document() for release in self.releases],
            "events_dropped": self.events_dropped,
        }
        if self.reused_from is not None:
            found["reused_from"] = self.reused_from
        if self.fingerprints:
            found["fingerprints"] = dict(self.fingerprints)
        return found


class Executor:
    """Runs wired components. Stateless between runs; safe to share.

    ``events`` bounds the telemetry a run may hold for its observers. ``interrupts`` is
    entered around each run, in the task awaiting it: the host's way of turning a signal into
    the cancellation of that task (``matrix.cancel_on_signals`` does this for SIGTERM and
    SIGINT). Without one, the host's signals are left alone. ``trace`` returns the trace and
    span ids (hex) of the tracing context current in the task that calls it, or None; each
    session row records what it returned when the session ended (composition passes one that
    reads OpenTelemetry's). Without one, session rows carry no trace ids.
    """

    def __init__(
        self,
        observers: Sequence[Observer] = (),
        *,
        events: EventPolicy | None = None,
        interrupts: Callable[[], AbstractContextManager[None]] = nullcontext,
        trace: Callable[[], tuple[str, str] | None] | None = None,
    ) -> None:
        self._observers = tuple(observers)
        self._events = events or EventPolicy()
        self._interrupts = interrupts
        self._trace = trace

    async def run(
        self,
        wiring: Wiring,
        inputs: Mapping[str, Any] | None = None,
        *,
        limits: Limits | None = None,
        store: ConstructStore | None = None,
        run_id: str | None = None,
        reuse: Reuse | None = None,
    ) -> Run:
        """Run ``wiring`` to a terminal state and return the Run.

        ``inputs`` maps each run-input kind to one row, or to a list or tuple of rows. With a
        ``store``, the run's record is opened there before anything starts, every row is
        durable in it as it lands, and the run's events are kept beside it when the store
        keeps them. ``reuse`` carries rows over from an earlier run for components that need
        not run again.

        Raises :class:`RunError` when a component failed or the record could not be kept, and
        :class:`RunCancelled` when the calling task was cancelled; both carry the partial
        Run, and both are raised only after every component stopped and every deferred
        release was called. Raises StoreError, before anything runs, when the store cannot
        open the run (its id is taken), and ConfigError when the inputs, the limits or the
        reuse do not fit the wiring.
        """
        limits = limits or Limits()
        supplied = _check_inputs(
            wiring, {kind: _rows(value) for kind, value in (inputs or {}).items()}, limits
        )
        if reuse is not None:
            _check_reuse(wiring, reuse)
        run = Run(
            run_id=run_id or uuid.uuid4().hex,
            construct=Construct(wiring.kinds),
            reused_from=None if reuse is None else reuse.run_id,
            fingerprints=dict(wiring.fingerprints),
        )
        writer = _open(store, run) if store is not None else None
        sink = events_of(writer) if writer is not None else None
        observers = self._observers if sink is None else (*self._observers, sink)
        channel = Channel(observers, self._events, run.run_id)
        session = _Session(wiring, run, limits, channel, writer, self._trace)
        # The run lives in its own task, shielded from the caller's cancellation: cancelling
        # the caller asks the run to stop, and the run answers once it has cleaned up.
        main = asyncio.ensure_future(session.execute(supplied, reuse))
        interrupted = False
        try:
            with self._interrupts():
                while not main.done():
                    try:
                        await asyncio.shield(main)
                    except asyncio.CancelledError:
                        if main.cancelled():
                            raise  # the loop is tearing every task down; nothing left to await
                        interrupted = True
                        session.cancel()
            main.result()
        finally:
            _close(sink)
        if interrupted:
            raise RunCancelled(run)
        if session.first is not None:
            name, cause = session.first
            raise RunError(
                f"run {run.run_id} failed at {name!r}: {cause}",
                run=run,
                component=name,
                cause_kind=cause.kind,
                fix=cause.fix,
            ) from cause
        return run


def _close(sink: Observer | None) -> None:
    """Close the record's event log once the run's events have drained into it."""
    close = getattr(sink, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.exception("the run's event log could not be closed")


def _check_reuse(wiring: Wiring, reuse: Reuse) -> None:
    """Raise ConfigError unless every reused component is in the wiring, provides only what
    it declares, and depends only on components that are reused too."""
    problems: list[str] = []
    for name, tables in reuse.rows.items():
        if name not in wiring.components:
            problems.append(f"{name!r} is not a component of this run")
            continue
        undeclared = sorted(set(tables) - set(wiring.components[name].provides))
        if undeclared:
            problems.append(f"{name!r} does not provide {undeclared}")
        upstream = [d for d in wiring.depends[name] if d not in reuse.rows]
        if upstream:
            problems.append(f"{name!r} depends on {upstream}, which would run again")
    if problems:
        raise ConfigError(
            f"rows from run {reuse.run_id!r} cannot be reused: " + "; ".join(problems),
            fix="reuse only components whose providers are reused too",
            run_id=reuse.run_id,
        )


def _check_inputs(
    wiring: Wiring, supplied: Mapping[str, tuple[Any, ...]], limits: Limits
) -> dict[str, tuple[Any, ...]]:
    """The run's own copy of ``supplied``, once it holds exactly the run inputs, each row meets
    its kind's schema and can be copied, and ``limits`` names only components of the run.
    Raises ConfigError, or ContractError for a row, before the run's record is opened."""
    problems = [
        problem(
            f"inputs.{kind}",
            "missing_key",
            f"the run input {kind!r} was not supplied",
            fix=f"supply rows of {kind!r}",
            allowed=sorted(wiring.inputs),
        )
        for kind in sorted(wiring.inputs - set(supplied))
    ]
    problems += [
        problem(
            f"inputs.{kind}",
            "unknown_key",
            f"{kind!r} is not a run input",
            fix="supply exactly the kinds declared as run inputs",
            allowed=sorted(wiring.inputs),
            got=kind,
        )
        for kind in sorted(set(supplied) - wiring.inputs)
    ]
    problems += [
        problem(
            f"limits.components.{name}",
            "name_unknown",
            f"limits name {name!r}, which is not a component of the run",
            fix="key limits.components by component name",
            allowed=sorted(wiring.components),
            got=name,
        )
        for name in sorted(set(limits.components) - set(wiring.components))
    ]
    if problems:
        raise invalid("the run cannot start", problems)
    return {
        kind: tuple(
            _private(_validate(wiring, kind, row, where=f"input {kind!r}"), kind) for row in rows
        )
        for kind, rows in supplied.items()
    }


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


class _Progress:
    """What changes while one component runs, each through one method."""

    def __init__(self) -> None:
        #: The executor's own stop flag; ``context.stopping`` mirrors it for the component.
        self.stop = asyncio.Event()
        self.reason: StopReason | None = None
        self.running = False
        #: The component's task while it runs.
        self._task: asyncio.Future[Any] | None = None
        #: The first store failure met while recording one of this component's rows.
        self.broke: StoreError | None = None

    def begin(self, task: asyncio.Future[Any]) -> None:
        self.running = True
        self._task = task

    def end(self) -> None:
        self.running = False

    def request_stop(self, reason: StopReason) -> bool:
        """Set the stop flag for ``reason``; False when a stop was already requested, whose
        reason stands, or when the component had already returned: what it returned then
        was not cut short."""
        if self.reason is not None or (self._task is not None and self._task.done()):
            return False
        self.reason = reason
        self.stop.set()
        return True

    def record_store_failure(self, error: StoreError) -> None:
        self.broke = self.broke or error


@dataclass(frozen=True)
class _Outcome:
    """How a component's task ended: its status, the error that failed it, and whether it
    was abandoned still running."""

    status: ComponentStatus
    error: MatrixError | None = None
    abandoned: bool = False


@dataclass(frozen=True, kw_only=True)
class _Step:
    """One component's passage through a run: what it is and its bounds, fixed when it
    starts, and its progress."""

    name: str
    component: Component
    provides: frozenset[str]
    deadline_s: float | None
    grace_s: float
    cleanup_s: float
    context: RunContext
    hooks: _Hooks
    span: str = field(default_factory=lambda: uuid.uuid4().hex)
    progress: _Progress = field(default_factory=_Progress)


class _Hooks:
    """What one component's :class:`RunContext` may ask of the run."""

    def __init__(self, session: _Session, name: str, cleanup_s: float) -> None:
        self._session = session
        self._name = name
        self._cleanup_s = cleanup_s
        self._warned = False
        self._closed = False

    def close(self) -> None:
        """The component has ended: what it asks of its context from now on is ignored."""
        self._closed = True

    def _late(self, call: str) -> bool:
        """Whether the component has ended; the first late call is reported, the rest quiet."""
        if self._closed and not self._warned:
            self._warned = True
            logger.warning(
                "component %r used its context (%s) after it ended; ignored", self._name, call
            )
            self._session.tell(COMPONENT_LATE, {"call": call}, component=self._name)
        return self._closed

    def _parent(self) -> str:
        scope = CURRENT_SPAN.get()
        if scope is not None and scope.run_id == self._session.run_id:
            return scope.span
        return self._session.run_id

    def provide(self, kind: str, row: Any) -> None:
        if not self._late("provide"):
            self._session.provide(self._name, kind, row)

    def session(self, row: Session) -> int | None:
        """Record an agent session or model call the component ran; its row, or None after
        the component ended."""
        if self._late("session"):
            return None
        return self._session.record_session(self._name, row)

    def sessions(self) -> tuple[int, ...]:
        """The session rows the component ran since its last hand-over."""
        return self._session.since(self._name)

    def defer(self, release: Release) -> None:
        if not callable(release):
            raise TypeError(f"defer() takes a callable, got {type(release).__name__}")
        if not self._late("defer"):
            self._session.releases.append((self._name, release, self._cleanup_s))

    def emit(self, name: str, fields: dict[str, Any]) -> None:
        check_event_name(name)
        if not self._late("emit"):
            self._session.emit(
                name,
                span=uuid.uuid4().hex,
                fields=fields,
                component=self._name,
                parent=self._parent(),
            )

    def measure(
        self, name: str, value: float, *, unit: str, kind: str, attrs: dict[str, Any]
    ) -> None:
        check_measure(name, value, kind)
        if not self._late("measure"):
            fields = {"name": name, "value": value, "unit": unit, "kind": kind, "attrs": attrs}
            self._session.emit(
                MEASURE,
                span=uuid.uuid4().hex,
                fields=fields,
                component=self._name,
                parent=self._parent(),
            )

    @asynccontextmanager
    async def span(self, name: str, fields: dict[str, Any]) -> AsyncIterator[None]:
        check_event_name(name, what="span")
        scope = CURRENT_SPAN.get()
        if self._late("span") or scope is None:
            yield
            return
        span = uuid.uuid4().hex
        begun = self._session.emit(
            f"{name}.start", span=span, fields=fields, component=self._name, parent=scope.span
        )
        token = CURRENT_SPAN.set(replace(scope, span=span))
        started = time.monotonic()
        status = "ok"
        with around(self._session.observers(), begun) as end:
            try:
                yield
            except BaseException:
                status = "error"
                raise
            finally:
                CURRENT_SPAN.reset(token)
                ended = {
                    "status": status,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
                end(
                    self._session.emit(
                        f"{name}.end",
                        span=span,
                        fields=ended,
                        component=self._name,
                        parent=scope.span,
                    )
                )


class _Session:
    """One run in flight: what is ready, the stop path, and the releases owed at the end."""

    def __init__(
        self,
        wiring: Wiring,
        run: Run,
        limits: Limits,
        channel: Channel,
        writer: RunWriter | None,
        trace: Callable[[], tuple[str, str] | None] | None = None,
    ) -> None:
        self._wiring = wiring
        self._trace = trace
        #: The session rows each component ran since its previous hand-over.
        self._sessions: dict[str, list[int]] = {}
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
        #: Set once something stopped the run: a component waiting to retry stops waiting.
        self._halt_signal = asyncio.Event()
        self._cancelled = False
        #: How many rows each component has handed over: one that has is never retried.
        self._recorded: dict[str, int] = {}
        self.releases: list[tuple[str, Release, float]] = []
        #: The first failure, which :class:`RunError` reports as the cause.
        self.first: tuple[str, MatrixError] | None = None

    def cancel(self) -> None:
        """Ask the run to stop: every running component is stop-requested, the rest skipped."""
        self._cancelled = True
        self._halt("cancelled", "cancelled")
        self._wake.set()

    async def execute(
        self, supplied: Mapping[str, tuple[Any, ...]], reuse: Reuse | None = None
    ) -> None:
        run = self._run
        started = time.monotonic()
        self._channel.start()
        fields: dict[str, Any] = {"components": list(self._wiring.components)}
        if reuse is not None:
            fields["reused_from"] = reuse.run_id
        begun = self.emit(RUN_START, span=run.run_id, fields=fields, parent=None)
        #: What escaped the run, if anything did: matrix's own defect, or an error nobody
        #: classified. The run then did not complete, whatever its failures say.
        escaped: BaseException | None = None
        #: What a release raised that is not an Exception, raised once the record is kept.
        unbound: BaseException | None = None
        try:
            # The run's span is current in this task, and so in every component's, which
            # this task starts.
            with around(self.observers(), begun) as end:
                try:
                    try:
                        for kind, rows in supplied.items():
                            for row in rows:
                                self._record(INPUT, kind, row)
                    except StoreError as e:
                        self._failed(INPUT, e)
                    if reuse is not None and self._halted is None:
                        self._reuse(reuse)
                    await self._schedule()
                except BaseException as e:
                    escaped = e
                    raise
                finally:
                    # Settled before the releases run: a cancellation that arrives while
                    # they do still reaches the caller, but it did not stop any component.
                    cancelled = self._cancelled or isinstance(escaped, asyncio.CancelledError)
                    unbound = await self._release()
                    run.ended = datetime.now(UTC)
                    run.status = (
                        "cancelled"
                        if cancelled
                        else "failed"
                        if run.failures or escaped is not None
                        else "completed"
                    )
                    ended = {
                        "status": run.status,
                        "rows": len(run.construct),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }
                    end(self.emit(RUN_END, span=run.run_id, fields=ended, parent=None))
        finally:
            # The record is finished last, so it counts every event no observer received.
            try:
                run.events_dropped = await self._channel.close()
                if run.events_dropped:
                    logger.warning(
                        "run %s: %d events were dropped before any observer received them",
                        run.run_id,
                        run.events_dropped,
                    )
            finally:
                self._finish()
        if unbound is not None:  # a release's SystemExit (say), once the record is kept
            raise unbound

    def _reuse(self, reuse: Reuse) -> None:
        """Record each reused component's rows as it recorded them, and end it ``reused``.

        A component whose rows cannot be recorded (its kind's schema now refuses one) ends
        ``failed`` and is not run; a reused component that depends on it is skipped. What
        comes after follows ``on_failure``, as for any failure: under ``isolate`` the other
        reused components are still reused.
        """
        ended = self._run.components
        why = f"from run {reuse.run_id}"
        for name in self._wiring.order():
            tables = reuse.rows.get(name)
            if tables is None:
                continue
            if self._halted is not None:
                return  # what is left is skipped with the rest of the run
            broken = [d for d in self._wiring.depends[name] if ended[d].status not in _DONE]
            if broken:
                self._skip(name, f"upstream: {broken[0]}")
                continue
            try:
                kept = [
                    (kind, _validate(self._wiring, kind, row, where=f"reused {name!r} {kind!r}"))
                    for kind, rows in tables.items()
                    for row in rows
                ]
                for kind, row in kept:
                    self._record(name, kind, row, checked=True)
            except MatrixError as e:
                ending = ComponentEnding(status="failed", reason=why)
                ended[name] = ending
                self.tell(COMPONENT_END, {**ending.document(), "kind": e.kind}, component=name)
                self._failed(name, e)
                continue
            ending = ComponentEnding(status="reused", reason=why)
            ended[name] = ending
            self.tell(COMPONENT_END, ending.document(), component=name)

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
                broken = [d for d in waits if d in ended and ended[d].status not in _DONE]
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
        deadline = None if deadline_s is None else time.monotonic() + deadline_s
        step = _Step(
            name=name,
            component=component,
            provides=frozenset(component.provides),
            deadline_s=deadline_s,
            grace_s=grace_s,
            cleanup_s=cleanup_s,
            context=RunContext(self.run_id, name, deadline, hooks=hooks),
            hooks=hooks,
        )
        self._steps[name] = step
        self._drivers.add(asyncio.ensure_future(self._drive(step)))

    async def _drive(self, step: _Step) -> None:
        """Run one component to a terminal state, retrying it as its policy allows. Never
        raises: every ending is recorded."""
        name = step.name
        if self._halted is not None:  # the run was stopped before this one got to start
            self._skip(name, self._halted)
            self._wake.set()
            return
        begun = self.tell(COMPONENT_START, {}, component=name, span=step.span)
        # The component's span is current in this task, and so in each attempt's task, which
        # this task starts: one span for all of its attempts.
        with around(self.observers(), begun) as end:
            started = time.monotonic()
            retry = self._limits.retry_of(name)
            attempt = 1
            while True:
                try:
                    outcome = await self._attempt(step)
                except MatrixError as e:
                    outcome = _Outcome("failed", e)
                finally:
                    step.progress.end()
                    step.hooks.close()
                if step.progress.broke is not None:
                    outcome = _Outcome("failed", step.progress.broke, outcome.abandoned)
                wait = self._retry_wait(step, outcome, retry, attempt)
                if wait is None or not await self._waited(name, step, outcome, attempt, wait):
                    break
                attempt += 1
                step = self._restart(step)
            if outcome.abandoned:
                logger.warning(
                    "component %r ignored cancellation for %ss and was abandoned; it may still "
                    "be running",
                    name,
                    step.cleanup_s,
                )
            ending = ComponentEnding(
                status=outcome.status,
                reason=step.progress.reason,
                duration_ms=int((time.monotonic() - started) * 1000),
                abandoned=outcome.abandoned,
                attempts=attempt,
            )
            self._run.components[name] = ending
            fields = ending.document()
            if outcome.error is not None:
                fields["kind"] = outcome.error.kind
            end(self.tell(COMPONENT_END, fields, component=name, span=step.span))
        if outcome.error is not None:
            self._failed(name, outcome.error)
        self._wake.set()

    def _retry_wait(
        self, step: _Step, outcome: _Outcome, retry: Retry, attempt: int
    ) -> float | None:
        """How long to wait before running ``step`` again, or None when it is not retried."""
        error = outcome.error
        if (
            outcome.status != "failed"
            or error is None
            or outcome.abandoned
            or attempt >= retry.attempts
            or self._halted is not None
            or step.progress.reason is not None
            or self._recorded.get(step.name, 0) > 0
            or not retry.matches(error)
        ):
            return None
        return retry.wait_s(attempt, error)

    async def _waited(
        self, name: str, step: _Step, outcome: _Outcome, attempt: int, wait: float
    ) -> bool:
        """Report the retry and wait it out; False when the run was stopped meanwhile."""
        error = outcome.error
        fields = {
            "attempt": attempt + 1,
            "kind": None if error is None else error.kind,
            "error": str(error),
            "wait_s": wait,
        }
        self.tell(COMPONENT_RETRY, fields, component=name, span=step.span)
        halted = asyncio.ensure_future(self._halt_signal.wait())
        try:
            await asyncio.wait({halted}, timeout=wait)
        finally:
            halted.cancel()
        return self._halted is None

    def _restart(self, step: _Step) -> _Step:
        """A fresh context for the next attempt of ``step``: the same component and span. The
        sessions the failed attempt ran produced none of the next attempt's rows."""
        self._sessions.pop(step.name, None)
        hooks = _Hooks(self, step.name, step.cleanup_s)
        deadline = None if step.deadline_s is None else time.monotonic() + step.deadline_s
        fresh = replace(
            step,
            context=RunContext(self.run_id, step.name, deadline, hooks=hooks),
            hooks=hooks,
            progress=_Progress(),
        )
        self._steps[step.name] = fresh
        return fresh

    def _failed(self, name: str, error: MatrixError) -> None:
        self._run.failures.append(
            ComponentFailure(component=name, kind=error.kind, message=str(error))
        )
        self.first = self.first or (name, error)
        if isinstance(error, StoreError):
            self._halt("store failed", "run_failed")  # no record, no run: whatever the policy
        elif self._limits.on_failure == "fast":
            self._halt("policy: fast", "run_failed")

    async def _attempt(self, step: _Step) -> _Outcome:
        """Inputs in, the stop path around the component, and how it ended."""
        inputs = Inputs(
            {
                kind: _private(self._run.construct.rows(kind), kind)
                for kind in step.component.requires
            }
        )
        scope = SpanScope(self.run_id, step.span, step.name, self._channel)
        task = asyncio.ensure_future(
            _call(step.component, inputs, step.context, scope, step.hooks.session)
        )
        step.progress.begin(task)
        sent = await self._await_with_stop_path(step, task)
        return self._ending(step, task, sent=sent)

    async def _await_with_stop_path(self, step: _Step, task: asyncio.Future[Any]) -> bool:
        """Wait for ``task`` through the stop path: its deadline or a stop request, then
        ``grace_s`` to return, then a cancellation and ``cleanup_s``. True when it had to be
        cancelled."""
        stopped = asyncio.ensure_future(step.progress.stop.wait())
        sent = False
        try:
            await asyncio.wait(
                {task, stopped}, timeout=step.deadline_s, return_when=asyncio.FIRST_COMPLETED
            )
            if not task.done():
                self._request_stop(step, "deadline")
                await asyncio.wait({task}, timeout=step.grace_s)
            if not task.done():
                sent = task.cancel()
                await asyncio.wait({task}, timeout=step.cleanup_s)
        finally:
            stopped.cancel()
        return sent

    def _ending(self, step: _Step, task: asyncio.Future[Any], *, sent: bool) -> _Outcome:
        """How a component's task ended, given whether it was cancelled. What it returned in
        time, or within the grace of a stop request, is recorded; after a stop request it
        ends ``stopped``, since it returned what it had, not necessarily all of it."""
        name = step.name
        overran = ComponentError(
            f"component {name!r} timed out after {step.deadline_s}s",
            component=name,
            reason="timeout",
        )
        on_deadline = step.progress.reason == "deadline"
        if not task.done():
            # It swallowed the cancellation. Nothing in-process can end it; say so and move on.
            task.add_done_callback(_retrieve)
            status: ComponentStatus = "failed" if on_deadline else "cancelled"
            return _Outcome(status, overran if on_deadline else None, abandoned=True)
        if sent or task.cancelled():
            if not task.cancelled():
                _retrieve(task)  # it returned or raised after the cancel: too late to count
            if on_deadline:
                return _Outcome("failed", overran)
            if step.progress.reason is not None:
                return _Outcome("cancelled")
            return _Outcome(
                "failed",
                ComponentError(
                    f"component {name!r} raised CancelledError without being cancelled",
                    component=name,
                ),
            )
        raised = task.exception()
        if isinstance(raised, MatrixError):
            return _Outcome("failed", raised)
        if raised is not None:
            failure = ComponentError(
                f"component {name!r} failed: {type(raised).__name__}: {raised}", component=name
            )
            failure.__cause__ = raised
            return _Outcome("failed", failure)
        try:
            self._commit(step, task.result())
        except MatrixError:
            raise
        except Exception as e:
            raise ContractError(
                f"component {name!r}: what it returned could not be recorded: "
                f"{type(e).__name__}: {e}",
                component=name,
            ) from e
        if on_deadline:
            return _Outcome("failed", overran)
        return _Outcome("completed" if step.progress.reason is None else "stopped")

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
        returned = [
            (kind, _validate(self._wiring, kind, row, where=f"component {name!r} kind {kind!r}"))
            for kind, value in result.items()
            for row in _rows(value)
        ]
        behind = self.since(name)
        for kind, row in returned:
            self._record(name, kind, row, checked=True, sessions=behind)
        self._sessions.pop(name, None)

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
            self._record(name, kind, row, sessions=self.since(name))
        except StoreError as e:
            step.progress.record_store_failure(e)
            self._halt("store failed", "run_failed")
            raise
        self._sessions.pop(name, None)

    def since(self, name: str) -> tuple[int, ...]:
        """The session rows component ``name`` ran since its last hand-over: those the next
        row it hands over names."""
        return tuple(self._sessions.get(name, ()))

    def record_session(self, name: str, row: Session) -> int:
        """Record a session component ``name`` ran, with the trace it ended in; durable when
        this returns. Its position is named by the next rows the component hands over."""
        ids = self._trace() if self._trace is not None else None
        if ids is not None and row.trace_id is None:
            row = row.model_copy(update={"trace_id": ids[0], "span_id": ids[1]})
        try:
            entry = self._append(name, SESSION, _private(row, SESSION))
        except StoreError as e:
            self._steps[name].progress.record_store_failure(e)
            self._halt("store failed", "run_failed")
            raise
        self._sessions.setdefault(name, []).append(entry.row)
        return entry.row

    def _record(
        self,
        name: str,
        kind: str,
        row: Any,
        *,
        checked: bool = False,
        sessions: tuple[int, ...] = (),
    ) -> None:
        """Record ``row``; ``checked`` when it is already as :func:`_validate` returns it.
        ``sessions`` are the session rows behind it."""
        if not checked:
            row = _validate(self._wiring, kind, row, where=f"component {name!r} kind {kind!r}")
        # The record keeps its own copy: a component that goes on to change the object it
        # handed over cannot change what was recorded.
        self._append(name, kind, _private(row, kind), sessions)
        self._recorded[name] = self._recorded.get(name, 0) + 1

    def _append(
        self, name: str, kind: str, kept: Any, sessions: tuple[int, ...] = ()
    ) -> LedgerEntry:
        """Write ``kept`` through to the record and report it."""
        try:
            entry = self._run.construct.append(kind, kept, component=name, sessions=sessions)
        except MatrixError:
            raise
        except Exception as e:
            raise StoreError(
                f"the store could not append a row of {kind!r}: {type(e).__name__}: {e}",
                table=kind,
            ) from e
        self.tell(
            ROW_APPEND,
            {"kind": kind, "row": entry.row, "seq": entry.seq},
            component=None if name == INPUT else name,
        )
        return entry

    def _request_stop(self, step: _Step, reason: StopReason) -> None:
        if step.progress.request_stop(reason):
            step.context.stopping.set()
            self.tell(COMPONENT_STOPPING, {"reason": reason}, component=step.name, span=step.span)

    def _halt(self, why: str, reason: StopReason) -> None:
        """Nothing more starts; everything running is asked to stop. The first cause stands."""
        if self._halted is None:
            self._halted = why
            self._halt_signal.set()
        for step in self._steps.values():
            if step.progress.running:
                self._request_stop(step, reason)

    def _skip(self, name: str, why: str) -> None:
        self._run.components[name] = ComponentEnding(status="skipped", reason=why)
        self.tell(COMPONENT_END, {"status": "skipped", "reason": why}, component=name)

    async def _release(self) -> BaseException | None:
        """Call every deferred release, last registered first, an async one under its own
        bound. Returns the first thing a release raised that is not an ``Exception``, to be
        raised once the record is finished; the releases after it are still called."""
        unbound: BaseException | None = None
        while self.releases:
            name, release, bound = self.releases.pop()
            span = uuid.uuid4().hex
            begun = self.tell(RELEASE_START, {}, component=name, span=span)
            with around(self.observers(), begun) as end:
                status: Literal["released", "failed", "abandoned"] = "released"
                error: str | None = None
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
                except BaseException as e:  # anything: the releases still owed are called
                    status, error = "failed", f"{type(e).__name__}: {e}"
                    if not isinstance(e, Exception):
                        unbound = unbound or e
                if error is not None:
                    logger.warning("a release deferred by %r %s: %s", name, status, error)
                outcome = ReleaseOutcome(component=name, status=status, error=error)
                self._run.releases.append(outcome)
                fields = {"status": status, "error": error}
                end(self.tell(RELEASE_END, fields, component=name, span=span))
        return unbound

    def observers(self) -> tuple[Observer, ...]:
        """The run's observers: who sees its events, and whose ``around`` its work enters."""
        return self._channel.observers()

    def emit(
        self,
        name: str,
        *,
        span: str,
        fields: dict[str, Any],
        component: str | None = None,
        parent: str | None,
    ) -> Event:
        """Put one event on the run's channel, under the span ``parent`` (None: under
        nothing, which only the run's own events are). Returns it."""
        event = Event(
            name,
            span=span,
            run_id=self.run_id,
            parent=parent,
            fields=fields,
            component=component,
        )
        self._channel.emit(event)
        return event

    def tell(
        self, name: str, fields: dict[str, Any], *, component: str | None, span: str | None = None
    ) -> Event:
        """Put one event about the run's work on its channel, under the run's own span.
        Returns it."""
        return self.emit(
            name,
            span=span or uuid.uuid4().hex,
            fields=fields,
            component=component,
            parent=self.run_id,
        )


async def _call(
    component: Component,
    inputs: Inputs,
    ctx: RunContext,
    scope: SpanScope,
    sessions: Callable[[Session], int | None],
) -> Any:
    CURRENT_SPAN.set(scope)  # this task's own context: an agent it calls reports as its child
    CURRENT_SESSIONS.set(sessions)  # and the sessions it runs are recorded as its own
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


def _validate(wiring: Wiring, kind: str, row: Any, *, where: str) -> Any:
    """``row`` as the record keeps it: an instance of its kind's schema, however it was handed
    over (a dict, or rows read back from a store, are validated into one), so a consumer gets
    the same type on a fresh run and on one that reuses the rows. A kind without a schema
    keeps ``row`` as it is. Raises ContractError when the schema refuses the row."""
    schema: type[BaseModel] | None = wiring.schemas.get(kind)
    if schema is None or isinstance(row, schema):
        return row
    try:
        return schema.model_validate(row)
    except ValidationError as e:
        issues = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
            for err in e.errors()
        )
        raise ContractError(f"{where}: row is not a valid {kind}: {issues}", table=kind) from None
    except Exception as e:
        # A validator may raise what pydantic does not wrap (a TypeError, say): the schema
        # still refused the row.
        raise ContractError(
            f"{where}: row is not a valid {kind}: {type(e).__name__}: {e}", table=kind
        ) from e
