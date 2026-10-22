"""The adapters behind the kinds ix can run, against real processes and no load tool.

The subject here is the example delay server, started on a free port. What k6 would report
is given to the parsing functions as a summary document, so nothing in this file needs k6.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

import ix
from ix.adapters._out import k6_load
from ix.adapters._out.environments import NoEnvironment, ProcessEnvironment
from ix.adapters._out.matrix_process import MatrixLauncher
from ix.adapters._out.processes import accepts, answers
from ix.adapters._out.sensors import MEMORY, MemorySensor, TimingSensor
from ix.adapters._out.subjects import HttpSubject, ProcessSubject
from ix.domain.experiment import EnvironmentConfig, SubjectConfig
from ix.domain.run import HarnessError, Interaction, SubjectError, Target

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

SERVER = Path(ix.__file__).parents[2] / "examples" / "delay-server" / "server.py"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _server(port: int, **options: Any) -> SubjectConfig:
    url = f"http://127.0.0.1:{port}"
    return SubjectConfig(
        kind="process",
        options={
            "command": [sys.executable, str(SERVER), "--port", str(port), "--delay-ms", "1"],
            "url": url,
            "ready": f"{url}/healthz",
            **options,
        },
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class Scope:
    """A stand-in for whatever runs ix's work: the three members and nothing more."""

    def __init__(self) -> None:
        self.stopping = asyncio.Event()
        self.releases: list[Any] = []
        self.events: list[str] = []

    def defer(self, release: Any, /) -> None:
        self.releases.append(release)

    def emit(self, name: str, /, **fields: Any) -> None:
        self.events.append(name)

    async def release(self) -> None:
        for release in reversed(self.releases):
            await release()


class TestReadiness:
    """A ready URL answers when the program gives any status below 500."""

    @pytest.mark.parametrize(
        ("status", "up"), [(200, True), (302, True), (404, True), (500, False), (503, False)]
    )
    async def test_any_status_below_500_is_an_answer(self, status: int, up: bool) -> None:
        async def reply(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\nLocation: /\r\n"
                "Connection: close\r\n\r\n".encode()
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            assert await answers(f"http://127.0.0.1:{port}/healthz") is up

    async def test_nothing_listening_and_a_url_that_is_not_one_are_not_answers(self) -> None:
        assert await answers(f"http://127.0.0.1:{_free_port()}/healthz") is False
        assert await answers("not a url") is False


class TestProcessSubject:
    async def test_it_is_up_when_reached_and_gone_when_released(self) -> None:
        port = _free_port()
        subject = ProcessSubject(MatrixLauncher())
        async with subject.reach("server", _server(port), base=".") as target:
            assert target.subject == "server" and target.facets["url"].endswith(str(port))
            pid = target.facets["pid"]
            assert _alive(pid) and await answers(f"{target.facets['url']}/work")
        assert not _alive(pid) and not await accepts(f"http://127.0.0.1:{port}")

    async def test_it_is_released_when_the_trial_is_cancelled(self) -> None:
        port = _free_port()
        subject = ProcessSubject(MatrixLauncher())
        reached = asyncio.Event()
        pids: list[int] = []

        async def trial() -> None:
            async with subject.reach("s", _server(port), base=".") as target:
                pids.append(target.facets["pid"])
                reached.set()
                await asyncio.Event().wait()

        task = asyncio.ensure_future(trial())
        await reached.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not _alive(pids[0])

    async def test_a_program_that_exits_before_it_is_up_is_the_subjects_failure(self) -> None:
        port = _free_port()
        broken = _server(port).model_copy(
            update={
                "options": {
                    "command": [sys.executable, "-c", "import sys; sys.exit('no config')"],
                    "ready": f"http://127.0.0.1:{port}/healthz",
                }
            }
        )
        with pytest.raises(SubjectError) as failed:
            async with ProcessSubject(MatrixLauncher()).reach("s", broken, base="."):
                pass
        assert failed.value.reason == "exited" and "no config" in str(failed.value)

    async def test_a_program_that_never_comes_up_is_not_ready_and_is_not_left_running(
        self,
    ) -> None:
        port = _free_port()
        silent = _server(port).model_copy(
            update={
                "options": {
                    "command": [sys.executable, "-c", "import time; time.sleep(600)"],
                    "ready": f"http://127.0.0.1:{port}/healthz",
                    "startup_timeout": "300ms",
                }
            }
        )
        started: list[int] = []

        class Recording(MatrixLauncher):
            async def start(self, argv: Any, **options: Any) -> Any:
                process = await super().start(argv, **options)
                started.append(process.pid)
                return process

        with pytest.raises(SubjectError) as failed:
            async with ProcessSubject(Recording()).reach("s", silent, base="."):
                pass
        assert failed.value.reason == "not_ready" and "0.3s" in str(failed.value)
        assert started and not _alive(started[0])

    def test_a_program_that_is_not_there_is_told_before_anything_starts(
        self, tmp_path: Path
    ) -> None:
        subject = ProcessSubject(MatrixLauncher())

        def told(command: list[str], **options: Any) -> str | None:
            found = subject.requirements({"command": command, **options}, base=str(tmp_path))
            return next((required.why for required in found if not required.holds), None)

        assert (
            told(["./proxy-typo"])
            == f"'./proxy-typo' is not an executable file at {tmp_path}/proxy-typo"
        )
        assert told(["no-such-program-anywhere"]) == "'no-such-program-anywhere' is not on PATH"
        assert told([sys.executable]) is None and told(["python3"]) is None
        assert told(["./{build}/proxy"]) is None  # bound per cell; known only then
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "proxy").write_text("#!/bin/sh\n")
        (tmp_path / "bin" / "proxy").chmod(0o755)
        assert told(["./proxy"], cwd="bin") is None

    async def test_a_command_that_cannot_be_started_is_the_subjects_failure(self) -> None:
        missing = SubjectConfig(kind="process", options={"command": ["./no-such-program"]})
        with pytest.raises(SubjectError) as failed:
            async with ProcessSubject(MatrixLauncher()).reach("s", missing, base="."):
                pass
        assert failed.value.reason == "not_started"

    async def test_an_address_that_already_answers_is_refused_as_the_harnesss(self) -> None:
        """A subject left behind by a killed run would otherwise be measured in place of
        the one this trial meant to start."""
        port = _free_port()
        subject = ProcessSubject(MatrixLauncher())
        async with subject.reach("first", _server(port), base="."):
            with pytest.raises(HarnessError) as failed:
                async with subject.reach("second", _server(port), base="."):
                    pass
        assert failed.value.reason == "address_in_use"

    async def test_a_program_that_dies_during_the_probe_is_the_subjects_failure(self) -> None:
        port = _free_port()
        with pytest.raises(SubjectError) as failed:
            async with ProcessSubject(MatrixLauncher()).reach(
                "s", _server(port), base="."
            ) as target:
                os.kill(target.facets["pid"], 9)
                while _alive(target.facets["pid"]) and await accepts(target.facets["url"]):
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.05)  # let the loop see the child exit
        assert failed.value.reason == "exited" and "during the probe" in str(failed.value)


class TestHttpSubject:
    async def test_it_starts_nothing_and_offers_the_url_and_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IX_TEST_TOKEN", "s3cret")
        subject = SubjectConfig(
            kind="http",
            options={
                "url": "https://example.test/",
                "headers": {"X-A": "1"},
                "auth": "env:IX_TEST_TOKEN",
            },
        )
        async with HttpSubject().reach("api", subject, base=".") as target:
            assert target.facets == {
                "url": "https://example.test",
                "headers": {"X-A": "1", "Authorization": "Bearer s3cret"},
            }

    async def test_a_missing_credential_is_the_harnesss_failure(self) -> None:
        subject = SubjectConfig(
            kind="http", options={"url": "https://x.test", "auth": "env:IX_UNSET_VAR"}
        )
        with pytest.raises(HarnessError) as failed:
            async with HttpSubject().reach("api", subject, base="."):
                pass
        assert failed.value.reason == "auth_missing"

    @pytest.mark.parametrize("auth", ["env:IX_UNSET_VAR", "IX_TEST_TOKEN", "env:"])
    def test_an_auth_that_names_no_token_is_told_before_anything_starts(
        self, auth: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IX_TEST_TOKEN", "s3cret")
        (unset,) = HttpSubject().requirements({"url": "https://x.test", "auth": auth}, base=".")
        assert not unset.holds
        assert "s3cret" not in f"{unset.need} {unset.why} {unset.fix}"
        (named,) = HttpSubject().requirements(
            {"url": "https://x.test", "auth": "env:IX_TEST_TOKEN"}, base="."
        )
        assert named.holds and named.need == "variable IX_TEST_TOKEN is set (auth)"


class TestEnvironments:
    async def test_none_brings_up_nothing(self) -> None:
        scope = Scope()
        assert await NoEnvironment().up(EnvironmentConfig(), base=".", scope=scope) == {}
        assert scope.releases == []

    async def test_process_starts_each_service_and_defers_its_stop(self) -> None:
        port = _free_port()
        server = _server(port).options
        environment = EnvironmentConfig(
            kind="process",
            options={
                "services": {"upstream": {"command": server["command"], "ready": server["ready"]}}
            },
        )
        scope = Scope()
        bindings = await ProcessEnvironment(MatrixLauncher()).up(environment, base=".", scope=scope)
        pid = bindings["upstream"]["pid"]
        assert bindings == {
            "upstream": {
                "pid": pid,
                "host": "127.0.0.1",
                "port": port,
                "url": f"http://127.0.0.1:{port}",
                "ready": server["ready"],
            }
        }
        assert _alive(pid) and await answers(server["ready"])
        await scope.release()
        assert not _alive(pid)

    async def test_a_service_that_goes_down_after_it_came_up_is_lost(self) -> None:
        port = _free_port()
        server = _server(port).options
        environment = EnvironmentConfig(
            kind="process",
            options={
                "services": {"upstream": {"command": server["command"], "ready": server["ready"]}}
            },
        )
        scope = Scope()
        provider = ProcessEnvironment(MatrixLauncher())
        bindings = await provider.up(environment, base=".", scope=scope)
        try:
            assert await provider.lost(bindings) is None
            os.kill(bindings["upstream"]["pid"], 9)
            while _alive(bindings["upstream"]["pid"]):
                await asyncio.sleep(0.01)
            assert await provider.lost(bindings) == "service upstream exited"
        finally:
            await scope.release()
        assert await NoEnvironment().lost({}) is None

    async def test_a_pid_it_did_not_start_is_not_read_as_its_service(self) -> None:
        """Regression (ix-outer 15): liveness was ``os.kill(pid, 0)`` on the stored pid, so a
        pid the system gave to another live process read as the service still up, and one
        another user owns raised out of the run. It asks the process it started."""
        provider = ProcessEnvironment(MatrixLauncher())
        assert await provider.lost({"upstream": {"pid": os.getpid()}}) == "service upstream exited"

    async def test_a_service_that_does_not_come_up_fails_and_is_still_released(self) -> None:
        port = _free_port()
        environment = EnvironmentConfig(
            kind="process",
            options={
                "services": {
                    "upstream": {
                        "command": [sys.executable, "-c", "raise SystemExit(3)"],
                        "ready": f"http://127.0.0.1:{port}/healthz",
                    }
                }
            },
        )
        scope = Scope()
        with pytest.raises(HarnessError, match="exited with code 3"):
            await ProcessEnvironment(MatrixLauncher()).up(environment, base=".", scope=scope)
        assert len(scope.releases) == 1
        await scope.release()


class TestSensors:
    def _interaction(self, **parts: Any) -> Interaction:
        return Interaction(seq=0, subject="s", role="control", started_ns=0, duration_ns=1, **parts)

    async def test_timing_reads_what_a_load_probe_left(self) -> None:
        spread = {"min": 1.0, "mean": 2.0, "p50": 2.0, "p90": 3.0, "p95": 3.5, "p99": 4.0,
                  "p999": 4.5, "max": 5.0}  # fmt: skip
        load = {
            "latency_ms": spread, "ttfb_ms": spread, "throughput": 80.0, "error_rate": 0.01,
            "sent": 200, "failed": 2,
        }  # fmt: skip
        sensor = TimingSensor()
        assert sensor.watch(Target(subject="s"), {}) is None
        measured = await sensor.sense(
            self._interaction(observations={"load": load}), truth={}, options={}
        )
        assert measured == {
            "latency": spread, "ttfb": spread, "throughput": 80.0,
            "error_rate": {"events": 2, "units": 200},
        }  # fmt: skip

    async def test_timing_counts_a_dropped_arrival_as_an_error(self) -> None:
        """Regression: arrivals an open model dropped, because every client was waiting on
        the subject, were left out of the error rate."""
        spread = dict.fromkeys(("min", "mean", "p50", "p90", "p95", "p99", "p999", "max"), 1.0)
        load = {"latency_ms": spread, "ttfb_ms": spread, "throughput": 4.8,
                "sent": 32, "failed": 8, "dropped": 219}  # fmt: skip
        measured = await TimingSensor().sense(
            self._interaction(observations={"load": load}), truth={}, options={}
        )
        assert measured["error_rate"] == {"events": 8 + 219, "units": 32 + 219}

    async def test_memory_samples_the_subjects_group_while_it_watches(self) -> None:
        port = _free_port()
        sensor = MemorySensor()
        async with ProcessSubject(MatrixLauncher()).reach("s", _server(port), base=".") as target:
            watch = sensor.watch(target, {"interval": "20ms"})
            assert watch is not None
            async with watch as seen:
                while seen["samples"] < 3:
                    await asyncio.sleep(0.01)
        assert seen["peak_bytes"] > 1_000_000 and seen["interval_s"] == 0.02
        measured = await sensor.sense(
            self._interaction(watched={MEMORY: seen}), truth={}, options={}
        )
        assert measured == {"memory_peak": float(seen["peak_bytes"])}

    async def test_memory_with_no_sample_is_a_sensor_failure_not_a_zero(self) -> None:
        empty = {"peak_bytes": 0, "samples": 0}
        with pytest.raises(RuntimeError, match="no memory sample"):
            await MemorySensor().sense(
                self._interaction(watched={MEMORY: empty}), truth={}, options={}
            )


class TestTheLoadProbeWithoutItsTool:
    def test_a_closed_model_holds_clients_and_warms_up_first(self) -> None:
        plan, duration = k6_load.scenarios(
            {"load_model": "closed", "concurrency": 4, "duration": "3s", "warmup": "500ms"}
        )
        assert duration == 3.0
        assert plan == {
            "warmup": {
                "executor": "constant-vus", "vus": 4, "duration": "0.5s", "gracefulStop": "0s",
            },
            "measured": {
                "executor": "constant-vus", "vus": 4, "duration": "3s", "startTime": "0.5s",
                "gracefulStop": "61s",
            },
        }  # fmt: skip

    def test_every_request_of_the_window_is_waited_for_up_to_its_timeout(self) -> None:
        """Regression: a request in flight when the window closed was cut off and counted
        nowhere, so a subject that hung on some requests read as having no errors. Now k6
        waits past the window for as long as a request may take (option ``timeout``, k6's
        60s when absent), so every request sent ends answered, failed or timed out."""
        plan, _ = k6_load.scenarios({"load_model": "closed", "duration": "3s", "timeout": "2s"})
        assert plan["measured"]["gracefulStop"] == "3s"
        assert k6_load.request_timeout({"timeout": "2s"}) == 2.0
        assert k6_load.request_timeout({}) == k6_load.REQUEST_TIMEOUT_S == 60.0
        probe = k6_load.K6Load(MatrixLauncher())
        assert probe.budget_s({"duration": "3s", "timeout": "2s"}) == 3 + 3 + k6_load.OVERHEAD_S

    def test_an_open_model_holds_an_arrival_rate(self) -> None:
        plan, _ = k6_load.scenarios({"load_model": "open", "rate": "80/s", "duration": "60s"})
        assert list(plan) == ["measured"]
        measured = plan["measured"]
        assert (measured["executor"], measured["rate"], measured["timeUnit"]) == (
            "constant-arrival-rate", 80, "1s",
        )  # fmt: skip
        per_minute, _ = k6_load.scenarios({"load_model": "open", "rate": "30/m", "duration": "60s"})
        assert (per_minute["measured"]["rate"], per_minute["measured"]["timeUnit"]) == (30, "1m")

    @pytest.mark.parametrize(
        "options",
        [
            {"load_model": "open", "duration": "1s"},
            {"load_model": "open", "rate": "fast", "duration": "1s"},
            {"load_model": "sideways", "duration": "1s"},
            {"load_model": "closed", "duration": "soon"},
        ],
    )
    def test_options_it_cannot_carry_out_are_the_harnesss_failure(
        self, options: dict[str, Any]
    ) -> None:
        with pytest.raises(HarnessError) as failed:
            k6_load.scenarios(options)
        assert failed.value.reason == "option"

    def _summary(
        self,
        *,
        sent: int = 268,
        answered: int = 201,
        attempts: int | None = None,
        late: int = 0,
        dropped: int = 2,
    ) -> dict[str, Any]:
        answers = {"min": 51.1, "avg": 59.5, "med": 60.3, "max": 68.4, "p(90)": 62.3,
                   "p(95)": 63.4, "p(99)": 66.2, "p(99.9)": 68.0, "count": answered}  # fmt: skip
        everything = {**{name: 0.0 for name in answers}, "count": sent}
        good = "{scenario:measured,expected_response:true}"
        return {
            "metrics": {
                "ix_attempts{scenario:measured}": {
                    "values": {"count": sent if attempts is None else attempts}
                },
                "ix_late_answers{scenario:measured}": {"values": {"count": late}},
                f"http_req_duration{good}": {"values": answers},
                f"http_req_waiting{good}": {"values": answers},
                f"http_reqs{good}": {"values": {"count": answered}},
                "http_req_duration{scenario:measured}": {"values": everything},
                "http_req_waiting{scenario:measured}": {"values": everything},
                "http_reqs{scenario:measured}": {"values": {"count": sent, "rate": 49.2}},
                # k6 counts a failed request as a "pass" of the http_req_failed rate.
                "http_req_failed{scenario:measured}": {
                    "values": {"rate": (sent - answered) / max(sent, 1), "passes": sent - answered}
                },
                "dropped_iterations{scenario:measured}": {"values": {"count": dropped}},
                "http_req_duration": {"values": {"med": 999.0}},
            },
            "setup_data": {"digest": "ab12"},
        }

    def test_the_observation_is_the_measured_part_only(self) -> None:
        seen = k6_load.observation(self._summary(), model="closed", duration_s=3.0)
        assert seen["requests"] == 201 and seen["throughput"] == pytest.approx(67.0)
        assert seen["error_rate"] == (67 + 2) / (268 + 2) and seen["dropped"] == 2
        assert seen["tool"] == "k6"
        assert seen["latency_ms"] == {
            "min": 51.1, "mean": 59.5, "p50": 60.3, "p90": 62.3, "p95": 63.4, "p99": 66.2,
            "p999": 68.0, "max": 68.4,
        }  # fmt: skip
        # Every statistic a histogram reading can be reduced by is there.
        from ix.domain.experiment import STATISTICS

        assert set(seen["latency_ms"]) == set(STATISTICS["histogram"]) == set(seen["ttfb_ms"])

    def _observed(self, summary: dict[str, Any], *, verify: bool = False) -> dict[str, Any]:
        return k6_load.load_observation(summary, verify=verify, duration_s=3.0, model="open")

    def test_an_open_model_starts_every_client_before_the_load(self) -> None:
        """k6 drops an arrival while it starts a client of its own, so with clients left to
        start, a drop would not mean every client was waiting on the subject."""
        for options in (
            {"load_model": "open", "rate": "20/s", "duration": "6s"},
            {"load_model": "open", "rate": "20/s", "duration": "6s", "concurrency": 5},
        ):
            plan, _ = k6_load.scenarios(options)
            measured = plan["measured"]
            assert measured["preAllocatedVUs"] == measured["maxVUs"]

    def test_a_real_k6_summary_of_a_subject_too_slow_for_the_rate_stands(self) -> None:
        """Captured from k6 v1.6.1: 20 arrivals/s for 3s, 5 clients, a subject that answers
        in 1.5s. k6 dropped 51 arrivals with all 5 clients waiting on the subject."""
        captured = json.loads(
            (Path(__file__).parent / "data" / "k6-summary-slow-subject.json").read_text()
        )
        seen = k6_load.load_observation(
            captured["summary"], verify=False, duration_s=3.0, model="open"
        )
        assert seen["dropped"] == 51 and seen["requests"] == 5
        assert seen["latency_ms"]["p50"] > 1500

    def test_a_body_that_differed_is_the_subjects_failure(self) -> None:
        differed = self._summary()
        differed["metrics"]["dropped_iterations{scenario:measured}"] = {"values": {"count": 0}}
        differed["metrics"]["ix_body_mismatch"] = {"values": {"rate": 0.02}}
        with pytest.raises(SubjectError) as failed:
            self._observed(differed, verify=True)
        assert failed.value.reason == "body_mismatch"
        assert self._observed(differed, verify=False)["requests"] == 201

    def test_failed_requests_count_as_errors_and_never_as_fast_answers(self) -> None:
        seen = k6_load.observation(self._summary(dropped=0), model="closed", duration_s=3.0)
        assert seen["requests"] == 201 and seen["error_rate"] == 0.25
        assert (seen["failed"], seen["sent"]) == (67, 268)
        assert seen["latency_ms"]["min"] == 51.1 and seen["ttfb_ms"]["p50"] == 60.3

    def test_requests_never_answered_and_dropped_arrivals_are_errors(self) -> None:
        """Regression, from a probe of a server that hangs on one request in four: 32
        requests were sent, 24 answered, 8 never were, and 219 arrivals were dropped, and
        the error rate read 0."""
        summary = self._summary(sent=24, answered=24, attempts=32, dropped=219)
        seen = k6_load.observation(summary, model="open", duration_s=5.0)
        assert (seen["sent"], seen["failed"], seen["dropped"]) == (32, 8, 219)
        assert seen["error_rate"] == (8 + 219) / (32 + 219)
        assert seen["requests"] == 24

    def test_an_answer_after_the_window_counts_for_errors_not_throughput(self) -> None:
        seen = k6_load.observation(
            self._summary(late=21, dropped=0), model="closed", duration_s=3.0
        )
        assert seen["requests"] == 180 and seen["throughput"] == pytest.approx(60.0)
        assert (seen["failed"], seen["sent"]) == (67, 268)

    def test_a_load_that_sent_nothing_is_the_harnesss_failure(self) -> None:
        with pytest.raises(HarnessError) as failed:
            k6_load.observation(self._summary(sent=0, answered=0), model="closed", duration_s=3.0)
        assert failed.value.reason == "no_requests"

    def test_a_subject_that_answered_nothing_is_the_subjects_failure(self) -> None:
        with pytest.raises(SubjectError) as failed:
            k6_load.observation(self._summary(answered=0), model="closed", duration_s=3.0)
        assert failed.value.reason == "no_answer"

    @pytest.mark.parametrize(("attempts", "late"), [(8, 0), (8, 8)])
    def test_nothing_answered_inside_the_window_is_the_subjects_failure(
        self, attempts: int, late: int
    ) -> None:
        """Regression: a subject that hung on every request left k6 no request to count,
        and that was classed as the harness's failure, so the trial was dropped as
        missing data. Requests were sent; none was answered inside the window."""
        summary = self._summary(sent=late, answered=late, attempts=attempts, late=late)
        with pytest.raises(SubjectError) as failed:
            k6_load.observation(summary, model="closed", duration_s=3.0)
        assert failed.value.reason == "no_answer"
        assert "inside the measured window" in str(failed.value)

    def test_a_first_response_with_no_body_under_verify_is_the_subjects_failure(self) -> None:
        refused = self._summary()
        refused["setup_data"] = {"digest": None, "failed": "dial: connection refused"}
        refused["metrics"]["dropped_iterations{scenario:measured}"] = {"values": {"count": 0}}
        with pytest.raises(SubjectError, match="connection refused") as failed:
            self._observed(refused, verify=True)
        assert failed.value.reason == "no_answer"
        assert self._observed(refused, verify=False)["requests"] == 201

    async def test_a_missing_tool_is_the_harnesss_failure(self) -> None:
        probe = k6_load.K6Load(MatrixLauncher(), binary="no-such-load-tool")
        options = {"target": "http://127.0.0.1:1/x", "load_model": "closed", "duration": "1s"}
        with pytest.raises(HarnessError) as failed:
            await probe.apply(options, Target(subject="s"), seed=1)
        assert failed.value.reason == "tool_missing"
        assert probe.budget_s({"duration": "3s", "warmup": "1s"}) == (
            4 + k6_load.REQUEST_TIMEOUT_S + 1 + k6_load.OVERHEAD_S
        )
