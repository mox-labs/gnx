"""The executor: run a compiled flow with inputs, and record what happened as a :class:`Run`.

Members run level by level (each level depends only on earlier ones). Within a level, up to
``Limits.concurrency`` members run at once. The executor is the Construct's only writer, and
it appends a level's artifacts in declaration order, so a run's Construct reads the same
however it was scheduled.

Each member gets :class:`~matrix.domain.flow.Inputs`, only what it bound. Its result must
hold exactly its declared provide ports (optional ones may be missing), and each value must
validate against its type's payload schema when one is registered; otherwise the member broke
its contract.

**Every member ends in one state**: ``completed``, ``failed``, ``cancelled`` or ``skipped``
(``Run.members``). **Every run ends in one of three**: ``completed``, ``failed`` (some member
failed; :class:`RunError` carries the partial Run) or ``cancelled`` (the caller, Ctrl-C or
SIGTERM cancelled it; :class:`RunCancelled` carries the partial Run). Nothing produced before
the end is lost in either case.

**One stop path.** A member is stopped the same way whatever the cause (its deadline passed,
another member failed, the run was cancelled):

1. *stop request*: ``ctx.stopping`` is set. A member that returns now keeps what it returns;
2. *grace*: the executor waits ``grace_s`` for that return;
3. *cancel*: the member's task is cancelled, and its own ``finally`` blocks run. A member
   that swallows the cancellation is **abandoned** after ``cleanup_s`` and reported; matrix
   cannot stop in-process code that will not stop;
4. *deferred release*: when the run ends, however it ends, everything members handed to
   ``ctx.defer`` is called, last registered first, each bounded by ``cleanup_s``. A release
   that raises or overruns is recorded in ``Run.releases``; it never stops the others.

What this cannot bound: a member that blocks the event loop (synchronous work) stalls every
deadline, sibling and release until it yields.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from matrix.domain.construct import INPUT_PRODUCER, Artifact, Construct
from matrix.domain.errors import (
    ComponentError,
    ConfigError,
    ContractError,
    MatrixError,
    RunCancelled,
    RunError,
)
from matrix.domain.flow import CompiledFlow, CompiledMember, Inputs, Release, RunContext
from matrix.domain.observer import CURRENT_SPAN, Event, Observer, emit
from matrix.domain.signals import cancel_on_signals

logger = logging.getLogger("matrix.executor")

#: How a member ended. Open: a consumer must tolerate a state it does not know.
MemberStatus = Literal["completed", "failed", "cancelled", "skipped"]
#: Why a member was asked to stop.
StopReason = Literal["deadline", "run_failed", "cancelled"]


@dataclass(frozen=True)
class MemberLimits:
    """Bounds for one member, overriding the run's. ``None`` inherits the run's value."""

    deadline_s: float | None = None
    grace_s: float | None = None
    cleanup_s: float | None = None

    def __post_init__(self) -> None:
        _check_bounds(self.deadline_s, self.grace_s, self.cleanup_s)


@dataclass(frozen=True)
class Limits:
    """Bounds on one run.

    ``deadline_s`` applies to each member separately, from the moment it starts; ``members``
    overrides it (and ``grace_s``, ``cleanup_s``) for the aliases it names. ``grace_s`` is how
    long a member has to return after a stop request before it is cancelled; ``cleanup_s`` how
    long a cancelled member, or one deferred release, has to finish before it is abandoned.

    ``on_failure``: ``fast`` (the default) stops every running member and skips the rest as
    soon as one fails; ``isolate`` skips only the members that depend on the failed one and
    lets the others finish. The run ends ``failed`` either way.
    """

    concurrency: int = 1
    deadline_s: float | None = None
    grace_s: float = 5.0
    cleanup_s: float = 30.0
    on_failure: Literal["fast", "isolate"] = "fast"
    members: Mapping[str, MemberLimits] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ConfigError(f"concurrency must be >= 1, got {self.concurrency}")
        if self.on_failure not in ("fast", "isolate"):
            raise ConfigError(f"on_failure must be 'fast' or 'isolate', got {self.on_failure!r}")
        _check_bounds(self.deadline_s, self.grace_s, self.cleanup_s)

    def of(self, alias: str) -> tuple[float | None, float, float]:
        """``(deadline_s, grace_s, cleanup_s)`` for the member ``alias``."""
        own = self.members.get(alias, MemberLimits())
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
    """Observed state of one execution. Separate from the flow, which a run never changes."""

    run_id: str
    flow: str
    construct: Construct
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended: datetime | None = None
    #: ``{member, kind, message}`` for each member that failed
    failures: list[dict[str, Any]] = field(default_factory=list)
    #: alias -> ``{status, reason?, duration_ms?, abandoned?}``: how each member ended.
    #: ``abandoned`` marks one that ignored cancellation and may still be running.
    members: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: ``{member, status, error}`` per deferred release, in the order they were called.
    #: ``status`` is ``released``, ``failed`` (it raised) or ``abandoned`` (it overran).
    releases: list[dict[str, Any]] = field(default_factory=list)
    episode: int = 0

    def outputs(self, topic: str) -> tuple[Any, ...]:
        """The values produced on ``topic`` in this run."""
        return self.construct.values(topic)


class Executor:
    """Runs compiled flows. Stateless between runs; safe to share.

    ``signals=False`` leaves SIGTERM and SIGINT alone; the caller then owns turning them into
    cancellation (see :mod:`matrix.domain.signals`).
    """

    def __init__(self, observers: Sequence[Observer] = (), *, signals: bool = True) -> None:
        self._observers = tuple(observers)
        self._signals = signals

    async def run(
        self,
        flow: CompiledFlow,
        inputs: Mapping[str, Any] | None = None,
        *,
        limits: Limits | None = None,
        run_id: str | None = None,
        episode: int = 0,
    ) -> Run:
        """Run ``flow`` to a terminal state and return the Run.

        Raises :class:`RunError` when a member failed and :class:`RunCancelled` when the
        calling task was cancelled; both carry the partial Run, and both are raised only after
        every member stopped and every deferred release was called.
        """
        limits = limits or Limits()
        supplied = dict(inputs or {})
        missing = sorted(set(flow.inputs) - set(supplied))
        unknown = sorted(set(supplied) - set(flow.inputs))
        if missing or unknown:
            raise ConfigError(
                f"flow {flow.name!r}: "
                + (f"missing inputs {missing}" if missing else "")
                + ("; " if missing and unknown else "")
                + (f"unknown inputs {unknown}" if unknown else "")
                + f". Declared inputs: {sorted(flow.inputs) or '(none)'}",
                fix="supply exactly the flow's declared inputs",
            )
        strangers = sorted(set(limits.members) - set(flow.members))
        if strangers:
            raise ConfigError(
                f"flow {flow.name!r}: limits name members {strangers} that are not in the flow. "
                f"Members: {sorted(flow.members)}",
                fix="key Limits.members by member alias",
            )
        for topic, value in supplied.items():
            _validate(flow, flow.inputs[topic], value, where=f"input {topic!r}")
        run = Run(
            run_id=run_id or uuid.uuid4().hex,
            flow=flow.name,
            construct=Construct(dict(flow.topics)),
            episode=episode,
        )
        session = _Session(flow, run, limits, self._observers)
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
            alias, cause = session.first
            raise RunError(
                f"flow {flow.name!r} failed at member {alias!r}: {cause}",
                run=run,
                member=alias,
                cause_kind=cause.kind,
            ) from cause
        return run


@dataclass
class _Step:
    """One member's passage through a run."""

    member: CompiledMember
    deadline_s: float | None
    grace_s: float
    cleanup_s: float
    context: RunContext
    hooks: _Hooks
    #: The executor's own stop flag; ``context.stopping`` mirrors it for the component.
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    reason: StopReason | None = None
    running: bool = False
    #: What the member returned, once it passed the output checks. Appended at level end.
    outputs: Mapping[str, Any] | None = None


class _Hooks:
    """What one member's :class:`RunContext` may ask of the run."""

    def __init__(self, session: _Session, alias: str, cleanup_s: float) -> None:
        self._session = session
        self._alias = alias
        self._cleanup_s = cleanup_s
        self.closed = False

    def defer(self, release: Release) -> None:
        if not callable(release):
            raise TypeError(f"defer() takes a callable, got {type(release).__name__}")
        if self.closed:
            logger.warning(
                "member %r called defer() after it ended; the release is ignored", self._alias
            )
            return
        self._session.releases.append((self._alias, release, self._cleanup_s))


class _Session:
    """One run in flight: the levels, the stop path, and the releases owed at the end."""

    def __init__(
        self, flow: CompiledFlow, run: Run, limits: Limits, observers: tuple[Observer, ...]
    ) -> None:
        self._flow = flow
        self._run = run
        self._limits = limits
        self._observers = observers
        self._depends = _dependencies(flow)
        self._steps: dict[str, _Step] = {}
        #: Why nothing more may start (a skip reason), once something stopped the run.
        self._halted: str | None = None
        self._cancelled = False
        self.releases: list[tuple[str, Release, float]] = []
        #: The first failure, which :class:`RunError` reports as the cause.
        self.first: tuple[str, MatrixError] | None = None

    def cancel(self) -> None:
        """Ask the run to stop: every running member is stop-requested, the rest skipped."""
        self._cancelled = True
        self._halt("cancelled", "cancelled")

    async def execute(self, supplied: Mapping[str, Any]) -> None:
        flow, run = self._flow, self._run
        self._emit("run.start", run.run_id, None, {"flow": flow.name})
        try:
            for topic, value in supplied.items():
                self._append(topic, flow.inputs[topic], INPUT_PRODUCER, topic, value)
            for level in flow.levels:
                await self._level(level)
        finally:
            # Settled before the releases run: a cancellation that arrives while they do
            # still reaches the caller, but it did not stop any member.
            if self._cancelled:
                status: Literal["completed", "failed", "cancelled"] = "cancelled"
            else:
                status = "failed" if run.failures else "completed"
            await self._release()
            run.status = status
            run.ended = datetime.now(UTC)
            self._emit(
                "run.end",
                run.run_id,
                None,
                {"flow": flow.name, "status": run.status, "artifacts": len(run.construct)},
            )

    async def _level(self, level: tuple[str, ...]) -> None:
        steps: list[_Step] = []
        for alias in level:
            blocked = next(
                (d for d in self._depends[alias] if self._run.members[d]["status"] != "completed"),
                None,
            )
            if self._halted is not None:
                self._skip(alias, self._halted)
            elif blocked is not None:
                self._skip(alias, f"upstream: {blocked}")
            else:
                steps.append(self._step(alias))
        if not steps:
            return
        gate = asyncio.Semaphore(self._limits.concurrency)

        async def one(step: _Step) -> None:
            async with gate:
                if self._halted is not None:
                    self._skip(step.member.alias, self._halted)
                else:
                    await self._drive(step)

        await asyncio.gather(*(one(step) for step in steps))
        for step in steps:
            member = step.member
            for port_name, value in (step.outputs or {}).items():
                topic = member.bindings.get(port_name)
                if topic is not None:
                    self._append(
                        topic, member.provides[port_name].type_url, member.alias, port_name, value
                    )

    def _step(self, alias: str) -> _Step:
        deadline_s, grace_s, cleanup_s = self._limits.of(alias)
        hooks = _Hooks(self, alias, cleanup_s)
        context = RunContext(self._run.run_id, alias, episode=self._run.episode, hooks=hooks)
        step = _Step(self._flow.members[alias], deadline_s, grace_s, cleanup_s, context, hooks)
        self._steps[alias] = step
        return step

    async def _drive(self, step: _Step) -> None:
        """Run one member to a terminal state. Never raises: every ending is recorded."""
        alias = step.member.alias
        span = uuid.uuid4().hex
        self._emit("member.start", span, self._run.run_id, {"member": alias})
        started = time.monotonic()
        abandoned = False
        try:
            status, error, abandoned = await self._attempt(step, span, started)
        except MatrixError as e:
            status, error = "failed", e
        finally:
            step.running = False
            step.hooks.closed = True
        record: dict[str, Any] = {
            "status": status,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        if step.reason is not None:
            record["reason"] = step.reason
        if abandoned:
            record["abandoned"] = True
            logger.warning(
                "member %r ignored cancellation for %ss and was abandoned; it may still be running",
                alias,
                step.cleanup_s,
            )
        self._run.members[alias] = record
        fields = {"member": alias, **record}
        if error is not None:
            fields["kind"] = error.kind
        self._emit("member.end", span, self._run.run_id, fields)
        if error is not None:
            self._run.failures.append({"member": alias, "kind": error.kind, "message": str(error)})
            self.first = self.first or (alias, error)
            if self._limits.on_failure == "fast":
                self._halt("policy: fast", "run_failed")

    async def _attempt(
        self, step: _Step, span: str, started: float
    ) -> tuple[MemberStatus, MatrixError | None, bool]:
        """Inputs in, the stop path around the component, and how it ended."""
        member = step.member
        alias = member.alias
        inputs = self._inputs(step)
        if step.deadline_s is not None:
            step.context.deadline = started + step.deadline_s
        task = asyncio.ensure_future(_call(member, inputs, (self._run.run_id, span)))
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
            f"member {alias!r} timed out after {step.deadline_s}s", member=alias, reason="timeout"
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
                    f"member {alias!r} raised CancelledError without being cancelled",
                    member=alias,
                ),
                False,
            )
        raised = task.exception()
        if isinstance(raised, MatrixError):
            return "failed", raised, False
        if raised is not None:
            failure = ComponentError(
                f"member {alias!r} failed: {type(raised).__name__}: {raised}", member=alias
            )
            failure.__cause__ = raised
            return "failed", failure, False
        result = task.result()
        _check_outputs(self._flow, member, result)
        # Returned in time, or within the grace of a stop request: what it produced is kept.
        step.outputs = result
        if step.reason == "deadline":
            return "failed", overran, False
        return "completed", None, False

    def _inputs(self, step: _Step) -> Inputs:
        member = step.member
        values: dict[str, Any] = {}
        for port_name, port in member.requires.items():
            topic = member.bindings.get(port_name)
            if topic is None:
                continue
            got = self._run.construct.values(topic)
            if not got and not port.many:
                producers = ", ".join(f"{a}.{p}" for a, p in self._flow.producers.get(topic, ()))
                raise ContractError(
                    f"member {member.alias!r} requires {port_name!r} from topic {topic!r}, but "
                    f"its producers ({producers or 'none'}) left it empty",
                    topic=topic,
                    member=member.alias,
                )
            # Each member gets its own copy: a component that mutates what it read cannot
            # change the recorded artifact, nor what a sibling in the same level sees.
            values[port_name] = _private(tuple(got) if port.many else got[-1], topic)
        return Inputs(values, step.context)

    def _request_stop(self, step: _Step, reason: StopReason) -> None:
        if step.reason is not None:
            return
        step.reason = reason
        step.context.stopping.set()
        step.stop.set()

    def _halt(self, why: str, reason: StopReason) -> None:
        """Nothing more starts; everything running is asked to stop. The first cause stands."""
        if self._halted is None:
            self._halted = why
        for step in self._steps.values():
            if step.running:
                self._request_stop(step, reason)

    def _skip(self, alias: str, why: str) -> None:
        self._run.members[alias] = {"status": "skipped", "reason": why}
        self._emit(
            "member.end",
            uuid.uuid4().hex,
            self._run.run_id,
            {"member": alias, "status": "skipped", "reason": why},
        )

    async def _release(self) -> None:
        """Call every deferred release, last registered first, each under its own bound."""
        while self.releases:
            alias, release, bound = self.releases.pop()
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
                logger.warning("a release deferred by member %r %s: %s", alias, status, error)
            self._run.releases.append({"member": alias, "status": status, "error": error})

    def _append(self, topic: str, type_url: str, producer: str, port: str, value: Any) -> None:
        run = self._run
        artifact = Artifact.create(
            run_id=run.run_id,
            topic=topic,
            type_url=type_url,
            producer=producer,
            port=port,
            value=value,
            episode=run.episode,
        )
        run.construct.append(artifact)
        self._emit(
            "artifact.append",
            artifact.id,
            run.run_id,
            {"topic": topic, "type_url": type_url, "producer": producer, "port": port},
        )

    def _emit(self, name: str, span: str, parent: str | None, fields: dict[str, Any]) -> None:
        emit(self._observers, Event(name, span, self._run.run_id, parent, fields))


async def _call(member: CompiledMember, inputs: Inputs, span: tuple[str, str]) -> Any:
    CURRENT_SPAN.set(span)  # this task's own context: an agent it calls reports as its child
    return await member.component.run(inputs)


def _retrieve(task: asyncio.Future[Any]) -> None:
    """Consume a late task's outcome, so asyncio does not log it as never retrieved."""
    if not task.cancelled():
        task.exception()


def _dependencies(flow: CompiledFlow) -> dict[str, tuple[str, ...]]:
    """alias -> the members it waits on (producers of what it reads, and ``after``).

    The one place the executor reads the wiring; everything else works on steps and what
    they depend on.
    """
    depends: dict[str, tuple[str, ...]] = {}
    for alias, member in flow.members.items():
        found: set[str] = set(member.after)
        for port_name in member.requires:
            topic = member.bindings.get(port_name)
            if topic is not None:
                found.update(a for a, _ in flow.producers.get(topic, ()))
        depends[alias] = tuple(a for a in flow.members if a in found)
    return depends


def _private(value: Any, topic: str) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception as e:
        raise ContractError(
            f"the value on topic {topic!r} cannot be copied ({type(e).__name__}: {e}); values "
            "that flow between members must be data, not live handles",
            topic=topic,
        ) from e


def _check_outputs(flow: CompiledFlow, member: CompiledMember, result: Any) -> None:
    if not isinstance(result, Mapping):
        raise ContractError(
            f"member {member.alias!r} returned {type(result).__name__}; components return a "
            f"mapping of provide-port name to value (declared: {sorted(member.provides)})",
            member=member.alias,
        )
    extra = sorted(set(result) - set(member.provides))
    missing = sorted(
        name for name, port in member.provides.items() if name not in result and not port.optional
    )
    if extra or missing:
        raise ContractError(
            f"member {member.alias!r} broke its ports: "
            + ", ".join(
                part
                for part in (
                    f"undeclared {extra}" if extra else "",
                    f"missing {missing}" if missing else "",
                )
                if part
            ),
            member=member.alias,
            declared=sorted(member.provides),
        )
    for name, value in result.items():
        _validate(
            flow,
            member.provides[name].type_url,
            value,
            where=f"member {member.alias!r} port {name!r}",
        )


def _validate(flow: CompiledFlow, type_url: str, value: Any, *, where: str) -> None:
    schema: type[BaseModel] | None = flow.schemas.get(type_url)
    if schema is None or isinstance(value, schema):
        return
    try:
        schema.model_validate(value)
    except ValidationError as e:
        issues = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
            for err in e.errors()
        )
        raise ContractError(
            f"{where}: value is not a valid {type_url}: {issues}", type_url=type_url
        ) from None
