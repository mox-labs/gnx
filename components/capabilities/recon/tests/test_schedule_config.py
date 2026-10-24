"""Ruling S1 and S2's default: what a mission's ``schedule:`` declares, and what the plan makes
of it. ``every`` is bare seconds, as every duration in a recon config is, at least 60; ``missed``
is ``once`` unless the mission says ``skip``; the default is no schedule. A scheduled mission's
``deadline:`` defaults to 0.8 × ``every``, so a tick ends before the next is due, and the plan
says each value and where it came from."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from recon.application.plan import Plan, build_plan
from recon.composition import load_plugins
from recon.domain.schedule import EVERY_ALLOWED

if TYPE_CHECKING:
    from recon.domain.collector import Plugins


@pytest.fixture(scope="module")
def plugins() -> Plugins:
    return load_plugins()


def _plan(plugins: Plugins, **top: Any) -> Plan:
    collectors = [{"name": "now", "type": "cli", "run": "date"}]
    return build_plan({"collectors": collectors, **top}, plugins, {})


def _values(plan: Plan) -> dict[str, tuple[Any, str, str]]:
    return {v.path: (v.value, v.origin, v.note) for v in plan.provenance}


def _problems(plan: Plan) -> dict[str, Any]:
    return {p.path: p for p in plan.problems}


class TestTheDefaults:
    def test_no_schedule_is_the_default_and_adds_nothing(self, plugins: Plugins) -> None:
        plan = _plan(plugins)

        assert plan.valid and plan.config is not None
        assert plan.config.schedule is None
        assert plan.config.deadline is None, "an unscheduled mission keeps no deadline by default"
        assert not any(path.startswith(("schedule", "deadline")) for path in _values(plan))

    def test_every_alone_misses_once_and_bounds_each_tick_at_0_8_of_every(
        self, plugins: Plugins
    ) -> None:
        plan = _plan(plugins, schedule={"every": 600})

        assert plan.valid, plan.problems
        assert plan.config is not None and plan.config.schedule is not None
        assert plan.config.schedule.every == 600 and plan.config.schedule.missed == "once"
        assert plan.config.deadline == 480.0
        values = _values(plan)
        assert values["schedule.every"][:2] == (600, "mission")
        assert values["schedule.missed"][:2] == ("once", "recon")
        value, origin, note = values["deadline"]
        assert (value, origin) == (480.0, "recon")
        assert "0.8" in note and "every" in note

    def test_a_deadline_the_mission_sets_is_kept_and_said(self, plugins: Plugins) -> None:
        plan = _plan(plugins, schedule={"every": 600, "missed": "skip"}, deadline=120)

        assert plan.config is not None and plan.config.deadline == 120
        values = _values(plan)
        assert values["deadline"][:2] == (120, "mission")
        assert values["schedule.missed"][:2] == ("skip", "mission")


class TestEveryProblemHasAPathAndAFix:
    @pytest.mark.parametrize(
        ("every", "nearest"),
        [(59, "60"), (61, "60 or 120"), (420, "360 or 480"), (90000, "86400")],
    )
    def test_an_every_no_scheduler_can_keep_is_refused_with_the_nearest(
        self, plugins: Plugins, every: int, nearest: str
    ) -> None:
        """Whole minutes that divide a day: launchd and cron count in minutes, and a systemd
        calendar that misses ticks (``Persistent=true``) repeats daily, so only such an
        ``every`` fires on one grid everywhere."""
        problem = _problems(_plan(plugins, schedule={"every": every}))["schedule.every"]

        assert problem.kind == "invalid_value"
        assert problem.got == every
        for value in nearest.split(" or "):
            assert f"every: {value}" in problem.fix, problem.fix

    def test_the_allowed_values_are_the_minutes_that_divide_a_day(self) -> None:
        assert EVERY_ALLOWED[0] == 60 and EVERY_ALLOWED[-1] == 86400
        assert all(e % 60 == 0 and 86400 % e == 0 for e in EVERY_ALLOWED)
        assert 600 in EVERY_ALLOWED and 5400 in EVERY_ALLOWED and 420 not in EVERY_ALLOWED
        assert len(EVERY_ALLOWED) == sum(1 for m in range(1, 1441) if 1440 % m == 0)

    def test_missed_is_once_or_skip(self, plugins: Plugins) -> None:
        problem = _problems(_plan(plugins, schedule={"every": 600, "missed": "all"}))

        assert problem["schedule.missed"].allowed == ("once", "skip")

    def test_every_is_required_in_a_schedule_and_unknown_keys_are_named(
        self, plugins: Plugins
    ) -> None:
        problems = _problems(_plan(plugins, schedule={"cron": "*/10 * * * *"}))

        assert problems["schedule.every"].kind == "missing_key"
        assert problems["schedule.cron"].kind == "unknown_key"

    def test_a_schedule_that_is_not_a_mapping_is_a_shape_problem(self, plugins: Plugins) -> None:
        problems = _problems(_plan(plugins, schedule=600))

        assert problems["schedule"].kind == "wrong_shape"
