"""Retries, fallbacks and cache accounting. How a provider's failure is classified is tested on
the wire, in test_hardline_backends.py.

Retry and fallback are separate: a retry asks the same model again after a transient
failure; a fallback asks another registry row once retries are spent. A non-transient
failure is neither retried nor fallen back from.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import pytest
from hardline_helpers import mock_runtime
from pydantic import BaseModel

from hardline import BackendError, ConfigError, ModelRuntime, SchemaError, Usage, build_runtime
from hardline.adapters._out.openai_compat import OpenAICompatBackend


class Sleeps:
    """Injected sleep: records delays instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _row(script: list[Any], **kw: Any) -> dict[str, Any]:
    return {"backend": "mock", "model": "m", "family": "qwen", "options": {"script": script}, **kw}


# --- retries -------------------------------------------------------------------------


async def test_transient_failures_are_retried_with_backoff() -> None:
    sleeps = Sleeps()
    rt = mock_runtime(
        {"a": _row([{"error": "rate_limit"}, {"error": "unavailable"}, "ok"])},
        sleep=sleeps,
        rng=random.Random(0),
    )
    c = await rt.complete("a", "hi")
    assert c.text == "ok" and c.retries == 2 and c.fallback_from is None
    assert len(sleeps.delays) == 2
    # full jitter under an exponential cap: 0.5s then 1.0s
    assert 0 <= sleeps.delays[0] <= 0.5 and 0 <= sleeps.delays[1] <= 1.0


async def test_retries_are_bounded_by_the_row() -> None:
    rt = mock_runtime({"a": _row([{"error": "timeout"}], retries=1)}, sleep=Sleeps())
    with pytest.raises(BackendError, match="exhausted retries") as e:
        await rt.complete("a", "hi")
    assert e.value.reason == "timeout" and e.value.retryable


@pytest.mark.parametrize("reason", ["auth", "bad_request", "unknown"])
async def test_non_transient_failures_are_raised_at_once(reason: str) -> None:
    sleeps = Sleeps()
    rt = mock_runtime(
        {"a": _row([{"error": reason}], fallbacks=["b"]), "b": _row(["from b"])}, sleep=sleeps
    )
    with pytest.raises(BackendError) as e:
        await rt.complete("a", "hi")
    assert e.value.reason == reason and not e.value.retryable
    assert sleeps.delays == []  # no retry, and no fallback hid the fault


async def test_retry_after_from_the_provider_wins_over_backoff() -> None:
    class Hinted:
        structured_modes = frozenset({"prompt"})
        calls = 0

        async def complete(self, spec: Any, request: Any, api_key: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                raise BackendError("slow down", reason="rate_limit", retry_after=3.0)
            from hardline import RawCompletion

            return RawCompletion(text="ok", model="m")

    from hardline import build_runtime

    sleeps = Sleeps()
    rt = build_runtime(
        {"models": {"a": {"backend": "hinted", "model": "m", "family": "x"}}},
        backends={"hinted": Hinted()},
        sleep=sleeps,
    )
    await rt.complete("a", "hi")
    assert sleeps.delays == [3.0]


async def test_exhausted_retries_keep_the_providers_retry_after() -> None:
    # The exhaustion error is what a caller sees; it must still carry the provider's hint.
    class AlwaysLimited:
        structured_modes = frozenset({"prompt"})

        async def complete(self, spec: Any, request: Any, api_key: Any) -> Any:
            raise BackendError("slow down", reason="rate_limit", retry_after=4.0)

    from hardline import build_runtime

    rt = build_runtime(
        {"models": {"a": {"backend": "limited", "model": "m", "family": "x", "retries": 1}}},
        backends={"limited": AlwaysLimited()},
        sleep=Sleeps(),
    )
    with pytest.raises(BackendError, match="exhausted retries") as e:
        await rt.complete("a", "hi")
    assert e.value.retry_after == 4.0


# --- fallbacks -----------------------------------------------------------------------


async def test_fallback_answers_and_the_completion_names_who_answered() -> None:
    rt = mock_runtime(
        {
            "primary": _row([{"error": "unavailable"}], retries=1, fallbacks=["backup"]),
            "backup": {**_row(["from backup"]), "family": "gemma"},
        },
        sleep=Sleeps(),
    )
    c = await rt.complete("primary", "hi")
    assert c.text == "from backup"
    assert c.name == "backup" and c.family == "gemma"  # the family of the model that answered
    assert c.fallback_from == "primary"
    assert c.retries == 1


async def test_every_model_failing_names_the_chain() -> None:
    rt = mock_runtime(
        {
            "a": _row([{"error": "unavailable"}], retries=0, fallbacks=["b"]),
            "b": _row([{"error": "rate_limit"}], retries=0),
        },
        sleep=Sleeps(),
    )
    with pytest.raises(BackendError, match=r"fallbacks \(a, b\)") as e:
        await rt.complete("a", "hi")
    assert e.value.reason == "rate_limit"


async def test_extract_falls_back_and_sums_usage() -> None:
    class Verdict(BaseModel):
        ok: bool

    rt = mock_runtime(
        {
            "a": _row([{"error": "unavailable"}], retries=0, fallbacks=["b"]),
            "b": {**_row(['{"ok": true}']), "family": "llama"},
        },
        sleep=Sleeps(),
    )
    s = await rt.extract("a", "judge this", Verdict)
    assert s.value.ok is True
    assert s.completion.family == "llama" and s.completion.fallback_from == "a"
    # F5: this asserted only `> 0`. The failed call reported nothing, so the total is exactly
    # what b reported for '{"ok": true}' (two words in the mock's count).
    assert s.completion.usage.output_tokens == 2
    assert [(t.name, t.reason, t.usage is None) for t in s.completion.attempt_log] == [
        ("a", "unavailable", True),
        ("b", None, False),
    ]
    assert s.completion.usage == s.completion.attempt_log[-1].usage


# --- the attempt log: each row tried, and why it failed (W3.2) -----------------------------


async def test_the_attempt_log_names_each_row_tried_and_why() -> None:
    rt = mock_runtime(
        {
            "primary": _row([{"error": "unavailable"}], retries=1, fallbacks=["backup"]),
            "backup": _row(["from backup"]),
        },
        sleep=Sleeps(),
    )
    c = await rt.complete("primary", "hi")
    assert [(t.name, t.reason) for t in c.attempt_log] == [
        ("primary", "unavailable"),
        ("primary", "unavailable"),
        ("backup", None),
    ]
    assert all(t.latency_ms >= 0 for t in c.attempt_log)


async def test_an_exhausted_chain_carries_its_log_and_what_it_cost() -> None:
    class Verdict(BaseModel):
        ok: bool

    # One schema attempt answers (not valid), the repair finds every row down: the first
    # attempt's tokens were spent, and the error says so rather than dropping them.
    rt = mock_runtime(
        {"a": _row(["not json", {"error": "rate_limit"}], retries=0)},
        sleep=Sleeps(),
    )
    with pytest.raises(BackendError) as e:
        await rt.extract("a", "?", Verdict)
    assert [(t.name, t.reason) for t in e.value.attempt_log] == [("a", None), ("a", "rate_limit")]
    assert e.value.usage is not None and e.value.usage == e.value.attempt_log[0].usage
    assert e.value.usage.output_tokens == 2


async def test_a_fault_raised_at_once_carries_its_log() -> None:
    rt = mock_runtime({"a": _row([{"error": "auth"}])}, sleep=Sleeps())
    with pytest.raises(BackendError) as e:
        await rt.complete("a", "hi")
    assert [(t.name, t.reason) for t in e.value.attempt_log] == [("a", "auth")]


async def test_a_schema_failure_carries_what_it_cost() -> None:
    class Verdict(BaseModel):
        ok: bool

    rt = mock_runtime({"a": _row(["nope", "still nope"])})
    with pytest.raises(SchemaError) as e:
        await rt.extract("a", "?", Verdict)
    assert [t.reason for t in e.value.attempt_log] == [None, None]
    assert e.value.usage is not None and e.value.usage.output_tokens == 1 + 2


async def test_a_schema_repair_goes_on_from_the_row_that_answered() -> None:
    # W6.1: each schema attempt restarted the chain at the primary that had just failed, so a
    # repair paid for the primary's failures again and could mix replies from two models.
    class Verdict(BaseModel):
        ok: bool

    rt = mock_runtime(
        {
            "a": _row([{"error": "unavailable"}], retries=0, fallbacks=["b"]),
            "b": _row(["not json", '{"ok": true}']),
        },
        sleep=Sleeps(),
    )
    s = await rt.extract("a", "?", Verdict)
    assert [(t.name, t.reason) for t in s.completion.attempt_log] == [
        ("a", "unavailable"),
        ("b", None),
        ("b", None),
    ]
    assert (s.completion.name, s.completion.fallback_from, s.completion.attempts) == ("b", "a", 2)


def test_a_repeated_fallback_is_refused() -> None:
    # F11: `[b, b]` was accepted, so b was retried twice in full and the warning for the first
    # b was suppressed.
    with pytest.raises(ConfigError, match=r"models\.a\.fallbacks.*'b' more than once"):
        mock_runtime({"a": _row(["x"], fallbacks=["b", "b"]), "b": _row(["y"])})


@pytest.mark.parametrize(
    ("fallbacks", "problem"), [(["a"], "the model itself"), (["nope"], "not in the registry")]
)
def test_bad_fallbacks_fail_at_construction(fallbacks: list[str], problem: str) -> None:
    with pytest.raises(ConfigError, match=f"models.a.fallbacks: .* is {problem}"):
        mock_runtime({"a": _row(["x"], fallbacks=fallbacks)})


# --- the logical call's own bound: attempts, wall time, tokens (W6.1) ----------------------


async def test_limits_attempts_bounds_every_backend_call_the_call_makes() -> None:
    # Before: one call could make (schema_retries + 1) x the sum over its chain of (retries + 1)
    # backend calls, each paid for, with nothing to bound the product.
    rt = mock_runtime(
        {
            "a": _row(
                [{"error": "unavailable"}], retries=5, fallbacks=["b"], limits={"attempts": 3}
            ),
            "b": _row(["from b"]),
        },
        sleep=Sleeps(),
    )
    with pytest.raises(BackendError, match=r"limits\.attempts=3") as e:
        await rt.complete("a", "hi")
    assert [t.name for t in e.value.attempt_log] == ["a", "a", "a"]
    assert (e.value.limit, e.value.reason, e.value.retryable) == ("attempts", "unavailable", True)


async def test_limits_attempts_counts_the_fallbacks_too() -> None:
    rt = mock_runtime(
        {
            "a": _row(
                [{"error": "unavailable"}], retries=0, fallbacks=["b", "c"], limits={"attempts": 2}
            ),
            "b": _row([{"error": "rate_limit"}], retries=0),
            "c": _row(["from c"]),
        },
        sleep=Sleeps(),
    )
    with pytest.raises(BackendError) as e:
        await rt.complete("a", "hi")
    assert [t.name for t in e.value.attempt_log] == ["a", "b"] and e.value.limit == "attempts"


async def test_limits_seconds_stops_before_a_wait_that_would_pass_it() -> None:
    class Limited:
        structured_modes = frozenset({"prompt"})

        async def complete(self, spec: Any, request: Any, api_key: Any) -> Any:
            raise BackendError("slow down", reason="rate_limit", retry_after=30.0)

    sleeps = Sleeps()
    rt = build_runtime(
        {"models": {"a": {"backend": "x", "model": "m", "family": "x", "limits": {"seconds": 10}}}},
        backends={"x": Limited()},
        sleep=sleeps,
    )
    with pytest.raises(BackendError, match=r"limits\.seconds=10") as e:
        await rt.complete("a", "hi")
    assert sleeps.delays == []  # it did not wait 30 s to be told it was out of time
    assert e.value.limit == "seconds" and e.value.reason == "rate_limit"


async def test_limits_seconds_cuts_an_attempt_that_would_pass_it() -> None:
    class Slow:
        structured_modes = frozenset({"prompt"})

        async def complete(self, spec: Any, request: Any, api_key: Any) -> Any:
            await asyncio.sleep(30)
            raise AssertionError("the attempt outlived the call's limit")

    rt = build_runtime(
        {
            "models": {
                "a": {"backend": "x", "model": "m", "family": "x", "limits": {"seconds": 0.05}}
            }
        },
        backends={"x": Slow()},
    )
    started = time.monotonic()
    with pytest.raises(BackendError) as e:
        await rt.complete("a", "hi")
    assert time.monotonic() - started < 5
    assert (e.value.reason, e.value.limit) == ("timeout", "seconds")
    assert [(t.name, t.reason) for t in e.value.attempt_log] == [("a", "timeout")]


async def test_limits_tokens_stops_the_schema_repairs() -> None:
    class Verdict(BaseModel):
        ok: bool

    # Each reply reports 1 input token per word sent and 1 output token per word replied; the
    # first call alone passes 10 tokens, so no repair is sent.
    rt = mock_runtime(
        {"a": _row(["not json at all, said at length"], limits={"tokens": 10})},
        schema_retries=3,
    )
    with pytest.raises(SchemaError, match=r"limits\.tokens=10") as e:
        await rt.extract("a", "?", Verdict)
    assert e.value.attempts == 1 and e.value.limit == "tokens"
    assert e.value.usage is not None
    assert e.value.usage.input_tokens + e.value.usage.output_tokens >= 10


async def test_no_limits_means_the_bound_is_the_chain_and_the_repairs() -> None:
    rt = mock_runtime({"a": _row([{"error": "unavailable"}], retries=2)}, sleep=Sleeps())
    with pytest.raises(BackendError) as e:
        await rt.complete("a", "hi")
    assert len(e.value.attempt_log) == 3 and e.value.limit is None


@pytest.mark.parametrize("limits", [{"attempts": 0}, {"seconds": 0}, {"tokens": 0}, {"calls": 3}])
def test_a_limit_that_bounds_nothing_is_refused(limits: dict[str, Any]) -> None:
    with pytest.raises(ConfigError, match=r"models\.a\.limits"):
        mock_runtime({"a": _row(["x"], limits=limits)})


# --- cache accounting ----------------------------------------------------------------


def test_usage_sums_cache_tokens() -> None:
    total = Usage(input_tokens=1, cache_read_tokens=5, cache_write_tokens=2) + Usage(
        input_tokens=1, cache_read_tokens=1
    )
    assert (total.cache_read_tokens, total.cache_write_tokens) == (6, 2)


# --- a fallback that shares its primary's fate (W6.2) ------------------------------------


def _located(**rows: dict[str, Any]) -> ModelRuntime:
    models = {
        name: {"backend": "openai-compat", "model": "m", "family": "x", **row}
        for name, row in rows.items()
    }
    return build_runtime({"models": models}, backends={"openai-compat": OpenAICompatBackend()})


def test_a_fallback_on_the_same_server_as_its_primary_is_warned_of() -> None:
    # Before: construction checked only that a fallback existed and was not the row itself, so a
    # fallback on the same local server was taken for protection when the server going down
    # takes both.
    local = "http://127.0.0.1:8080/v1"
    rt = _located(a={"base_url": local, "fallbacks": ["b"]}, b={"base_url": local, "model": "n"})
    (warning,) = rt.warnings
    assert (warning.path, warning.kind) == ("models.a.fallbacks", "shared_fate")
    assert "'b'" in warning.message and local in warning.message and warning.fix


@pytest.mark.parametrize(
    "fallback",
    [
        {"base_url": "http://127.0.0.1:8081/v1"},  # another server
        {"backend": "mock", "options": {}},  # another backend, with no location at all
    ],
)
def test_a_fallback_elsewhere_is_not_warned_of(fallback: dict[str, Any]) -> None:
    from hardline.adapters._out.mock import MockBackend

    models = {
        "a": {
            "backend": "openai-compat",
            "model": "m",
            "family": "x",
            "base_url": "http://127.0.0.1:8080/v1",
            "fallbacks": ["b"],
        },
        "b": {"backend": "openai-compat", "model": "m", "family": "x", **fallback},
    }
    rt = build_runtime(
        {"models": models},
        backends={"openai-compat": OpenAICompatBackend(), "mock": MockBackend()},
    )
    assert rt.warnings == ()


def test_mock_rows_share_no_server() -> None:
    rt = mock_runtime({"a": _row(["x"], fallbacks=["b"]), "b": _row(["y"])})
    assert rt.warnings == ()
