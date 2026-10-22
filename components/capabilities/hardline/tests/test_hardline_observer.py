"""The logical call, observed: each attempt that failed, each fallback, and the answer.

Retries, fallbacks and schema repairs are known only to the runtime. A driven port in domain
types (``CallObserver``) hands them to whoever the composition root chose, with no logging and
no OpenTelemetry inside the domain; the default observes nothing.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from hardline_helpers import mock_runtime
from pydantic import BaseModel

from hardline import (
    Answered,
    AttemptFailed,
    BackendError,
    CallEvent,
    CallObserver,
    FellBack,
    ModelRegistry,
    ModelRuntime,
)
from hardline.adapters._out.mock import MockBackend


class Recorder:
    def __init__(self) -> None:
        self.events: list[CallEvent] = []

    def observe(self, event: CallEvent) -> None:
        self.events.append(event)


async def _no_sleep(seconds: float) -> None:
    pass


def _row(script: list[Any], **kw: Any) -> dict[str, Any]:
    return {"backend": "mock", "model": "m", "family": "x", "options": {"script": script}, **kw}


def _observed(models: dict[str, Any], **kw: Any) -> tuple[ModelRuntime, Recorder]:
    recorder = Recorder()
    return mock_runtime(models, observer=recorder, sleep=_no_sleep, **kw), recorder


def test_a_recorder_is_a_call_observer() -> None:
    assert isinstance(Recorder(), CallObserver)


async def test_a_retry_is_observed_with_its_wait() -> None:
    rt, seen = _observed({"a": _row([{"error": "rate_limit"}, "ok"], retries=1)})
    c = await rt.complete("a", "hi")
    failed, answered = seen.events
    assert isinstance(failed, AttemptFailed) and isinstance(answered, Answered)
    assert (failed.requested, failed.attempt.name) == ("a", "a")
    assert failed.attempt.reason == "rate_limit"
    assert failed.retry_in_s is not None and failed.retry_in_s >= 0
    assert answered.requested == "a" and answered.completion == c


async def test_a_fallback_is_observed_and_is_no_longer_only_a_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Before: the one signal that a fallback answered was a WARNING on the `hardline` logger,
    # which hardline installs no handler for; the person who edits the registry rarely saw it.
    rt, seen = _observed(
        {"a": _row([{"error": "unavailable"}], retries=0, fallbacks=["b"]), "b": _row(["from b"])}
    )
    with caplog.at_level(logging.INFO, logger="hardline"):
        c = await rt.complete("a", "hi")
    assert [type(e).__name__ for e in seen.events] == ["AttemptFailed", "FellBack", "Answered"]
    failed, fell_back, answered = seen.events
    assert isinstance(failed, AttemptFailed) and failed.retry_in_s is None
    assert fell_back == FellBack(requested="a", name="a", to="b", reason="unavailable")
    assert isinstance(answered, Answered) and answered.completion.fallback_from == "a"
    assert c.name == "b"
    assert not [r for r in caplog.records if r.name == "hardline"]


async def test_a_failed_call_is_observed_up_to_its_failure() -> None:
    rt, seen = _observed({"a": _row([{"error": "auth"}])})
    with pytest.raises(BackendError):
        await rt.complete("a", "hi")
    (failed,) = seen.events
    assert isinstance(failed, AttemptFailed) and failed.attempt.reason == "auth"
    assert failed.retry_in_s is None


async def test_an_extract_is_answered_once_after_its_repairs() -> None:
    class Verdict(BaseModel):
        ok: bool

    rt, seen = _observed({"a": _row(["not json", '{"ok": true}'])})
    s = await rt.extract("a", "?", Verdict)
    (answered,) = seen.events
    assert isinstance(answered, Answered) and answered.completion == s.completion
    assert answered.completion.attempts == 2


async def test_an_observer_that_raises_does_not_fail_the_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken:
        def observe(self, event: CallEvent) -> None:
            raise RuntimeError("observer bug")

    rt = mock_runtime({"a": _row(["ok"])}, observer=Broken())
    with caplog.at_level(logging.WARNING, logger="hardline"):
        assert (await rt.complete("a", "hi")).text == "ok"
    assert any("observer bug" in r.getMessage() for r in caplog.records)


async def test_the_default_observes_nothing() -> None:
    registry = ModelRegistry.from_config({"models": {"a": _row(["ok"])}})
    rt = ModelRuntime(registry, {"mock": MockBackend()}, secrets=_NoSecrets())
    assert (await rt.complete("a", "hi")).text == "ok"


class _NoSecrets:
    def resolve(self, reference: str) -> str:
        raise AssertionError("no row names a key")
