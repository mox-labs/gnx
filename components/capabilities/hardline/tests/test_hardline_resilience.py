"""Retries, fallbacks and cache accounting. How a provider's failure is classified is tested on
the wire, in test_hardline_backends.py.

Retry and fallback are separate: a retry asks the same model again after a transient
failure; a fallback asks another registry row once retries are spent. A non-transient
failure is neither retried nor fallen back from.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
from hardline_helpers import mock_runtime
from pydantic import BaseModel

from hardline import BackendError, ConfigError, SchemaError, Usage


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
    assert e.value.usage == e.value.attempt_log[0].usage and e.value.usage.output_tokens == 2


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
    assert e.value.usage.output_tokens == 1 + 2


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


# --- cache accounting ----------------------------------------------------------------


def test_usage_sums_cache_tokens() -> None:
    total = Usage(input_tokens=1, cache_read_tokens=5, cache_write_tokens=2) + Usage(
        input_tokens=1, cache_read_tokens=1
    )
    assert (total.cache_read_tokens, total.cache_write_tokens) == (6, 2)
