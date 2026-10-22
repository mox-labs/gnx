"""ix's run path as three matrix components, wired by the kinds they exchange.

matrix drives ix through them: ``environment`` provides ``ix.v1.bindings``; ``trials``
requires them and provides ``ix.v1.interaction``, one row as each trial finishes, so a
killed run keeps every trial it finished; ``sense`` requires the interactions and provides
``ix.v1.reading``. ``ix.v1.run`` (the manifest) and ``ix.v1.trial`` (the schedule) are run
inputs. Each component needs :data:`NEED`, the :class:`~ix.application.running.Kit` of
adapters the run uses, and passes its matrix context on as the run's scope, traced by the
kit's tracer when it has one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ix.application import running
from ix.domain import schema
from ix.domain.run import BindingsRow, Interaction, Reading, RunManifest, Trial

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from contextlib import AbstractContextManager

    from matrix import Inputs, RunContext

    from ix.application.running import Kit
    from ix.domain.ports import Flag, Scope, Span, Traced
    from ix.domain.schema import Stored

ENVIRONMENT = "ix.v1.component.environment"
TRIALS = "ix.v1.component.trials"
SENSE = "ix.v1.component.sense"

#: The need the three components declare: the adapters of the run.
NEED = "ix_kit"

#: One run of an experiment, as matrix composes it: the inputs it is given and the
#: components that carry it out.
COMPOSITION: dict[str, Any] = {
    "inputs": [schema.RUN, schema.TRIAL],
    "components": [
        {"name": "environment", "type": ENVIRONMENT},
        {"name": "trials", "type": TRIALS},
        {"name": "sense", "type": SENSE},
    ],
}

#: The row schema of each kind a run records.
PAYLOADS: dict[str, type[Stored]] = {
    schema.RUN: RunManifest,
    schema.TRIAL: Trial,
    schema.BINDINGS: BindingsRow,
    schema.INTERACTION: Interaction,
    schema.READING: Reading,
}


class _TracedScope:
    """A matrix context as the run's scope, with ix's own spans from ``tracer``."""

    def __init__(self, ctx: RunContext, tracer: Traced) -> None:
        self._ctx = ctx
        self._tracer = tracer

    @property
    def stopping(self) -> Flag:
        return self._ctx.stopping

    def defer(self, release: Callable[[], Awaitable[None] | None], /) -> None:
        self._ctx.defer(release)

    def emit(self, name: str, /, **fields: Any) -> None:
        self._ctx.emit(name, **fields)

    def trace(self, name: str, /, **attributes: Any) -> AbstractContextManager[Span]:
        return self._tracer.trace(name, **attributes)


def _scope(ctx: RunContext, kit: Kit) -> Scope:
    return ctx if kit.tracer is None else _TracedScope(ctx, kit.tracer)


def _setup(inputs: Inputs) -> running.Setup:
    return running.setup_of(RunManifest.model_validate(inputs.one(schema.RUN)))


def _trials(inputs: Inputs) -> list[Trial]:
    return [Trial.model_validate(row) for row in inputs[schema.TRIAL]]


class EnvironmentComponent:
    """Brings the experiment's environment up, once, and provides how to reach it."""

    requires = (schema.RUN,)
    provides = (schema.BINDINGS,)

    def __init__(self, kit: Kit) -> None:
        self._kit = kit

    async def run(self, inputs: Inputs, ctx: RunContext) -> Mapping[str, Any]:
        bindings = await running.bring_up(_setup(inputs), self._kit, ctx)
        return {schema.BINDINGS: BindingsRow(services=bindings)}


class TrialsComponent:
    """Carries out the schedule, one trial at a time, in order."""

    requires = (schema.RUN, schema.TRIAL, schema.BINDINGS)
    provides = (schema.INTERACTION,)

    def __init__(self, kit: Kit) -> None:
        self._kit = kit

    async def run(self, inputs: Inputs, ctx: RunContext) -> None:
        bindings = BindingsRow.model_validate(inputs.one(schema.BINDINGS)).services
        await running.carry_out(
            _setup(inputs),
            self._kit,
            trials=_trials(inputs),
            bindings=bindings,
            scope=_scope(ctx, self._kit),
            land=lambda interaction: ctx.provide(schema.INTERACTION, interaction),
        )


class SenseComponent:
    """Turns the interactions into readings."""

    requires = (schema.RUN, schema.TRIAL, schema.INTERACTION)
    provides = (schema.READING,)

    def __init__(self, kit: Kit) -> None:
        self._kit = kit

    async def run(self, inputs: Inputs, ctx: RunContext) -> Mapping[str, Any]:
        interactions = [Interaction.model_validate(row) for row in inputs[schema.INTERACTION]]
        readings = await running.sense(
            _setup(inputs),
            self._kit,
            trials=_trials(inputs),
            interactions=interactions,
            scope=_scope(ctx, self._kit),
        )
        return {schema.READING: readings}


def _environment(*, ix_kit: Kit) -> EnvironmentComponent:
    return EnvironmentComponent(ix_kit)


def _carry_out(*, ix_kit: Kit) -> TrialsComponent:
    return TrialsComponent(ix_kit)


def _sense(*, ix_kit: Kit) -> SenseComponent:
    return SenseComponent(ix_kit)


#: The three components by type URL: how each is built and what it does. Their effects are
#: whatever the kinds of the experiment being run declare, so none is declared: unknown,
#: never "none".
COMPONENTS: dict[str, tuple[Callable[..., object], str]] = {
    ENVIRONMENT: (
        _environment,
        "Brings an experiment's environment up for the run; provides ix.v1.bindings",
    ),
    TRIALS: (
        _carry_out,
        "Carries out an experiment's trials in schedule order; provides ix.v1.interaction, "
        "one row as each trial finishes",
    ),
    SENSE: (_sense, "Reads each interaction with the experiment's sensors; provides ix.v1.reading"),
}
