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

from hardline import BackendError, ConfigError, Usage


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
    assert s.completion.usage.output_tokens > 0


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
