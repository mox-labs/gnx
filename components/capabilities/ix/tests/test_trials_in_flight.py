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

import matrix
import pytest
import yaml
from ix_fakes import FakeSubject, FakeTiming, experiment, fake_registry
from matrix import TASK, Executor, RunContext, Session, digest, record_session, wire

import ix
from ix.application import running
from ix.composition import Root
from ix.composition.settings import IxSettings
from ix.domain import schema

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ix.domain.ports import Probe
    from ix.domain.problem import Problem
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


# --- the width: where it is written and what it is held to -----------------------------


def _canned(tmp_path: Path, **changes: Any) -> Path:
    """An ``evaluate`` of an agent on matrix's mock runtime, every reply canned: it calls no
    model, and every kind it uses is ``shared``."""
    data: dict[str, Any] = {
        "name": "canned",
        "question": "Does the reply start with BANANA?",
        "genre": "evaluate",
        "subjects": {"agent": {"kind": "agent-session", "runtime": "mock"}},
        "probes": [
            {"id": "fruit", "kind": "prompt", "prompt": "Name a fruit.",
             "mock_response": "BANANA bread", "truth": {"starts_with": "BANANA"}},
            {"id": "colour", "kind": "prompt", "prompt": "Name a colour.",
             "mock_response": "teal", "truth": {"starts_with": "BANANA"}},
        ],
        "primary": {"metric": "text_matched"},
        "threshold": {"op": ">=", "value": 0.5},
        "repeats": 5,
        **changes,
    }  # fmt: skip
    folder = tmp_path / "canned"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return folder


def _root() -> Root:
    return Root(settings=matrix.Config[IxSettings](client=IxSettings()))


class TestTheWidthIsAConditionOfTheRun:
    """X1/X2: ``trials_in_flight`` is a value of the design, 1 unless the experiment says
    otherwise, in the plan's digest and frozen by registration."""

    def test_one_written_out_is_the_default_and_digests_as_omitting_it(
        self, tmp_path: Path
    ) -> None:
        omitted = ix.plan(_canned(tmp_path / "a"))
        written = ix.plan(_canned(tmp_path / "b", trials_in_flight=1))
        assert omitted.digest == written.digest
        assert omitted.experiment_digest == written.experiment_digest
        (sourced,) = [v for v in omitted.provenance if v.path == "trials_in_flight"]
        assert (sourced.value, sourced.source) == (1, "ix")
        (sourced,) = [v for v in written.provenance if v.path == "trials_in_flight"]
        assert (sourced.value, sourced.source) == (1, "experiment")
        assert omitted.order.trials_in_flight == 1
        assert "one at a time" in omitted.order.description

    def test_a_width_is_in_the_digest_the_plan_and_the_registration(self, tmp_path: Path) -> None:
        folder = _canned(tmp_path, trials_in_flight=4)
        plan = ix.plan(folder)
        assert plan.digest != ix.plan(_canned(tmp_path / "one")).digest
        assert plan.order.trials_in_flight == 4
        assert "4 trials in flight" in plan.order.description
        ix.register(folder)
        _canned(tmp_path, trials_in_flight=2)
        (moved,) = [d for d in ix.deviations(folder).deviations if d.field.startswith("design")]
        assert (moved.field, moved.registered, moved.current) == (
            "design.trials_in_flight", 4, 2,
        )  # fmt: skip

    def test_a_width_below_one_is_refused(self, tmp_path: Path) -> None:
        result = ix.validate(_canned(tmp_path, trials_in_flight=0))
        assert not result.valid
        assert [p.path for p in result.problems] == ["trials_in_flight"]

    def test_the_command_line_width_is_an_override_a_verdict_refuses(self, tmp_path: Path) -> None:
        folder = _canned(tmp_path)
        report = _root().run(folder, exploratory=True, trials_in_flight=3)
        assert report.trials_in_flight == 3
        assert report.overridden == ("trials_in_flight: 3 in place of 1 (--trials-in-flight)",)
        ix.register(folder)
        with pytest.raises(ix.ConfigError, match="changed for this run") as refused:
            _root().run(folder, trials_in_flight=3)
        assert "exploratory" in (refused.value.fix or "")
        assert _root().run(folder).trials_in_flight == 1
        with pytest.raises(ix.UsageError) as below:
            _root().run(folder, exploratory=True, trials_in_flight=0)
        assert below.value.details == {"path": "--trials-in-flight", "value": 0}


def _refusal(found: ix.Validation) -> Problem:
    (refused,) = [p for p in found.problems if p.kind == "trials_in_flight_refused"]
    assert (refused.path, refused.got) == ("trials_in_flight", 2)
    assert refused.fix.startswith("trials_in_flight: 1")
    return refused


class TestOverlapIsRefusedWhereItWouldConfound:
    """X5: a width above one is a validation problem, never a clamp, when a kind in use is
    ``exclusive``, a deciding metric is ``contended`` or the genre is ``exclusive``; its fix
    names each and says ``trials_in_flight: 1``."""

    def test_shared_kinds_and_uncontended_decisions_may_overlap(self, tmp_path: Path) -> None:
        found = ix.validate(_canned(tmp_path, trials_in_flight=4))
        assert found.valid and found.warnings == ()

    def test_each_exclusive_kind_and_contended_decision_is_named(
        self, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment(trials_in_flight=2))
        refused = _refusal(ix.validate(file))
        for named in ("subject kind process", "probe kind http-load", "sensor timing",
                      "latency (primary)"):  # fmt: skip
            assert named in refused.message, named
            assert named.split(" (")[0].rsplit(" ", 1)[-1] in refused.fix
        assert ix.validate(write_experiment(experiment())).valid  # one at a time: as ever

    def test_a_gates_genre_keeps_its_trials_apart(self, tmp_path: Path) -> None:
        subjects = {
            "baseline": {"kind": "agent-session", "runtime": "mock"},
            "candidate": {"kind": "agent-session", "runtime": "mock", "max_turns": 2},
        }
        gate = _canned(
            tmp_path, genre="regress", subjects=subjects, threshold=None, margin="10pp",
            trials_in_flight=2,
        )  # fmt: skip
        refused = _refusal(ix.validate(gate))
        assert "genre regress" in refused.message and "regress" in refused.fix

    def test_a_contended_metric_decides_nothing_in_flight(self, tmp_path: Path) -> None:
        timed = _canned(
            tmp_path, primary={"metric": "duration"}, threshold={"op": "<=", "value": 5000},
            trials_in_flight=2,
        )  # fmt: skip
        refused = _refusal(ix.validate(timed))
        assert "duration (primary)" in refused.message

    def test_a_contended_metric_only_reported_is_warned_of(self, tmp_path: Path) -> None:
        found = ix.validate(_canned(tmp_path, metrics=["duration"], trials_in_flight=2))
        assert found.valid
        (warned,) = found.warnings
        assert (warned.kind, warned.path) == ("trials_in_flight_contended", "trials_in_flight")
        assert "duration" in warned.message

    def test_the_command_lines_width_is_held_to_the_same_rule(
        self, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment())
        with pytest.raises(ix.InvalidExperimentError) as refused:
            Root(registry=fake_registry()).run(file, exploratory=True, trials_in_flight=2)
        assert refused.value.exit_code == 3
        assert [p.kind for p in refused.value.problems] == ["trials_in_flight_refused"]
