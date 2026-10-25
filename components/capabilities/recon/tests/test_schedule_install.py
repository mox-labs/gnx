"""Ruling S3: ``recon schedule install | list | remove | status``, each through the scheduler
port. Its adapters run ``launchctl``, ``systemctl --user`` and ``crontab``; here they run
against a fake that records each argv and runs nothing, with the files written under a
temporary home (ixian round 4, C: S4 and S5). Nothing is installed on this machine.

- install writes the projected files, private, and has the scheduler load them; again with the
  same config it changes nothing; with another it replaces them.
- list shows what is installed for this project; remove leaves no file and no entry, and
  removing again is ``absent``.
- cron's block is recon's alone: every line outside it is kept byte for byte.
- S4: a sentinel secret in the environment and in the config's credentials appears in no
  byte projected, recorded, run or printed; the PATH a tick runs with holds only absolute
  directories no other user can write.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from click.testing import CliRunner

from recon.adapters._in.cli import main
from recon.adapters._out.schedulers import Ran, make_scheduler, safe_path
from recon.composition import root
from recon.domain.errors import ReconError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from recon.composition import Recon
    from recon.domain.schedule import Platform, Scheduler

SENTINEL = "s3ntinel-7f3a9c1e5b"


@dataclass
class FakeRunner:
    """The scheduler's commands, recorded and never run. ``launchctl`` and ``systemctl``
    answer as a willing scheduler does; ``crontab -l`` answers with what ``crontab -`` was
    last given, and with "no crontab" before that."""

    crontab: str | None = None
    ran: list[tuple[str, ...]] = field(default_factory=list)
    given: list[str] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], stdin: str | None = None) -> Ran:
        assert argv[0].startswith("/"), f"a scheduler command by its absolute path: {argv}"
        self.ran.append(tuple(argv))
        if stdin is not None:
            self.given.append(stdin)
        if Path(argv[0]).name == "crontab":
            if list(argv[1:]) == ["-l"]:
                if self.crontab is None:
                    return Ran(1, "", "crontab: no crontab for someone\n")
                return Ran(0, self.crontab, "")
            assert list(argv[1:]) == ["-"] and stdin is not None
            self.crontab = stdin
        return Ran(0, "", "")


@dataclass
class Host:
    home: Path
    runner: FakeRunner

    def scheduler(self, platform: Platform | None) -> Scheduler:
        return make_scheduler(
            platform or "launchd", home=self.home, config_home=self.home / ".config", uid=501,
            run=self.runner, commands={"launchctl": "/bin/launchctl",
                                       "systemctl": "/usr/bin/systemctl",
                                       "crontab": "/usr/bin/crontab"},
        )  # fmt: skip


@pytest.fixture
def host(tmp_path: Path) -> Host:
    home = tmp_path / "home"
    home.mkdir()
    return Host(home, FakeRunner())


def _mission(project: Path, schedule: dict[str, Any] | None = None, name: str = "inbox") -> None:
    config: dict[str, Any] = {"collectors": [{"name": "now", "type": "cli", "run": "date"}]}
    if schedule is not None:
        config["schedule"] = schedule
    mission = project / ".recon" / name
    mission.mkdir(parents=True, exist_ok=True)
    (mission / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))


def _root(project: Path, host: Host, env: dict[str, str] | None = None) -> Recon:
    return root(env or {"PATH": "/usr/bin:/bin"}, project=project, schedulers=host.scheduler)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


PLATFORMS: tuple[Platform, ...] = ("launchd", "systemd", "cron")


class TestInstall:
    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_install_writes_private_files_and_has_the_scheduler_load_them(
        self, tmp_path: Path, host: Host, platform: Platform
    ) -> None:
        project = tmp_path / "project"
        _mission(project, {"every": 600})

        document = _root(project, host).schedule_install("inbox", platform=platform)

        assert document.outcome == "installed"
        assert document.platform == platform and document.every == 600
        assert document.label.startswith("recon.") and document.label.endswith(".inbox")
        assert document.argv[1:4] == ["schedule", "tick", "inbox"]
        assert document.argv[document.argv.index("--project") + 1] == str(project.resolve())
        label, ran = document.label, host.runner.ran
        if platform == "launchd":
            plist = host.home / "Library" / "LaunchAgents" / f"{label}.plist"
            assert document.files == [str(plist)]
            assert _mode(plist) == 0o600 and _mode(plist.parent) == 0o700
            assert ran == [("/bin/launchctl", "bootout", f"gui/501/{label}"),
                           ("/bin/launchctl", "bootstrap", "gui/501", str(plist))]  # fmt: skip
        elif platform == "systemd":
            units = host.home / ".config" / "systemd" / "user"
            unit_files = [str(units / f"{label}.{kind}") for kind in ("service", "timer")]
            assert document.files == unit_files
            assert all(_mode(Path(f)) == 0o600 for f in document.files)
            systemctl = ("/usr/bin/systemctl", "--user")
            assert ran == [(*systemctl, "daemon-reload"), (*systemctl, "enable", f"{label}.timer"),
                           (*systemctl, "restart", f"{label}.timer")]  # fmt: skip
        else:
            assert document.files == ["crontab"]
            assert ran[-1] == ("/usr/bin/crontab", "-"), "the crontab written once, last"
            assert set(ran[:-1]) == {("/usr/bin/crontab", "-l")}, "and only read before"
            assert host.runner.crontab is not None and f"# {label}:" in host.runner.crontab
            assert [w["kind"] for w in document.warnings] == ["missed_ignored"]

    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_install_again_changes_nothing_and_another_config_replaces_it(
        self, tmp_path: Path, host: Host, platform: Platform
    ) -> None:
        _mission(tmp_path, {"every": 600})
        recon = _root(tmp_path, host)
        recon.schedule_install("inbox", platform=platform)
        before = list(host.runner.ran)

        again = recon.schedule_install("inbox", platform=platform)

        assert again.outcome == "unchanged"
        assert [argv for argv in host.runner.ran[len(before) :] if argv[1:] != ("-l",)] == []
        _mission(tmp_path, {"every": 1800})
        assert recon.schedule_install("inbox", platform=platform).outcome == "replaced"

    def test_a_mission_without_a_schedule_is_refused_with_a_fix(
        self, tmp_path: Path, host: Host
    ) -> None:
        _mission(tmp_path)

        with pytest.raises(ReconError) as raised:
            _root(tmp_path, host).schedule_install("inbox")

        assert raised.value.kind == "config"
        [problem] = raised.value.problems
        assert (problem.path, problem.kind) == ("schedule", "missing_key")
        assert "every" in problem.fix
        assert host.runner.ran == []

    def test_a_mission_that_does_not_exist_is_not_found(self, tmp_path: Path, host: Host) -> None:
        with pytest.raises(ReconError) as raised:
            _root(tmp_path, host).schedule_install("nope")
        assert raised.value.kind == "not_found"

    def test_the_scheduler_refusing_is_run_failed_with_what_it_said(
        self, tmp_path: Path, host: Host
    ) -> None:
        _mission(tmp_path, {"every": 600})

        def refuses(argv: Sequence[str], stdin: str | None = None) -> Ran:
            return Ran(5, "", "Bootstrap failed: 5: Input/output error\n")

        host.runner = refuses  # type: ignore[assignment]
        with pytest.raises(ReconError, match="Input/output error") as raised:
            _root(tmp_path, host).schedule_install("inbox", platform="launchd")
        assert raised.value.kind == "run_failed" and raised.value.fix


class TestListAndRemove:
    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_list_round_trips_and_remove_leaves_nothing(
        self, tmp_path: Path, host: Host, platform: Platform
    ) -> None:
        _mission(tmp_path, {"every": 600})
        _mission(tmp_path, {"every": 3600}, name="feeds")
        recon = _root(tmp_path, host)
        recon.schedule_install("inbox", platform=platform)
        recon.schedule_install("feeds", platform=platform)

        listed = recon.schedule_list(platform=platform)
        assert [(s.mission, s.installed) for s in listed.schedules] == [
            ("feeds", True), ("inbox", True)
        ]  # fmt: skip

        removed = recon.schedule_remove("inbox", platform=platform)
        assert removed.outcome == "removed"
        assert [s.mission for s in recon.schedule_list(platform=platform).schedules] == ["feeds"]
        assert recon.schedule_remove("inbox", platform=platform).outcome == "absent"
        recon.schedule_remove("feeds", platform=platform)
        assert recon.schedule_list(platform=platform).schedules == []
        written = [p for p in host.home.rglob("*") if p.is_file()]
        assert written == [], written
        if platform == "cron":
            assert host.runner.crontab == ""

    def test_cron_keeps_every_line_outside_recon_s_block(self, tmp_path: Path, host: Host) -> None:
        mine = "MAILTO=me@example.org\n# my backup\n0 3 * * * /usr/local/bin/backup --all\n"
        host.runner.crontab = mine
        _mission(tmp_path, {"every": 600})
        _mission(tmp_path, {"every": 3600}, name="feeds")
        recon = _root(tmp_path, host)

        recon.schedule_install("inbox", platform="cron")
        recon.schedule_install("feeds", platform="cron")
        assert host.runner.crontab is not None
        assert host.runner.crontab.startswith(mine)
        assert host.runner.crontab.count("# BEGIN recon schedules") == 1
        recon.schedule_remove("inbox", platform="cron")
        recon.schedule_remove("feeds", platform="cron")

        assert host.runner.crontab == mine

    @pytest.mark.parametrize("edit", ["install", "remove"])
    def test_a_block_with_no_end_is_refused_and_the_crontab_kept(
        self, tmp_path: Path, host: Host, edit: str
    ) -> None:
        """Round-4 review M4: with recon's BEGIN line and no END line, every line after the
        BEGIN read as recon's, so an edit would have taken the user's own lines with it. Such
        a crontab is refused, with the fix, and nothing is written."""
        from recon.adapters._out.schedulers import CRON_BEGIN

        theirs = "0 3 * * * /usr/local/bin/backup --all\n"
        broken = f"MAILTO=me@example.org\n{CRON_BEGIN}\n# recon.old.inbox: x\n* * * * * x\n{theirs}"
        host.runner.crontab = broken
        _mission(tmp_path, {"every": 600})
        recon = _root(tmp_path, host)

        with pytest.raises(ReconError) as raised:
            getattr(recon, f"schedule_{edit}")("inbox", platform="cron")

        assert raised.value.kind == "config"
        assert "# END recon schedules" in (raised.value.fix or "")
        assert host.runner.crontab == broken, "nothing written"
        assert not [argv for argv in host.runner.ran if argv[-1] == "-"]

    def test_list_is_this_project_s_schedules_alone(self, tmp_path: Path, host: Host) -> None:
        for project in ("a", "b"):
            _mission(tmp_path / project, {"every": 600})
            _root(tmp_path / project, host).schedule_install("inbox", platform="launchd")

        listed = _root(tmp_path / "a", host).schedule_list(platform="launchd")

        assert [s.mission for s in listed.schedules] == ["inbox"]
        assert len(list((host.home / "Library" / "LaunchAgents").iterdir())) == 2


class TestStatus:
    def test_status_says_installed_and_when_the_next_tick_is_due(
        self, tmp_path: Path, host: Host
    ) -> None:
        _mission(tmp_path, {"every": 600})
        recon = _root(tmp_path, host)
        assert recon.schedule_status("inbox", platform="launchd").schedules[0].installed is False

        recon.schedule_install("inbox", platform="launchd")
        [status] = recon.schedule_status("inbox", platform="launchd").schedules

        assert status.installed and status.config_changed is False
        assert status.next_at is not None and status.every == 600

    def test_a_changed_config_is_said_with_the_fix_to_reinstall(
        self, tmp_path: Path, host: Host
    ) -> None:
        _mission(tmp_path, {"every": 600})
        recon = _root(tmp_path, host)
        recon.schedule_install("inbox", platform="launchd")
        config = tmp_path / ".recon" / "inbox" / "config.yaml"
        config.write_text(config.read_text() + "# a line an agent added\n")

        document = recon.schedule_status("inbox", platform="launchd")

        assert document.schedules[0].config_changed is True
        [warning] = document.warnings
        assert warning["kind"] == "config_changed"
        assert "recon schedule install inbox" in warning["fix"]

    def test_a_projected_file_changed_by_hand_reads_not_installed(
        self, tmp_path: Path, host: Host
    ) -> None:
        _mission(tmp_path, {"every": 600})
        recon = _root(tmp_path, host)
        [plist] = recon.schedule_install("inbox", platform="launchd").files
        Path(plist).write_text(Path(plist).read_text().replace("tick", "survey"))

        assert recon.schedule_status("inbox", platform="launchd").schedules[0].installed is False


class TestTheCommandLine:
    def test_install_list_remove_and_status_print_their_documents(
        self, tmp_path: Path, host: Host
    ) -> None:
        _mission(tmp_path, {"every": 600})
        recon = _root(tmp_path, host)

        def run(*args: str) -> dict[str, Any]:
            result = CliRunner().invoke(main, ["schedule", *args, "--json"], obj=recon,
                                        catch_exceptions=False)  # fmt: skip
            assert result.exit_code == 0, result.stderr
            document: dict[str, Any] = json.loads(result.stdout)
            return document

        assert run("install", "inbox", "--platform", "cron")["schema"] == "recon.v1.schedule"
        listed = run("list", "--platform", "cron")
        assert listed["schema"] == "recon.v1.schedules"
        assert [s["mission"] for s in listed["schedules"]] == ["inbox"]
        assert run("status", "--platform", "cron")["schedules"][0]["installed"] is True
        assert run("remove", "inbox", "--platform", "cron")["outcome"] == "removed"


class TestNoSecretIsProjected:
    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_a_sentinel_secret_is_in_no_byte_projected_recorded_run_or_printed(
        self, tmp_path: Path, host: Host, platform: Platform
    ) -> None:
        """S4: a tick's credentials come from what the mission names at run time (its
        ``auth.token: env:``, a ``file:``, a helper), never from the schedule's files, so
        neither the environment ``install`` runs in nor the config's credential names put a
        secret in any of them."""
        env = {
            "PATH": "/usr/bin:/bin",
            "MAIL_TOKEN": SENTINEL,
            "OTEL_EXPORTER_OTLP_HEADERS": f"authorization=Bearer {SENTINEL}",
            "HOME": str(host.home),
        }
        mission = tmp_path / ".recon" / "inbox"
        mission.mkdir(parents=True)
        config = {
            "schedule": {"every": 600},
            "catalog": [{"name": "mail", "type": "api", "url": "https://mail.example.org",
                         "auth": {"token": "env:MAIL_TOKEN", "header": "Authorization"}}],
            "collectors": [{"name": "new", "type": "api", "source": "mail", "endpoint": "/m"}],
        }  # fmt: skip
        (mission / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
        os.environ["RECON_TEST_SENTINEL"] = SENTINEL  # the process's own, too
        try:
            document = _root(tmp_path, host, env).schedule_install("inbox", platform=platform)
        finally:
            del os.environ["RECON_TEST_SENTINEL"]

        written = b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
                           and p.name != "config.yaml")  # fmt: skip
        ran = json.dumps([host.runner.ran, host.runner.given]).encode()
        printed = json.dumps(document.document()).encode()
        # Ixian S6: check the checker, that something was projected for it to search.
        assert document.label and document.label.encode() in written + ran, "nothing written"
        for where, data in (("files", written), ("commands", ran), ("document", printed)):
            assert SENTINEL.encode() not in data, where
            assert b"OTEL_EXPORTER_OTLP_HEADERS" not in data, where
        assert b"MAIL_TOKEN" not in written + ran, "not even the variable's name is needed"


class TestThePathATickRunsWith:
    def test_only_absolute_directories_no_other_user_can_write(self, tmp_path: Path) -> None:
        good, shared, world = tmp_path / "good", tmp_path / "shared", tmp_path / "world"
        for directory in (good, shared, world):
            directory.mkdir()
        shared.chmod(0o775)
        world.chmod(0o777)
        path = os.pathsep.join([str(good), ".", "", "relative/bin", str(shared), str(world),
                                str(tmp_path / "missing"), str(good)])  # fmt: skip

        assert safe_path(path) == (str(good),)

    def test_only_directories_the_user_or_root_owns(self, tmp_path: Path) -> None:
        """Vector S3: a directory another user owns can be written by that user whatever its
        mode, so it is left out; root's (``/usr/bin``) and the user's own are kept."""
        mine = tmp_path / "mine"
        mine.mkdir()
        path = os.pathsep.join([str(mine), "/usr/bin"])

        assert safe_path(path) == (str(mine), "/usr/bin")
        assert safe_path(path, uid=os.getuid() + 4242) == ("/usr/bin",), "as another user"

    def test_no_directory_inside_the_project(self, tmp_path: Path) -> None:
        """S3: a directory under the project (direnv's ``bin``, ``node_modules/.bin``) holds
        what a checkout or a dependency put there, which the config's digest does not cover:
        a tick never finds a command in it."""
        project = tmp_path / "project"
        inside = [project / "bin", project / "node_modules" / ".bin"]
        outside = tmp_path / "tools"
        for directory in [*inside, outside]:
            directory.mkdir(parents=True)
            directory.chmod(0o755)
        project.chmod(0o755)
        (project / "node_modules").chmod(0o755)
        linked = tmp_path / "linked"
        linked.symlink_to(project / "bin")
        path = os.pathsep.join([*map(str, inside), str(linked), str(outside)])

        assert safe_path(path, project=project) == (str(outside),)

    def test_a_link_inside_the_project_is_dropped_wherever_it_points(self, tmp_path: Path) -> None:
        """Vector's re-check of S3: a link committed in the project (``tools`` to
        ``/usr/bin``) resolves outside it, and a later pull can point it anywhere: an entry
        is judged by its own path too, not only by where it resolves."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "tools").symlink_to("/usr/bin")
        path = os.pathsep.join([str(project / "tools"), "/usr/bin"])

        assert safe_path(path, project=project) == ("/usr/bin",)

    def test_the_root_gives_the_tick_a_path_without_the_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin").chmod(0o755)
        recon = root({"PATH": f"{tmp_path / 'bin'}:/usr/bin"}, project=tmp_path)
        assert recon._schedules is not None
        assert recon._schedules._host.path == ("/usr/bin",)
