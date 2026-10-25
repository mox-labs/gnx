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

    @pytest.mark.parametrize("every", [420, 3540, 86400 * 7, 59, 90])
    def test_an_every_that_does_not_divide_a_day_is_refused_by_the_plan(
        self, plugins: Plugins, every: int
    ) -> None:
        """Ixian S6: 420 s (7 min), 3540 s, a week, under a minute, a minute and a half: no
        scheduler keeps them on one daily grid, so each is a plan problem at
        ``schedule.every`` with the values that are kept."""
        plan = _plan(plugins, schedule={"every": every})

        assert not plan.valid
        assert _problems(plan)["schedule.every"].kind == "invalid_value"

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


def _sourced(
    plugins: Plugins, sources: list[dict[str, Any]], env: dict[str, str] | None = None, **top: Any
) -> Plan:
    """A plan of a mission reading each of ``sources``, one collector a source kind."""
    kinds = {source["type"] for source in sources}
    collectors = [{"name": "mail", "type": "api", "endpoint": "/m"}] if "api" in kinds else []
    collectors += [{"name": "tools", "type": "mcp", "tool": "t"}] if "mcp" in kinds else []
    return build_plan({"catalog": sources, "collectors": collectors, **top}, plugins, env or {})


def _unscheduled(plan: Plan) -> dict[str, Any]:
    return {w.path: w for w in plan.warnings if w.kind == "env_unscheduled"}


class TestATickSeesNoShellVariable:
    """A tick runs in the scheduler's environment, never the user's shell's, so a credential a
    mission names as ``env:NAME`` is unset on every tick even when it is set where the plan
    runs. The plan of a scheduled mission says so for each such reference, with the forms a
    tick resolves itself: a ``file:`` only its owner can read, or ``headers_helper``."""

    API = {"name": "inbox", "type": "api", "url": "https://mail.example.org",
           "auth": {"token": "env:MAIL_TOKEN", "header": "Authorization"}}  # fmt: skip

    def test_an_api_token_from_the_environment_is_warned_with_file_and_helper(
        self, plugins: Plugins
    ) -> None:
        plan = _sourced(plugins, [self.API], schedule={"every": 600})

        assert plan.valid, plan.problems
        [warning] = _unscheduled(plan).values()
        assert (warning.path, warning.got) == ("catalog.0.auth.token", "MAIL_TOKEN")
        assert "tick" in warning.message
        for named in ("auth.token: file:", "0600", "auth.headers_helper",
                      "recon schedule install"):  # fmt: skip
            assert named in warning.fix, named

    def test_it_is_warned_even_when_the_variable_is_set_where_the_plan_runs(
        self, plugins: Plugins
    ) -> None:
        plan = _sourced(plugins, [self.API], {"MAIL_TOKEN": "s3cret"}, schedule={"every": 600})

        assert "catalog.0.auth.token" in _unscheduled(plan)
        assert not [w for w in plan.warnings if w.kind == "env_unset"], "set here: not unset"
        assert all("s3cret" not in str(w) + w.fix for w in plan.warnings)

    def test_an_unscheduled_mission_is_not_warned(self, plugins: Plugins) -> None:
        assert _unscheduled(_sourced(plugins, [self.API], {"MAIL_TOKEN": "x"})) == {}

    @pytest.mark.parametrize(
        "auth",
        [
            {"token": "file:mail.token", "header": "Authorization"},
            {"headers_helper": "/usr/bin/security find-generic-password -w -s mail"},
            {},
        ],
    )
    def test_a_form_a_tick_resolves_itself_is_not_warned(
        self, plugins: Plugins, auth: dict[str, Any]
    ) -> None:
        plan = _sourced(plugins, [{**self.API, "auth": auth}], schedule={"every": 600})

        assert plan.valid, plan.problems
        assert _unscheduled(plan) == {}

    def test_an_mcp_source_s_token_and_server_variables_are_warned_each(
        self, plugins: Plugins
    ) -> None:
        """A kind's own references (``CollectorType.source_env``) are warned as ``auth.token``
        is, so a third party's source kind is held to it through the same entry point."""
        sources: list[dict[str, Any]] = [
            {"name": "s", "type": "mcp", "command": "/usr/local/bin/srv",
             "env": {"GH": "env:GITHUB_TOKEN", "LEVEL": "debug"}},
            {"name": "r", "type": "mcp", "url": "https://x.test/mcp", "token": "env:REMOTE"},
        ]  # fmt: skip
        plan = _sourced(plugins, sources, schedule={"every": 600})

        warned = _unscheduled(plan)
        assert {path: w.got for path, w in warned.items()} == {
            "catalog.0.env.GH": "GITHUB_TOKEN",
            "catalog.1.token": "REMOTE",
        }
        assert "token: file:" in warned["catalog.1.token"].fix
        assert "headers_helper" in warned["catalog.1.token"].fix
        assert "env.GH: file:" in warned["catalog.0.env.GH"].fix
        assert "headers_helper" not in warned["catalog.0.env.GH"].fix, "a server's variable"

    def test_a_source_no_collector_reads_is_not_warned(self, plugins: Plugins) -> None:
        plan = build_plan(
            {"catalog": [self.API], "collectors": [{"name": "now", "type": "cli", "run": "date"}],
             "schedule": {"every": 600}},
            plugins,
            {},
        )  # fmt: skip

        assert _unscheduled(plan) == {}

    def test_the_kind_is_a_warning_kind_recon_publishes(self) -> None:
        from recon.domain.errors import PROBLEM_KINDS

        assert "env_unscheduled" in PROBLEM_KINDS


def _unpinned(plan: Plan) -> dict[str, Any]:
    return {w.path: w for w in plan.warnings if w.kind == "package_unpinned"}


def _cli(plugins: Plugins, run: str, **top: Any) -> Plan:
    return build_plan({"collectors": [{"name": "c", "type": "cli", "run": run}], **top},
                      plugins, {})  # fmt: skip


def _server(plugins: Plugins, command: str, args: list[str], **top: Any) -> Plan:
    source = {"name": "srv", "type": "mcp", "command": command, "args": args}
    collectors = [{"name": "tools", "type": "mcp", "tool": "t"},
                  {"name": "more", "type": "mcp", "tool": "u"}]  # fmt: skip
    return build_plan({"catalog": [source], "collectors": collectors, **top}, plugins, {})


class TestATickRunsTheCodeItWasInstalledWith:
    """``npx`` and ``uvx`` fetch a package they are not given a version of, every time they
    run: a scheduled mission that starts one runs, every tick, whatever was published last.
    The plan of a scheduled mission warns of each, with the pinned form as its fix."""

    @pytest.mark.parametrize(
        ("args", "got"),
        [
            (["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
             "@modelcontextprotocol/server-filesystem"),
            (["--yes", "server-everything@latest"], "server-everything@latest"),
            (["-y", "server-everything@^1.2.0"], "server-everything@^1.2.0"),
            (["server-everything"], "server-everything"),
            (["-y", "-p", "mcp-tools", "tools-server"], "mcp-tools"),
        ],
    )  # fmt: skip
    def test_an_unpinned_npx_server_is_warned_once_with_the_pinned_form(
        self, plugins: Plugins, args: list[str], got: str
    ) -> None:
        plan = _server(plugins, "npx", args, schedule={"every": 600})

        assert plan.valid, plan.problems
        [warning] = _unpinned(plan).values()
        assert (warning.path, warning.got) == ("catalog.0", got)
        assert "every tick" in warning.message and "npx" in warning.message
        for named in ("@<version>", "exact", "recon schedule install"):
            assert named in warning.fix, named

    @pytest.mark.parametrize(
        ("args", "got"),
        [
            (["mcp-server-fetch"], "mcp-server-fetch"),
            (["--from", "mcp-server-git", "mcp-server-git", "--repository", "."],
             "mcp-server-git"),
            (["--python", "3.12", "mcp-server-time@latest"], "mcp-server-time@latest"),
            (["mcp-server-fetch>=2025.1"], "mcp-server-fetch>=2025.1"),
        ],
    )  # fmt: skip
    def test_an_unpinned_uvx_server_is_warned_with_the_pinned_form(
        self, plugins: Plugins, args: list[str], got: str
    ) -> None:
        plan = _server(plugins, "/opt/homebrew/bin/uvx", args, schedule={"every": 600})

        [warning] = _unpinned(plan).values()
        assert warning.got == got
        assert "==<version>" in warning.fix

    @pytest.mark.parametrize(
        ("command", "args"),
        [
            ("npx", ["-y", "@modelcontextprotocol/server-filesystem@2025.8.21", "/tmp"]),
            ("npx", ["-y", "server-everything@1.2.3-beta.1"]),
            ("npx", ["./tools/server.js"]),
            ("uvx", ["mcp-server-fetch==2025.4.7"]),
            ("uvx", ["mcp-server-time@2025.1.0"]),
            ("uvx", ["--from", "mcp-server-git==2025.1.14", "mcp-server-git"]),
            ("uvx", ["--from", "./servers/git", "mcp-server-git"]),
            ("/usr/local/bin/my-server", ["--stdio"]),
        ],
    )
    def test_a_pinned_package_a_local_path_or_another_command_is_not_warned(
        self, plugins: Plugins, command: str, args: list[str]
    ) -> None:
        assert _unpinned(_server(plugins, command, args, schedule={"every": 600})) == {}

    @pytest.mark.parametrize(
        ("run", "got"),
        [
            ("npx -y some-tool --json | jq -c '.[]'", "some-tool"),
            ("cd sub && uvx ruff check --output-format json .", "ruff"),
            ("echo start; /usr/bin/env npx --yes some-tool@next", "some-tool@next"),
        ],
    )
    def test_an_unpinned_package_a_cli_command_runs_is_warned(
        self, plugins: Plugins, run: str, got: str
    ) -> None:
        plan = _cli(plugins, run, schedule={"every": 600})

        [warning] = _unpinned(plan).values()
        assert (warning.path, warning.got) == ("collectors.0", got)

    def test_a_pinned_cli_command_is_not_warned(self, plugins: Plugins) -> None:
        plan = _cli(plugins, "uvx ruff@0.6.9 check . && npx -y some-tool@1.0.0", schedule={
            "every": 600})  # fmt: skip

        assert _unpinned(plan) == {}

    def test_an_unscheduled_mission_is_not_warned(self, plugins: Plugins) -> None:
        assert _unpinned(_server(plugins, "npx", ["-y", "server-everything"])) == {}
        assert _unpinned(_cli(plugins, "uvx ruff check .")) == {}

    # The owner's ruling, round-4 review S7: every fetcher of the kind, helpers too, the
    # command word alone, and a commit as a pin.

    @pytest.mark.parametrize(
        ("run", "got", "pinned"),
        [
            ("bunx some-tool --json", "some-tool", "some-tool@<version>"),
            ("pnpm dlx some-tool", "some-tool", "some-tool@<version>"),
            ("npm exec -- some-tool@latest", "some-tool@latest", "some-tool@<version>"),
            ("pipx run some-tool", "some-tool", "some-tool==<version>"),
            ("pipx run --spec 'some-tool>=2' tool", "some-tool>=2", "some-tool==<version>"),
            ("uv tool run ruff check .", "ruff", "ruff==<version>"),
            ("env FOO=1 npx -y some-tool", "some-tool", "some-tool@<version>"),
            ("FOO=1 BAR=2 uvx some-tool", "some-tool", "some-tool==<version>"),
            ("uvx --from git+https://github.com/o/r@main r", "git+https://github.com/o/r@main",
             None),
            ("npx github:o/r", "github:o/r", None),
        ],
    )  # fmt: skip
    def test_every_fetcher_of_the_kind_is_judged(
        self, plugins: Plugins, run: str, got: str, pinned: str | None
    ) -> None:
        [warning] = _unpinned(_cli(plugins, run, schedule={"every": 600})).values()
        assert warning.got == got
        assert pinned is None or pinned in warning.fix

    @pytest.mark.parametrize(
        "run",
        [
            "bunx some-tool@1.2.3",
            "pnpm dlx some-tool@1.2.3",
            "npm exec -- some-tool@1.0.0",
            "pipx run some-tool==1.0.0",
            "pipx run --spec some-tool==1.0.0 tool",
            "uv tool run ruff==0.6.9 check .",
            "uvx --from git+https://github.com/o/r@0123456789abcdef0123456789abcdef01234567 r",
            "npx github:o/r#0123456789abcdef0123456789abcdef01234567",
            "echo npx some-tool",
            "grep -c uvx build.log",
            "printf 'run pnpm dlx x'",
        ],
    )
    def test_a_pin_or_a_fetcher_that_is_no_command_is_not_warned(
        self, plugins: Plugins, run: str
    ) -> None:
        assert _unpinned(_cli(plugins, run, schedule={"every": 600})) == {}

    def test_a_headers_helper_that_fetches_is_warned_at_its_key(self, plugins: Plugins) -> None:
        sources: list[dict[str, Any]] = [
            {"name": "mail", "type": "api", "url": "https://mail.example.org",
             "auth": {"headers_helper": "npx -y mail-token-helper"}},
            {"name": "r", "type": "mcp", "url": "https://x.test/mcp",
             "headers_helper": "uvx --from 'mcp-auth>=1' mcp-auth"},
        ]  # fmt: skip
        plan = _sourced(plugins, sources, schedule={"every": 600})

        assert {path: w.got for path, w in _unpinned(plan).items()} == {
            "catalog.0.auth.headers_helper": "mail-token-helper",
            "catalog.1.headers_helper": "mcp-auth>=1",
        }

    def test_the_kind_is_a_warning_kind_recon_publishes(self) -> None:
        from recon.domain.errors import PROBLEM_KINDS

        assert "package_unpinned" in PROBLEM_KINDS
