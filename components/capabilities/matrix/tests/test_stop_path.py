"""However a run ends, its components stop the same way and what they deferred is released.

Every test here synchronises on events the components set, never on elapsed time: a bound
(``grace_s``, ``cleanup_s``, ``deadline_s``) is only ever waited out against a component that
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
    ComponentLimits,
    ConfigError,
    Executor,
    Inputs,
    Limits,
    RunCancelled,
    RunContext,
    RunError,
    wire,
)

if TYPE_CHECKING:
    from pathlib import Path

A, B, C, D = "acme.v1.a", "acme.v1.b", "acme.v1.c", "acme.v1.d"


class Step:
    """A component whose body is a coroutine function of its context; what the body returns
    is one row of the kind the step provides."""

    def __init__(self, body: Any, *, requires: Any = (), provides: str | None = None) -> None:
        self.requires = frozenset(requires)
        self.provides = frozenset([provides] if provides else [])
        self._body = body

    async def run(self, inputs: Inputs, ctx: RunContext) -> Any:
        row = await self._body(ctx)
        return {kind: row for kind in self.provides}


async def _boom(ctx: Any) -> str:
    raise RuntimeError("kaput")


async def test_releases_run_after_a_component_raises_last_registered_first() -> None:
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

    wiring = wire(
        {
            "provision": Step(provision, provides=A),
            "use": Step(fail_holding_a_resource, requires=[A], provides=B),
            "teardown": Step(_boom, requires=[B]),
        }
    )
    with pytest.raises(RunError) as e:
        await Executor().run(wiring)
    run = e.value.run
    assert released == ["third", "second", "first"]
    assert [r.document() for r in run.releases] == [
        {"component": "use", "status": "released", "error": None},
        {"component": "provision", "status": "released", "error": None},
        {"component": "provision", "status": "released", "error": None},
    ]
    assert run.status == "failed" and run.rows(A) == ("handle",)
    assert run.components["provision"].status == "completed"
    assert run.components["use"].status == "failed"
    # A teardown written as a component is skipped exactly when it is needed; a release is not.
    assert run.components["teardown"].document() == {"status": "skipped", "reason": "policy: fast"}


async def test_releases_run_on_success_too() -> None:
    released: list[str] = []

    async def body(ctx: Any) -> str:
        ctx.defer(lambda: released.append("x"))
        return "v"

    run = await Executor().run(wire({"a": Step(body, provides=A)}))
    assert run.status == "completed" and released == ["x"]
    assert run.components["a"].status == "completed"


async def test_a_failure_asks_running_components_to_stop_and_keeps_what_they_return() -> None:
    """What a component returns after a stop request is recorded, and it ends ``stopped``:
    what it returned may be partial, so it is not ``completed``."""
    running = asyncio.Event()

    async def cooperative(ctx: Any) -> str:
        running.set()
        await ctx.stopping.wait()
        return "partial"

    async def fails_once_its_sibling_runs(ctx: Any) -> str:
        await running.wait()
        raise RuntimeError("kaput")

    wiring = wire(
        {
            "sampler": Step(cooperative, provides=A),
            "bad": Step(fails_once_its_sibling_runs, provides=B),
            "after": Step(_boom, requires=[A]),
        }
    )
    with pytest.raises(RunError) as e:
        await Executor().run(wiring)
    run = e.value.run
    assert e.value.component == "bad"
    assert run.components["sampler"].status == "stopped"
    assert run.components["sampler"].reason == "run_failed"
    assert run.rows(A) == ("partial",)
    assert run.components["after"].document() == {"status": "skipped", "reason": "policy: fast"}
    assert [f.component for f in run.failures] == ["bad"]


async def test_a_component_that_ignores_the_stop_request_is_cancelled_after_the_grace() -> None:
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

    wiring = wire({"deaf": Step(deaf, provides=A), "bad": Step(fails)})
    with pytest.raises(RunError) as e:
        await Executor().run(wiring, limits=Limits(grace_s=0))
    run = e.value.run
    assert unwound == ["finally"]
    assert run.components["deaf"].status == "cancelled"
    assert run.components["deaf"].reason == "run_failed"
    assert A not in run.construct


async def test_isolate_lets_independent_components_finish_and_skips_only_dependents() -> None:
    async def ok(ctx: Any) -> str:
        return "fine"

    wiring = wire(
        {
            "bad": Step(_boom, provides=A),
            "needs-bad": Step(ok, requires=[A], provides=B),
            "needs-that": Step(ok, requires=[B]),
            "free": Step(ok, provides=C),
            "needs-free": Step(ok, requires=[C], provides=D),
        }
    )
    with pytest.raises(RunError) as e:
        await Executor().run(wiring, limits=Limits(on_failure="isolate"))
    run = e.value.run
    assert run.status == "failed"
    assert {name: c.status for name, c in run.components.items()} == {
        "bad": "failed",
        "needs-bad": "skipped",
        "needs-that": "skipped",
        "free": "completed",
        "needs-free": "completed",
    }
    assert run.components["needs-bad"].reason == "upstream: bad"
    assert run.components["needs-that"].reason == "upstream: needs-bad"
    assert run.rows(D) == ("fine",)


async def test_cancelling_mid_component_stops_it_releases_and_keeps_the_partial_run() -> None:
    running = asyncio.Event()
    never = asyncio.Event()
    trail: list[str] = []

    async def first(ctx: Any) -> str:
        ctx.defer(lambda: trail.append("released"))
        return "kept"

    async def hangs(ctx: Any) -> str:
        ctx.provide(B, "handed over before the cancel")
        running.set()
        try:
            await never.wait()
        finally:
            trail.append("unwound")
        return "unreachable"

    wiring = wire(
        {
            "first": Step(first, provides=A),
            "hangs": Step(hangs, requires=[A], provides=B),
            "never-starts": Step(_boom, requires=[B]),
        }
    )
    task = asyncio.ensure_future(Executor().run(wiring, limits=Limits(grace_s=0)))
    await running.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as e:
        await task
    assert isinstance(e.value, RunCancelled)
    run = e.value.run
    assert task.cancelled()  # still a cancellation to asyncio
    assert trail == ["unwound", "released"]
    assert run.status == "cancelled" and run.ended is not None
    assert run.rows(A) == ("kept",)
    assert run.rows(B) == ("handed over before the cancel",)
    assert run.components["hangs"].status == "cancelled"
    assert run.components["hangs"].reason == "cancelled"
    assert run.components["never-starts"].document() == {"status": "skipped", "reason": "cancelled"}
    assert run.failures == []


async def test_a_cancelled_component_that_returns_within_the_grace_keeps_its_rows() -> None:
    running = asyncio.Event()

    async def cooperative(ctx: Any) -> str:
        running.set()
        await ctx.stopping.wait()
        return "what I had"

    task = asyncio.ensure_future(Executor().run(wire({"m": Step(cooperative, provides=A)})))
    await running.wait()
    task.cancel()
    with pytest.raises(RunCancelled) as e:
        await task
    assert e.value.run.rows(A) == ("what I had",)
    assert e.value.run.components["m"].document()["status"] == "stopped"
    assert e.value.run.components["m"].reason == "cancelled"
    assert e.value.run.status == "cancelled"


async def test_a_component_that_returned_before_the_stop_request_is_completed() -> None:
    """A failure handled after a sibling has already returned does not make the sibling's
    finished result read as stopped."""
    failing = asyncio.Event()

    async def done_first(ctx: Any) -> str:
        await failing.wait()
        return "whole"

    async def fails(ctx: Any) -> str:
        failing.set()
        raise RuntimeError("kaput")

    wiring = wire({"done": Step(done_first, provides=A), "bad": Step(fails, provides=B)})
    with pytest.raises(RunError) as e:
        await Executor().run(wiring)
    assert e.value.run.components["done"].document()["status"] == "completed"
    assert e.value.run.components["done"].reason is None
    assert e.value.run.rows(A) == ("whole",)


async def test_a_component_that_swallows_cancellation_is_abandoned_and_reported() -> None:
    running = asyncio.Event()
    let_go = asyncio.Event()
    swallowed = asyncio.Event()
    released: list[str] = []
    kept: list[Any] = []

    async def stubborn(ctx: Any) -> str:
        kept.append(ctx)
        ctx.defer(lambda: released.append("released"))
        running.set()
        while not let_go.is_set():
            try:
                await let_go.wait()
            except asyncio.CancelledError:
                swallowed.set()
        return "too late"

    wiring = wire({"stubborn": Step(stubborn, provides=A)})
    task = asyncio.ensure_future(Executor().run(wiring, limits=Limits(grace_s=0, cleanup_s=0.01)))
    await running.wait()
    task.cancel()
    with pytest.raises(RunCancelled) as e:
        await task
    run = e.value.run
    try:
        assert swallowed.is_set() and not let_go.is_set()  # it is still running
        assert run.components["stubborn"].status == "cancelled"
        assert run.components["stubborn"].abandoned is True
        assert released == ["released"]  # the run still ended, and still released
        kept[0].provide(A, "after the run ended")  # and it can no longer write to the record
        assert A not in run.construct
    finally:
        let_go.set()
        await asyncio.sleep(0)


async def test_a_component_past_its_deadline_that_swallows_cancellation_fails_abandoned() -> None:
    let_go = asyncio.Event()

    async def stubborn(ctx: Any) -> str:
        while not let_go.is_set():
            with suppress(asyncio.CancelledError):
                await let_go.wait()
        return "too late"

    wiring = wire({"stubborn": Step(stubborn, provides=A)})
    limits = Limits(deadline_s=0.01, grace_s=0, cleanup_s=0.01)
    try:
        with pytest.raises(RunError, match="timed out after 0.01s") as e:
            await Executor().run(wiring, limits=limits)
        record = e.value.run.components["stubborn"]
        assert (record.status, record.reason, record.abandoned) == (
            "failed",
            "deadline",
            True,
        )
    finally:
        let_go.set()
        await asyncio.sleep(0)


async def test_each_component_has_its_own_deadline() -> None:
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

    wiring = wire(
        {
            "hangs": Step(hangs, provides=A),
            "unbounded": Step(unbounded, provides=B),
            "timed-out-by-now": Step(after_the_timeout, provides=C),
        }
    )
    limits = Limits(
        concurrency=2,  # the third component starts only once `hangs` has timed out
        on_failure="isolate",
        grace_s=0,
        components={"hangs": ComponentLimits(deadline_s=0.01)},
    )
    with pytest.raises(RunError, match="'hangs' timed out after 0.01s") as e:
        await Executor().run(wiring, limits=limits)
    run = e.value.run
    assert run.components["hangs"].status == "failed"
    assert run.components["hangs"].reason == "deadline"
    # The sibling with no deadline outlived the one that timed out, and was left alone.
    assert run.components["unbounded"].status == "completed"
    assert run.rows(B) == ("done",)


async def test_a_deadline_is_a_stop_request_first_and_what_is_returned_is_kept() -> None:
    async def returns_what_it_has(ctx: Any) -> str:
        await ctx.stopping.wait()
        return "so far"

    wiring = wire({"m": Step(returns_what_it_has, provides=A)})
    with pytest.raises(RunError, match="timed out") as e:
        await Executor().run(wiring, limits=Limits(deadline_s=0.01))
    run = e.value.run
    assert run.components["m"].status == "failed"
    assert run.components["m"].reason == "deadline"
    assert run.rows(A) == ("so far",)


async def test_limits_naming_an_unknown_component_are_refused() -> None:
    wiring = wire({"a": Step(_boom)})
    with pytest.raises(ConfigError) as e:
        await Executor().run(wiring, limits=Limits(components={"b": ComponentLimits(deadline_s=1)}))
    ((path, kind, allowed),) = [(p.path, p.kind, p.allowed) for p in e.value.problems]
    assert (path, kind, allowed) == ("limits.components.b", "name_unknown", ("a",))


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

    run = await Executor().run(wire({"a": Step(body, provides=A)}), limits=Limits(cleanup_s=0.01))
    assert released == ["last", "first"]
    assert run.status == "completed"  # a failed release is reported, not a failed run
    assert [r.document() for r in run.releases] == [
        {"component": "a", "status": "released", "error": None},
        {"component": "a", "status": "abandoned", "error": "still running after 0.01s"},
        {"component": "a", "status": "failed", "error": "OSError: device busy"},
        {"component": "a", "status": "released", "error": None},
    ]


async def test_a_release_deferred_after_the_component_ended_is_ignored(
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

    wiring = wire(
        {
            "a": Step(leaks_its_context, provides=A),
            "b": Step(uses_the_leaked_context, requires=[A]),
        }
    )
    run = await Executor().run(wiring)
    assert called == [] and run.releases == []
    assert "after it ended" in caplog.text


_SIGNALLED = """
    import asyncio, sys
    from pathlib import Path
    from matrix import Executor, Limits, RunCancelled, cancel_on_signals, wire

    marker = Path(sys.argv[1])

    class Holds:
        requires = ()
        provides = ("acme.v1.a",)

        async def run(self, inputs, ctx):
            ctx.defer(lambda: marker.write_text("released"))
            print("ready", flush=True)
            await asyncio.Event().wait()

    async def main():
        try:
            executor = Executor(interrupts=cancel_on_signals)
            await executor.run(wire({"holds": Holds()}), limits=Limits(grace_s=0))
        except RunCancelled as e:
            print(e.run.status, e.run.components["holds"].status, flush=True)
            raise

    try:
        asyncio.run(main())
    except (asyncio.CancelledError, KeyboardInterrupt):
        sys.exit(42)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signalled_run_stops_its_components_and_releases(
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
        assert proc.stdout.readline().strip() == "ready"  # the component holds its resource
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=60)
    assert out.strip() == "cancelled cancelled", err
    assert marker.read_text() == "released"
    assert proc.returncode == 42, err


class _Abort(BaseException):
    """What a release may raise that is not an ``Exception``: as ``SystemExit`` is."""


async def test_a_release_that_raises_a_base_exception_does_not_skip_the_releases_owed(
    tmp_path: Path,
) -> None:
    """A synchronous release that raised a ``BaseException`` escaped ``except Exception``, so
    the releases still owed were never called and the record never finished (mx-src F17).
    Every release is called, the record is finished, and then it is raised."""
    from matrix import JsonlConstructStore

    released: list[str] = []

    def aborts() -> None:
        raise _Abort("the process is going down")

    async def body(ctx: Any) -> str:
        ctx.defer(lambda: released.append("first"))
        ctx.defer(aborts)
        return "v"

    store = JsonlConstructStore(tmp_path)
    with pytest.raises(_Abort):
        await Executor().run(wire({"a": Step(body, provides=A)}), store=store, run_id="r")
    assert released == ["first"]
    recorded = store.describe("r")
    assert recorded["status"] == "completed"
    assert [(r["status"], r["error"]) for r in recorded["releases"]] == [
        ("failed", "_Abort: the process is going down"),
        ("released", None),
    ]
