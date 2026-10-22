"""``environment.compose``: a docker compose project brought up for a run and down after it.

The requirements pinned here (W8.7): ``down`` is deferred before ``up``, so the stack comes
down on every way the run can end (up done, up failed, up cancelled); each running service
is bound with its published port and the image it runs; liveness is ``compose ps``; a
project a killed run left is swept and a named project already in use is refused; the
preflight says what the host lacks; a dry run says the stack is real; bindings reach the
trials. A fake ``docker`` on PATH plays compose through the real launcher. One live test
runs real compose, and skips cleanly where docker is not there.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from ix_fakes import LOG, FakeLoad, experiment, fake_registry

from ix.adapters._out.compose import ComposeEnvironment
from ix.adapters._out.matrix_process import MatrixLauncher
from ix.application import running
from ix.composition import Root
from ix.composition.extension import POINTS
from ix.domain import schema
from ix.domain.experiment import EnvironmentConfig
from ix.domain.run import HarnessError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from ix.domain.run import Target

Write = Any

#: The fake ``docker``: logs each argv as a JSON line, and answers from a JSON state file.
FAKE_DOCKER = """\
import json, os, sys, time

args = sys.argv[1:]
path = os.environ["FAKE_DOCKER_STATE"]
state = json.load(open(path))
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")


def save():
    with open(path + ".tmp", "w") as out:
        json.dump(state, out)
    os.replace(path + ".tmp", path)


if args[:1] == ["info"]:
    if state.get("daemon_down"):
        sys.stderr.write("Cannot connect to the Docker daemon. Is the docker daemon running?")
        sys.exit(1)
    print("29.0.0")
    sys.exit(0)
if args[:2] == ["compose", "version"]:
    print("Docker Compose version v2.30.0")
    sys.exit(0)
if args[:2] == ["compose", "ls"]:
    print(json.dumps(state.get("projects", [])))
    sys.exit(0)
if "up" in args:
    state["up_pid"] = os.getpid()
    save()
    time.sleep(state.get("up_sleep", 0))
    if state.get("up_exit"):
        sys.stderr.write("service web: unhealthy")
        sys.exit(state["up_exit"])
    state["up"] = state.get("up", 0) + 1
    save()
    sys.exit(0)
if "down" in args:
    state["down"] = state.get("down", 0) + 1
    save()
    sys.exit(0)
if "ps" in args:
    if "-q" in args:
        print(state.get("existing", ""))
        sys.exit(0)
    for row in state.get("containers", []):
        if "--all" in args or row["State"] == "running":
            print(json.dumps(row))
    sys.exit(0)
sys.exit(2)
"""

_WEB = {
    "Service": "web",
    "State": "running",
    "Health": "",
    "ExitCode": 0,
    "Image": "nginx:1.27",
    "Labels": "com.docker.compose.project=p,com.docker.compose.image=sha256:abc123",
    "Publishers": [
        {"URL": "0.0.0.0", "TargetPort": 443, "PublishedPort": 0, "Protocol": "tcp"},
        {"URL": "0.0.0.0", "TargetPort": 80, "PublishedPort": 32768, "Protocol": "tcp"},
    ],
}
_DB = {
    "Service": "db",
    "State": "running",
    "Health": "healthy",
    "ExitCode": 0,
    "Image": "postgres:17",
    "Labels": "com.docker.compose.image=sha256:def456",
    "Publishers": [],
}


class Docker:
    """The fake docker's state and log."""

    def __init__(self, root: Path) -> None:
        self.state_file = root / "docker-state.json"
        self.log_file = root / "docker-log.jsonl"
        self.set(containers=[_WEB, _DB])

    def set(self, **state: Any) -> None:
        current = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        self.state_file.write_text(json.dumps({**current, **state}))

    @property
    def state(self) -> dict[str, Any]:
        found: dict[str, Any] = json.loads(self.state_file.read_text())
        return found

    @property
    def calls(self) -> list[list[str]]:
        if not self.log_file.exists():
            return []
        return [json.loads(line) for line in self.log_file.read_text().splitlines()]

    def verbs(self) -> list[str]:
        """Each compose command run, in order: ``up``, ``ps``, ``down``, ``ls``."""
        found = []
        for argv in self.calls:
            if argv[:1] != ["compose"]:
                continue
            found.append(next((v for v in ("up", "down", "ps", "ls") if v in argv), argv[1]))
        return found


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Docker:
    tools = tmp_path / "bin"
    tools.mkdir()
    binary = tools / "docker"
    binary.write_text(f"#!{sys.executable}\n{FAKE_DOCKER}")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    fake = Docker(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(fake.state_file))
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(fake.log_file))
    return fake


class Scope:
    """A run's scope: its id, what it deferred, what it emitted."""

    def __init__(self, run_id: str = "exp-20261022T101500Z-ab12") -> None:
        self.run_id = run_id
        self.stopping = asyncio.Event()
        self.releases: list[Any] = []
        self.events: list[tuple[str, dict[str, Any]]] = []

    def defer(self, release: Any, /) -> None:
        self.releases.append(release)

    def emit(self, name: str, /, **fields: Any) -> None:
        self.events.append((name, fields))

    async def release(self) -> None:
        for release in reversed(self.releases):
            await release()


def _compose(tmp_path: Path, **options: Any) -> EnvironmentConfig:
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    return EnvironmentConfig(kind="compose", options={"file": "compose.yaml", **options})


def _environment() -> ComposeEnvironment:
    return ComposeEnvironment(MatrixLauncher())


class TestUpAndDown:
    async def test_each_running_service_is_bound_and_the_stack_comes_down_at_release(
        self, docker: Docker, tmp_path: Path
    ) -> None:
        scope = Scope()
        bindings = await _environment().up(_compose(tmp_path), base=str(tmp_path), scope=scope)
        assert bindings == {
            "db": {"image": "postgres:17", "image_id": "sha256:def456"},
            "web": {
                "host": "127.0.0.1",
                "port": 32768,
                "url": "http://127.0.0.1:32768",
                "image": "nginx:1.27",
                "image_id": "sha256:abc123",
            },
        }
        up = next(argv for argv in docker.calls if "up" in argv)
        project = f"ix-exp-20261022t101500z-ab12-{os.getpid()}"
        assert up == [
            "compose", "-f", str((tmp_path / "compose.yaml").resolve()), "-p", project,
            "up", "-d", "--wait", "--wait-timeout", "120",
        ]  # fmt: skip
        assert docker.verbs() == ["ls", "up", "ps"]  # the sweep, then up, then the bindings
        assert len(scope.releases) == 1 and docker.state.get("down") is None
        await scope.release()
        down = docker.calls[-1]
        assert down[-5:] == ["down", "-v", "--remove-orphans", "--timeout", "10"]
        assert down[:5] == up[:5]  # the same file and project

    async def test_a_failed_up_fails_the_run_and_still_comes_down(
        self, docker: Docker, tmp_path: Path
    ) -> None:
        docker.set(up_exit=1)
        scope = Scope()
        with pytest.raises(HarnessError) as failed:
            await _environment().up(_compose(tmp_path), base=str(tmp_path), scope=scope)
        assert failed.value.reason == "not_ready" and "unhealthy" in str(failed.value)
        await scope.release()
        assert docker.state["down"] == 1

    async def test_a_cancelled_up_is_stopped_and_still_comes_down(
        self, docker: Docker, tmp_path: Path
    ) -> None:
        docker.set(up_sleep=30)
        scope = Scope()
        bringing = asyncio.ensure_future(
            _environment().up(_compose(tmp_path), base=str(tmp_path), scope=scope)
        )
        while "up_pid" not in docker.state:
            await asyncio.sleep(0.02)
        bringing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(bringing, 10)
        with pytest.raises(ProcessLookupError):  # the up command was stopped, not left running
            os.kill(docker.state["up_pid"], 0)
        await scope.release()
        assert docker.state["down"] == 1 and docker.state.get("up") is None

    async def test_its_options_reach_compose(self, docker: Docker, tmp_path: Path) -> None:
        (tmp_path / "ci.env").write_text("TAG=1\n")
        environment = _compose(
            tmp_path,
            project="mine",
            services=["web"],
            wait_timeout="90s",
            profiles=["debug"],
            env_file="ci.env",
            build=True,
            pull="never",
        )
        await _environment().up(environment, base=str(tmp_path), scope=Scope())
        up = next(argv for argv in docker.calls if "up" in argv)
        assert up[3:] == [
            "-p", "mine", "--profile", "debug", "--env-file", str(tmp_path / "ci.env"),
            "up", "-d", "--wait", "--wait-timeout", "90", "--build", "--pull", "never", "web",
        ]  # fmt: skip


class TestTheProject:
    async def test_a_named_project_that_already_has_containers_is_refused(
        self, docker: Docker, tmp_path: Path
    ) -> None:
        docker.set(existing="3f2a9c1d")
        scope = Scope()
        with pytest.raises(HarnessError) as refused:
            await _environment().up(
                _compose(tmp_path, project="mine"), base=str(tmp_path), scope=scope
            )
        assert refused.value.reason == "address_in_use" and "down -v" in str(refused.value)
        assert "up" not in docker.verbs() and scope.releases == []

    async def test_a_stack_a_killed_run_left_is_swept_and_a_live_ones_is_not(
        self, docker: Docker, tmp_path: Path
    ) -> None:
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        file = str((tmp_path / "compose.yaml").resolve())
        docker.set(
            projects=[
                {"Name": f"ix-killed-run-{gone.pid}", "ConfigFiles": file},
                {"Name": f"ix-live-run-{os.getpid()}", "ConfigFiles": file},
                {"Name": f"ix-elsewhere-{gone.pid}", "ConfigFiles": "/other/compose.yaml"},
                {"Name": "someone-else", "ConfigFiles": file},
            ]
        )
        scope = Scope()
        await _environment().up(_compose(tmp_path), base=str(tmp_path), scope=scope)
        swept = [argv for argv in docker.calls if "down" in argv]
        assert swept == [
            ["compose", "-p", f"ix-killed-run-{gone.pid}", "down", "-v", "--remove-orphans"]
        ]
        assert ("ix.environment.swept", {"project": f"ix-killed-run-{gone.pid}", "ok": True}) in (
            scope.events
        )


class TestLiveness:
    async def test_a_service_that_stopped_or_turned_unhealthy_is_lost(
        self, docker: Docker, tmp_path: Path
    ) -> None:
        provider = _environment()
        bindings = await provider.up(_compose(tmp_path), base=str(tmp_path), scope=Scope())
        assert await provider.lost(bindings) is None
        docker.set(containers=[_WEB, {**_DB, "Health": "unhealthy"}])
        assert await provider.lost(bindings) == "service db is unhealthy"
        docker.set(containers=[{**_WEB, "State": "exited", "ExitCode": 137}, _DB])
        assert await provider.lost(bindings) == "service web is exited (exit code 137)"
        docker.set(containers=[_DB])
        assert await provider.lost(bindings) == "service web is gone"


class TestPreflight:
    def test_what_the_host_lacks_is_told_before_anything_starts(
        self, docker: Docker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = _environment()

        def told(**options: Any) -> dict[str, tuple[bool, str]]:
            found = provider.requirements({"file": "compose.yaml", **options}, base=str(tmp_path))
            return {r.need: (r.holds, r.why) for r in found}

        assert told()["the compose file 'compose.yaml' is there"] == (
            False,
            f"'compose.yaml' is not a file at {tmp_path / 'compose.yaml'}",
        )
        (tmp_path / "compose.yaml").write_text("services: {}\n")
        assert all(holds for holds, _ in told().values())
        assert set(told()) == {
            "'docker' on PATH",
            "the docker daemon answers",
            "docker compose is installed",
            "the compose file 'compose.yaml' is there",
        }
        docker.set(daemon_down=True)
        holds, why = told()["the docker daemon answers"]
        assert not holds and "Is the docker daemon running?" in why
        monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
        assert told() == {
            "'docker' on PATH": (False, "'docker' is not on PATH"),
            "the compose file 'compose.yaml' is there": (True, ""),
        }


class _Aiming(FakeLoad):
    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        LOG.append(f"target {options['target']}")
        return await super().apply(options, target, seed=seed)


def _registry() -> Any:
    registry = fake_registry(probe=_Aiming)
    registry.register(
        POINTS["environment"],
        schema.url("environment", "compose"),
        lambda *, launcher: ComposeEnvironment(launcher),
        needs={"launcher"},
    )
    return registry


def _lab(write_experiment: Write, target: str = "{web.url}/x") -> Path:
    data = experiment(repeats=5)
    data["environment"] = {"kind": "compose", "file": "compose.yaml"}
    data["probes"][0]["target"] = target
    file: Path = write_experiment(data)
    (file.parent / "compose.yaml").write_text(yaml.safe_dump({"services": {"web": {}}}))
    return file


class TestARun:
    def test_a_run_brings_the_stack_up_binds_it_into_the_trials_and_takes_it_down(
        self, docker: Docker, write_experiment: Write
    ) -> None:
        LOG.clear()
        root = Root(registry=_registry())
        file = _lab(write_experiment)
        assert root.validate(file).valid  # {web.url}: compose binds url, services unnamed
        planned = root.plan(file)
        assert planned.runnable, planned.unmet
        report = root.run(file, exploratory=True)
        assert report.status == "completed", report
        assert {line for line in LOG if line.startswith("target")} == {
            "target http://127.0.0.1:32768/x"
        }
        assert docker.state["up"] == 1 and docker.state["down"] == 1
        (row,) = [
            json.loads(line)
            for line in Path(report.location, f"{schema.BINDINGS}.jsonl").read_text().splitlines()
        ]
        assert row["services"]["web"]["image_id"] == "sha256:abc123"
        (manifest,) = [
            json.loads(line)
            for line in Path(report.location, f"{schema.RUN}.jsonl").read_text().splitlines()
        ]
        (compose_file,) = [c for c in manifest["contents"] if c["entry"] == "environment"]
        assert (compose_file["option"], compose_file["path"]) == ("file", "compose.yaml")
        assert len(compose_file["digest"]) == 64

    def test_a_service_the_stack_does_not_bring_up_fails_the_run_before_any_trial(
        self, docker: Docker, write_experiment: Write
    ) -> None:
        LOG.clear()
        report = Root(registry=_registry()).run(
            _lab(write_experiment, "{api.url}/x"), exploratory=True
        )
        assert report.status == "failed"
        assert not [line for line in LOG if line.startswith("target")]
        assert docker.state["down"] == 1
        assert any("api.url" in error for error in report.errors)

    def test_a_dry_run_says_the_stack_is_brought_up_for_real(
        self, docker: Docker, write_experiment: Write
    ) -> None:
        file = _lab(write_experiment)
        experiment_ = Root(registry=_registry()).load(file)
        stood, notes = running.stand_in(
            experiment_.model_copy(update={"subjects": {}}),
            _probes(experiment_),
            lambda kind: ComposeEnvironment(MatrixLauncher()) if "compose" in kind else None,
        )
        assert "environment: compose is brought up for real; it has no stand-in" in notes
        assert stood.environment == experiment_.environment


def _probes(found: Any) -> Any:
    from ix.domain.experiment import probe_set

    return probe_set(found.probes)


class TestTracing:
    async def test_bringing_the_environment_up_is_a_span(self) -> None:
        """Regression (ix-outer 20): the environment component ran untraced."""

        class Up:
            async def up(self, environment: Any, *, base: str, scope: Any) -> Any:
                return {"web": {"url": "http://127.0.0.1:1"}}

        class Traced(Scope):
            def __init__(self) -> None:
                super().__init__()
                self.spans: list[tuple[str, dict[str, Any]]] = []

            def trace(self, name: str, /, **attributes: Any) -> Any:
                from contextlib import contextmanager

                spans = self.spans

                @contextmanager
                def span() -> Iterator[Any]:
                    class Span:
                        def set(self, **more: Any) -> None:
                            attributes.update(more)

                        def event(self, name: str, /, **_: Any) -> None:
                            return None

                    yield Span()
                    spans.append((name, attributes))

                return span()

        manifest = _manifest()
        setup = running.setup_of(manifest)
        kit = running.Kit(
            subjects={}, probes={}, sensors={},
            environments={schema.url("environment", "none"): Up()}, clock_ns=lambda: 0,
        )  # fmt: skip
        scope = Traced()
        await running.bring_up(setup, kit, scope)
        assert scope.spans == [
            (
                "ix.environment",
                {"ix.run.id": manifest.run, "ix.environment": "none", "ix.environment.services": 1},
            )
        ]


def _manifest() -> Any:
    from ix.application.experiments import examine

    vocabulary = Root(registry=fake_registry()).vocabulary()
    examined = examine(experiment(), vocabulary, base=".")
    return running.prepare(
        examined, vocabulary, None, run="r-1", base=".", now="", version="", exploratory=True
    ).manifest


# --- the real thing ----------------------------------------------------------------------

_LIVE_IMAGE = "alpine:3.20"


def _docker_here() -> str | None:
    """Why the live test cannot run here; None when it can."""
    if shutil.which("docker") is None:
        return "docker is not on PATH"
    try:
        info = subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=False)
        image = subprocess.run(
            ["docker", "image", "inspect", _LIVE_IMAGE],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"docker does not answer: {error}"
    if info.returncode != 0:
        return "the docker daemon is not running"
    if image.returncode != 0:
        return f"{_LIVE_IMAGE} is not pulled here (the live test pulls nothing)"
    return None


@pytest.mark.skipif(_docker_here() is not None, reason=str(_docker_here()))
async def test_a_real_stack_comes_up_bound_is_watched_and_comes_down(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text(
        textwrap.dedent(
            f"""\
            services:
              web:
                image: {_LIVE_IMAGE}
                pull_policy: never
                command: ["sleep", "300"]
                ports: ["127.0.0.1::8080"]
            """
        )
    )
    provider = ComposeEnvironment(MatrixLauncher())
    scope = Scope(run_id=f"live-{os.getpid()}")
    try:
        bindings = await provider.up(
            EnvironmentConfig(kind="compose", options={"file": "compose.yaml"}),
            base=str(tmp_path),
            scope=scope,
        )
        web = bindings["web"]
        assert web["host"] == "127.0.0.1" and web["url"] == f"http://127.0.0.1:{web['port']}"
        assert str(web["image_id"]).startswith("sha256:")
        assert await provider.lost(bindings) is None
        project = f"ix-live-{os.getpid()}-{os.getpid()}"
        subprocess.run(
            ["docker", "compose", "-p", project, "kill"], capture_output=True, check=True
        )
        assert (await provider.lost(bindings) or "").startswith("service web is exited")
    finally:
        await scope.release()
    left = subprocess.run(
        ["docker", "compose", "ls", "--all", "--format", "json"], capture_output=True, text=True
    )
    assert f"ix-live-{os.getpid()}" not in left.stdout
