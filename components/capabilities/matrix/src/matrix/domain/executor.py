"""The executor: run a compiled flow with inputs, and record what happened as a :class:`Run`.

Members run level by level (each level depends only on earlier ones). Within a level, up to
``Limits.concurrency`` members run at once. The executor is the Construct's only writer, and
it appends a level's artifacts in declaration order, so a run's Construct reads the same
however it was scheduled.

Each member gets :class:`~matrix.domain.flow.Inputs`, only what it bound. Its result must
hold exactly its declared provide ports (optional ones may be missing), and each value must
validate against its type's payload schema when one is registered; otherwise the member broke
its contract.

A run either **completes**, or **fails**: the first failing member's level is allowed to
finish (work already paid for is kept), then :class:`RunError` is raised carrying the
partial Run, with everything produced so far.
"""

from __future__ import annotations

import asyncio
import copy
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
    RunError,
)
from matrix.domain.flow import CompiledFlow, CompiledMember, Inputs, RunContext
from matrix.domain.observer import CURRENT_SPAN, Event, Observer, emit


@dataclass(frozen=True)
class Limits:
    """Bounds on one run. ``member_timeout_s`` applies to each member separately."""

    concurrency: int = 1
    member_timeout_s: float | None = None

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ConfigError(f"concurrency must be >= 1, got {self.concurrency}")
        if self.member_timeout_s is not None and self.member_timeout_s <= 0:
            raise ConfigError(f"member_timeout_s must be > 0, got {self.member_timeout_s}")


@dataclass
class Run:
    """Observed state of one execution. Separate from the flow, which a run never changes."""

    run_id: str
    flow: str
    construct: Construct
    status: Literal["running", "completed", "failed"] = "running"
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended: datetime | None = None
    #: ``{member, kind, message}`` for each member that failed
    failures: list[dict[str, Any]] = field(default_factory=list)
    episode: int = 0

    def outputs(self, topic: str) -> tuple[Any, ...]:
        """The values produced on ``topic`` in this run."""
        return self.construct.values(topic)


class Executor:
    """Runs compiled flows. Stateless between runs; safe to share."""

    def __init__(self, observers: Sequence[Observer] = ()) -> None:
        self._observers = tuple(observers)

    async def run(
        self,
        flow: CompiledFlow,
        inputs: Mapping[str, Any] | None = None,
        *,
        limits: Limits | None = None,
        run_id: str | None = None,
        episode: int = 0,
    ) -> Run:
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
        run = Run(
            run_id=run_id or uuid.uuid4().hex,
            flow=flow.name,
            construct=Construct(dict(flow.topics)),
            episode=episode,
        )
        emit(
            self._observers, Event("run.start", run.run_id, run.run_id, fields={"flow": flow.name})
        )
        try:
            for topic, value in supplied.items():
                type_url = flow.inputs[topic]
                _validate(flow, type_url, value, where=f"input {topic!r}")
                self._append(run, topic, type_url, INPUT_PRODUCER, topic, value)
            for level in flow.levels:
                await self._run_level(flow, level, run, limits)
            run.status = "completed"
        except BaseException:
            run.status = "failed"
            raise
        finally:
            run.ended = datetime.now(UTC)
            emit(
                self._observers,
                Event(
                    "run.end",
                    run.run_id,
                    run.run_id,
                    fields={
                        "flow": flow.name,
                        "status": run.status,
                        "artifacts": len(run.construct),
                    },
                ),
            )
        return run

    async def _run_level(
        self, flow: CompiledFlow, level: tuple[str, ...], run: Run, limits: Limits
    ) -> None:
        gate = asyncio.Semaphore(limits.concurrency)

        async def one(alias: str) -> Mapping[str, Any]:
            async with gate:
                return await self._invoke(flow, flow.members[alias], run, limits)

        results = await asyncio.gather(*(one(a) for a in level), return_exceptions=True)
        first: tuple[str, BaseException] | None = None
        for alias, result in zip(level, results, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result  # cancellation and friends propagate untouched
                error = result if isinstance(result, MatrixError) else None
                run.failures.append(
                    {
                        "member": alias,
                        "kind": error.kind if error else "component",
                        "message": str(result),
                    }
                )
                first = first or (alias, result)
                continue
            member = flow.members[alias]
            for port_name, value in result.items():
                topic = member.bindings.get(port_name)
                if topic is not None:
                    self._append(
                        run, topic, member.provides[port_name].type_url, alias, port_name, value
                    )
        if first is not None:
            alias, cause = first
            run.status = "failed"
            kind = cause.kind if isinstance(cause, MatrixError) else "component"
            raise RunError(
                f"flow {flow.name!r} failed at member {alias!r}: {cause}",
                run=run,
                member=alias,
                cause_kind=kind,
            ) from cause

    async def _invoke(
        self, flow: CompiledFlow, member: CompiledMember, run: Run, limits: Limits
    ) -> Mapping[str, Any]:
        values: dict[str, Any] = {}
        for port_name, port in member.requires.items():
            topic = member.bindings.get(port_name)
            if topic is None:
                continue
            got = run.construct.values(topic)
            if not got and not port.many:
                producers = ", ".join(f"{a}.{p}" for a, p in flow.producers.get(topic, ()))
                raise ContractError(
                    f"member {member.alias!r} requires {port_name!r} from topic {topic!r}, but "
                    f"its producers ({producers or 'none'}) left it empty",
                    topic=topic,
                    member=member.alias,
                )
            # Each member gets its own copy: a component that mutates what it read cannot
            # change the recorded artifact, nor what a sibling in the same level sees.
            values[port_name] = _private(tuple(got) if port.many else got[-1], topic)
        deadline = time.monotonic() + limits.member_timeout_s if limits.member_timeout_s else None
        inputs = Inputs(
            values, RunContext(run.run_id, member.alias, episode=run.episode, deadline=deadline)
        )
        span = uuid.uuid4().hex
        emit(
            self._observers,
            Event("member.start", span, run.run_id, run.run_id, {"member": member.alias}),
        )
        started = time.monotonic()
        status = "ok"
        token = CURRENT_SPAN.set((run.run_id, span))
        try:
            try:
                async with asyncio.timeout(limits.member_timeout_s):
                    result = await member.component.run(inputs)
            except MatrixError:
                raise
            except TimeoutError as e:
                raise ComponentError(
                    f"member {member.alias!r} timed out after {limits.member_timeout_s}s",
                    member=member.alias,
                    reason="timeout",
                ) from e
            except Exception as e:
                raise ComponentError(
                    f"member {member.alias!r} failed: {type(e).__name__}: {e}",
                    member=member.alias,
                ) from e
            _check_outputs(flow, member, result)
            return result
        except Exception as e:
            status = e.kind if isinstance(e, MatrixError) else "component"
            raise
        finally:
            CURRENT_SPAN.reset(token)
            emit(
                self._observers,
                Event(
                    "member.end",
                    span,
                    run.run_id,
                    run.run_id,
                    {
                        "member": member.alias,
                        "status": status,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                ),
            )

    def _append(
        self, run: Run, topic: str, type_url: str, producer: str, port: str, value: Any
    ) -> None:
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
        emit(
            self._observers,
            Event(
                "artifact.append",
                artifact.id,
                run.run_id,
                run.run_id,
                {"topic": topic, "type_url": type_url, "producer": producer, "port": port},
            ),
        )


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
