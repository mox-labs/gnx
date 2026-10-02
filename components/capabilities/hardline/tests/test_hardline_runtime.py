"""ModelRuntime behaviour: tagging, structured output with repair, the contract check."""

from __future__ import annotations

from typing import Any

import pytest
from hardline_helpers import DictSecrets, mock_runtime
from pydantic import BaseModel

from hardline import (
    COMPLETION_TYPE_URL,
    ContractError,
    Message,
    ModelRegistry,
    ModelRuntime,
    RawCompletion,
    SchemaError,
    build_runtime,
)
from hardline.adapters._out.mock import MockBackend


class Verdict(BaseModel):
    supported: bool
    reason: str


async def test_completion_carries_family_usage_and_provenance() -> None:
    rt = mock_runtime(
        {"q": {"backend": "mock", "model": "qwen-8b", "family": "qwen", "local": True}}
    )
    c = await rt.complete("q", "hello there")
    assert c.text == "mock:qwen-8b:hello there"
    assert (c.family, c.backend, c.local, c.attempts) == ("qwen", "mock", True, 1)
    assert c.type_url == COMPLETION_TYPE_URL
    assert c.usage.input_tokens == 2 and c.usage.output_tokens == 2  # "mock:qwen-8b:hello there"
    assert c.latency_ms >= 0 and c.request_id


async def test_default_model_used_when_none_named() -> None:
    rt = mock_runtime({"q": {"backend": "mock", "model": "m", "family": "qwen"}}, default="q")
    assert (await rt.complete(None, "x")).name == "q"


async def test_system_message_is_prepended() -> None:
    seen: list[tuple[str, ...]] = []

    class Spy(MockBackend):
        async def complete(self, spec, request, api_key):  # type: ignore[no-untyped-def]
            seen.append(tuple(m.role for m in request.messages))
            return await super().complete(spec, request, api_key)

    rt = build_runtime(
        {"models": {"q": {"backend": "spy", "model": "m", "family": "qwen"}}},
        backends={"spy": Spy()},
        secrets=DictSecrets(),
    )
    await rt.complete("q", [Message(role="user", content="a")], system="be brief")
    assert seen == [("system", "user")]


async def test_extract_validates_on_first_attempt() -> None:
    rt = mock_runtime(
        {
            "q": {
                "backend": "mock",
                "model": "m",
                "family": "qwen",
                "options": {"default": '```json\n{"supported": true, "reason": "quoted"}\n```'},
            }
        }
    )
    s = await rt.extract("q", "is it supported?", Verdict)
    assert s.value == Verdict(supported=True, reason="quoted")
    assert s.completion.attempts == 1


async def test_planted_schema_failure_is_repaired_once() -> None:
    rt = mock_runtime(
        {
            "q": {
                "backend": "mock",
                "model": "m",
                "family": "qwen",
                "options": {
                    "script": [
                        "Sure! It is supported.",
                        '{"supported": false, "reason": "no quote"}',
                    ]
                },
            }
        }
    )
    s = await rt.extract("q", "?", Verdict)
    assert s.value.supported is False
    assert s.completion.attempts == 2
    assert s.completion.usage.output_tokens > 0  # summed across both attempts


async def test_schema_failure_after_retries_raises_with_evidence() -> None:
    rt = mock_runtime(
        {
            "q": {
                "backend": "mock",
                "model": "m",
                "family": "qwen",
                "options": {"script": ['{"supported": "maybe"}']},
            }
        }
    )
    with pytest.raises(SchemaError) as e:
        await rt.extract("q", "?", Verdict)
    assert e.value.attempts == 2
    assert e.value.last_text == '{"supported": "maybe"}'
    assert "reason" in e.value.validation


async def test_zero_retries_means_one_attempt() -> None:
    rt = mock_runtime(
        {"q": {"backend": "mock", "model": "m", "family": "qwen", "options": {"script": ["nope"]}}},
        schema_retries=0,
    )
    with pytest.raises(SchemaError) as e:
        await rt.extract("q", "?", Verdict)
    assert e.value.attempts == 1


async def test_repair_turn_shows_the_model_its_own_reply_and_the_error() -> None:
    turns: list[list[tuple[str, str]]] = []

    class Spy(MockBackend):
        async def complete(self, spec, request, api_key):  # type: ignore[no-untyped-def]
            turns.append([(m.role, m.content) for m in request.messages])
            return await super().complete(spec, request, api_key)

    rt = build_runtime(
        {
            "models": {
                "q": {
                    "backend": "spy",
                    "model": "m",
                    "family": "qwen",
                    "options": {"script": ["not json", '{"supported": true, "reason": "r"}']},
                }
            }
        },
        backends={"spy": Spy()},
        secrets=DictSecrets(),
    )
    await rt.extract("q", "?", Verdict)
    second = turns[1]
    assert second[-2] == ("assistant", "not json")
    assert second[-1][0] == "user" and "did not validate" in second[-1][1]


async def test_backend_returning_wrong_shape_is_a_contract_error() -> None:
    class Liar:
        structured_modes = frozenset({"prompt"})

        async def complete(self, spec: Any, request: Any, api_key: Any) -> Any:
            return {"text": "a dict, not RawCompletion"}

    rt = ModelRuntime(
        ModelRegistry.from_config(
            {"models": {"q": {"backend": "liar", "model": "m", "family": "x"}}}
        ),
        {"liar": Liar()},
        DictSecrets(),
    )
    with pytest.raises(ContractError, match="not RawCompletion"):
        await rt.complete("q", "hi")


async def test_secret_reference_is_resolved_and_handed_to_backend() -> None:
    got: list[str | None] = []

    class Spy(MockBackend):
        async def complete(self, spec, request, api_key):  # type: ignore[no-untyped-def]
            got.append(api_key)
            return RawCompletion(text="ok", model=spec.model)

    secrets = DictSecrets({"env:KEY": "s3cret"})
    rt = build_runtime(
        {"models": {"q": {"backend": "spy", "model": "m", "family": "x", "api_key": "env:KEY"}}},
        backends={"spy": Spy()},
        secrets=secrets,
    )
    await rt.complete("q", "hi")
    assert got == ["s3cret"] and secrets.asked == ["env:KEY"]


async def test_call_overrides_beat_row_defaults() -> None:
    seen: list[tuple[int, float | None]] = []

    class Spy(MockBackend):
        async def complete(self, spec, request, api_key):  # type: ignore[no-untyped-def]
            seen.append((request.max_tokens, request.temperature))
            return await super().complete(spec, request, api_key)

    rt = build_runtime(
        {
            "models": {
                "q": {
                    "backend": "spy",
                    "model": "m",
                    "family": "x",
                    "max_tokens": 50,
                    "temperature": 0.2,
                }
            }
        },
        backends={"spy": Spy()},
        secrets=DictSecrets(),
    )
    await rt.complete("q", "a")
    await rt.complete("q", "a", max_tokens=7, temperature=0.0)
    assert seen == [(50, 0.2), (7, 0.0)]
