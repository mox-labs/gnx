"""Delivery (ruling R4) and its trust boundary (V1): a subscription's ``command:``, given each
row it has not acked as one JSON line on standard input, by no shell.

- Erlang: a burst of 500 new messages is at most 11 runs of the command, the last counting
  the rest exactly; a poison row is set aside after its third attempt and stops holding the
  rows after it; a delivery keeps to its deadline, a hung command included.
- Vector: a mail subject carrying a shell's substitutions, an AppleScript's quotes, a
  terminal's escapes, bidirectional controls and tag characters stays inert through a real
  command, which runs nothing but itself, and reaches no network.
- Acks: what is acked moves only when the command has exited 0, so a delivery that dies
  before an ack gives the same row, with the same id, again.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from click.testing import CliRunner

from recon.adapters._in.cli import main
from recon.adapters._out.command_deliverer import CommandDeliverer
from recon.adapters._out.subscription_files import SubscriptionFiles
from recon.adapters._out.system_clock import SystemClock
from recon.application.plan import build_plan
from recon.application.schedules import offset_s
from recon.composition import load_plugins, root
from recon.domain.documents import ChangesSince, Delivery
from recon.domain.schedule import ScheduleHost
from recon.domain.subscriptions import Delivered, clean
from tests.recon_fakes import FixedClock
from tests.test_changes import Inbox
from tests.test_schedule_install import FakeRunner, Host

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from recon.composition import Recon


class Recording:
    """The Deliverer port, recording each delivery and answering as ``answer`` says."""

    def __init__(self, answer: Callable[[dict[str, Any]], Delivered] | None = None) -> None:
        self.given: list[dict[str, Any]] = []
        self.answer = answer or (lambda delivery: Delivered(True, 0))

    def deliver(
        self, argv: Sequence[str], payload: bytes, *, timeout_s: float, cwd: Path
    ) -> Delivered:
        delivery = json.loads(payload)
        assert payload.endswith(b"\n") and payload.count(b"\n") == 1, "one JSON line"
        self.given.append(delivery)
        return self.answer(delivery)


def _mission(project: Path, deliver: dict[str, Any], **top: Any) -> None:
    mission = {
        "catalog": [{"name": "mail", "url": "https://mail.test"}],
        "collectors": [{"name": "inbox", "type": "api", "source": "mail", "endpoint": "/m",
                        "extract": "items", "changes": {"key": ["id"]},
                        "normalize": {"id": "id", "subject": "subject", "from": "from"}}],
        "subscriptions": [{"name": "new", "output": "inbox", "match": {"op": "insert"},
                           "deliver": {"command": ["/usr/bin/true"], **deliver}}],
        **top,
    }  # fmt: skip
    (project / ".recon" / "m").mkdir(parents=True, exist_ok=True)
    (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))


class Mail:
    """Surveys of a fake inbox, and deliveries through ``deliverer``."""

    def __init__(self, project: Path, deliverer: Any, clock: Any = None) -> None:
        self.inbox = Inbox()
        self.clock = clock or FixedClock()
        self.recon = root({}, project=project, clock=self.clock,
                          requester=lambda t, d, r: self.inbox, deliverer=deliverer)  # fmt: skip

    def survey(self, *messages: dict[str, Any]) -> None:
        if isinstance(self.clock, FixedClock):
            self.clock.advance(1)
        self.inbox.messages = list(messages)
        self.recon.survey("m")


def _messages(n: int, start: int = 0) -> list[dict[str, Any]]:
    return [{"id": f"m{i}", "subject": f"subject {i}", "from": "a@b"} for i in range(start, n)]


class TestABurstIsAFewRuns:
    def test_500_new_messages_are_at_most_11_deliveries_the_last_counting_the_rest(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, {})
        recording = Recording()
        mail = Mail(tmp_path, recording)
        mail.survey(*_messages(500))
        document = mail.recon.deliver("m")
        assert len(recording.given) <= 11
        assert [d["kind"] for d in recording.given] == ["change"] * 10 + ["summary"]
        assert [d["key"]["id"] for d in recording.given[:10]] == [f"m{i}" for i in range(10)]
        summary = recording.given[-1]
        assert summary["count"] == 490 and summary["first"] and summary["last"]
        [run] = document.deliveries
        assert (run.delivered, run.coalesced, run.dead_lettered, run.more) == (10, 490, 0, False)
        assert document.warnings == []

        recording.given.clear()
        assert mail.recon.deliver("m").deliveries[0].delivered == 0 and recording.given == []
        mail.survey(*_messages(503))
        mail.recon.deliver("m")
        assert [d["key"]["id"] for d in recording.given] == ["m500", "m501", "m502"]

    def test_max_per_tick_is_the_mission_s_and_the_plan_says_each_default(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, {"max_per_tick": 2})
        recording = Recording()
        mail = Mail(tmp_path, recording)
        mail.survey(*_messages(5))
        mail.recon.deliver("m")
        assert [d["kind"] for d in recording.given] == ["change", "change", "summary"]
        plan = mail.recon.plan("m")
        said = {value.path: (value.value, value.origin) for value in plan.provenance}
        at = "subscriptions.0.deliver"
        assert said[f"{at}.max_per_tick"] == (2, "mission")
        assert said[f"{at}.attempts"] == (3, "recon")
        assert said[f"{at}.deadline"] == (30.0, "recon")
        assert said[f"{at}.timeout"] == (10.0, "recon")
        assert said[f"{at}.max_chars"] == (200, "recon")
        assert said[f"{at}.fields"] == ([], "recon")
        assert said[f"{at}.command"] == (["/usr/bin/true"], "mission")


class TestAPoisonRow:
    def test_it_is_set_aside_after_3_attempts_and_stops_holding_the_rest(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, {"max_per_tick": 50})
        poison = Recording(lambda d: Delivered(d["key"]["id"] != "m2", 1, "it chokes on m2"))
        mail = Mail(tmp_path, poison)
        mail.survey(*_messages(5))
        mission = tmp_path / ".recon" / "m"
        for attempt in (1, 2):
            document = mail.recon.deliver("m")
            [run] = document.deliveries
            assert run.failing is not None and run.failing.attempts == attempt and run.more
            assert run.failing.error == "it chokes on m2"
            assert [w["kind"] for w in document.warnings] == ["delivery_failed"]
        held = [d["key"]["id"] for d in poison.given]
        assert held == ["m0", "m1", "m2", "m2"], "the rows after it wait"
        document = mail.recon.deliver("m")
        [run] = document.deliveries
        assert (run.delivered, run.dead_lettered, run.failing) == (2, 1, None)
        assert [w["kind"] for w in document.warnings] == ["delivery_dead_lettered"]
        assert [d["key"]["id"] for d in poison.given[4:]] == ["m2", "m3", "m4"]
        assert SubscriptionFiles().dead_letters(mission, "new") == 1
        [letter] = [json.loads(line) for line in (mission / "subscriptions" / "new.dead.jsonl")
                    .read_text().splitlines()]  # fmt: skip
        assert letter["attempts"] == 3 and letter["delivery"]["key"] == {"id": "m2"}
        assert mail.recon.status("m").missions[0].subscriptions[0].dead_letters == 1

    def test_a_failing_row_s_attempts_are_its_own(self, tmp_path: Path) -> None:
        """A row that fails, then goes through, starts the next one's count from nothing."""
        _mission(tmp_path, {"max_per_tick": 50})
        flaky = {"m1": 2}

        def answer(d: dict[str, Any]) -> Delivered:
            left = flaky.get(d["key"]["id"], 0)
            flaky[d["key"]["id"]] = left - 1
            return Delivered(left <= 0, 1, "busy line")

        recording = Recording(answer)
        mail = Mail(tmp_path, recording)
        mail.survey(*_messages(3))
        for _ in range(3):
            mail.recon.deliver("m")
        assert [d["key"]["id"] for d in recording.given] == ["m0", "m1", "m1", "m1", "m2"]
        assert SubscriptionFiles().dead_letters(tmp_path / ".recon" / "m", "new") == 0


def _script(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "bin" / "deliver.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text(body)
    return [sys.executable, str(script)]


class TestADeadline:
    def test_a_delivery_keeps_to_its_deadline_and_leaves_the_rest_for_the_next(
        self, tmp_path: Path
    ) -> None:
        command = _script(tmp_path, "import sys, time\nsys.stdin.read()\ntime.sleep(0.3)\n")
        _mission(tmp_path, {"command": command, "deadline": 1.0})
        mail = Mail(tmp_path, CommandDeliverer({}), clock=SystemClock())
        mail.survey(*_messages(20))
        started = time.monotonic()
        document = mail.recon.deliver("m")
        took = time.monotonic() - started
        [run] = document.deliveries
        assert took < 1.0 + 0.5, f"{took:.2f}s for a deadline of 1 s"
        assert 1 <= run.delivered < 10 and run.more and run.coalesced == 0
        on = mail.recon.deliver("m").deliveries[0]
        assert on.delivered >= 1, "the rest are the next delivery's"

    def test_a_command_that_hangs_is_stopped_at_its_timeout(self, tmp_path: Path) -> None:
        marker = tmp_path / "child.pid"
        body = ("import os, sys, time, subprocess\nsys.stdin.read()\n"
                f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(marker)!r}, 'w').write(str(child.pid))\ntime.sleep(60)\n")  # fmt: skip
        _mission(tmp_path, {"command": _script(tmp_path, body), "timeout": 1.0})
        mail = Mail(tmp_path, CommandDeliverer({}), clock=SystemClock())
        mail.survey(*_messages(2))
        started = time.monotonic()
        document = mail.recon.deliver("m")
        assert time.monotonic() - started < 5
        [run] = document.deliveries
        assert run.delivered == 0 and run.failing is not None
        assert "did not exit within 1s" in run.failing.error
        pid = int(marker.read_text())
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(pid), "every process the command started was stopped with it"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


#: A subject a stranger sent: a shell's substitutions and separators, an AppleScript's quotes,
#: a terminal's OSC title and CSI colour, bidirectional controls and tag characters.
HOSTILE = (
    'Invoice $(touch PWN1) ; touch PWN2 `touch PWN3` " & do shell script "touch PWN4" & "'
    "\x1b]0;owned\x07\x1b[31mred\x1b[0m \u202egnp.exe\u202c \U000e0049\U000e0047\U000e004e"
    "\x9b2J \ud800 end"
)


class TestAHostileSubjectStaysInert:
    def _deliver(self, tmp_path: Path, **deliver: Any) -> tuple[list[bytes], Path]:
        out = tmp_path / "given.jsonl"
        body = ("import sys\n"
                "with open(sys.argv[1], 'ab') as f:\n"
                "    f.write(sys.stdin.buffer.read())\n")  # fmt: skip
        _mission(tmp_path, {"command": [*_script(tmp_path, body), str(out)], **deliver})
        mail = Mail(tmp_path, CommandDeliverer({"PATH": "/usr/bin:/bin"}))
        mail.survey({"id": "x1", "subject": HOSTILE, "from": "mallory@evil.test"})
        document = mail.recon.deliver("m")
        assert document.deliveries[0].delivered == 1, document.warnings
        return out.read_bytes().splitlines(), tmp_path

    def test_through_a_real_command_nothing_runs_and_no_escape_survives(
        self, tmp_path: Path
    ) -> None:
        lines, project = self._deliver(tmp_path, fields=["subject", "from"])
        [line] = lines
        assert line.isascii(), "ASCII JSON: no raw control, bidi or tag byte reaches it"
        given = json.loads(line)
        subject = given["fields"]["subject"]
        assert "$(touch PWN1)" in subject and "`touch PWN3`" in subject, "kept, as text"
        assert '"touch PWN4"' in subject
        for stripped in ("\x1b", "\x07", "\x9b", "\u202e", "\u202c", "\U000e0049", "\ud800"):
            assert stripped not in subject, repr(stripped)
        assert "owned" not in subject, "an OSC sequence goes whole, its text with it"
        assert len(subject) <= 200
        assert not [p for p in project.rglob("PWN*")], "no shell ran"
        assert not list(project.parent.glob("PWN*"))

    def test_by_default_no_body_and_no_subject_is_given(self, tmp_path: Path) -> None:
        [line], _ = self._deliver(tmp_path)
        given = json.loads(line)
        assert given["fields"] == {} and "PWN" not in line.decode()
        shape = {"schema", "kind", "mission", "subscription", "output", "id", "archive", "op",
                 "key", "fields", "count", "first", "last"}  # fmt: skip
        assert set(given) == shape
        assert given["schema"] == "recon.v1.delivery" and given["op"] == "insert"

    def test_the_command_gets_no_variable_of_recon_s_but_the_few_a_notifier_needs(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "env.json"
        body = ("import json, os, sys\nsys.stdin.read()\n"
                f"json.dump(dict(os.environ), open({str(out)!r}, 'w'))\n")  # fmt: skip
        _mission(tmp_path, {"command": _script(tmp_path, body)})
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "MAIL_TOKEN": "sk-SENTINEL"}
        mail = Mail(tmp_path, CommandDeliverer(env))
        mail.survey(*_messages(1))
        mail.recon.deliver("m")
        given = json.loads(out.read_text())
        assert "MAIL_TOKEN" not in given and "sk-SENTINEL" not in out.read_text()
        assert given.get("PATH") == "/usr/bin:/bin"

    def test_the_schema_marks_what_is_untrusted(self) -> None:
        """V1: untrusted content is marked by the schema, never by delimiters in the text."""
        delivery = Delivery.model_json_schema()["properties"]
        assert delivery["key"]["untrusted"] is True and delivery["fields"]["untrusted"] is True
        assert "untrusted" not in delivery["op"] and "untrusted" not in delivery["id"]
        row = ChangesSince.model_json_schema()["$defs"]["ChangeRow"]["properties"]
        assert row["record"]["untrusted"] is True and row["key"]["untrusted"] is True


def test_recon_s_source_holds_no_raw_control_or_bidi_character() -> None:
    """Regression: a formatter turned the stripping pattern's escapes into the raw bidi
    controls they name, which an editor shows as reordered text (Trojan Source). Every
    character the delivery strips is written in recon's source as its escape."""
    source = Path(__file__).resolve().parents[1] / "src" / "recon"
    found = [
        (file.name, number)
        for file in sorted(source.rglob("*.py"))
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1)
        if any(unicodedata.category(c) in ("Cc", "Cf") and c != "\t" for c in line)
    ]
    assert found == []


class TestClean:
    @pytest.mark.parametrize(
        ("text", "kept"),
        [
            ("plain", "plain"),
            ("\x1b[1;31mred\x1b[0m", "red"),
            ("a\x1b]8;;https://evil.test\x1b\\link\x1b]8;;\x1b\\b", "alinkb"),
            ("\x1bPdcs\x1b\\after", "after"),
            ("tab\tand\nline", "tab\tand\nline"),
            ("bell\x07 nul\x00 del\x7f", "bell nul del"),
            ("\u202eabc\u2066x\u2069\u200f", "abcx"),
            ("hi\U000e0068\U000e0069", "hi"),
            ("lone \ud83d surrogate", "lone  surrogate"),
            ("\U0001f600 kept", "\U0001f600 kept"),
        ],
    )  # fmt: skip
    def test_what_it_strips(self, text: str, kept: str) -> None:
        assert clean(text, 200) == kept

    def test_it_cuts_to_max_chars_and_says_so(self) -> None:
        assert clean("x" * 500, 10) == "x" * 9 + "…"
        for text in ("x" * 500, HOSTILE):
            once = clean(text, 50)
            assert len(once) <= 50 and clean(once, 50) == once


class TestAcksInADelivery:
    def test_a_delivery_that_dies_before_its_ack_gives_the_row_again(self, tmp_path: Path) -> None:
        _mission(tmp_path, {})
        given = {"n": 0}

        def dies_on_the_second(d: dict[str, Any]) -> Delivered:
            given["n"] += 1
            if given["n"] == 2:
                raise SystemExit("killed after its command ran, before its ack")
            return Delivered(True, 0)

        recording = Recording(dies_on_the_second)
        mail = Mail(tmp_path, recording)
        mail.survey(*_messages(3))
        with pytest.raises(SystemExit):
            mail.recon.deliver("m")
        state = SubscriptionFiles().state(tmp_path / ".recon" / "m", "new")
        assert state is not None
        acked = mail.recon.changes_since("m", "inbox", state.cursor)
        assert [row.key for row in acked.rows] == [{"id": "m1"}, {"id": "m2"}]
        mail.recon.deliver("m")
        ids = [d["id"] for d in recording.given]
        assert ids[1] == ids[2], "the row given before the crash is given again, same id"
        assert [d["key"]["id"] for d in recording.given] == ["m0", "m1", "m1", "m2"]

    def test_a_consumer_holding_it_makes_a_delivery_skip_it_and_say_so(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, {})
        recording = Recording()
        mail = Mail(tmp_path, recording)
        mail.survey(*_messages(1))
        store = SubscriptionFiles()
        store.hold(tmp_path / ".recon" / "m", "new")
        try:
            document = mail.recon.deliver("m")
        finally:
            store.release(tmp_path / ".recon" / "m", "new")
        [run] = document.deliveries
        assert run.skipped_by == "busy" and recording.given == []
        assert [w["kind"] for w in document.warnings] == ["subscription_busy"]

    def test_a_delivery_that_cannot_keep_its_ack_says_so_and_never_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tick delivers after its survey: a full disk, or a subscription redeclared for
        another output, is that subscription's warning, never the delivery's (or the tick's)
        end."""
        _mission(tmp_path, {})
        recording = Recording()
        mail = Mail(tmp_path, recording)
        mail.survey(*_messages(1))

        def full(*args: Any, **kw: Any) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(SubscriptionFiles, "keep", full)
        document = mail.recon.deliver("m")
        [run] = document.deliveries
        assert run.more and [w["kind"] for w in document.warnings] == ["delivery_failed"]
        assert "free space" in document.warnings[0]["fix"]
        monkeypatch.undo()

        mission = tmp_path / ".recon" / "m"
        from recon.domain.subscriptions import SubscriptionState

        SubscriptionFiles().keep(mission, "new", SubscriptionState(output="outbox", cursor="c1"))
        document = mail.recon.deliver("m")
        assert [w["kind"] for w in document.warnings] == ["delivery_failed"]
        assert "unsubscribe" in document.warnings[0]["fix"]
        SubscriptionFiles().remove(mission, "new")

        from recon.application.subscriptions import ChangeReader
        from recon.domain.errors import ReconError

        def unreadable(*args: Any, **kw: Any) -> None:
            raise ReconError("the archive went", kind="store", fix="survey again")

        monkeypatch.setattr(ChangeReader, "read", unreadable)
        document = mail.recon.deliver("m")
        assert [w["kind"] for w in document.warnings] == ["delivery_failed"]
        assert document.warnings[0]["fix"] == "survey again"

    def test_a_subscription_without_deliver_is_read_not_delivered(self, tmp_path: Path) -> None:
        _mission(tmp_path, {})
        mission = yaml.safe_load((tmp_path / ".recon" / "m" / "config.yaml").read_text())
        mission["subscriptions"].append({"name": "pulled", "output": "inbox"})
        (tmp_path / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))
        recording = Recording()
        mail = Mail(tmp_path, recording)
        mail.survey(*_messages(1))
        assert [run.subscription for run in mail.recon.deliver("m").deliveries] == ["new"]
        from recon.domain.errors import UsageError

        with pytest.raises(UsageError):
            mail.recon.deliver("m", subscription="pulled")


class TestThePlan:
    def _plan(self, deliver: Any, normalize: Any = None) -> Any:
        mission = {
            "catalog": [{"name": "mail", "url": "https://mail.test"}],
            "collectors": [{"name": "inbox", "type": "api", "source": "mail", "endpoint": "/m",
                            "changes": {"key": ["id"]},
                            **({"normalize": normalize} if normalize else {})}],
            "subscriptions": [{"name": "s", "output": "inbox", "deliver": deliver}],
        }  # fmt: skip
        return build_plan(mission, load_plugins(), {})

    def test_a_command_as_one_string_is_refused_with_the_argument_list_to_write(self) -> None:
        plan = self._plan({"command": "notify-send $subject"})
        [problem] = plan.problems
        assert problem.path == "subscriptions.0.deliver.command"
        assert "argument list" in problem.fix

    @pytest.mark.parametrize(
        "deliver",
        [{"command": []}, {"command": [""]}, {"command": ["a\0b"]},
         {"command": ["x"], "max_per_tick": 0}, {"command": ["x"], "attempts": 0},
         {"command": ["x"], "deadline": 0}, {"command": ["x"], "timeout": -1},
         {"command": ["x"], "max_chars": 0}, {"command": ["x"], "fields": ["_position"]},
         {"command": ["x"], "shell": True}],
    )  # fmt: skip
    def test_what_deliver_may_not_say(self, deliver: dict[str, Any]) -> None:
        plan = self._plan(deliver)
        assert plan.problems and all(p.path.startswith("subscriptions.0") for p in plan.problems)

    def test_a_field_normalize_does_not_make_is_refused(self) -> None:
        plan = self._plan({"command": ["x"], "fields": ["subject", "body"]},
                          normalize={"id": "id", "subject": "subject"})  # fmt: skip
        [problem] = plan.problems
        assert (problem.path, problem.kind) == ("subscriptions.0.deliver.fields.1", "name_unknown")


EVERY = 600
SLOT = datetime(2026, 10, 24, 10, 0, tzinfo=UTC) + timedelta(seconds=offset_s("m", EVERY))


class TestATickDelivers:
    def _root(self, project: Path, host: Host, clock: Any, mail: Inbox, deliverer: Any) -> Recon:
        return root(
            {"PATH": "/usr/bin:/bin"}, project=project, clock=clock,
            requester=lambda t, d, r: mail, schedulers=host.scheduler, deliverer=deliverer,
            schedule_host=ScheduleHost("/opt/recon/bin/recon", ("/usr/bin", "/bin")),
            local=lambda at: at,
        )  # fmt: skip

    def test_after_its_survey_seals_and_within_the_config_it_checked(self, tmp_path: Path) -> None:
        _mission(tmp_path, {}, schedule={"every": EVERY})
        home = tmp_path / "home"
        home.mkdir()
        host = Host(home, FakeRunner())
        inbox = Inbox()
        inbox.messages = _messages(2)
        recording = Recording()
        clock = FixedClock(SLOT + timedelta(seconds=2))
        recon = self._root(tmp_path, host, clock, inbox, recording)
        digest = recon.schedule_install("m", platform="launchd").config_sha256 or ""
        tick = recon.schedule_tick("m", config_sha256=digest)
        assert tick.survey is not None and tick.deliveries is not None
        assert [d["key"]["id"] for d in recording.given] == ["m0", "m1"]
        assert tick.deliveries.deliveries[0].delivered == 2
        assert tick.document()["deliveries"]["schema"] == "recon.v1.deliver"

    def test_a_tick_refused_for_a_changed_config_delivers_nothing(self, tmp_path: Path) -> None:
        _mission(tmp_path, {}, schedule={"every": EVERY})
        home = tmp_path / "home"
        home.mkdir()
        host = Host(home, FakeRunner())
        inbox = Inbox()
        inbox.messages = _messages(2)
        recording = Recording()
        clock = FixedClock(SLOT + timedelta(seconds=2))
        recon = self._root(tmp_path, host, clock, inbox, recording)
        digest = recon.schedule_install("m", platform="launchd").config_sha256 or ""
        config = tmp_path / ".recon" / "m" / "config.yaml"
        changed = yaml.safe_load(config.read_text())
        changed["subscriptions"][0]["deliver"]["command"] = ["/tmp/evil"]
        config.write_text(yaml.safe_dump(changed))
        from recon.domain.errors import ReconError

        with pytest.raises(ReconError) as refused:
            recon.schedule_tick("m", config_sha256=digest)
        assert refused.value.kind == "stale" and recording.given == []


def test_recon_deliver_on_the_command_line(tmp_path: Path) -> None:
    _mission(tmp_path, {})
    recording = Recording()
    mail = Mail(tmp_path, recording)
    mail.survey(*_messages(2))
    result = CliRunner().invoke(main, ["deliver", "m", "--json"], obj=mail.recon,
                                catch_exceptions=False)  # fmt: skip
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["schema"] == "recon.v1.deliver"
    assert document["deliveries"][0]["delivered"] == 2
    human = CliRunner().invoke(main, ["deliver", "m"], obj=mail.recon, catch_exceptions=False)
    assert human.exit_code == 0 and "0 delivered" in human.stderr
