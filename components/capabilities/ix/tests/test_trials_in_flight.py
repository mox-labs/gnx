"""Trials in flight: what a run that carries out several trials at once is held to.

Each test names the ruling it holds (``docs/design/2026-10-19-record.md`` §27): the sessions
behind each trial are that trial's own (I6), and the readings and the analysis do not
depend on the order trials finished in.

Nothing here calls a model: a session is recorded with matrix as a runtime records one.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any

import yaml
from ix_fakes import FakeSubject, FakeTiming, experiment, fake_registry
from matrix import TASK, Executor, RunContext, Session, digest, record_session, wire

import ix
from ix.application import running
from ix.composition import Root
from ix.domain import schema

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ix.domain.ports import Probe
    from ix.domain.run import Interaction, Target

Write = Any


def _prepared(file: Path, **changes: Any) -> running.Prepared:
    """The run ``file`` would be, exploratory, as the root prepares it."""
    root = Root(registry=fake_registry())
    vocabulary = root.vocabulary(file.parent)
    data = {**yaml.safe_load(file.read_text()), **changes}
    examined = ix.application.experiments.examine(data, vocabulary)
    return running.prepare(
        examined, vocabulary, None, run="r", base=str(file.parent), now="", version="",
        exploratory=True,
    )  # fmt: skip


class _Answering:
    """A load probe whose every application makes two fake sessions, each recorded with
    matrix as a runtime records one and labelled by the trial's seed, with seeded waits
    between them: the sessions of trials in flight interleave."""

    def budget_s(self, options: Mapping[str, Any]) -> float | None:
        return 30.0

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        wait = random.Random(seed)
        for turn in range(2):
            await asyncio.sleep(wait.uniform(0, 0.01))
            record_session(Session(port="agent-runtime", agent=f"{seed}/{turn}", sent=digest(seed)))
        return {"load": {}}


def _kit(probe: Probe) -> running.Kit:
    return running.Kit(
        subjects={schema.url("subject", "process"): FakeSubject()},
        probes={schema.url("probe", "http-load"): probe},
        sensors={schema.url("sensor", "timing"): FakeTiming()},
        environments={},
        clock_ns=lambda: 0,
    )


class TestEachTrialNamesItsOwnSessions:
    """I6: an interaction names exactly the sessions made in its own trial's task, however
    the trials in flight interleave."""

    def test_trials_invoked_side_by_side_each_name_exactly_their_own(
        self, write_experiment: Write
    ) -> None:
        prepared = _prepared(write_experiment(experiment()))
        setup = running.setup_of(prepared.manifest)
        trials = prepared.trials[:6]
        landed: list[Interaction] = []

        class _SideBySide:
            requires = frozenset({TASK})
            provides = frozenset({schema.INTERACTION})

            async def run(self, inputs: Any, ctx: RunContext) -> None:
                kit = _kit(_Answering())
                async with asyncio.TaskGroup() as group:
                    running_now = [
                        group.create_task(running.invoke(trial, setup, kit, ctx))
                        for trial in trials
                    ]
                for task in running_now:
                    interaction = task.result()
                    landed.append(interaction)
                    ctx.provide(schema.INTERACTION, interaction, sessions=interaction.sessions)

        wiring = wire({"trials": _SideBySide()}, inputs=[TASK])
        run = asyncio.run(Executor().run(wiring, {TASK: "go"}))
        sessions = run.rows("matrix.v1.session")
        by_seq = {trial.seq: trial.seed for trial in trials}
        for interaction in landed:
            labels = [sessions[row].agent for row in interaction.sessions]
            assert labels == [f"{by_seq[interaction.seq]}/{turn}" for turn in range(2)]
        named = [row for interaction in landed for row in interaction.sessions]
        assert sorted(named) == list(range(len(sessions))) == list(range(2 * len(trials)))
        ledger = {e.row: e.sessions for e in run.construct.provenance(schema.INTERACTION)}
        assert [ledger[row] for row in range(len(landed))] == [i.sessions for i in landed]

    def test_a_trial_carried_out_alone_names_its_sessions_as_before(
        self, write_experiment: Write
    ) -> None:
        """At one trial at a time (the default), the sessions each trial names are the ones
        a serial run always named: every session, once, in order."""
        prepared = _prepared(write_experiment(experiment()))
        setup = running.setup_of(prepared.manifest)
        trials = prepared.trials[:4]
        landed: list[Interaction] = []

        class _OneByOne:
            requires = frozenset({TASK})
            provides = frozenset({schema.INTERACTION})

            async def run(self, inputs: Any, ctx: RunContext) -> None:
                for trial in trials:
                    interaction = await running.invoke(trial, setup, _kit(_Answering()), ctx)
                    landed.append(interaction)

        asyncio.run(Executor().run(wire({"trials": _OneByOne()}, inputs=[TASK]), {TASK: "go"}))
        assert [i.sessions for i in landed] == [(0, 1), (2, 3), (4, 5), (6, 7)]
