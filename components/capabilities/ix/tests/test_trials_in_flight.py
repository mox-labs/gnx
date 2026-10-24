"""Trials in flight: what a run that carries out several trials at once is held to.

Each test names the ruling it holds (``docs/design/2026-10-19-record.md`` §27): the sessions
behind each trial are that trial's own (I6), and the readings and the analysis do not
depend on the order trials finished in.

Nothing here calls a model: a session is recorded with matrix as a runtime records one.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import json
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix
import pytest
import yaml
from ix_fakes import LOG, FakeSubject, FakeTiming, experiment, fake_registry
from matrix import (
    TASK,
    Executor,
    JsonlConstructStore,
    Limits,
    MatrixConfig,
    RunContext,
    Session,
    compose,
    digest,
    record_session,
    wire,
)

import ix
from ix.adapters._in.matrix_components import COMPOSITION, NEED
from ix.adapters._out import matrix_runs
from ix.adapters._out.environments import NoEnvironment
from ix.application import running
from ix.composition import Root
from ix.composition.settings import IxSettings
from ix.domain import schema
from ix.domain.report import report as build_report
from ix.domain.run import HarnessError, Interaction, Reading, Target

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from ix.domain.kind import Kinds
    from ix.domain.ports import Environment, Probe
    from ix.domain.problem import Problem
    from ix.domain.run import Trial

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
    model, and every kind it uses allows overlap."""
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
    return _refusal_at(found, 2)


def _refusal_at(found: ix.Validation, width: int) -> Problem:
    (refused,) = [p for p in found.problems if p.kind == "trials_in_flight_refused"]
    assert (refused.path, refused.got) == ("trials_in_flight", width)
    assert refused.fix.startswith("trials_in_flight: 1")
    return refused


class TestOverlapIsRefusedWhereItWouldConfound:
    """X5: a width above one is a validation problem, never a clamp, when a kind in use is
    ``overlap: forbidden``, a deciding metric is ``contended`` or the genre forbids it; its fix
    names each and says ``trials_in_flight: 1``."""

    def test_shared_kinds_and_uncontended_decisions_may_overlap(self, tmp_path: Path) -> None:
        found = ix.validate(_canned(tmp_path, trials_in_flight=4))
        assert found.valid and found.warnings == ()

    def test_each_kind_that_forbids_overlap_and_contended_decision_is_named(
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

    def test_a_metric_declared_contended_after_registration_is_refused_at_run(
        self, tmp_path: Path
    ) -> None:
        """``contended`` is in no digest and no registration, so leaving it out must fail
        closed (review, ixian): an experiment registered at width 2 on a metric its sensor
        then declares contended is refused when it is run, gated or not, though its
        registration holds nothing that changed."""
        sensor = {
            "name": "lab.v1.sensor.waiting", "summary": "How long the reply took.",
            "needs": ["response"], "overlap": "allowed",
            "metrics": [{"name": "wait", "unit": "ms", "direction": "lower", "shape": "scalar"}],
        }  # fmt: skip
        folder = _canned(
            tmp_path, primary={"metric": "wait"}, threshold={"op": "<=", "value": 1000},
            metrics=["text_matched"], trials_in_flight=2,
        )  # fmt: skip
        (folder / "kinds").mkdir()
        (folder / "kinds" / "waiting.yaml").write_text(yaml.safe_dump(sensor))
        assert ix.validate(folder).valid
        ix.register(folder)
        sensor["metrics"][0]["contended"] = True  # type: ignore[index]
        (folder / "kinds" / "waiting.yaml").write_text(yaml.safe_dump(sensor))
        assert "contended" not in (folder / "experiment.registration.json").read_text()
        for exploratory in (False, True):
            with pytest.raises(ix.InvalidExperimentError) as refused:
                _root().run(folder, exploratory=exploratory)
            (problem,) = [p for p in refused.value.problems if p.kind != "kind_unimplemented"]
            assert problem.kind == "trials_in_flight_refused"
            assert "wait (primary) is contended" in problem.message

    def test_a_third_partys_kind_declares_both_as_a_built_in_does(self, tmp_path: Path) -> None:
        """ACES, extensible: a kind file of a lab (read as a package's ``ix.kind`` files
        are) declares ``overlap`` and ``contended`` and is held to them; one that says
        nothing of its overlap forbids it."""
        sensor = {
            "name": "lab.v1.sensor.waiting", "summary": "How long the reply took.",
            "needs": ["response"], "overlap": "allowed",
            "metrics": [{"name": "wait", "unit": "ms", "direction": "lower",
                         "shape": "scalar", "contended": True}],
        }  # fmt: skip
        silent = {**sensor, "name": "lab.v1.sensor.silent", "overlap": None}
        silent = {key: value for key, value in silent.items() if value is not None}
        silent["metrics"] = [{"name": "quiet", "direction": "lower", "shape": "scalar"}]
        folder = _canned(tmp_path, metrics=["wait"], trials_in_flight=2)
        (folder / "kinds").mkdir()
        (folder / "kinds" / "waiting.yaml").write_text(yaml.safe_dump(sensor))
        (folder / "kinds" / "silent.yaml").write_text(yaml.safe_dump(silent))
        found = ix.validate(folder)
        assert found.valid
        warned = [w for w in found.warnings if w.kind.startswith("trials_in_flight")]
        assert [(w.kind, "wait" in w.message) for w in warned] == [
            ("trials_in_flight_contended", True)
        ]  # and kind_unimplemented: a declaration with no adapter here
        _canned(tmp_path, metrics=["wait", "quiet"], trials_in_flight=2)
        assert "sensor lab.v1.sensor.silent" in _refusal(ix.validate(folder)).message

    def test_the_command_lines_width_is_held_to_the_same_rule(
        self, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment())
        with pytest.raises(ix.InvalidExperimentError) as refused:
            Root(registry=fake_registry()).run(file, exploratory=True, trials_in_flight=2)
        assert refused.value.exit_code == 3
        assert [p.kind for p in refused.value.problems] == ["trials_in_flight_refused"]


# --- dispatch: what carry_out is held to (X6, X7) ---------------------------------------
#
# The stubs log what happened, in one list, in the order it happened: a trial's dispatch
# (``ix.trial.start``), its probe beginning (``applying``), its subject released, the last
# thing a trial does (``released``), each liveness check's beginning and end, and each
# landing. Order in that list is the instrument; wall-clock time is not.

type Log = list[tuple[Any, ...]]


class _Scope:
    """A scope that logs the events a run emits and keeps what it defers."""

    run_id = "r"

    def __init__(self, log: Log) -> None:
        self.stopping = asyncio.Event()
        self.log = log
        self.releases: list[Any] = []

    def defer(self, release: Any, /) -> None:
        self.releases.append(release)

    def emit(self, name: str, /, **fields: Any) -> None:
        self.log.append((name, fields.get("seq")))

    async def release(self) -> None:
        for release in reversed(self.releases):
            done = release()
            if done is not None:
                await done


class _Subject:
    """Counts the trials holding a subject; logs each release with its trial's ``seq``,
    which the probe writes into the target. A release takes ``release_s``; the one of the
    trial ``fails`` raises once it has let go."""

    def __init__(self, log: Log, release_s: float = 0.0, fails: int | None = None) -> None:
        self.log = log
        self.holding = 0
        self.most = 0
        self.release_s = release_s
        self.fails = fails

    @asynccontextmanager
    async def reach(self, name: str, subject: Any, *, base: str) -> AsyncIterator[Target]:
        self.holding += 1
        self.most = max(self.most, self.holding)
        target = Target(subject=name)
        try:
            yield target
        finally:
            if self.release_s:
                await asyncio.sleep(self.release_s)
            self.holding -= 1
            seq = target.facets.get("seq")
            self.log.append(("released", seq))
            if seq is not None and seq == self.fails:
                raise RuntimeError(f"the subject of trial {seq} would not stop")


class _Stuck:
    """A sensor whose watch never begins and never looks at the stop flag: a stop is not
    honoured, so matrix cuts the trials off after its grace."""

    def watch(self, target: Target, options: Any) -> Any:
        @asynccontextmanager
        async def watching() -> AsyncIterator[dict[str, Any]]:
            await asyncio.Event().wait()
            yield {}

        return watching()

    async def sense(self, interaction: Any, *, truth: Any, options: Any) -> Any:
        return {}


class _Probe:
    """Sleeps a while drawn from the trial's seed; the trials in ``held`` wait for
    ``hold`` too (forever, unless it is set)."""

    def __init__(self, log: Log, seqs: Mapping[int, int], held: frozenset[int] = frozenset()):
        self.log = log
        self.seqs = seqs
        self.held = held
        self.hold = asyncio.Event()

    def budget_s(self, options: Mapping[str, Any]) -> float | None:
        return 30.0

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        seq = self.seqs[seed]
        target.facets["seq"] = seq
        self.log.append(("applying", seq))
        await asyncio.sleep(random.Random(seed).uniform(0, 0.01))
        if seq in self.held:
            await self.hold.wait()
        return {"load": {"seq": seq}}


class _Live:
    """An environment that answers whether it is up after a short wait: lost from its
    ``lose_on``-th check on, and never answering when ``hang``."""

    def __init__(self, log: Log, *, lose_on: int | None = None, hang: bool = False) -> None:
        self.log = log
        self.lose_on = lose_on
        self.hang = hang
        self.checks = 0

    async def up(self, environment: Any, *, base: str, scope: Any) -> Any:
        return {}

    async def lost(self, bindings: Any) -> str | None:
        self.checks += 1
        check = self.checks
        self.log.append(("check", check))
        if self.hang:
            await asyncio.Event().wait()
        await asyncio.sleep(0.002)
        found = "gone" if self.lose_on is not None and check >= self.lose_on else None
        self.log.append(("checked", check, found))
        return found


@dataclass
class _Dispatch:
    """One carry_out over the fake experiment's trials at ``width``."""

    width: int
    log: Log = field(default_factory=list)
    landed: list[Interaction] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.scope = _Scope(self.log)
        self.subject = _Subject(self.log)

    def setup(self, write: Write, repeats: int = 5) -> tuple[running.Setup, tuple[Trial, ...]]:
        prepared = _prepared(write(experiment(repeats=repeats)))
        setup = running.setup_of(prepared.manifest)
        widened = setup.design.model_copy(update={"trials_in_flight": self.width})
        return dataclasses.replace(setup, design=widened), prepared.trials

    def kit(self, probe: _Probe, environment: Environment, timeout_s: float = 30.0) -> running.Kit:
        return running.Kit(
            subjects={schema.url("subject", "process"): self.subject},
            probes={schema.url("probe", "http-load"): probe},
            sensors={schema.url("sensor", "timing"): FakeTiming()},
            environments={schema.url("environment", "none"): environment},
            clock_ns=time.monotonic_ns,
            liveness_timeout_s=timeout_s,
        )

    def land(self, interaction: Interaction) -> None:
        self.log.append(("landed", interaction.seq))
        self.landed.append(interaction)

    def at(self, *entry: Any) -> int:
        return self.log.index(entry)

    def started(self) -> list[int]:
        return [seq for name, seq, *_ in self.log if name == running.TRIAL_START]


def _seeds(trials: tuple[Trial, ...]) -> dict[int, int]:
    return {trial.seed: trial.seq for trial in trials}


def _confirmed_after_it_ended(run: _Dispatch, interaction: Interaction) -> bool:
    """I3: a ``lost()`` that began after the trial ended returned None."""
    ended = run.at("released", interaction.seq)
    return any(
        entry[0] == "checked" and entry[2] is None and run.at("check", entry[1]) > ended
        for entry in run.log
    )


class TestDispatch:
    @pytest.mark.parametrize("width", [1, 3])
    def test_trials_start_in_order_and_no_more_than_the_width_are_in_flight(
        self, width: int, write_experiment: Write
    ) -> None:
        """I1 and I2: dispatched in seq order, applied in that order, never more than the
        width holding a subject, and every trial landed, each with its seq."""
        run = _Dispatch(width)
        setup, trials = run.setup(write_experiment)
        kit = run.kit(_Probe(run.log, _seeds(trials)), NoEnvironment())
        done = asyncio.run(
            running.carry_out(
                setup, kit, trials=trials, bindings={}, scope=run.scope, land=run.land
            )
        )
        everything = list(range(len(trials)))
        assert done == len(trials) and sorted(i.seq for i in run.landed) == everything
        assert run.started() == everything
        assert [seq for name, seq, *_ in run.log if name == "applying"] == everything
        assert run.subject.most == width

    def test_one_at_a_time_is_todays_order_exactly(self, write_experiment: Write) -> None:
        """I3 at width 1: each trial is checked after it ends and lands before the next
        starts, as before trials could overlap."""
        run = _Dispatch(1)
        setup, trials = run.setup(write_experiment)
        kit = run.kit(_Probe(run.log, _seeds(trials)), _Live(run.log))
        asyncio.run(
            running.carry_out(
                setup, kit, trials=trials[:3], bindings={}, scope=run.scope, land=run.land
            )
        )
        each = [
            [(running.TRIAL_START, seq), ("applying", seq), ("released", seq),
             ("check", seq + 1), ("checked", seq + 1, None), ("landed", seq),
             (running.TRIAL_END, seq)]
            for seq in range(3)
        ]  # fmt: skip
        assert run.log == [entry for trial in each for entry in trial]

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_a_trial_lands_unlost_only_once_a_check_begun_after_it_ended_says_so(
        self, seed: int, write_experiment: Write
    ) -> None:
        """I3: one serial checker; a finished trial waits for the next check to begin, and
        lands as up only when that one finds the environment up."""
        run = _Dispatch(4)
        setup, trials = run.setup(write_experiment)
        seeds = {s ^ seed: seq for s, seq in _seeds(trials).items()}
        trials = tuple(t.model_copy(update={"seed": t.seed ^ seed}) for t in trials)
        kit = run.kit(_Probe(run.log, seeds), _Live(run.log))
        asyncio.run(
            running.carry_out(
                setup, kit, trials=trials, bindings={}, scope=run.scope, land=run.land
            )
        )
        assert len(run.landed) == len(trials)
        assert all(_confirmed_after_it_ended(run, i) for i in run.landed)
        checks = [entry for entry in run.log if entry[0] in ("check", "checked")]
        assert all(a[0] != b[0] for a, b in itertools.pairwise(checks))  # never two at once

    def test_on_the_first_loss_nothing_starts_and_every_trial_unconfirmed_is_lost(
        self, write_experiment: Write
    ) -> None:
        """I3 on a loss: nothing starts after it is found; every trial started lands, those
        not already confirmed up as ``environment_lost``; then the run fails."""
        run = _Dispatch(4)
        setup, trials = run.setup(write_experiment)
        kit = run.kit(_Probe(run.log, _seeds(trials)), _Live(run.log, lose_on=3))

        with pytest.raises(HarnessError) as failed:
            asyncio.run(
                running.carry_out(
                    setup, kit, trials=trials, bindings={}, scope=run.scope, land=run.land
                )
            )
        assert failed.value.reason == "environment_lost"
        found = run.at("checked", 3, "gone")
        assert run.started() == list(range(len(run.started())))
        assert all(run.at(running.TRIAL_START, seq) < found for seq in run.started())
        assert sorted(i.seq for i in run.landed) == run.started()
        for interaction in run.landed:
            lost = interaction.failure is not None and interaction.failure.reason == (
                "environment_lost"
            )
            assert lost or _confirmed_after_it_ended(run, interaction)
            assert lost == (run.at("landed", interaction.seq) > found)

    def test_a_check_that_does_not_answer_is_a_loss(self, write_experiment: Write) -> None:
        """Each check has a timeout: one that never answers confirms nothing."""
        run = _Dispatch(2)
        setup, trials = run.setup(write_experiment)
        kit = run.kit(_Probe(run.log, _seeds(trials)), _Live(run.log, hang=True), 0.05)
        with pytest.raises(HarnessError, match="did not say"):
            asyncio.run(
                running.carry_out(
                    setup, kit, trials=trials, bindings={}, scope=run.scope, land=run.land
                )
            )
        assert run.landed and all(
            i.failure is not None and i.failure.reason == "environment_lost" for i in run.landed
        )

    def test_a_stop_starts_nothing_more_and_every_trial_in_flight_lands(
        self, write_experiment: Write
    ) -> None:
        """I4 and I2 on a soft stop: after the stop nothing starts, and each trial in flight
        lands with its own outcome or ``cancelled``."""
        run = _Dispatch(3)
        setup, trials = run.setup(write_experiment)
        probe = _Probe(run.log, _seeds(trials), held=frozenset(range(2, len(trials))))

        def land(interaction: Interaction) -> None:
            run.land(interaction)
            if len(run.landed) == 2:
                run.scope.stopping.set()

        kit = run.kit(probe, NoEnvironment())
        done = asyncio.run(
            running.carry_out(setup, kit, trials=trials, bindings={}, scope=run.scope, land=land)
        )
        stopped = run.at("landed", run.landed[1].seq)
        assert done == len(run.landed) == len(run.started())
        assert all(run.at(running.TRIAL_START, seq) < stopped for seq in run.started())
        assert sorted(i.seq for i in run.landed) == run.started() == list(range(done))
        for interaction in run.landed[2:]:
            assert interaction.failure is not None
            assert (interaction.failure.owner, interaction.failure.reason) == (
                "harness", "cancelled",
            )  # fmt: skip
        assert run.subject.holding == 0

    def test_a_hard_cancel_leaves_at_most_the_width_unlanded_and_each_is_released(
        self, write_experiment: Write
    ) -> None:
        """I2 and I4 on a hard cancel: the trials in flight, at most the width, land no
        row; what each holds is released by the release the run deferred."""
        run = _Dispatch(4)
        setup, trials = run.setup(write_experiment)
        probe = _Probe(run.log, _seeds(trials), held=frozenset(range(len(trials))))
        kit = run.kit(probe, NoEnvironment())

        async def cancelled() -> None:
            carried = asyncio.ensure_future(
                running.carry_out(
                    setup, kit, trials=trials, bindings={}, scope=run.scope, land=run.land
                )
            )
            while run.subject.holding < 4:
                await asyncio.sleep(0.001)
            carried.cancel()
            with pytest.raises(asyncio.CancelledError):
                await carried
            await run.scope.release()

        asyncio.run(cancelled())
        assert run.landed == [] and run.started() == [0, 1, 2, 3]
        assert run.subject.holding == 0
        assert sorted(seq for name, seq, *_ in run.log if name == "released") == [0, 1, 2, 3]

    def test_the_trials_cut_off_are_released_together_within_matrixs_cleanup(
        self, write_experiment: Write, tmp_path: Path
    ) -> None:
        """I4's bound at width 4: matrix gives the deferred release ``cleanup_s`` and then
        abandons it. Each subject takes 0.4 of that to let go, so four released one after
        another would overrun it; released together, all four are let go in time.
        Regression: the release closed what each trial held one at a time."""
        cleanup_s = 0.5
        run = _Dispatch(4)
        run.subject.release_s = 0.4 * cleanup_s
        prepared = _prepared(write_experiment(experiment()))
        design = {**prepared.manifest.design, "trials_in_flight": 4}
        manifest = prepared.manifest.model_copy(update={"design": design})
        kit = dataclasses.replace(
            run.kit(_Probe(run.log, _seeds(prepared.trials)), NoEnvironment()),
            sensors={schema.url("sensor", "timing"): _Stuck()},
        )

        async def cancelled() -> None:
            container = compose(
                MatrixConfig.model_validate(COMPOSITION),
                registry=fake_registry(),
                context={NEED: kit},
            )
            carried = asyncio.ensure_future(
                container.executor().run(
                    container.wire(),
                    {schema.RUN: manifest, schema.TRIAL: list(prepared.trials)},
                    store=JsonlConstructStore(tmp_path / "runs"),
                    run_id=manifest.run,
                    limits=Limits(grace_s=0.05, cleanup_s=cleanup_s),
                )
            )
            while run.subject.holding < 4:
                await asyncio.sleep(0.001)
            carried.cancel()
            with pytest.raises(asyncio.CancelledError):
                await carried

        asyncio.run(cancelled())
        assert run.subject.most == 4 and run.subject.holding == 0
        assert [name for name, *_ in run.log].count("released") == 4

    def test_a_release_that_fails_leaves_the_others_released_and_is_reported(
        self, write_experiment: Write
    ) -> None:
        """One subject that raises as it is let go does not strand the others, and its error
        is what the release reports. Regression: the first error ended the release, and the
        trials after it were never let go."""
        run = _Dispatch(4)
        run.subject.fails = 1
        setup, trials = run.setup(write_experiment)
        probe = _Probe(run.log, _seeds(trials), held=frozenset(range(len(trials))))
        kit = run.kit(probe, NoEnvironment())

        async def cancelled() -> None:
            carried = asyncio.ensure_future(
                running.carry_out(
                    setup, kit, trials=trials, bindings={}, scope=run.scope, land=run.land
                )
            )
            while run.subject.holding < 4:
                await asyncio.sleep(0.001)
            carried.cancel()
            with pytest.raises(asyncio.CancelledError):
                await carried
            with pytest.raises(HarnessError, match="trial 1 would not stop") as failed:
                await run.scope.release()
            assert failed.value.reason == "release_failed"

        asyncio.run(cancelled())
        assert run.subject.holding == 0
        assert sorted(seq for name, seq, *_ in run.log if name == "released") == [0, 1, 2, 3]

    def test_matrix_releases_every_trial_it_cut_off_when_the_stop_is_not_honoured(
        self, write_experiment: Write, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """I4 through matrix: the trials in flight are stuck where a stop is not seen (a
        watch that never begins), so after its grace matrix cancels the trials component; the
        release the run deferred, within the cleanup matrix allows it, releases all four."""
        monkeypatch.setattr(matrix_runs, "GRACE_S", 0.05)
        run = _Dispatch(4)
        prepared = _prepared(write_experiment(experiment()))
        design = {**prepared.manifest.design, "trials_in_flight": 4}
        manifest = prepared.manifest.model_copy(update={"design": design})
        kit = dataclasses.replace(
            run.kit(_Probe(run.log, _seeds(prepared.trials)), NoEnvironment()),
            sensors={schema.url("sensor", "timing"): _Stuck()},
        )

        async def cancelled() -> None:
            carried = asyncio.ensure_future(
                matrix_runs.execute(
                    store=JsonlConstructStore(tmp_path / "runs"),
                    registry=fake_registry(),
                    config=MatrixConfig.model_validate(COMPOSITION),
                    needs={NEED: kit},
                    manifest=manifest,
                    trials=prepared.trials,
                )
            )
            while run.subject.holding < 4:
                await asyncio.sleep(0.001)
            carried.cancel()
            with pytest.raises(asyncio.CancelledError):
                await carried

        asyncio.run(cancelled())
        assert run.subject.most == 4 and run.subject.holding == 0


# --- sensing, and an analysis that does not depend on order (X7, X8) ---------------------


class _Judging:
    """A sensor whose every reading makes one fake model call, labelled by the trial, after
    a wait drawn from it; it counts the readings under way."""

    def __init__(self) -> None:
        self.reading = 0
        self.most = 0

    def watch(self, target: Target, options: Mapping[str, Any]) -> None:
        return None

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.reading += 1
        self.most = max(self.most, self.reading)
        try:
            await asyncio.sleep(random.Random(interaction.seq).uniform(0, 0.01))
            label = f"judge/{interaction.seq}"
            record_session(Session(port="model-runtime", name=label, sent=digest(label)))
            await asyncio.sleep(random.Random(-interaction.seq).uniform(0, 0.01))
            return {"throughput": float(interaction.seq)}
        finally:
            self.reading -= 1


def _sensed(
    write: Write, width: int, overlap: str, builtin: Kinds
) -> tuple[list[Reading], tuple[Session, ...], _Judging]:
    """Sense eight interactions, given in reverse, at ``width``, inside a matrix run (so its
    scope is Recorded), with the timing sensor's kind declaring ``overlap``."""
    prepared = _prepared(write(experiment()))
    setup = running.setup_of(prepared.manifest)
    setup = dataclasses.replace(
        setup, design=setup.design.model_copy(update={"trials_in_flight": width})
    )
    trials = prepared.trials[:8]
    interactions = [
        Interaction(seq=t.seq, subject=t.subject, role=t.role, probe=t.probe, started_ns=0,
                    duration_ns=0)
        for t in reversed(trials)
    ]  # fmt: skip
    timing = builtin.kinds[schema.url("sensor", "timing")]
    sensor = _Judging()
    kit = dataclasses.replace(
        _kit(_Answering()),
        sensors={timing.name: sensor},
        kinds={timing.name: timing.model_copy(update={"overlap": overlap})},
    )
    found: list[Reading] = []

    class _Sense:
        requires = frozenset({TASK})
        provides = frozenset({schema.READING})

        async def run(self, inputs: Any, ctx: RunContext) -> None:
            found.extend(
                await running.sense(setup, kit, trials=trials, interactions=interactions, scope=ctx)
            )

    run = asyncio.run(Executor().run(wire({"sense": _Sense()}, inputs=[TASK]), {TASK: "go"}))
    return found, run.rows("matrix.v1.session"), sensor


class TestSensing:
    def test_readings_are_taken_side_by_side_each_naming_its_own_sessions(
        self, write_experiment: Write, builtin_kinds: Kinds
    ) -> None:
        """X8: sensing runs at the same width as the trials, each reading in a task of its
        own, so each names only its own model call; the readings come in seq order whatever
        order the interactions were given in."""
        readings, sessions, sensor = _sensed(write_experiment, 4, "allowed", builtin_kinds)
        assert sensor.most == 4
        assert [r.seq for r in readings] == sorted(r.seq for r in readings)
        assert {r.seq for r in readings} == set(range(8))
        for reading in readings:
            assert [sessions[row].name for row in reading.sessions] == [f"judge/{reading.seq}"]

    def test_sensing_begins_once_every_trial_has_ended(self, write_experiment: Write) -> None:
        """X8, confirmed: the sense component requires the interactions, so matrix starts it
        once the trials component has ended; no reading overlaps a trial."""

        class _Logging(FakeTiming):
            async def sense(self, interaction: Any, *, truth: Any, options: Any) -> Any:
                LOG.append("sense")
                return await super().sense(interaction, truth=truth, options=options)

        LOG.clear()
        Root(registry=fake_registry(sensor=_Logging)).run(
            write_experiment(experiment()), exploratory=True
        )
        first = LOG.index("sense")
        assert LOG.count("sense") == 15
        assert not [line for line in LOG[first:] if line != "sense"]

    def test_a_sensor_that_forbids_overlap_reads_one_trial_at_a_time(
        self, write_experiment: Write, builtin_kinds: Kinds
    ) -> None:
        readings, _, sensor = _sensed(write_experiment, 4, "forbidden", builtin_kinds)
        assert sensor.most == 1 and {r.seq for r in readings} == set(range(8))

    def test_a_shuffled_record_gives_the_same_report_byte_for_byte(
        self, tmp_path: Path, write_experiment: Write
    ) -> None:
        """The permutation invariant: every reduction sorts by key before it folds, so the
        order rows were landed in (which at a width above one is the order trials finished
        in) changes nothing in the analysis, the confusion table, the flags or the screen.
        Regression: the confusion table was built in the order its readings came."""
        routing = {
            "name": "routing",
            "question": "Does the right skill fire?",
            "genre": "evaluate",
            "subjects": {"catalog": {"kind": "agent-session", "runtime": "mock"}},
            "probes": [
                {"id": "ask", "kind": "prompt", "prompt": "Review this.", "mock_response": "ok",
                 "truth": {"expectation": "must_trigger", "expected_skill": "review"}},
                {"id": "decoy", "kind": "prompt", "prompt": "Rename it.", "mock_response": "ok",
                 "truth": {"expectation": "should_not_trigger"}},
            ],
            "primary": {"metric": "activated_correctly"},
            "threshold": {"op": ">=", "value": 0.5},
            "metrics": ["tokens_total"],
            "repeats": 5,
        }  # fmt: skip
        report = _root().run(write_experiment(routing), exploratory=True)
        found = matrix_runs.Runs.at(report.location, JsonlConstructStore)
        assert found is not None
        records, run = found
        record = records.record(run)
        assert report.confusion, "the activation sensor tabulates expected against observed"
        shuffle = random.Random(7)
        for _ in range(5):
            interactions, readings = list(record.interactions), list(record.readings)
            shuffle.shuffle(interactions)
            shuffle.shuffle(readings)
            shuffled = dataclasses.replace(
                record, interactions=tuple(interactions), readings=tuple(readings)
            )
            assert build_report(shuffled).model_dump_json() == (
                build_report(record).model_dump_json()
            )


# --- the CI validation slice (X10): one at a time against several, on stand-ins ----------


def _per_seq(readings: Sequence[Reading]) -> dict[tuple[int, str, str], Any]:
    """What each trial read, by (seq, sensor, metric), without the session rows, whose
    numbers follow the order sessions were recorded in: the equivalence check of the
    slice."""
    return {(r.seq, r.sensor, r.metric): r.model_dump(exclude={"sessions"}) for r in readings}


def _seq(row: Mapping[str, Any]) -> int:
    return int(row["seq"])


def _tables(report: ix.Report, table: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(report.tables[table]).read_text().splitlines()]


class TestTheValidationSlice:
    def test_four_in_flight_read_what_one_at_a_time_reads(self, tmp_path: Path) -> None:
        """X10.1: on the mock runtime, a run at width 4 and one at width 1 give the same
        readings for each trial and the same analysis."""
        serial = _root().run(_canned(tmp_path / "one"), exploratory=True)
        wide = _root().run(_canned(tmp_path / "four", trials_in_flight=4), exploratory=True)
        assert (serial.trials_in_flight, wide.trials_in_flight) == (1, 4)
        assert serial.trials.carried_out == wide.trials.carried_out == 10
        one = [Reading.model_validate(r) for r in _tables(serial, schema.READING)]
        four = [Reading.model_validate(r) for r in _tables(wide, schema.READING)]
        assert _per_seq(one) == _per_seq(four)
        assert serial.analysis.model_dump_json() == wide.analysis.model_dump_json()

    def test_each_trial_in_flight_names_its_own_sessions_as_many_as_alone(
        self, tmp_path: Path
    ) -> None:
        """X10.2 (I6) through a whole run: at width 4 each interaction names sessions no
        other names, each the session of its own probe, and as many as at width 1."""
        counts = []
        for width in (1, 4):
            report = _root().run(
                _canned(tmp_path / str(width), trials_in_flight=width), exploratory=True
            )
            sessions = _tables(report, "matrix.v1.session")
            interactions = _tables(report, schema.INTERACTION)
            named = [row for i in interactions for row in i["sessions"]]
            assert sorted(named) == list(range(len(sessions)))  # each once: disjoint, all
            sent = {i["probe"]: {sessions[row]["sent"] for row in i["sessions"]}
                    for i in interactions}  # fmt: skip
            assert all(len(digests) == 1 for digests in sent.values())  # never a neighbour's
            assert len(set.union(*sent.values())) == len(sent)
            counts.append([len(i["sessions"]) for i in sorted(interactions, key=_seq)])
        assert counts[0] == counts[1]

    def test_the_overlap_the_rows_show_is_the_overlap_there_was(
        self, write_experiment: Write
    ) -> None:
        """X10.4, checking the checker: the most trials whose recorded times overlap is the
        most the stub subject counted held at once."""
        run = _Dispatch(4)
        setup, trials = run.setup(write_experiment)
        kit = run.kit(_Probe(run.log, _seeds(trials)), NoEnvironment())
        asyncio.run(
            running.carry_out(
                setup, kit, trials=trials, bindings={}, scope=run.scope, land=run.land
            )
        )
        edges = sorted(
            edge
            for i in run.landed
            for edge in ((i.started_ns, 1), (i.started_ns + i.duration_ns, -1))
        )
        overlap = max(itertools.accumulate(step for _, step in edges))
        assert overlap == run.subject.most == 4

    @pytest.mark.parametrize(("crowded", "detected"), [(True, True), (False, False)])
    def test_a_reading_that_grows_with_overlap_is_caught(
        self, crowded: bool, detected: bool, write_experiment: Write
    ) -> None:
        """X10.5, the negative control: a stub whose reading is the number of trials in
        flight beside it reads differently at width 4, and the equivalence check says so;
        one that reads a constant passes it (the check is not always false)."""
        one, four = (_crowding(write_experiment, width, crowded) for width in (1, 4))
        assert (one != four) is detected


class _Crowded(_Probe):
    """Leaves behind how many trials held a subject when it began (``beside``), or 1."""

    def __init__(self, run: _Dispatch, seqs: Mapping[int, int], crowded: bool) -> None:
        super().__init__(run.log, seqs)
        self.run = run
        self.crowded = crowded

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        beside = self.run.subject.holding
        await super().apply(options, target, seed=seed)
        return {"load": {"beside": beside if self.crowded else 1}}


class _ReadsTheCrowd(FakeTiming):
    """A contended reading: what the probe found beside it."""

    async def sense(self, interaction: Any, *, truth: Any, options: Any) -> Mapping[str, Any]:
        return {"latency": {"p50": float(interaction.observations["load"]["beside"])}}


def _crowding(write: Write, width: int, crowded: bool) -> dict[tuple[int, str, str], Any]:
    """Carry the fake experiment out at ``width`` and read it with the crowd's sensor."""
    run = _Dispatch(width)
    setup, trials = run.setup(write)
    kit = dataclasses.replace(
        run.kit(_Crowded(run, _seeds(trials), crowded), NoEnvironment()),
        sensors={schema.url("sensor", "timing"): _ReadsTheCrowd()},
    )

    async def both() -> list[Reading]:
        await running.carry_out(
            setup, kit, trials=trials, bindings={}, scope=run.scope, land=run.land
        )
        return await running.sense(setup, kit, trials=trials, interactions=run.landed)

    return _per_seq(asyncio.run(both()))


SLICE = Path(__file__).resolve().parents[1] / "examples" / "trials-in-flight"


def test_the_live_slice_is_what_its_protocol_says(tmp_path: Path) -> None:
    """X10's live slice is never run here; what is checked is that it can be run as its
    README says: the agent experiment writes no width and takes 4 (every kind allows overlap, its
    decision uncontended), and the control is refused one, so its load must come from
    separate runs."""
    agent = yaml.safe_load((SLICE / "experiment.yaml").read_text())
    assert "trials_in_flight" not in agent
    assert ix.plan(SLICE).order.trials_in_flight == 1
    wide = tmp_path / "wide"
    wide.mkdir()
    (wide / "experiment.yaml").write_text(yaml.safe_dump({**agent, "trials_in_flight": 4}))
    found = ix.validate(wide)
    assert found.valid and found.warnings == ()
    control = yaml.safe_load((SLICE / "k6-control.yaml").read_text())
    assert ix.validate(SLICE / "k6-control.yaml").valid
    (wide / "experiment.yaml").write_text(yaml.safe_dump({**control, "trials_in_flight": 4}))
    _refusal_at(ix.validate(wide), 4)
    readme = (SLICE / "README.md").read_text()
    assert "S P P S P S S P" in readme and "5 percentage points" in readme and "5%" in readme
