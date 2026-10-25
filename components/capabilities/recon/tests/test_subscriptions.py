"""Named subscriptions (rulings R2, R3): ``subscriptions:`` in a mission's config, checked by the
plan; ``match:`` choosing rows by the change row's own fields; and recon keeping what each
acked: an ack is monotone, the stored cursor moves only on one, and one consumer at a time
holds a subscription."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from click.testing import CliRunner

from recon.adapters._in.cli import main
from recon.adapters._out.subscription_files import SubscriptionFiles
from recon.application.plan import build_plan
from recon.composition import load_plugins
from recon.domain.errors import NotFoundError, ReconError, UsageError
from recon.domain.subscriptions import matches
from tests.test_changes import Surveys

if TYPE_CHECKING:
    from pathlib import Path

A = {"id": "a", "subject": "hello", "from": "ann@example.com"}
B = {"id": "b", "subject": "invoice due", "from": "billing@example.com"}
C = {"id": "c", "subject": "lunch?", "from": "cal@example.org"}


def _inbox(**entry: Any) -> dict[str, Any]:
    return {"name": "inbox", "type": "api", "source": "mail", "endpoint": "/m", "extract": "items",
            "changes": {"key": ["id"], "deletes": True}, **entry}  # fmt: skip


def _mission(project: Path, subscriptions: list[dict[str, Any]], **entry: Any) -> None:
    mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}],
               "collectors": [_inbox(**entry)], "subscriptions": subscriptions}  # fmt: skip
    (project / ".recon" / "m").mkdir(parents=True, exist_ok=True)
    (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))


def _plan(subscriptions: Any, **entry: Any) -> Any:
    mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}],
               "collectors": [_inbox(**entry)], "subscriptions": subscriptions}  # fmt: skip
    return build_plan(mission, load_plugins(), {})


def _problems(plan: Any) -> dict[str, str]:
    return {problem.path: problem.kind for problem in plan.problems}


class TestThePlan:
    def test_a_subscription_plans_and_says_its_match(self) -> None:
        billing = {"op": ["insert", "update"], "record.from": "*@example.com"}
        plan = _plan(
            [{"name": "new", "output": "inbox"},
             {"name": "billing", "output": "inbox", "match": billing}]
        )  # fmt: skip
        assert plan.valid, plan.problems
        assert [s.name for s in plan.config.subscriptions] == ["new", "billing"]
        said = {value.path: (value.value, value.origin) for value in plan.provenance}
        assert said["subscriptions.0.match"] == ({}, "recon")
        assert said["subscriptions.1.match"][1] == "mission"

    def test_no_subscriptions_is_the_default(self) -> None:
        mission = {"collectors": [{"name": "x", "type": "cli", "run": "echo"}]}
        plan = build_plan(mission, load_plugins(), {})
        assert plan.valid and plan.config is not None and plan.config.subscriptions == []

    def test_an_output_with_no_change_rows_is_refused_naming_those_with(self) -> None:
        plan = _plan([{"name": "s", "output": "outbox"}])
        [problem] = plan.problems
        assert (problem.path, problem.kind) == ("subscriptions.0.output", "name_unknown")
        assert problem.allowed == ("inbox",) and problem.fix

    def test_an_output_of_a_collector_without_changes_has_no_change_rows(self) -> None:
        mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}],
                   "collectors": [{"name": "inbox", "type": "api", "source": "mail",
                                   "endpoint": "/m"}],
                   "subscriptions": [{"name": "s", "output": "inbox"}]}  # fmt: skip
        plan = build_plan(mission, load_plugins(), {})
        assert _problems(plan) == {"subscriptions.0.output": "name_unknown"}

    def test_a_match_field_no_change_row_has_is_refused_at_plan_time(self) -> None:
        """R2: a field is checked against the change row's schema, {op, key, record}: the key
        holds the fields of changes.key, the record the columns of normalize:."""
        normalize = {"id": "id", "subject": "subject"}
        plan = _plan([{"name": "s", "output": "inbox",
                       "match": {"subject": "*", "key.subject": "*", "record.from": "*",
                                 "record.subject": "*", "key.id": "a*", "op": "insert"}}],
                     normalize=normalize)  # fmt: skip
        assert _problems(plan) == {
            "subscriptions.0.match.subject": "name_unknown",
            "subscriptions.0.match.key.subject": "name_unknown",
            "subscriptions.0.match.record.from": "name_unknown",
        }
        allowed = plan.problems[0].allowed
        assert allowed == ("op", "key.id", "record.id", "record.subject")

    def test_without_normalize_a_record_field_cannot_be_judged_and_is_taken(self) -> None:
        plan = _plan([{"name": "s", "output": "inbox", "match": {"record.anything": "*"}}])
        assert plan.valid, plan.problems

    @pytest.mark.parametrize(
        ("subscriptions", "path"),
        [
            ([{"name": "s", "output": "inbox"}] * 2, "subscriptions.1.name"),
            ([{"name": "../s", "output": "inbox"}], "subscriptions.0.name"),
            ([{"name": "s", "output": "inbox", "match": {"op": []}}], "subscriptions.0"),
            ([{"name": "s", "output": "inbox", "where": "1=1"}], "subscriptions.0.where"),
            ([{"name": "s"}], "subscriptions.0.output"),
            ({"s": {"output": "inbox"}}, "subscriptions"),
        ],
    )  # fmt: skip
    def test_what_a_subscription_may_not_say(self, subscriptions: Any, path: str) -> None:
        plan = _plan(subscriptions)
        assert path in _problems(plan), plan.problems
        assert all(problem.fix for problem in plan.problems)

    def test_no_sql_filter_is_a_key_of_it(self) -> None:
        """R2 (vector, knuth): no where:, no SQL. A key the schema does not have is refused."""
        plan = _plan([{"name": "s", "output": "inbox", "where": "op = 'insert'"}])
        [problem] = plan.problems
        assert problem.kind == "unknown_key" and "match" in problem.allowed


class TestMatch:
    ROW = {"op": "insert", "key": {"id": "m-1"}, "record": {"subject": "Invoice 12", "n": 3}}

    @pytest.mark.parametrize(
        ("match", "kept"),
        [
            ({}, True),
            ({"op": "insert"}, True),
            ({"op": ["update", "delete"]}, False),
            ({"op": ["update", "ins*"]}, True),
            ({"key.id": "m-?"}, True),
            ({"record.subject": "Invoice*"}, True),
            ({"record.subject": "invoice*"}, False),  # globs match case as written
            ({"record.n": "3"}, True),  # a value matched as its text
            ({"record.missing": "*"}, False),  # absent matches no glob
            ({"op": "insert", "record.subject": "Lunch*"}, False),  # every field must match
        ],
    )  # fmt: skip
    def test_each_field_a_glob_or_any_of_a_list(self, match: dict[str, Any], kept: bool) -> None:
        assert matches(match, self.ROW) is kept

    def test_a_delete_s_record_is_null_and_matches_no_record_glob(self) -> None:
        deleted = {"op": "delete", "key": {"id": "m-1"}, "record": None}
        assert not matches({"record.subject": "*"}, deleted)
        assert matches({"key.id": "*"}, deleted)

    def test_a_subscription_reads_only_the_rows_its_match_keeps(self, tmp_path: Path) -> None:
        bills = {"name": "billing", "output": "inbox", "match": {"record.from": "billing@*"}}
        deletes = {"name": "gone", "output": "inbox", "match": {"op": "delete"}}
        _mission(tmp_path, [bills, deletes])
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        surveys.survey(A, C)
        billing = surveys.recon.subscribe("m", "billing")
        assert [row.key for row in billing.rows] == [{"id": "b"}]
        assert billing.subscription == "billing" and billing.count == 1
        gone = surveys.recon.subscribe("m", "gone")
        assert [(row.op, row.key) for row in gone.rows] == [("delete", {"id": "b"})]


class TestAcks:
    def _surveyed(self, tmp_path: Path) -> Surveys:
        _mission(tmp_path, [{"name": "all", "output": "inbox"}])
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        return surveys

    def test_the_stored_cursor_moves_only_on_an_ack(self, tmp_path: Path) -> None:
        """A consumer that read and crashed before it acked reads the same rows again, with the
        same ids, and what is kept is unchanged."""
        surveys = self._surveyed(tmp_path)
        first = surveys.recon.subscribe("m", "all")
        kept = SubscriptionFiles().state(tmp_path / ".recon" / "m", "all")
        assert kept is None, "a read keeps nothing"
        again = surveys.recon.subscribe("m", "all")
        assert [row.id for row in again.rows] == [row.id for row in first.rows] != []
        acked = surveys.recon.ack("m", "all", first.cursor)
        assert acked.outcome == "acked" and acked.cursor == first.cursor and acked.acked_at
        assert surveys.recon.subscribe("m", "all").rows == []
        surveys.survey(A, B, C)
        assert [row.key for row in surveys.recon.subscribe("m", "all").rows] == [{"id": "c"}]

    def test_an_ack_is_monotone(self, tmp_path: Path) -> None:
        surveys = self._surveyed(tmp_path)
        start = surveys.recon.subscribe("m", "all", max_bytes=1).cursor  # one row read
        later = surveys.recon.subscribe("m", "all").cursor
        assert surveys.recon.ack("m", "all", later).outcome == "acked"
        with pytest.raises(UsageError) as refused:
            surveys.recon.ack("m", "all", start)
        assert refused.value.details["acked"]["row"] > refused.value.details["got"]["row"]
        assert refused.value.fix
        assert surveys.recon.ack("m", "all", later).outcome == "unchanged"
        kept = SubscriptionFiles().state(tmp_path / ".recon" / "m", "all")
        assert kept is not None and kept.cursor == later

    def test_an_ack_of_a_cursor_of_another_output_or_mission_is_refused(
        self, tmp_path: Path
    ) -> None:
        surveys = self._surveyed(tmp_path)
        records = surveys.recon.read_since("m").cursor
        with pytest.raises(UsageError):
            surveys.recon.ack("m", "all", records)
        body = {"mission": "other", "output": "inbox", "archive": None, "row": 0, "at": None,
                "done": False}  # fmt: skip
        with pytest.raises(UsageError):
            surveys.recon.ack("m", "all", "c1" + json.dumps(body).encode().hex())
        never = {**body, "mission": "m", "archive": "2001-01-01-000000-000000"}
        with pytest.raises(UsageError) as refused:
            surveys.recon.ack("m", "all", "c1" + json.dumps(never).encode().hex())
        assert "neither has nor retired" in str(refused.value)
        assert SubscriptionFiles().state(tmp_path / ".recon" / "m", "all") is None

    def test_one_consumer_a_name_a_second_is_busy(self, tmp_path: Path) -> None:
        surveys = self._surveyed(tmp_path)
        cursor = surveys.recon.subscribe("m", "all").cursor
        mission = tmp_path / ".recon" / "m"
        holder = textwrap.dedent(f"""
            import sys, time
            from pathlib import Path
            from recon.adapters._out.subscription_files import SubscriptionFiles
            SubscriptionFiles().hold(Path({str(mission)!r}), "all")
            print("held", flush=True)
            time.sleep(60)
        """)
        child = subprocess.Popen([sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True)
        try:
            assert child.stdout is not None and child.stdout.readline().strip() == "held"
            for consume in (lambda: surveys.recon.subscribe("m", "all"),
                            lambda: surveys.recon.ack("m", "all", cursor),
                            lambda: surveys.recon.unsubscribe("m", "all")):  # fmt: skip
                with pytest.raises(ReconError) as busy:
                    consume()
                assert busy.value.kind == "busy" and busy.value.details["pid"] == child.pid
                assert busy.value.fix
        finally:
            child.kill()
            child.wait()
        assert surveys.recon.subscribe("m", "all").count == 2, "free once its holder died"
        assert SubscriptionFiles().state(mission, "all") is None, "nothing was acked meanwhile"

    def test_one_process_s_second_consumer_is_busy_too(self, tmp_path: Path) -> None:
        self._surveyed(tmp_path)
        store = SubscriptionFiles()
        mission = tmp_path / ".recon" / "m"
        store.hold(mission, "all")
        try:
            with pytest.raises(ReconError) as busy:
                store.hold(mission, "all")
            assert busy.value.kind == "busy"
            with pytest.raises(ReconError):
                SubscriptionFiles().hold(mission, "all")
        finally:
            store.release(mission, "all")
        store.hold(mission, "all")
        store.release(mission, "all")

    def test_a_subscription_the_config_does_not_declare_is_not_found(self, tmp_path: Path) -> None:
        surveys = self._surveyed(tmp_path)
        with pytest.raises(NotFoundError) as missing:
            surveys.recon.subscribe("m", "nope")
        assert missing.value.details["allowed"] == ["all"]

    def test_a_subscription_redeclared_for_another_output_says_how_to_start_again(
        self, tmp_path: Path
    ) -> None:
        surveys = self._surveyed(tmp_path)
        surveys.recon.ack("m", "all", surveys.recon.subscribe("m", "all").cursor)
        mission = yaml.safe_load((tmp_path / ".recon" / "m" / "config.yaml").read_text())
        mission["collectors"].append({**_inbox(), "name": "outbox"})
        mission["subscriptions"] = [{"name": "all", "output": "outbox"}]
        (tmp_path / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))
        with pytest.raises(UsageError) as refused:
            surveys.recon.subscribe("m", "all")
        assert "unsubscribe" in (refused.value.fix or "")
        assert surveys.recon.unsubscribe("m", "all").outcome == "removed"
        assert surveys.recon.subscribe("m", "all").output == "outbox"


class TestStatusAndRemoval:
    def test_status_lists_each_declared_and_each_kept(self, tmp_path: Path) -> None:
        new = {"name": "new", "output": "inbox", "match": {"op": "insert"}}
        _mission(tmp_path, [{"name": "all", "output": "inbox"}, new])
        surveys = Surveys(tmp_path)
        surveys.survey(A)
        surveys.recon.ack("m", "new", surveys.recon.subscribe("m", "new").cursor)
        listed = surveys.recon.status("m").missions[0].subscriptions
        assert [(s.name, s.output, s.declared, s.cursor is not None) for s in listed] == [
            ("all", "inbox", True, False), ("new", "inbox", True, True),
        ]  # fmt: skip
        _mission(tmp_path, [])
        [kept] = surveys.recon.status("m").missions[0].subscriptions
        assert (kept.name, kept.declared, kept.output) == ("new", False, "inbox")
        assert surveys.recon.unsubscribe("m", "new").outcome == "removed"
        assert surveys.recon.status("m").missions[0].subscriptions == []
        assert surveys.recon.unsubscribe("m", "new").outcome == "absent"

    def test_a_removed_subscription_reads_from_the_first_archive_again(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, [{"name": "all", "output": "inbox"}])
        surveys = Surveys(tmp_path)
        surveys.survey(A)
        surveys.recon.ack("m", "all", surveys.recon.subscribe("m", "all").cursor)
        assert surveys.recon.subscribe("m", "all").rows == []
        surveys.recon.unsubscribe("m", "all")
        assert surveys.recon.subscribe("m", "all").count == 1


class TestTheCommandLine:
    def _run(self, recon: Any, *args: str) -> Any:
        return CliRunner().invoke(main, list(args), obj=recon, catch_exceptions=False)

    def test_subscribe_ack_and_unsubscribe(self, tmp_path: Path) -> None:
        _mission(tmp_path, [{"name": "all", "output": "inbox"}])
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        read = self._run(surveys.recon, "subscribe", "m", "all", "--json")
        assert read.exit_code == 0, read.output
        document = json.loads(read.stdout)
        assert document["schema"] == "recon.v1.changes-since" and document["count"] == 2
        assert {"id", "archive", "op", "key", "record"} == set(document["rows"][0])
        acked = self._run(surveys.recon, "ack", "m", "all", document["cursor"], "--json")
        assert json.loads(acked.stdout)["outcome"] == "acked"
        human = self._run(surveys.recon, "subscribe", "m", "all")
        assert human.exit_code == 0 and human.stdout == "" and "recon ack m all" in human.stderr
        removed = self._run(surveys.recon, "unsubscribe", "m", "all", "--json")
        assert json.loads(removed.stdout)["outcome"] == "removed"

    def test_an_older_ack_exits_2_with_the_envelope(self, tmp_path: Path) -> None:
        _mission(tmp_path, [{"name": "all", "output": "inbox"}])
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        first = surveys.recon.subscribe("m", "all", max_bytes=1).cursor
        surveys.recon.ack("m", "all", surveys.recon.subscribe("m", "all").cursor)
        refused = self._run(surveys.recon, "ack", "m", "all", first, "--json")
        assert refused.exit_code == 2
        envelope = json.loads(refused.stderr)
        assert envelope["error"]["kind"] == "usage" and envelope["error"]["fix"]
