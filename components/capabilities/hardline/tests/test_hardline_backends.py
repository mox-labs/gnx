"""Every backend satisfies the port; the provider adapters map requests and responses faithfully."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hardline import (
    BackendError,
    ConfigError,
    Message,
    ModelBackend,
    ModelSpec,
    RawCompletion,
    Request,
)
from hardline.adapters._out.anthropic_native import AnthropicBackend
from hardline.adapters._out.mock import MockBackend
from hardline.adapters._out.openai_compat import OpenAICompatBackend
from hardline.composition import discover_backends


def _spec(**kw: Any) -> ModelSpec:
    base = {"name": "q", "backend": "openai-compat", "model": "m", "family": "qwen"}
    return ModelSpec.model_validate({**base, **kw})


def _request(schema: dict[str, Any] | None = None) -> Request:
    return Request(
        messages=(Message(role="system", content="sys"), Message(role="user", content="hi")),
        json_schema=schema,
        max_tokens=64,
        temperature=0.1,
    )


# --- the contract test: every installed backend, same assertions ---------------------


@pytest.mark.parametrize("name", sorted(discover_backends()))
def test_every_backend_satisfies_the_port(name: str) -> None:
    backend = discover_backends()[name]
    assert isinstance(backend, ModelBackend)
    assert isinstance(backend.structured_modes, frozenset) and "prompt" in backend.structured_modes


async def test_mock_returns_the_declared_shape() -> None:
    raw = await MockBackend().complete(_spec(backend="mock"), _request(), None)
    assert isinstance(raw, RawCompletion) and raw.text == "mock:m:hi"


async def test_mock_script_repeats_last_entry() -> None:
    backend = MockBackend()
    spec = _spec(backend="mock", options={"script": ["a", "b"]})
    texts = [(await backend.complete(spec, _request(), None)).text for _ in range(3)]
    assert texts == ["a", "b", "b"]


# --- openai-compat -------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self._response = response
        self._error = error

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self._error:
            raise self._error
        return self._response


def _openai_response(text: str = "out") -> Any:
    class Resp(SimpleNamespace):
        def model_dump(self, **_: Any) -> dict[str, Any]:
            return {"id": self.id, "model": self.model}

    return Resp(
        id="chatcmpl-1",
        model="served-model",
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3),
    )


def _patched(backend: OpenAICompatBackend, completions: _FakeCompletions) -> list[Any]:
    seen: list[Any] = []

    def fake_client(spec: ModelSpec, api_key: str | None) -> Any:
        seen.append(api_key)
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    backend._client = fake_client  # type: ignore[method-assign]
    return seen


async def test_openai_compat_maps_request_and_response() -> None:
    backend, fake = OpenAICompatBackend(), _FakeCompletions(_openai_response("hello"))
    _patched(backend, fake)
    raw = await backend.complete(_spec(), _request(), "k")
    assert fake.kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert fake.kwargs["max_tokens"] == 64 and fake.kwargs["temperature"] == 0.1
    assert "response_format" not in fake.kwargs  # prompt mode: schema is in the instruction only
    assert (raw.text, raw.model, raw.request_id) == ("hello", "served-model", "chatcmpl-1")
    assert (raw.usage.input_tokens, raw.usage.output_tokens) == (11, 3)


@pytest.mark.parametrize(
    ("mode", "expected_type"), [("json_object", "json_object"), ("json_schema", "json_schema")]
)
async def test_openai_compat_structured_modes(mode: str, expected_type: str) -> None:
    backend, fake = OpenAICompatBackend(), _FakeCompletions(_openai_response("{}"))
    _patched(backend, fake)
    await backend.complete(_spec(structured=mode), _request({"type": "object"}), None)
    assert fake.kwargs["response_format"]["type"] == expected_type


async def test_openai_compat_token_param_is_configurable() -> None:
    backend, fake = OpenAICompatBackend(), _FakeCompletions(_openai_response())
    _patched(backend, fake)
    await backend.complete(
        _spec(options={"token_param": "max_completion_tokens"}), _request(), None
    )
    assert fake.kwargs["max_completion_tokens"] == 64 and "max_tokens" not in fake.kwargs


async def test_openai_compat_rejects_unknown_token_param() -> None:
    backend = OpenAICompatBackend()
    _patched(backend, _FakeCompletions(_openai_response()))
    with pytest.raises(ConfigError, match="token_param"):
        await backend.complete(_spec(options={"token_param": "tokens"}), _request(), None)


def test_openai_compat_never_reads_ambient_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-should-not-be-used")
    client = OpenAICompatBackend()._client(_spec(base_url="http://127.0.0.1:9/v1"), None)
    assert client.api_key == "hardline-no-key"


async def test_openai_compat_error_is_wrapped_and_key_scrubbed() -> None:
    import openai

    secret = "sk-proj-abcdefghijklmnop"
    err = openai.APIConnectionError(message=f"refused for key {secret}", request=None)  # type: ignore[arg-type]
    backend = OpenAICompatBackend()
    _patched(backend, _FakeCompletions(error=err))
    with pytest.raises(BackendError) as e:
        await backend.complete(_spec(base_url="http://127.0.0.1:9/v1"), _request(), secret)
    assert secret not in str(e.value) and secret[:8] not in str(e.value)
    assert "127.0.0.1:9" in str(e.value) and "model 'q'" in str(e.value)


# --- anthropic -----------------------------------------------------------------------


class _FakeMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs

        class Resp(SimpleNamespace):
            def model_dump(self, **_: Any) -> dict[str, Any]:
                return {"id": self.id}

        return Resp(
            id="msg_1",
            model="claude-x",
            content=[
                SimpleNamespace(type="text", text="a"),
                SimpleNamespace(type="tool_use"),
                SimpleNamespace(type="text", text="b"),
            ],
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
        )


async def test_anthropic_lifts_system_and_joins_text_blocks() -> None:
    backend, fake = AnthropicBackend(), _FakeMessages()
    backend._client = lambda spec, key: SimpleNamespace(messages=fake)  # type: ignore[method-assign]
    raw = await backend.complete(_spec(backend="anthropic"), _request(), "k")
    assert fake.kwargs["system"] == "sys"
    assert fake.kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert raw.text == "ab" and raw.usage.input_tokens == 5


async def test_anthropic_refuses_without_a_key_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient")
    with pytest.raises(BackendError, match="no api_key reference"):
        await AnthropicBackend().complete(_spec(backend="anthropic"), _request(), None)
