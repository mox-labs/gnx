"""What a stream's entry declares, and what the plan makes of it (rulings B2 and B4): the keys an
agent writes, the defaults recon fills in and says it filled, and every problem with a path and a
fix. A collector type declares ``unbounded`` and ``paced_by`` through the same entry point a
third party's does."""

from __future__ import annotations

from typing import Any

import pytest

from recon.application.plan import Plan, build_plan
from recon.composition import load_plugins
from recon.domain.collector import Collector, CollectorContext, CollectorType, Plugins


def _never_built(ctx: CollectorContext) -> Collector:
    raise AssertionError("planning builds no collector")


#: A third party's types, registered as any plugin's: one that never ends and paces itself,
#: one that never ends and is read at recon's pace, and one that ends.
PUSH = CollectorType(create=_never_built, effects=frozenset({"network"}), unbounded=True,
                     paced_by="source")  # fmt: skip
PULL = CollectorType(create=_never_built, effects=frozenset({"filesystem"}), unbounded=True)
BOUNDED = CollectorType(create=_never_built, effects=frozenset())


@pytest.fixture(scope="module")
def plugins() -> Plugins:
    installed = load_plugins()
    types = {**installed.collector_types, "push": PUSH, "pull": PULL, "once": BOUNDED}
    return Plugins(types, installed.transforms, installed.broken)


def _plan(entry: dict[str, Any], plugins: Plugins, **top: Any) -> Plan:
    return build_plan({"collectors": [{"name": "events", **entry}], **top}, plugins, {})


def _values(plan: Plan) -> dict[str, tuple[Any, str]]:
    return {v.path: (v.value, v.source) for v in plan.provenance}


def _problems(plan: Plan) -> dict[str, tuple[str, str]]:
    return {p.path: (p.kind, p.fix) for p in plan.problems}


class TestTheDefaultsAreFilledAndSaid:
    def test_an_unbounded_type_without_a_window_gets_300_seconds_and_plan_says_so(
        self, plugins: Plugins
    ) -> None:
        plan = _plan({"type": "pull"}, plugins)

        assert plan.valid, plan.problems
        values = _values(plan)
        assert values["collectors.0.window.seconds"] == (300.0, "recon")
        assert values["collectors.0.buffer.records"] == (10_000, "recon")
        assert values["collectors.0.buffer.bytes"] == (16 * 1024 * 1024, "recon")
        assert values["collectors.0.buffer.max_record_bytes"] == (1024 * 1024, "recon")
        assert values["collectors.0.retain"] == ("all", "recon")
        [run] = plan.runs
        assert run.document()["stream"] == "events"

    @pytest.mark.parametrize(("kind", "overflow"), [("pull", "block"), ("push", "drop_newest")])
    def test_overflow_follows_who_paces_the_source(
        self, plugins: Plugins, kind: str, overflow: str
    ) -> None:
        plan = _plan({"type": kind}, plugins)

        [overflowing] = [v for v in plan.provenance if v.path.endswith("buffer.overflow")]
        assert (overflowing.value, overflowing.source) == (overflow, "type")
        assert "paced_by" in overflowing.note

    def test_what_the_mission_writes_is_its_own(self, plugins: Plugins) -> None:
        plan = _plan(
            {
                "type": "push",
                "window": {"records": 500, "bytes": 65536},
                "buffer": {"records": 64, "overflow": "drop_oldest"},
                "retain": {"windows": 48},
            },
            plugins,
        )

        assert plan.valid, plan.problems
        values = _values(plan)
        assert values["collectors.0.window.records"] == (500, "mission")
        assert values["collectors.0.window.bytes"] == (65536, "mission")
        assert "collectors.0.window.seconds" not in values, "a bound not set is not one"
        assert values["collectors.0.buffer.records"] == (64, "mission")
        assert values["collectors.0.buffer.overflow"] == ("drop_oldest", "mission")
        assert values["collectors.0.buffer.bytes"][1] == "recon"
        assert values["collectors.0.retain"] == ({"windows": 48}, "mission")
        assert not [w for w in plan.warnings if w.kind == "retain_unset"]

    def test_keeping_every_window_by_default_is_a_warning_with_the_growth_and_a_fix(
        self, plugins: Plugins
    ) -> None:
        plan = _plan({"type": "pull", "window": {"seconds": 60}}, plugins)

        [warning] = [w for w in plan.warnings if w.kind == "retain_unset"]
        assert warning.path == "collectors.0.retain"
        assert "1440 windows a day" in warning.message
        assert "retain:" in warning.fix

    def test_a_snapshot_entry_is_no_stream_and_reports_nothing(self, plugins: Plugins) -> None:
        plan = _plan({"type": "once"}, plugins)

        assert plan.valid and plan.provenance == []
        assert "stream" not in plan.runs[0].document()

    def test_window_on_a_type_that_ends_makes_it_a_stream(self, plugins: Plugins) -> None:
        plan = _plan({"type": "once", "window": {"seconds": 30}}, plugins)

        assert plan.valid, plan.problems
        assert _values(plan)["collectors.0.window.seconds"] == (30.0, "mission")
        assert _values(plan)["collectors.0.buffer.overflow"] == ("block", "type")


class TestEveryProblemHasAPathAndAFix:
    def test_a_window_with_no_bound_is_refused(self, plugins: Plugins) -> None:
        plan = _plan({"type": "pull", "window": {}}, plugins)

        assert "collectors.0.window" in _problems(plan)

    def test_a_window_under_a_second_is_refused(self, plugins: Plugins) -> None:
        """Review item 19 (Erlang): a window's end-markers are not held to the buffer's
        bound, so a window of milliseconds would fill it with markers; a second is the floor."""
        plan = _plan({"type": "pull", "window": {"seconds": 0.5}}, plugins)

        assert "collectors.0.window.seconds" in _problems(plan)
        assert not _problems(_plan({"type": "pull", "window": {"seconds": 1}}, plugins))

    def test_buffer_and_retain_without_a_stream_are_refused(self, plugins: Plugins) -> None:
        plan = _plan({"type": "once", "buffer": {"records": 5}, "retain": "all"}, plugins)

        problems = _problems(plan)
        assert problems["collectors.0.buffer"][0] == "invalid_value"
        assert "window:" in problems["collectors.0.buffer"][1]
        assert "collectors.0.retain" in problems

    def test_a_normalize_column_named_position_is_refused(self, plugins: Plugins) -> None:
        plan = _plan({"type": "pull", "normalize": {"_position": "offset"}}, plugins)

        assert _problems(plan)["collectors.0.normalize._position"][0] == "name_taken"

    def test_a_deadline_on_a_stream_is_refused(self, plugins: Plugins) -> None:
        plan = _plan({"type": "pull", "deadline": 30}, plugins)

        kind, fix = _problems(plan)["collectors.0.deadline"]
        assert kind == "invalid_value" and "window:" in fix

    def test_a_stream_runs_against_one_source(self, plugins: Plugins) -> None:
        catalog = [{"name": "a", "url": "https://a.test"}, {"name": "b", "url": "https://b.test"}]
        plan = _plan({"type": "push"}, plugins, catalog=catalog)

        kind, fix = _problems(plan)["collectors.0.source"]
        assert "source:" in fix

    def test_a_record_larger_than_the_buffer_is_refused(self, plugins: Plugins) -> None:
        plan = _plan({"type": "pull", "buffer": {"bytes": 1000, "max_record_bytes": 2000}}, plugins)

        assert "collectors.0.buffer.max_record_bytes" in _problems(plan)

    @pytest.mark.parametrize("retain", [{}, {"windows": 0}, {"seconds": -1}, "some", {"age": 30}])
    def test_retain_is_all_or_a_bound(self, plugins: Plugins, retain: Any) -> None:
        plan = _plan({"type": "pull", "retain": retain}, plugins)

        assert any(path.startswith("collectors.0.retain") for path in _problems(plan))
