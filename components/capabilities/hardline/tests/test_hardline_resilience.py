"""Retries, fallbacks, error reasons and cache accounting.

Retry and fallback are separate: a retry asks the same model again after a transient
failure; a fallback asks another registry row once retries are spent. A non-transient
failure is neither retried nor fallen back from.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from hardline_helpers import mock_runtime
from pydantic import BaseModel

from hardline import BackendError, ConfigError, Usage
from hardline.adapters._out._classify import classify
from hardline.adapters._out.anthropic_native import AnthropicBackend
from hardline.adapters._out.openai_compat import OpenAICompatBackend
from hardline.domain.types import ModelSpec


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


@pytest.mark.parametrize(
    ("fallbacks", "problem"), [(["a"], "the model itself"), (["nope"], "not in the registry")]
)
def test_bad_fallbacks_fail_at_construction(fallbacks: list[str], problem: str) -> None:
    with pytest.raises(ConfigError, match=f"models.a.fallbacks: .* is {problem}"):
        mock_runtime({"a": _row(["x"], fallbacks=fallbacks)})


def test_scripted_error_reason_is_validated() -> None:
    from hardline.adapters._out.mock import _scripted

    spec = ModelSpec(name="a", backend="mock", model="m", family="x")
    with pytest.raises(ConfigError, match="error 'flaky'"):
        _scripted(spec, {"error": "flaky"})


# --- classification ------------------------------------------------------------------


def _status_error(cls: Any, status: int, headers: dict[str, str] | None = None) -> Any:
    request = httpx.Request("POST", "http://x")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return cls("boom", response=response, body=None)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "rate_limit"),
        (408, "timeout"),
        (500, "unavailable"),
        (529, "unavailable"),
        (400, "bad_request"),
        (404, "bad_request"),
    ],
)
def test_openai_and_anthropic_status_errors_classify_alike(status: int, reason: str) -> None:
    import anthropic
    import openai

    assert classify(_status_error(openai.APIStatusError, status))[0] == reason
    assert classify(_status_error(anthropic.APIStatusError, status))[0] == reason


def test_timeouts_and_connection_failures_are_transient() -> None:
    import openai

    request = httpx.Request("POST", "http://x")
    assert classify(openai.APITimeoutError(request=request))[0] == "timeout"
    assert classify(openai.APIConnectionError(request=request))[0] == "unavailable"


def test_retry_after_header_is_read() -> None:
    import openai

    error = _status_error(openai.RateLimitError, 429, {"retry-after": "7"})
    assert classify(error) == ("rate_limit", 7.0)


def test_provider_sdks_do_not_retry_behind_the_runtime() -> None:
    spec = ModelSpec(name="a", backend="openai-compat", model="m", family="x", base_url="http://x")
    assert OpenAICompatBackend()._client(spec, None).max_retries == 0
    anthropic_spec = spec.model_copy(update={"backend": "anthropic"})
    assert AnthropicBackend()._client(anthropic_spec, "k").max_retries == 0


# --- cache accounting ----------------------------------------------------------------


def test_usage_sums_cache_tokens() -> None:
    total = Usage(input_tokens=1, cache_read_tokens=5, cache_write_tokens=2) + Usage(
        input_tokens=1, cache_read_tokens=1
    )
    assert (total.cache_read_tokens, total.cache_write_tokens) == (6, 2)


async def test_anthropic_reports_cache_tokens() -> None:
    class Resp(SimpleNamespace):
        def model_dump(self, **_: Any) -> dict[str, Any]:
            return {}

    async def create(**_: Any) -> Any:
        return Resp(
            id="m",
            model="claude",
            content=[SimpleNamespace(type="text", text="a")],
            usage=SimpleNamespace(
                input_tokens=3,
                output_tokens=1,
                cache_read_input_tokens=40,
                cache_creation_input_tokens=9,
            ),
        )

    backend = AnthropicBackend()
    backend._client = lambda spec, key: SimpleNamespace(  # type: ignore[method-assign]
        messages=SimpleNamespace(create=create)
    )
    from hardline import Message, Request

    raw = await backend.complete(
        ModelSpec(name="a", backend="anthropic", model="m", family="claude"),
        Request(messages=(Message(role="user", content="hi"),), max_tokens=8),
        "k",
    )
    assert (raw.usage.cache_read_tokens, raw.usage.cache_write_tokens) == (40, 9)
