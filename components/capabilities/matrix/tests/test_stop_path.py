"""However a run ends, its members stop the same way and what they deferred is released.

Every test here synchronises on events the components set, never on elapsed time: a bound
(``grace_s``, ``cleanup_s``, ``deadline_s``) is only ever waited out against a member that
would not have ended on its own.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import pytest

from matrix import (
    ConfigError,
    Executor,
    Flow,
    Inputs,
    Limits,
    Member,
    MemberLimits,
    RunCancelled,
    RunError,
    compile_flow,
)

if TYPE_CHECKING:
    from pathlib import Path

TEXT = "acme.v1.text"


class Step:
    """A component whose body is the coroutine function it is given."""

    def __init__(self, body: Any, *, requires: bool = False) -> None:
        self.requires: dict[str, Any] = {"in": TEXT} if requires else {}
        self.provides = {"out": TEXT}
        self._body = body

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        return {"out": await self._body(inputs.context)}


def flow_of(*members: Member) -> Any:
    return compile_flow(Flow("f", members))


async def _boom(ctx: Any) -> str:
    raise RuntimeError("kaput")


async def test_releases_run_after_a_member_raises_last_registered_first() -> None:
    released: list[str] = []

    async def provision(ctx: Any) -> str:
        ctx.defer(lambda: released.append("first"))

        async def later() -> None:
            released.append("second")

        ctx.defer(later)
        return "handle"

    async def fail_holding_a_resource(ctx: Any) -> str:
        ctx.defer(lambda: released.append("third"))
        raise RuntimeError("kaput")

    compiled = flow_of(
        Member("provision", Step(provision), {"out": "h"}),
        Member("use", Step(fail_holding_a_resource, requires=True), {"in": "h", "out": "u"}),
        Member("teardown", Step(_boom, requires=True), {"in": "u"}),
    )
    with pytest.raises(RunError) as e:
        await Executor().run(compiled)
    run = e.value.run
    assert released == ["third", "second", "first"]
    assert run.releases == [
        {"member": "use", "status": "released", "error": None},
        {"member": "provision", "status": "released", "error": None},
        {"member": "provision", "status": "released", "error": None},
    ]
    assert run.status == "failed" and run.outputs("h") == ("handle",)
    assert run.members["provision"]["status"] == "completed"
    assert run.members["use"]["status"] == "failed"
    # A teardown written as a member is skipped exactly when it is needed; a release is not.
    assert run.members["teardown"] == {"status": "skipped", "reason": "policy: fast"}


async def test_releases_run_on_success_too() -> None:
    released: list[str] = []

    async def body(ctx: Any) -> str:
        ctx.defer(lambda: released.append("x"))
        return "v"

    run = await Executor().run(flow_of(Member("a", Step(body), {"out": "t"})))
    assert run.status == "completed" and released == ["x"]
    assert run.members["a"]["status"] == "completed"


async def test_a_failure_asks_running_members_to_stop_and_keeps_what_they_return() -> None:
    running = asyncio.Event()

    async def cooperative(ctx: Any) -> str:
        running.set()
        await ctx.stopping.wait()
        return "partial"

    async def fails_once_its_sibling_runs(ctx: Any) -> str:
        await running.wait()
        raise RuntimeError("kaput")

    compiled = flow_of(
        Member("sampler", Step(cooperative), {"out": "s"}),
        Member("bad", Step(fails_once_its_sibling_runs), {"out": "b"}),
        Member("after", Step(_boom, requires=True), {"in": "s"}),
    )
    with pytest.raises(RunError) as e:
        await Executor().run(compiled, limits=Limits(concurrency=2))
    run = e.value.run
    assert e.value.member == "bad"
    assert run.members["sampler"]["status"] == "completed"
    assert run.members["sampler"]["reason"] == "run_failed"
    assert run.outputs("s") == ("partial",)
    assert run.members["after"] == {"status": "skipped", "reason": "policy: fast"}
    assert [f["member"] for f in run.failures] == ["bad"]


async def test_a_member_that_ignores_the_stop_request_is_cancelled_after_the_grace() -> None:
    running = asyncio.Event()
    never = asyncio.Event()
    unwound: list[str] = []

    async def deaf(ctx: Any) -> str:
        running.set()
        try:
            await never.wait()
        finally:
            unwound.append("finally")
        return "unreachable"

    async def fails(ctx: Any) -> str:
        await running.wait()
        raise RuntimeError("kaput")

    compiled = flow_of(Member("deaf", Step(deaf), {"out": "d"}), Member("bad", Step(fails)))
    with pytest.raises(RunError) as e:
        await Executor().run(compiled, limits=Limits(concurrency=2, grace_s=0))
    run = e.value.run
    assert unwound == ["finally"]
    assert run.members["deaf"]["status"] == "cancelled"
    assert run.members["deaf"]["reason"] == "run_failed"
    assert "d" not in run.construct


async def test_isolate_lets_independent_members_finish_and_skips_only_dependents() -> None:
    async def ok(ctx: Any) -> str:
        return "fine"

    compiled = flow_of(
        Member("bad", Step(_boom), {"out": "b"}),
        Member("needs-bad", Step(ok, requires=True), {"in": "b", "out": "nb"}),
        Member("needs-that", Step(ok, requires=True), {"in": "nb"}),
        Member("free", Step(ok), {"out": "f"}),
        Member("needs-free", Step(ok, requires=True), {"in": "f", "out": "nf"}),
    )
    with pytest.raises(RunError) as e:
        await Executor().run(compiled, limits=Limits(on_failure="isolate"))
    run = e.value.run
    assert run.status == "failed"
    assert {a: m["status"] for a, m in run.members.items()} == {
        "bad": "failed",
        "needs-bad": "skipped",
        "needs-that": "skipped",
        "free": "completed",
        "needs-free": "completed",
    }
    assert run.members["needs-bad"]["reason"] == "upstream: bad"
    assert run.members["needs-that"]["reason"] == "upstream: needs-bad"
    assert run.outputs("nf") == ("fine",)


async def test_cancelling_mid_member_stops_it_releases_and_keeps_the_partial_run() -> None:
    running = asyncio.Event()
    never = asyncio.Event()
    trail: list[str] = []

    async def first(ctx: Any) -> str:
        ctx.defer(lambda: trail.append("released"))
        return "kept"

    async def hangs(ctx: Any) -> str:
        running.set()
        try:
            await never.wait()
        finally:
            trail.append("unwound")
        return "unreachable"

    compiled = flow_of(
        Member("first", Step(first), {"out": "a"}),
        Member("hangs", Step(hangs, requires=True), {"in": "a", "out": "b"}),
        Member("never-starts", Step(_boom, requires=True), {"in": "b"}),
    )
    task = asyncio.ensure_future(Executor().run(compiled, limits=Limits(grace_s=0)))
    await running.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as e:
        await task
    assert isinstance(e.value, RunCancelled)
    run = e.value.run
    assert task.cancelled()  # still a cancellation to asyncio
    assert trail == ["unwound", "released"]
    assert run.status == "cancelled" and run.ended is not None
    assert run.outputs("a") == ("kept",)
    assert run.members["hangs"]["status"] == "cancelled"
    assert run.members["hangs"]["reason"] == "cancelled"
    assert run.members["never-starts"] == {"status": "skipped", "reason": "cancelled"}
    assert run.failures == []


async def test_a_cancelled_member_that_returns_within_the_grace_keeps_its_output() -> None:
    running = asyncio.Event()

    async def cooperative(ctx: Any) -> str:
        running.set()
        await ctx.stopping.wait()
        return "what I had"

    task = asyncio.ensure_future(
        Executor().run(flow_of(Member("m", Step(cooperative), {"out": "t"})))
    )
    await running.wait()
    task.cancel()
    with pytest.raises(RunCancelled) as e:
        await task
    assert e.value.run.outputs("t") == ("what I had",)
    assert e.value.run.members["m"]["status"] == "completed"
    assert e.value.run.status == "cancelled"


async def test_a_member_that_swallows_cancellation_is_abandoned_and_reported() -> None:
    running = asyncio.Event()
    let_go = asyncio.Event()
    swallowed = asyncio.Event()
    released: list[str] = []

    async def stubborn(ctx: Any) -> str:
        ctx.defer(lambda: released.append("released"))
        running.set()
        while not let_go.is_set():
            try:
                await let_go.wait()
            except asyncio.CancelledError:
                swallowed.set()
        return "too late"

    compiled = flow_of(Member("stubborn", Step(stubborn), {"out": "t"}))
    task = asyncio.ensure_future(Executor().run(compiled, limits=Limits(grace_s=0, cleanup_s=0.01)))
    await running.wait()
    task.cancel()
    with pytest.raises(RunCancelled) as e:
        await task
    run = e.value.run
    try:
        assert swallowed.is_set() and not let_go.is_set()  # it is still running
        assert run.members["stubborn"]["status"] == "cancelled"
        assert run.members["stubborn"]["abandoned"] is True
        assert released == ["released"]  # the run still ended, and still released
        assert "t" not in run.construct
    finally:
        let_go.set()
        await asyncio.sleep(0)


async def test_a_member_past_its_deadline_that_swallows_cancellation_fails_abandoned() -> None:
    let_go = asyncio.Event()

    async def stubborn(ctx: Any) -> str:
        while not let_go.is_set():
            with suppress(asyncio.CancelledError):
                await let_go.wait()
        return "too late"

    compiled = flow_of(Member("stubborn", Step(stubborn), {"out": "t"}))
    limits = Limits(deadline_s=0.01, grace_s=0, cleanup_s=0.01)
    try:
        with pytest.raises(RunError, match="timed out after 0.01s") as e:
            await Executor().run(compiled, limits=limits)
        record = e.value.run.members["stubborn"]
        assert (record["status"], record["reason"], record["abandoned"]) == (
            "failed",
            "deadline",
            True,
        )
    finally:
        let_go.set()
        await asyncio.sleep(0)


async def test_each_member_has_its_own_deadline() -> None:
    never = asyncio.Event()
    proceed = asyncio.Event()

    async def hangs(ctx: Any) -> str:
        assert ctx.deadline is not None
        await never.wait()
        return "unreachable"

    async def unbounded(ctx: Any) -> str:
        assert ctx.deadline is None
        await proceed.wait()
        return "done"

    async def after_the_timeout(ctx: Any) -> str:
        proceed.set()
        return "x"

    compiled = flow_of(
        Member("hangs", Step(hangs), {"out": "h"}),
        Member("unbounded", Step(unbounded), {"out": "u"}),
        Member("timed-out-by-now", Step(after_the_timeout), {"out": "o"}),
    )
    limits = Limits(
        concurrency=2,  # the third member starts only once `hangs` has timed out
        on_failure="isolate",
        grace_s=0,
        members={"hangs": MemberLimits(deadline_s=0.01)},
    )
    with pytest.raises(RunError, match="'hangs' timed out after 0.01s") as e:
        await Executor().run(compiled, limits=limits)
    run = e.value.run
    assert run.members["hangs"]["status"] == "failed"
    assert run.members["hangs"]["reason"] == "deadline"
    # The sibling with no deadline outlived the one that timed out, and was left alone.
    assert run.members["unbounded"]["status"] == "completed"
    assert run.outputs("u") == ("done",)


async def test_a_deadline_is_a_stop_request_first_and_what_is_returned_is_kept() -> None:
    async def returns_what_it_has(ctx: Any) -> str:
        await ctx.stopping.wait()
        return "so far"

    compiled = flow_of(Member("m", Step(returns_what_it_has), {"out": "t"}))
    with pytest.raises(RunError, match="timed out") as e:
        await Executor().run(compiled, limits=Limits(deadline_s=0.01))
    run = e.value.run
    assert run.members["m"]["status"] == "failed" and run.members["m"]["reason"] == "deadline"
    assert run.outputs("t") == ("so far",)


async def test_limits_naming_an_unknown_member_are_refused() -> None:
    compiled = flow_of(Member("a", Step(_boom)))
    with pytest.raises(ConfigError, match="not in the flow"):
        await Executor().run(compiled, limits=Limits(members={"b": MemberLimits(deadline_s=1)}))


async def test_a_release_that_raises_or_overruns_is_reported_and_the_rest_still_run() -> None:
    never = asyncio.Event()
    released: list[str] = []

    def raises() -> None:
        raise OSError("device busy")

    async def overruns() -> None:
        await never.wait()

    async def body(ctx: Any) -> str:
        ctx.defer(lambda: released.append("first"))
        ctx.defer(raises)
        ctx.defer(overruns)
        ctx.defer(lambda: released.append("last"))
        return "v"

    run = await Executor().run(
        flow_of(Member("a", Step(body), {"out": "t"})), limits=Limits(cleanup_s=0.01)
    )
    assert released == ["last", "first"]
    assert run.status == "completed"  # a failed release is reported, not a failed run
    assert run.releases == [
        {"member": "a", "status": "released", "error": None},
        {"member": "a", "status": "abandoned", "error": "still running after 0.01s"},
        {"member": "a", "status": "failed", "error": "OSError: device busy"},
        {"member": "a", "status": "released", "error": None},
    ]


async def test_a_release_deferred_after_the_member_ended_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    kept: list[Any] = []
    called: list[str] = []

    async def leaks_its_context(ctx: Any) -> str:
        kept.append(ctx)
        return "v"

    async def uses_the_leaked_context(ctx: Any) -> str:
        kept[0].defer(lambda: called.append("late"))
        return "v"

    compiled = flow_of(
        Member("a", Step(leaks_its_context), {"out": "t"}),
        Member("b", Step(uses_the_leaked_context, requires=True), {"in": "t"}),
    )
    run = await Executor().run(compiled)
    assert called == [] and run.releases == []
    assert "after it ended" in caplog.text


_SIGNALLED = """
    import asyncio, sys
    from pathlib import Path
    from matrix import Executor, Flow, Member, RunCancelled, compile_flow

    marker = Path(sys.argv[1])

    class Holds:
        requires = {}
        provides = {"out": "acme.v1.text"}

        async def run(self, inputs):
            inputs.context.defer(lambda: marker.write_text("released"))
            print("ready", flush=True)
            await asyncio.Event().wait()

    async def main():
        flow = compile_flow(Flow("f", (Member("holds", Holds(), {"out": "t"}),)))
        try:
            await Executor().run(flow)
        except RunCancelled as e:
            print(e.run.status, e.run.members["holds"]["status"], flush=True)
            raise

    try:
        asyncio.run(main())
    except (asyncio.CancelledError, KeyboardInterrupt):
        sys.exit(42)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signalled_run_stops_its_members_and_releases(
    tmp_path: Path, sig: signal.Signals
) -> None:
    marker = tmp_path / "marker"
    script = tmp_path / "signalled.py"
    script.write_text(textwrap.dedent(_SIGNALLED))
    with subprocess.Popen(
        [sys.executable, str(script), str(marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    ) as proc:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"  # the member holds its resource
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=60)
    assert out.strip() == "cancelled cancelled", err
    assert marker.read_text() == "released"
    assert proc.returncode == 42, err
