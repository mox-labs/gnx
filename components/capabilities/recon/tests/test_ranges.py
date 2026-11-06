"""Ranges (owner rulings R1, R5 and R-TOMB): ``range:`` asks a snapshot entry's source for its
time step by step, run after run, and the core fills ``{range.*}`` in any type's settings.

- **The plan** (:func:`~recon.core.domain.ranges.plan_range`, pure): a continuing range goes on from
  its checkpoint, a fixed one stops at ``end``, a rolling one reads the latest ``last`` again;
  every run ends ``settle`` behind now, asks at most ``max_steps`` steps, and says a backlog.
- **The checkpoint** moves only when the table's archive commits (its meta recorded, the table
  sealed ``ok``), in that one commit; a failed run commits nothing and asks again; a
  tombstone keeps what its archive committed.
- **Overrides**: ``--range`` collects an interval and moves nothing; ``--rebase`` and
  ``--restart`` are how a checkpoint survives a changed request.
- **Defaults**: each key left out is reported by the plan with its origin.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from click.testing import CliRunner
from pydantic import BaseModel, ConfigDict

from recon.adapters._in.cli import main
from recon.composition import load_plugins, root
from recon.core.application.plan import build_plan
from recon.core.domain.collector import Record
from recon.core.domain.errors import CollectionError, ConfigError, UsageError
from recon.core.domain.models import CollectorEntry
from recon.core.domain.ranges import (
    DEFAULT_MAX_STEPS,
    DEFAULT_SETTLE_S,
    DEFAULT_STEP_S,
    Committed,
    Interval,
    RangeSpec,
    committed_in,
    fill,
    interval_of,
    iso,
    unknown_placeholders,
    uses_range,
)
from recon.observe.application.ranges import committed_intervals, request_digest
from recon.observe.domain.spans import plan_range
from tests.recon_fakes import FakeCollector, FixedClock, files_of, installed, kept

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from recon.composition import Recon
    from recon.core.domain.collector import (
        Collector,
        Effect,
        Intent,
        Item,
        Plugins,
        Position,
        Run,
    )
    from recon.core.domain.errors import Problem

NOW = datetime(2026, 10, 24, 12, 0, 30, 250000, tzinfo=UTC)
#: ``NOW`` less the default settle, to the whole second.
HORIZON = datetime(2026, 10, 24, 11, 59, 30, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _committed(start: datetime, end: datetime, request: str = "r") -> Committed:
    return Committed("continuing", Interval(start, end), request, "checkpoint")


# --- the plan of one run: pure --------------------------------------------------------------


class TestPlanRange:
    def test_a_first_continuing_run_asks_for_one_step_back_from_the_horizon(self) -> None:
        run = plan_range(RangeSpec(), NOW, "r", None)

        assert run.kind == "continuing" and run.began == "default" and run.moves
        assert run.interval == Interval(HORIZON - HOUR, HORIZON)
        assert run.steps == (run.interval,)
        assert (run.behind_s, run.current, run.complete) == (0.0, False, False)

    def test_a_continuing_run_goes_on_from_its_checkpoint_to_the_horizon(self) -> None:
        checkpoint = _committed(HORIZON - 3 * HOUR, HORIZON - 2 * HOUR)
        run = plan_range(RangeSpec(), NOW, "r", checkpoint)

        assert run.began == "checkpoint"
        assert run.interval == Interval(HORIZON - 2 * HOUR, HORIZON)
        assert [s.seconds for s in run.steps] == [3600.0, 3600.0]

    def test_a_backlog_is_drained_max_steps_at_a_time_and_said(self) -> None:
        spec = RangeSpec(start=HORIZON - 10 * HOUR, max_steps=4)
        run = plan_range(spec, NOW, "r", None)

        assert run.began == "start"
        assert run.interval == Interval(HORIZON - 10 * HOUR, HORIZON - 6 * HOUR)
        assert len(run.steps) == 4 and run.behind_s == 6 * 3600

    def test_the_last_step_is_short_where_the_horizon_cuts_it(self) -> None:
        checkpoint = _committed(HORIZON - 2 * HOUR, HORIZON - 90 * timedelta(minutes=1))
        run = plan_range(RangeSpec(), NOW, "r", checkpoint)

        assert [s.seconds for s in run.steps] == [3600.0, 1800.0]
        assert run.steps[0].end == run.steps[1].start, "half-open steps meet, sharing nothing"

    def test_nothing_new_past_settle_asks_for_nothing(self) -> None:
        run = plan_range(RangeSpec(), NOW, "r", _committed(HORIZON - HOUR, HORIZON))

        assert run.steps == () and run.current and run.interval == Interval(HORIZON, HORIZON)

    def test_settle_holds_the_end_back_from_now(self) -> None:
        run = plan_range(RangeSpec(settle=600), NOW, "r", None)

        assert run.interval.end == datetime(2026, 10, 24, 11, 50, 30, tzinfo=UTC)

    def test_align_floors_the_horizon_and_the_first_start(self) -> None:
        run = plan_range(RangeSpec(step=600, align=300), NOW, "r", None)

        assert run.interval == Interval(_at("2026-10-24T11:45:00+00:00"),
                                        _at("2026-10-24T11:55:00+00:00"))  # fmt: skip

    def test_a_fixed_range_never_asks_past_its_end_and_then_is_complete(self) -> None:
        start, end = HORIZON - 5 * HOUR, HORIZON - 3 * HOUR - timedelta(minutes=30)
        spec = RangeSpec(start=start, end=end)
        run = plan_range(spec, NOW, "r", None)
        assert run.kind == "fixed" and run.interval == Interval(start, end)
        assert run.behind_s == 0.0, "nothing past end is owed"

        done = plan_range(spec, NOW, "r", _committed(start, end))
        assert done.steps == () and done.complete and not done.current

    def test_a_rolling_range_reads_the_latest_span_again_and_moves_nothing(self) -> None:
        run = plan_range(RangeSpec(last=900), NOW, "r", _committed(HORIZON - HOUR, HORIZON))

        assert run.kind == "rolling" and not run.moves and run.began == "default"
        assert run.interval == Interval(HORIZON - timedelta(seconds=900), HORIZON)
        assert len(run.steps) == 1, "a rolling range's step is its whole span by default"

    def test_an_override_asks_for_its_interval_and_moves_nothing(self) -> None:
        asked = Interval(HORIZON - 30 * HOUR, HORIZON - 28 * HOUR)
        run = plan_range(RangeSpec(), NOW, "r", _committed(HORIZON - HOUR, HORIZON),
                         override=asked)  # fmt: skip

        assert run.began == "override" and not run.moves and run.interval == asked
        assert len(run.steps) == 2 and not run.refused

    def test_an_override_past_max_steps_is_refused(self) -> None:
        asked = Interval(HORIZON - 30 * HOUR, HORIZON)
        run = plan_range(RangeSpec(max_steps=24), NOW, "r", None, override=asked)

        [(kind, message, _)] = run.refused
        assert kind == "range_steps" and "30 step(s)" in message

    def test_a_changed_request_refuses_to_go_on_from_the_checkpoint(self) -> None:
        checkpoint = _committed(HORIZON - 2 * HOUR, HORIZON - HOUR, request="old")
        run = plan_range(RangeSpec(), NOW, "new", checkpoint)

        [(kind, _, fix)] = run.refused
        assert kind == "checkpoint_request_changed"
        assert "--rebase" in fix and "--restart" in fix

    def test_rebase_goes_on_from_the_checkpoint_and_restart_begins_at_start(self) -> None:
        start = HORIZON - 5 * HOUR
        checkpoint = _committed(HORIZON - 2 * HOUR, HORIZON - HOUR, request="old")
        spec = RangeSpec(start=start)

        rebased = plan_range(spec, NOW, "new", checkpoint, rebase=True)
        restarted = plan_range(spec, NOW, "new", checkpoint, restart=True)

        assert (rebased.began, rebased.interval.start, rebased.refused) == (
            "rebase", HORIZON - HOUR, [])  # fmt: skip
        assert (restarted.began, restarted.interval.start, restarted.refused) == (
            "restart", start, [])  # fmt: skip

    def test_the_defaults_are_the_ones_documented(self) -> None:
        spec = RangeSpec()
        assert (spec.step_s, spec.settle, spec.max_steps, spec.align) == (
            DEFAULT_STEP_S, DEFAULT_SETTLE_S, DEFAULT_MAX_STEPS, None)  # fmt: skip
        assert (DEFAULT_STEP_S, DEFAULT_SETTLE_S, DEFAULT_MAX_STEPS) == (3600.0, 60.0, 24)


class TestFill:
    STEP = Interval(_at("2026-10-24T10:00:00+00:00"), _at("2026-10-24T11:00:00+00:00"))

    def test_each_bound_in_each_format(self) -> None:
        settings = {
            "q": "from {range.start} to {range.end}",
            "from": "{range.start:epoch}",
            "to_ms": "{range.end:epoch_ms}",
            "day": "{range.start:date}",
            "until": "{range.last}",
            "until_s": "{range.last:epoch}",
            "nested": [{"at": "{range.start:iso}"}],
            "n": 3,
        }
        assert fill(settings, self.STEP) == {
            "q": "from 2026-10-24T10:00:00Z to 2026-10-24T11:00:00Z",
            "from": 1792836000,
            "to_ms": 1792839600000,
            "day": "2026-10-24",
            "until": "2026-10-24T10:59:59Z",
            "until_s": 1792839599,
            "nested": [{"at": "2026-10-24T10:00:00Z"}],
            "n": 3,
        }

    def test_a_number_inside_a_string_stays_text(self) -> None:
        assert fill("t>{range.start:epoch}", self.STEP) == "t>1792836000"

    def test_an_escaped_placeholder_is_text(self) -> None:
        assert fill("{{range.start}}", self.STEP) == "{{range.start}}"
        assert not uses_range({"q": "{{range.start}}"})

    def test_a_placeholder_nothing_fills_is_found(self) -> None:
        settings = {"a": "{range.begin}", "b": "{range.start:unix}", "c": "{range.end:epoch}"}
        assert unknown_placeholders(settings) == ["{range.begin}", "{range.start:unix}"]
        assert uses_range(settings)


class TestCommitted:
    def _meta(self, **table: Any) -> dict[str, Any]:
        recorded = {"name": "metrics", "source": None, "status": "ok",
                    "range": {"start": "2026-10-24T10:00:00Z", "end": "2026-10-24T11:00:00Z",
                              "kind": "continuing", "from": "checkpoint", "request": "r",
                              "steps": 1}}  # fmt: skip
        return {"format_version": 2, "ended": "2026-10-24T12:00:00Z",
                "tables": [{**recorded, **table}]}  # fmt: skip

    def test_an_ok_table_of_an_ended_meta_commits_its_range(self) -> None:
        [(output, source, committed)] = committed_in(self._meta())
        assert (output, source) == ("metrics", None)
        assert committed.interval == TestFill.STEP

    @pytest.mark.parametrize(
        "table",
        [{"status": "failed"}, {"truncated": True},
         {"range": {"start": "2026-10-24T10:00:00Z", "end": "2026-10-24T11:00:00Z",
                    "kind": "continuing", "from": "override", "request": "r"}},
         {"range": {"start": "2026-10-24T10:00:00Z", "end": "2026-10-24T11:00:00Z",
                    "kind": "rolling", "from": "default", "request": "r"}},
         {"range": None}],
        ids=["failed", "truncated", "override", "rolling", "unranged"],
    )  # fmt: skip
    def test_what_commits_nothing(self, table: dict[str, Any]) -> None:
        assert committed_in(self._meta(**table)) == []

    def test_a_meta_recorded_before_its_survey_ended_commits_nothing(self) -> None:
        assert committed_in({**self._meta(), "ended": None}) == []
        assert committed_in({"format_version": None, "tables": []}) == []


class TestIntervalOf:
    def test_a_bound_with_no_zone_is_refused(self) -> None:
        with pytest.raises(UsageError):
            interval_of(datetime(2026, 10, 1), datetime(2026, 10, 2, tzinfo=UTC))  # noqa: DTZ001

    def test_an_end_not_after_its_start_is_refused(self) -> None:
        at = datetime(2026, 10, 1, tzinfo=UTC)
        with pytest.raises(UsageError):
            interval_of(at, at)


def test_the_request_digest_is_the_question_and_not_its_shape() -> None:
    entry = CollectorEntry.model_validate(
        {"name": "m", "type": "steps", "since": "{range.start}", "range": {"step": 60}}
    )
    reshaped = entry.model_copy(update={"range": RangeSpec(step=120)})
    asked_again = CollectorEntry.model_validate(
        {"name": "m", "type": "steps", "since": "{range.end}", "range": {"step": 60}}
    )

    assert request_digest(entry, None) == request_digest(reshaped, None)
    assert request_digest(entry, None) != request_digest(asked_again, None)


# --- the plan of a mission ------------------------------------------------------------------


class StepsSettings(BaseModel):
    """The ``steps`` type's settings: the bounds of what it is asked for."""

    model_config = ConfigDict(extra="forbid")

    since: str
    until: str


class Steps(FakeCollector):
    """A collector type's collector that answers each request with one record naming the
    interval it was asked for (``since``, ``until``), or fails when :attr:`fails` is set."""

    effects: frozenset[Effect] | None = frozenset()

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []
        self.fails = False

    def describe(self, run: Run) -> Intent:
        return {"asks": run.entry.settings}

    def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
        asked = run.settings_as(StepsSettings)
        if self.fails:
            raise CollectionError("the source is down", kind="transient")
        self.asked.append((asked.since, asked.until))
        yield Record({"id": asked.since, "since": asked.since, "until": asked.until})


def _plugins(steps: Collector) -> Plugins:
    plugins = load_plugins()
    plugins.collector_types["steps"] = installed(
        "steps", lambda **needs: steps, settings=StepsSettings, declared=steps
    )
    return plugins


def _entry(**keys: Any) -> dict[str, Any]:
    return {"name": "metrics", "type": "steps", "since": "{range.start}",
            "until": "{range.end}", "range": {}, **keys}  # fmt: skip


def _problems(*entries: dict[str, Any], **top: Any) -> dict[tuple[str, str], Problem]:
    plan = build_plan({"collectors": list(entries), **top}, _plugins(Steps()), {})
    return {(p.path, p.kind): p for p in plan.problems}


class TestThePlan:
    def test_a_placeholder_with_no_range_is_unresolved(self) -> None:
        entry = _entry()
        del entry["range"]
        assert ("collectors.0", "unresolved") in _problems(entry)

    def test_a_range_no_setting_names_is_unused(self) -> None:
        assert ("collectors.0.range", "range_unused") in _problems(
            {"name": "metrics", "type": "steps", "since": "a", "until": "b", "range": {}}
        )

    @pytest.mark.parametrize(
        ("keys", "where"),
        [({"end": "2026-10-02T00:00:00Z", "last": 60}, ("collectors.0.range", "range_ambiguous")),
         ({"end": "2026-10-02T00:00:00Z"}, ("collectors.0.range.start", "missing_key")),
         ({"start": "2026-10-02T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
          ("collectors.0.range.end", "invalid_value")),
         ({"step": 90, "align": 60}, ("collectors.0.range.step", "invalid_value"))],
        ids=["end-and-last", "end-without-start", "end-before-start", "unaligned-step"],
    )  # fmt: skip
    def test_what_a_range_may_not_say(self, keys: dict[str, Any], where: tuple[str, str]) -> None:
        assert where in _problems(_entry(range=keys))

    def test_deletes_are_refused_where_a_table_holds_new_time_alone(self) -> None:
        problems = _problems(_entry(changes={"key": ["id"], "deletes": True}))
        assert ("collectors.0.changes.deletes", "range_deletes") in problems
        rolling = _problems(_entry(range={"last": 600}, changes={"key": ["id"], "deletes": True}))
        assert ("collectors.0.changes.deletes", "range_deletes") not in rolling

    def test_a_placeholder_nothing_fills_is_a_problem(self) -> None:
        problem = _problems(_entry(until="{range.stop}"))[("collectors.0", "invalid_value")]
        assert problem.got == "{range.stop}"

    def test_a_range_without_a_time_zone_is_refused(self) -> None:
        assert any(path.startswith("collectors.0.range") for path, _ in
                   _problems(_entry(range={"start": "2026-10-01T00:00:00"})))  # fmt: skip

    def test_each_default_is_reported_with_its_origin(self) -> None:
        plan = build_plan({"collectors": [_entry(range={"step": 600})]}, _plugins(Steps()), {})

        assert plan.valid, plan.problems
        values = {v.path: (v.value, v.origin) for v in plan.provenance}
        assert values["collectors.0.range.kind"] == ("continuing", "mission")
        assert values["collectors.0.range.step"] == (600, "mission")
        assert values["collectors.0.range.settle"] == (60.0, "recon")
        assert values["collectors.0.range.max_steps"] == (24, "recon")
        assert values["collectors.0.range.start"] == (None, "recon")
        assert values["collectors.0.range.align"] == (None, "recon")


# --- surveys --------------------------------------------------------------------------------


class Mission:
    """Mission ``m`` of ``project``, its one entry ranged over :class:`Steps`, on a clock that
    moves only when told."""

    def __init__(self, project: Path, **keys: Any) -> None:
        self.project = project
        self.steps = Steps()
        self.clock = FixedClock(NOW)
        self.write(**keys)

    def write(self, **keys: Any) -> None:
        mission = self.project / ".recon" / "m"
        mission.mkdir(parents=True, exist_ok=True)
        config = {"collectors": [_entry(**keys)]}
        (mission / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))

    @property
    def recon(self) -> Recon:
        return root({}, project=self.project, clock=self.clock, plugins=_plugins(self.steps))

    def committed(self) -> list[Interval]:
        files = files_of(self.project, self.clock)
        return sorted(committed_intervals(files, "m", ("metrics", None)),
                      key=lambda i: (i.start, i.end))  # fmt: skip


class TestSurveys:
    def test_a_survey_asks_each_step_and_tags_each_record_with_it(self, tmp_path: Path) -> None:
        mission = Mission(tmp_path, range={"step": 1800, "start": iso(HORIZON - HOUR)})

        document = mission.recon.observation.survey("m")

        assert document.status == "complete"
        assert mission.steps.asked == [(iso(HORIZON - HOUR), iso(HORIZON - HOUR / 2)),
                                       (iso(HORIZON - HOUR / 2), iso(HORIZON))]  # fmt: skip
        rows = [json.loads(line) for line in
                (kept(tmp_path, document) / "metrics.jsonl").read_text().splitlines()]  # fmt: skip
        assert [row["_range"] for row in rows] == [
            {"start": iso(HORIZON - HOUR), "end": iso(HORIZON - HOUR / 2)},
            {"start": iso(HORIZON - HOUR / 2), "end": iso(HORIZON)},
        ]
        [table] = document.tables
        assert table.range is not None
        assert (table.range.start, table.range.end, table.range.from_, table.range.steps) == (
            iso(HORIZON - HOUR), iso(HORIZON), "start", 2)  # fmt: skip

    def test_the_next_survey_goes_on_from_the_checkpoint(self, tmp_path: Path) -> None:
        mission = Mission(tmp_path)
        mission.recon.observation.survey("m")
        mission.clock.advance(1800)

        document = mission.recon.observation.survey("m")

        assert mission.steps.asked[-1] == (iso(HORIZON), iso(HORIZON + HOUR / 2))
        [table] = document.tables
        assert table.range is not None and table.range.from_ == "checkpoint"
        assert mission.committed() == [Interval(HORIZON - HOUR, HORIZON),
                                       Interval(HORIZON, HORIZON + HOUR / 2)]  # fmt: skip

    def test_a_failed_survey_commits_nothing_so_the_next_asks_again(self, tmp_path: Path) -> None:
        mission = Mission(tmp_path)
        mission.recon.observation.survey("m")
        mission.clock.advance(600)
        mission.steps.fails = True

        failed = mission.recon.observation.survey("m")
        mission.steps.fails = False
        mission.clock.advance(600)
        again = mission.recon.observation.survey("m")

        assert failed.status == "incomplete"
        [table] = again.tables
        assert table.range is not None and table.range.start == iso(HORIZON)
        assert table.range.end == iso(HORIZON + 2 * timedelta(minutes=10))
        assert mission.committed() == [Interval(HORIZON - HOUR, HORIZON),
                                       Interval(HORIZON, HORIZON + HOUR / 3)]  # fmt: skip

    def test_a_backfill_moves_no_checkpoint_and_says_an_overlap(self, tmp_path: Path) -> None:
        mission = Mission(tmp_path)
        mission.recon.observation.survey("m")
        mission.clock.advance(60)
        asked = (HORIZON - 3 * HOUR, HORIZON - HOUR / 2)

        document = mission.recon.observation.survey("m", interval=asked)

        assert [w["kind"] for w in document.warnings if w["kind"].startswith("range")] == [
            "range_overlaps"
        ]
        [table] = document.tables
        assert table.range is not None and table.range.from_ == "override"
        assert mission.committed() == [Interval(HORIZON - HOUR, HORIZON)]
        mission.clock.advance(60)
        mission.recon.observation.survey("m")
        assert mission.steps.asked[-1][0] == iso(HORIZON), "on from the checkpoint, as before"

    def test_a_changed_request_is_refused_until_rebased_or_restarted(self, tmp_path: Path) -> None:
        start = HORIZON - 2 * HOUR
        mission = Mission(tmp_path, range={"start": iso(start), "max_steps": 1})
        mission.recon.observation.survey("m")
        mission.write(range={"start": iso(start), "max_steps": 1}, until="{range.last}")
        mission.clock.advance(60)

        with pytest.raises(ConfigError) as refused:
            mission.recon.observation.survey("m")
        [problem] = refused.value.problems
        assert problem.kind == "checkpoint_request_changed"
        with pytest.raises(UsageError):
            mission.recon.observation.survey("m", rebase=["other"])

        rebased = mission.recon.observation.survey("m", rebase=["metrics"])
        mission.clock.advance(60)
        restarted = mission.recon.observation.survey("m", restart=["metrics"])

        assert rebased.tables[0].range is not None and restarted.tables[0].range is not None
        assert (rebased.tables[0].range.from_, rebased.tables[0].range.start) == (
            "rebase", iso(start + HOUR))  # fmt: skip
        assert (restarted.tables[0].range.from_, restarted.tables[0].range.start) == (
            "restart", iso(start))  # fmt: skip
        mission.clock.advance(60)
        [after] = mission.recon.observation.survey("m").tables
        assert after.range is not None
        assert (after.range.from_, after.range.start) == ("checkpoint", iso(start + HOUR)), (
            "a restart's range is the latest committed, so the next goes on from it"
        )

    def test_retention_keeps_the_checkpoint_in_the_tombstone(self, tmp_path: Path) -> None:
        mission = Mission(tmp_path, retain={"archives": 1}, range={"step": 600})
        for _ in range(3):
            mission.recon.observation.survey("m")
            mission.clock.advance(600)

        document = mission.recon.observation.survey("m")

        archives = tmp_path / ".recon" / "m" / "archive"
        lines = (archives / "retired.jsonl").read_text().splitlines()
        stones = [json.loads(line) for line in lines]
        assert len(stones) == 3 and all(stone["ranges"] for stone in stones)
        [table] = document.tables
        assert table.range is not None and table.range.from_ == "checkpoint"
        assert table.range.start == iso(HORIZON + timedelta(minutes=20))
        committed = mission.committed()
        assert all(a.end == b.start for a, b in zip(committed, committed[1:], strict=False))

    def test_the_plan_says_the_interval_and_the_first_step_asked(self, tmp_path: Path) -> None:
        mission = Mission(tmp_path, range={"step": 1800})

        plan = mission.recon.observation.plan("m")

        [run] = plan.runs
        assert run["range"] == {"start": iso(HORIZON - HOUR / 2), "end": iso(HORIZON),
                                "kind": "continuing", "from": "default", "steps": 1}  # fmt: skip
        assert run["asks"] == {"since": iso(HORIZON - HOUR / 2), "until": iso(HORIZON)}
        assert mission.steps.asked == [], "a plan asks nothing"

    def test_status_reads_each_tables_range_back(self, tmp_path: Path) -> None:
        mission = Mission(tmp_path)
        mission.recon.observation.survey("m")

        [status] = mission.recon.observation.status("m").missions
        assert status.latest is not None
        [table] = status.latest.tables
        assert table.range is not None and table.range.end == iso(HORIZON)

    def test_a_range_with_no_ranged_entry_is_refused(self, tmp_path: Path) -> None:
        mission = Mission(tmp_path)
        (tmp_path / ".recon" / "m" / "config.yaml").write_text(
            yaml.safe_dump(
                {"collectors": [{"name": "plain", "type": "steps", "since": "a", "until": "b"}]}
            )  # fmt: skip
        )
        with pytest.raises(UsageError):
            mission.recon.observation.plan("m", interval=(HORIZON - HOUR, HORIZON))


class TestTheCli:
    def _invoke(self, mission: Mission, *args: str) -> Any:
        return CliRunner().invoke(main, ["survey", "m", *args], obj=mission.recon)

    def test_a_range_is_a_backfill_the_plan_shows(self, tmp_path: Path) -> None:
        mission = Mission(tmp_path)
        result = self._invoke(mission, "--dry-run", "--json", "--range",
                              "2026-10-24T09:00Z..2026-10-24T10:00:00+00:00")  # fmt: skip

        assert result.exit_code == 0, result.output
        [run] = json.loads(result.output)["runs"]
        assert run["range"] == {"start": "2026-10-24T09:00:00Z", "end": "2026-10-24T10:00:00Z",
                                "kind": "continuing", "from": "override", "steps": 1}  # fmt: skip

    @pytest.mark.parametrize("given", ["2026-10-24T09:00Z", "2026-10-24T09:00Z..", "..x",
                                       "2026-10-24T09:00Z..soon"])  # fmt: skip
    def test_a_range_that_is_not_two_times_is_a_usage_error(
        self, tmp_path: Path, given: str
    ) -> None:
        result = self._invoke(Mission(tmp_path), "--dry-run", "--range", given)
        assert result.exit_code == 2, result.output

    def test_a_range_whose_end_is_not_after_its_start_is_refused(self, tmp_path: Path) -> None:
        result = self._invoke(Mission(tmp_path), "--dry-run", "--json", "--range",
                              "2026-10-24T10:00Z..2026-10-24T09:00Z")  # fmt: skip
        assert result.exit_code != 0
        assert json.loads(result.output)["error"]["kind"] == "usage"

    def test_rebase_and_restart_name_outputs(self, tmp_path: Path) -> None:
        mission = Mission(tmp_path)
        result = self._invoke(mission, "--json", "--rebase", "metrics")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["tables"][0]["range"]["from"] == "default"
        refused = self._invoke(mission, "--json", "--restart", "nothing")
        assert json.loads(refused.output)["error"]["kind"] == "usage"
