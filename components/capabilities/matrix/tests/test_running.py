"""Running from config: the store and limits it names, retries, and reuse of a prior record."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from matrix import (
    AgentRuntimeError,
    ComponentEnding,
    ComponentFailure,
    ConfigError,
    Executor,
    Inputs,
    JsonlConstructStore,
    Limits,
    MatrixConfig,
    MemoryConstructStore,
    RecordingObserver,
    ReleaseOutcome,
    Retry,
    Reuse,
    Run,
    RunContext,
    RunError,
    compose,
    default_registry,
    wire,
)

TEXT, UPPER, COUNT = "acme.v1.text", "acme.v1.upper", "acme.v1.count"


class Flaky:
    """Fails ``times`` times with a runtime error of ``reason``, then succeeds."""

    requires = (TEXT,)
    provides = (UPPER,)

    def __init__(self, times: int, reason: str = "rate_limited", **details: Any) -> None:
        self.times = times
        self.reason = reason
        self.details = details
        self.calls = 0

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.times:
            raise AgentRuntimeError("busy", reason=self.reason, **self.details)  # type: ignore[arg-type]
        return {UPPER: [t.upper() for t in inputs[TEXT]]}


def _limits(**retry: Any) -> Limits:
    return Limits(retry=Retry(backoff_s=0, **retry))


# --- retries ----------------------------------------------------------------------------


async def test_a_transient_failure_is_retried_and_the_attempts_are_recorded() -> None:
    flaky = Flaky(times=2)
    seen = RecordingObserver()
    run = await Executor([seen]).run(
        wire({"up": flaky}, inputs=[TEXT]), {TEXT: "a"}, limits=_limits(attempts=3)
    )
    assert run.status == "completed" and run.rows(UPPER) == ("A",)
    assert run.components["up"].attempts == 3 and flaky.calls == 3
    assert run.components["up"].document()["attempts"] == 3
    retries = [e for e in seen.events if e.name == "matrix.component.retry"]
    assert [e.fields["attempt"] for e in retries] == [2, 3]
    assert retries[0].fields["kind"] == "transient"


async def test_by_default_nothing_is_retried() -> None:
    flaky = Flaky(times=1)
    with pytest.raises(RunError) as e:
        await Executor().run(wire({"up": flaky}, inputs=[TEXT]), {TEXT: "a"})
    assert flaky.calls == 1 and e.value.run.components["up"].attempts == 1
    assert e.value.kind == "transient"  # the cause's kind: retry later


async def test_the_attempts_run_out() -> None:
    flaky = Flaky(times=5)
    with pytest.raises(RunError):
        await Executor().run(
            wire({"up": flaky}, inputs=[TEXT]), {TEXT: "a"}, limits=_limits(attempts=2)
        )
    assert flaky.calls == 2


async def test_only_the_failures_the_policy_names_are_retried() -> None:
    timeouts = Flaky(times=1, reason="timeout")
    with pytest.raises(RunError):
        await Executor().run(
            wire({"up": timeouts}, inputs=[TEXT]),
            {TEXT: "a"},
            limits=_limits(attempts=3, errors=("rate_limited",)),
        )
    assert timeouts.calls == 1
    refused = Flaky(times=1, reason="refused")  # the model declined: not transient
    with pytest.raises(RunError):
        await Executor().run(
            wire({"up": refused}, inputs=[TEXT]), {TEXT: "a"}, limits=_limits(attempts=3)
        )
    assert refused.calls == 1


def test_a_reported_retry_after_is_the_wait_up_to_the_cap() -> None:
    policy = Retry(attempts=3, backoff_s=1, multiplier=2, max_wait_s=10)
    hinted = AgentRuntimeError("busy", reason="rate_limited", retry_after_s=4)
    plain = AgentRuntimeError("busy", reason="unavailable")
    assert policy.wait_s(1, hinted) == 4
    assert policy.wait_s(1, AgentRuntimeError("x", reason="rate_limited", retry_after_s=99)) == 10
    assert (policy.wait_s(1, plain), policy.wait_s(2, plain), policy.wait_s(5, plain)) == (1, 2, 10)


async def test_the_wait_honours_retry_after() -> None:
    flaky = Flaky(times=1, retry_after_s=0.2)
    started = time.monotonic()
    await Executor().run(
        wire({"up": flaky}, inputs=[TEXT]),
        {TEXT: "a"},
        limits=Limits(retry=Retry(attempts=2, backoff_s=5, max_wait_s=1)),
    )
    assert 0.15 < time.monotonic() - started < 2


async def test_a_component_that_handed_over_rows_is_not_retried() -> None:
    """Its rows are part of the record: a second attempt would add to them, not replace them."""

    class Partial:
        requires = (TEXT,)
        provides = (UPPER,)
        calls = 0

        async def run(self, inputs: Inputs, ctx: RunContext) -> None:
            Partial.calls += 1
            ctx.provide(UPPER, "half")
            raise AgentRuntimeError("busy", reason="unavailable")

    with pytest.raises(RunError) as e:
        await Executor().run(
            wire({"p": Partial()}, inputs=[TEXT]), {TEXT: "a"}, limits=_limits(attempts=3)
        )
    assert Partial.calls == 1 and e.value.run.rows(UPPER) == ("half",)


async def test_a_component_policy_overrides_the_run_s() -> None:
    from matrix import ComponentLimits

    flaky = Flaky(times=1)
    limits = Limits(components={"up": ComponentLimits(retry=Retry(attempts=2, backoff_s=0))})
    run = await Executor().run(wire({"up": flaky}, inputs=[TEXT]), {TEXT: "a"}, limits=limits)
    assert run.components["up"].attempts == 2


async def test_a_run_stopped_while_a_component_waits_to_retry_does_not_retry_it() -> None:
    class Boom:
        requires: tuple[str, ...] = ()
        provides = (COUNT,)

        async def run(self, inputs: Inputs, ctx: RunContext) -> None:
            raise RuntimeError("down")

    flaky = Flaky(times=1)
    limits = Limits(retry=Retry(attempts=3, backoff_s=30), on_failure="fast")
    started = time.monotonic()
    with pytest.raises(RunError):
        await Executor().run(
            wire({"up": flaky, "boom": Boom()}, inputs=[TEXT]), {TEXT: "a"}, limits=limits
        )
    assert time.monotonic() - started < 5 and flaky.calls == 1


# --- run records are pydantic models -----------------------------------------------------


def test_the_run_record_parts_are_models_with_the_published_document() -> None:
    from matrix import Construct

    run = Run(run_id="r", construct=Construct())
    run.components["a"] = ComponentEnding(status="completed", duration_ms=3)
    run.failures.append(ComponentFailure(component="b", kind="contract", message="m"))
    run.releases.append(ReleaseOutcome(component="a", status="released"))
    document = run.document()
    assert document["components"] == {"a": {"status": "completed", "duration_ms": 3}}
    assert document["failures"] == [{"component": "b", "kind": "contract", "message": "m"}]
    assert document["releases"] == [{"component": "a", "status": "released", "error": None}]
    assert ComponentEnding.model_validate({"status": "reused"}).document() == {"status": "reused"}
    assert run.construct is not None and "construct" not in run.model_dump()


# --- the store and limits come from config ----------------------------------------------


class Upper:
    requires = (TEXT,)
    provides = (UPPER,)

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        return {UPPER: [t.upper() for t in inputs[TEXT]]}


class Count:
    requires = (UPPER,)
    provides = (COUNT,)
    calls = 0

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        Count.calls += 1
        return {COUNT: len(inputs[UPPER])}


def _registry() -> Any:
    registry = default_registry(discover=False)
    registry.register("component", "acme.v1.upper", Upper, effects=set())
    registry.register("component", "acme.v1.count", Count, effects=set())
    return registry


def _config(**extra: Any) -> MatrixConfig:
    return MatrixConfig.model_validate(
        {
            "inputs": [TEXT],
            "components": [
                {"name": "upper", "type": "acme.v1.upper"},
                {"name": "count", "type": "acme.v1.count"},
            ],
            **extra,
        }
    )


async def test_the_default_store_records_each_run_under_matrix_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    container = compose(_config(), registry=_registry())
    assert isinstance(container.store, JsonlConstructStore)
    run = await container.run({TEXT: ["a", "b"]})
    location = Path(container.location(run.run_id))
    assert location.parent == Path(".matrix/runs") and (tmp_path / location / "run.json").is_file()
    assert json.loads((tmp_path / location / "run.json").read_text())["status"] == "completed"


async def test_the_record_keeps_the_run_s_events(tmp_path: Path) -> None:
    store = JsonlConstructStore(tmp_path)
    await Executor().run(wire({"up": Upper()}, inputs=[TEXT]), {TEXT: "a"}, store=store, run_id="r")
    lines = (tmp_path / "r" / "events.jsonl").read_text().splitlines()
    names = [json.loads(line)["name"] for line in lines]
    assert names[0] == "matrix.run.start" and names[-1] == "matrix.run.end"
    assert all(json.loads(line)["schema"] == "matrix.v1.event" for line in lines)


async def test_a_store_and_limits_from_config(tmp_path: Path) -> None:
    config = _config(
        store={"type": "jsonl", "root": str(tmp_path / "kept")},
        limits={"concurrency": 1, "retry": {"attempts": 2}},
    )
    container = compose(config, registry=_registry())
    assert container.limits.concurrency == 1 and container.limits.retry.attempts == 2
    run = await container.run({TEXT: ["a"]}, run_id="one")
    assert (tmp_path / "kept" / "one" / "run.json").is_file() and run.rows(COUNT) == (1,)


async def test_the_memory_store_is_a_config_choice_too() -> None:
    container = compose(_config(store={"type": "memory"}), registry=_registry())
    assert isinstance(container.store, MemoryConstructStore)
    run = await container.run({TEXT: ["a"]})
    assert container.store.describe(run.run_id)["status"] == "completed"


def test_a_store_s_options_are_checked_by_its_own_schema() -> None:
    with pytest.raises(ConfigError) as e:
        compose(_config(store={"type": "jsonl", "rooot": "x"}), registry=_registry())
    assert [(p.path, p.kind) for p in e.value.problems] == [("matrix.store.rooot", "unknown_key")]
    with pytest.raises(ConfigError) as e:
        compose(_config(store={"type": "s3"}), registry=_registry())
    (found,) = e.value.problems
    assert found.kind == "name_unknown" and "matrix.v1.store.jsonl" in found.allowed


def test_a_third_party_store_registers_and_is_configured() -> None:
    from pydantic import BaseModel

    class BucketConfig(BaseModel):
        bucket: str

    class Bucket(MemoryConstructStore):
        def __init__(self, config: BucketConfig) -> None:
            super().__init__()
            self.bucket = config.bucket

    registry = _registry()
    registry.register("store", "acme.v1.store.bucket", Bucket, config=BucketConfig)
    container = compose(
        _config(store={"type": "acme.v1.store.bucket", "bucket": "runs"}), registry=registry
    )
    assert isinstance(container.store, Bucket) and container.store.bucket == "runs"


# --- starting from a recorded run --------------------------------------------------------


async def test_a_run_from_a_record_reuses_what_completed_unchanged(tmp_path: Path) -> None:
    config = _config(store={"type": "jsonl", "root": str(tmp_path)})
    first = await compose(config, registry=_registry()).run({TEXT: ["a", "b"]}, run_id="first")
    Count.calls = 0
    container = compose(config, registry=_registry())
    again = await container.run(run_id="again", reuse_from="first")
    assert again.components["upper"].status == "reused"
    assert again.components["count"].status == "reused" and Count.calls == 0
    assert again.rows(COUNT) == first.rows(COUNT) and again.reused_from == "first"
    described = container.store.describe("again")
    assert described["reused_from"] == "first" and described["components"]["upper"]["status"] == (
        "reused"
    )


async def test_a_changed_component_runs_again_and_so_does_what_depends_on_it(
    tmp_path: Path,
) -> None:
    store = {"type": "jsonl", "root": str(tmp_path)}
    await compose(_config(store=store), registry=_registry()).run({TEXT: ["a"]}, run_id="first")
    changed = MatrixConfig.model_validate(
        {
            "inputs": [TEXT],
            "store": store,
            "components": [
                {"name": "upper", "type": "acme.v1.upper"},
                {"name": "count", "type": "acme.v1.count"},
            ],
        }
    )
    registry = _registry()
    container = compose(changed, registry=registry)
    wiring = container.wire()
    fingerprints = dict(wiring.fingerprints)
    assert fingerprints["upper"].startswith("sha256:")
    # The record of "first" says count completed; pretend its build changed.
    reuse = container.reuse("first", wiring)
    assert set(reuse.rows) == {"upper", "count"}
    edited = wire(
        dict(wiring.components),
        inputs=[TEXT],
        fingerprints={**fingerprints, "upper": "sha256:changed"},
    )
    assert container.reuse("first", edited).rows == {}  # upper changed, so count is not reused


async def test_a_failed_component_is_not_reused(tmp_path: Path) -> None:
    class Broken:
        requires = (UPPER,)
        provides = (COUNT,)

        async def run(self, inputs: Inputs, ctx: RunContext) -> None:
            raise RuntimeError("bug")

    registry = default_registry(discover=False)
    registry.register("component", "acme.v1.upper", Upper, effects=set())
    registry.register("component", "acme.v1.count", Broken, effects=set())
    config = _config(store={"type": "jsonl", "root": str(tmp_path)})
    with pytest.raises(RunError):
        await compose(config, registry=registry).run({TEXT: ["a"]}, run_id="first")
    reuse = compose(config, registry=_registry()).reuse("first")
    assert set(reuse.rows) == {"upper"}


async def test_a_component_stopped_before_it_finished_is_not_reused(tmp_path: Path) -> None:
    """Asked to stop, a component returns what it has: its record says ``stopped``, and a run
    from that record runs it again instead of taking its partial rows as finished work."""
    running = asyncio.Event()

    class Partial:
        requires = (TEXT,)
        provides = (UPPER,)

        async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
            running.set()
            await ctx.stopping.wait()
            return {UPPER: ["partial"]}

    class Fails:
        requires = (TEXT,)
        provides = ("acme.v1.other",)

        async def run(self, inputs: Inputs, ctx: RunContext) -> None:
            await running.wait()
            raise RuntimeError("kaput")

    registry = default_registry(discover=False)
    registry.register("component", "acme.v1.upper", Partial, effects=set())
    registry.register("component", "acme.v1.fails", Fails, effects=set())
    config = MatrixConfig.model_validate(
        {
            "inputs": [TEXT],
            "store": {"type": "jsonl", "root": str(tmp_path)},
            "components": [
                {"name": "upper", "type": "acme.v1.upper"},
                {"name": "fails", "type": "acme.v1.fails"},
            ],
        }
    )
    container = compose(config, registry=registry)
    with pytest.raises(RunError):
        await container.run({TEXT: ["a"]}, run_id="first")
    ending = container.store.describe("first")["components"]["upper"]
    assert ending["status"] == "stopped" and ending["reason"] == "run_failed"
    assert container.reuse("first").rows == {}
    # A record written before ``stopped`` existed says ``completed`` with the stop's reason.
    record = tmp_path / "first" / "run.json"
    record.write_text(record.read_text().replace('"stopped"', '"completed"'))
    assert container.store.describe("first")["components"]["upper"]["status"] == "completed"
    assert container.reuse("first").rows == {}


async def test_reuse_takes_the_prior_inputs_and_refuses_new_ones(tmp_path: Path) -> None:
    config = _config(store={"type": "jsonl", "root": str(tmp_path)})
    await compose(config, registry=_registry()).run({TEXT: ["a"]}, run_id="first")
    with pytest.raises(ConfigError, match="takes that run's inputs"):
        await compose(config, registry=_registry()).run({TEXT: ["b"]}, reuse_from="first")


async def test_rows_that_cannot_be_reused_fail_that_component_and_skip_its_dependents() -> None:
    """Under ``isolate`` the rest of the run goes on: the component whose stored rows its kind
    now refuses ends ``failed`` and is not run again, what depends on it is skipped, and an
    independent reused component is still reused."""
    from pydantic import BaseModel

    class Row(BaseModel):
        n: int

    ran: list[str] = []

    class Make:
        requires = (TEXT,)
        provides = (UPPER,)

        async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
            ran.append("make")
            return {UPPER: {"n": 1}}

    class Use:
        requires = (UPPER,)
        provides = (COUNT,)

        async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
            ran.append("use")
            return {COUNT: 1}

    class Free:
        requires = (TEXT,)
        provides = ("acme.v1.free",)

        async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
            ran.append("free")
            return {"acme.v1.free": 1}

    wiring = wire(
        {"make": Make(), "use": Use(), "free": Free()}, inputs=[TEXT], schemas={UPPER: Row}
    )
    reuse = Reuse(
        run_id="old",
        rows={
            "make": {UPPER: ({"n": 1}, {"n": "not a number"})},
            "use": {COUNT: (2,)},
            "free": {"acme.v1.free": (3,)},
        },
    )
    with pytest.raises(RunError) as e:
        await Executor().run(wiring, {TEXT: "a"}, reuse=reuse, limits=Limits(on_failure="isolate"))
    run = e.value.run
    assert ran == []
    assert {name: c.status for name, c in run.components.items()} == {
        "make": "failed",
        "use": "skipped",
        "free": "reused",
    }
    assert run.components["use"].reason == "upstream: make"
    assert [(f.component, f.kind) for f in run.failures] == [("make", "contract")]
    assert run.rows(UPPER) == () and run.rows(COUNT) == () and run.rows("acme.v1.free") == (3,)


async def test_the_executor_refuses_reuse_that_would_not_be_sound() -> None:
    wiring = wire({"upper": Upper(), "count": Count()}, inputs=[TEXT])
    with pytest.raises(ConfigError, match="depends on"):
        await Executor().run(
            wiring, {TEXT: "a"}, reuse=Reuse(run_id="x", rows={"count": {COUNT: (1,)}})
        )
