"""Every backend satisfies the port; the provider adapters map requests and responses faithfully.

The provider adapters are tested on the wire: each is handed an httpx2 ``MockTransport`` that
records the request and answers as the provider would. Nothing leaves the process.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from hardline_helpers import DictSecrets

from hardline import (
    BackendError,
    ConfigError,
    Endpoint,
    Message,
    ModelBackend,
    ModelSpec,
    RawCompletion,
    Request,
    build_runtime,
)
from hardline.adapters._in.cli import error_payload
from hardline.adapters._out.anthropic_native import AnthropicBackend
from hardline.adapters._out.mock import MockBackend
from hardline.adapters._out.openai_compat import OpenAICompatBackend

if TYPE_CHECKING:
    from collections.abc import Callable

LOCAL = "http://127.0.0.1:9/v1"


def _spec(**kw: Any) -> Endpoint:
    """A registry row, as the runtime hands it to a backend."""
    base = {"name": "q", "backend": "openai-compat", "model": "m", "family": "qwen"}
    if kw.get("backend", base["backend"]) != "mock":
        base["base_url"] = LOCAL
    return ModelSpec.model_validate({**base, **kw}).endpoint()


def _request(schema: dict[str, Any] | None = None) -> Request:
    return Request(
        messages=(Message(role="system", content="sys"), Message(role="user", content="hi")),
        json_schema=schema,
        max_tokens=64,
        temperature=0.1,
    )


class Wire:
    """A MockTransport that records each request and answers with ``reply``."""

    def __init__(self, reply: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.requests: list[httpx2.Request] = []
        self._reply = reply
        self.transport = httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self._reply(request)

    @property
    def body(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.requests[-1].content)
        return loaded


def _answer(body: dict[str, Any], status: int = 200, **headers: str) -> Wire:
    return Wire(lambda _: httpx2.Response(status, json=body, headers=headers))


def _chat(text: str | None = "out", **choice: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "served-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
                **choice,
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }


def _message(*blocks: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-x",
        "content": list(blocks) or [{"type": "text", "text": "a"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 2},
        **extra,
    }


# The contract every installed backend meets is test_hardline_conformance.py's, run through the
# helper hardline ships.


async def test_mock_returns_the_declared_shape() -> None:
    raw = await MockBackend().complete(_spec(backend="mock"), _request(), None)
    assert isinstance(raw, RawCompletion) and raw.text == "mock:m:hi"


async def test_mock_says_which_turn_it_echoes_and_names_each_call() -> None:
    # The docstring said the echo was the last user message; it is the first. And the
    # request id was whatever the call counter held after the reply was picked; it is now the
    # call's own ordinal, counted from 1, and says so.
    backend = MockBackend()
    spec = _spec(backend="mock", options={"responses": {"later": "keyed on the last turn"}})
    turns = (
        Message(role="user", content="first"),
        Message(role="assistant", content="a"),
        Message(role="user", content="second"),
    )
    request = Request(messages=turns, max_tokens=8)
    first = await backend.complete(spec, request, None)
    assert first.text == "mock:m:first" and first.request_id == "mock-q-1"
    keyed = Request(messages=(*turns, Message(role="user", content="later")), max_tokens=8)
    second = await backend.complete(spec, keyed, None)
    assert second.text == "keyed on the last turn" and second.request_id == "mock-q-2"


async def test_mock_script_repeats_last_entry() -> None:
    backend = MockBackend()
    spec = _spec(backend="mock", options={"script": ["a", "b"]})
    texts = [(await backend.complete(spec, _request(), None)).text for _ in range(3)]
    assert texts == ["a", "b", "b"]


# --- openai-compat -------------------------------------------------------------------


async def test_openai_compat_maps_request_and_response() -> None:
    wire = _answer(_chat("hello"))
    raw = await OpenAICompatBackend(wire.transport).complete(_spec(), _request(), "k")
    sent = wire.requests[-1]
    assert (sent.method, str(sent.url)) == ("POST", f"{LOCAL}/chat/completions")
    assert sent.headers["authorization"] == "Bearer k"
    assert wire.body["model"] == "m"
    assert wire.body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert wire.body["max_tokens"] == 64 and wire.body["temperature"] == 0.1
    assert "response_format" not in wire.body  # prompt mode: schema is in the instruction only
    assert (raw.text, raw.model, raw.request_id) == ("hello", "served-model", "chatcmpl-1")
    assert (raw.usage.input_tokens, raw.usage.output_tokens) == (11, 3)


@pytest.mark.parametrize(
    ("mode", "expected_type"), [("json_object", "json_object"), ("json_schema", "json_schema")]
)
async def test_openai_compat_structured_modes(mode: str, expected_type: str) -> None:
    wire = _answer(_chat("{}"))
    await OpenAICompatBackend(wire.transport).complete(
        _spec(structured=mode), _request({"type": "object"}), None
    )
    assert wire.body["response_format"]["type"] == expected_type
    if mode == "json_schema":
        assert wire.body["response_format"]["json_schema"]["schema"] == {"type": "object"}


async def test_openai_compat_token_param_is_configurable() -> None:
    wire = _answer(_chat())
    await OpenAICompatBackend(wire.transport).complete(
        _spec(options={"token_param": "max_completion_tokens"}), _request(), None
    )
    assert wire.body["max_completion_tokens"] == 64 and "max_tokens" not in wire.body


async def test_openai_compat_input_tokens_include_the_cache() -> None:
    # The protocol's prompt_tokens already counts the cached tokens it details, which is what
    # the GenAI conventions ask of gen_ai.usage.input_tokens. They are kept, not added again.
    body = _chat()
    body["usage"]["prompt_tokens_details"] = {"cached_tokens": 7}
    wire = _answer(body)
    raw = await OpenAICompatBackend(wire.transport).complete(_spec(), _request(), None)
    assert (raw.usage.input_tokens, raw.usage.cache_read_tokens) == (11, 7)


async def test_openai_compat_sends_no_key_it_was_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    # The SDK needed a placeholder key to stop it reading OPENAI_API_KEY. Without the SDK, a
    # keyless row sends no Authorization header at all.
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-should-not-be-used")
    wire = _answer(_chat())
    await OpenAICompatBackend(wire.transport).complete(_spec(), _request(), None)
    assert "authorization" not in wire.requests[-1].headers


async def test_openai_compat_error_is_wrapped_and_key_scrubbed() -> None:
    secret = "sk-proj-abcdefghijklmnop"
    wire = _answer({"error": {"message": f"Incorrect API key provided: {secret}"}}, status=401)
    with pytest.raises(BackendError) as e:
        await OpenAICompatBackend(wire.transport).complete(_spec(), _request(), secret)
    assert e.value.reason == "auth"
    assert secret not in str(e.value) and secret[:8] not in str(e.value)
    assert "127.0.0.1:9" in str(e.value) and "model 'q'" in str(e.value)
    assert "Incorrect API key" in str(e.value)  # the provider's own words reach the caller


# --- anthropic -----------------------------------------------------------------------


async def test_anthropic_maps_request_and_response() -> None:
    wire = _answer(
        _message(
            {"type": "text", "text": "a"},
            {"type": "tool_use", "id": "t", "name": "x", "input": {}},
            {"type": "text", "text": "b"},
        )
    )
    spec = _spec(backend="anthropic", base_url="http://127.0.0.1:9")
    raw = await AnthropicBackend(wire.transport).complete(spec, _request(), "k")
    sent = wire.requests[-1]
    assert str(sent.url) == "http://127.0.0.1:9/v1/messages"
    assert sent.headers["x-api-key"] == "k" and sent.headers["anthropic-version"]
    assert "authorization" not in sent.headers
    assert wire.body["system"] == "sys"
    assert wire.body["messages"] == [{"role": "user", "content": "hi"}]
    assert (wire.body["max_tokens"], wire.body["temperature"]) == (64, 0.1)
    assert raw.text == "ab" and raw.usage.input_tokens == 5
    assert (raw.model, raw.request_id) == ("claude-x", "msg_1")


async def test_anthropic_input_tokens_include_the_cache() -> None:
    # The API's input_tokens counts only the tokens after the last cache breakpoint; the ones
    # read from and written to the cache are reported apart. input_tokens means one thing on
    # every backend: every input token, the cache's included.
    usage = {
        "input_tokens": 3,
        "output_tokens": 1,
        "cache_read_input_tokens": 40,
        "cache_creation_input_tokens": 9,
    }
    wire = _answer(_message(usage=usage))
    raw = await AnthropicBackend(wire.transport).complete(
        _spec(backend="anthropic"), _request(), "k"
    )
    assert raw.usage.input_tokens == 3 + 40 + 9
    assert (raw.usage.cache_read_tokens, raw.usage.cache_write_tokens) == (40, 9)


async def test_anthropic_refuses_without_a_key_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    # A missing reference is the row's fault, so a config error, never "the key was rejected".
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient")
    wire = _answer(_message())
    with pytest.raises(ConfigError, match=r"models\.q\.api_key"):
        await AnthropicBackend(wire.transport).complete(
            _spec(backend="anthropic"), _request(), None
        )
    assert wire.requests == []


def _says_nothing(turn: dict[str, Any]) -> bool:
    """A turn the Messages API refuses: a text block's ``text`` has a minimum length of 1."""
    content = turn["content"]
    if isinstance(content, str):
        return not content.strip()
    return not content or any(
        b.get("type") == "text" and not str(b.get("text", "")).strip() for b in content
    )


def _messages_api(reply: dict[str, Any]) -> Wire:
    """The Messages API as far as empty turns go: a request with a turn that says nothing is a
    400 ``invalid_request_error``; any other is answered with ``reply``."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        if any(_says_nothing(turn) for turn in json.loads(request.content)["messages"]):
            error = {"type": "invalid_request_error", "message": "text content blocks must be"}
            return httpx2.Response(400, json={"type": "error", "error": error})
        return httpx2.Response(200, json=reply)

    return Wire(answer)


@pytest.mark.parametrize(
    ("backend", "wire"),
    [
        ("anthropic", _messages_api(_message(content=[], stop_reason="max_tokens"))),
        ("openai-compat", _answer(_chat("", finish_reason="length"))),
    ],
)
async def test_a_repair_after_an_empty_reply_sends_no_empty_assistant_turn(
    backend: str, wire: Wire
) -> None:
    # An empty reply was echoed back as `{"role": "assistant", "content": ""}` ahead of the
    # repair instruction. The Messages API refuses that turn, so the repair came back a 400
    # (`bad_request`, exit 3) where the caller is owed a SchemaError.
    from pydantic import BaseModel

    from hardline import SchemaError

    class Verdict(BaseModel):
        ok: bool

    backends: dict[str, ModelBackend] = {
        "openai-compat": OpenAICompatBackend(wire.transport),
        "anthropic": AnthropicBackend(wire.transport),
    }
    rt = build_runtime(
        {
            "models": {
                "r": {"backend": backend, "model": "m", "family": "x", "base_url": LOCAL}
                | ({"api_key": "env:K"} if backend == "anthropic" else {})
            }
        },
        backends=backends,
        secrets=DictSecrets({"env:K": "k"}),
    )
    with pytest.raises(SchemaError) as e:
        await rt.extract("r", "hi", Verdict)
    assert e.value.attempts == 2 and e.value.validation == "the reply was empty"
    repair = json.loads(wire.requests[1].content)["messages"]
    assert [turn["role"] for turn in repair] == ["user", "user", "user"]
    assert "empty" in repair[-1]["content"]


async def test_anthropic_leaves_out_an_assistant_turn_that_says_nothing() -> None:
    # A caller's own conversation (pydantic-ai's, through matrix) can hold an empty assistant
    # turn; the API refuses it, and leaving it out changes nothing the model is told. The two
    # user turns either side are combined by the API into one.
    wire = _messages_api(_message())
    request = Request(
        messages=(
            Message(role="user", content="hi"),
            Message(role="assistant", content=""),
            Message(role="user", content="again"),
            Message(role="assistant", content=" \n"),
            Message(role="user", content="and again"),
        ),
        max_tokens=8,
    )
    await AnthropicBackend(wire.transport).complete(_spec(backend="anthropic"), request, "k")
    assert wire.body["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "user", "content": "again"},
        {"role": "user", "content": "and again"},
    ]


# --- locations come from the row, never the environment -----------------------

_AMBIENT = {
    "OPENAI_BASE_URL": "http://evil.example/v1",
    "OPENAI_ORG_ID": "org-x",
    "OPENAI_PROJECT_ID": "proj-x",
    "ANTHROPIC_BASE_URL": "http://evil2.example",
}


@pytest.mark.parametrize(
    ("backend", "reply", "default", "sent_to"),
    [
        (
            "openai-compat",
            _chat(),
            "https://api.openai.com/v1",
            "https://api.openai.com/v1/chat/completions",
        ),
        (
            "anthropic",
            _message(),
            "https://api.anthropic.com",
            "https://api.anthropic.com/v1/messages",
        ),
    ],
)
async def test_a_row_without_base_url_goes_to_its_backends_default_whatever_the_environment(
    backend: str,
    reply: dict[str, Any],
    default: str,
    sent_to: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no base_url the SDKs read OPENAI_BASE_URL / ANTHROPIC_BASE_URL, and the OpenAI
    # client sent OPENAI_ORG_ID and OPENAI_PROJECT_ID as headers: a row's key went to a host
    # no config named.
    for name, value in _AMBIENT.items():
        monkeypatch.setenv(name, value)
    wire = _answer(reply)
    backends: dict[str, ModelBackend] = {
        "openai-compat": OpenAICompatBackend(wire.transport),
        "anthropic": AnthropicBackend(wire.transport),
    }
    rt = build_runtime(
        {"models": {"r": {"backend": backend, "model": "m", "family": "x", "api_key": "env:K"}}},
        backends=backends,
        secrets=DictSecrets({"env:K": "k"}),
    )
    # The composition root writes the default into the row, so the row records where it sends.
    assert rt.registry.get("r").base_url == default
    await rt.complete("r", "hi")
    sent = wire.requests[-1]
    assert str(sent.url) == sent_to
    assert not {"openai-organization", "openai-project"} & set(sent.headers)


async def test_a_row_names_where_its_error_came_from() -> None:
    wire = _answer({"error": {"message": "overloaded"}}, status=503)
    with pytest.raises(BackendError) as e:
        await OpenAICompatBackend(wire.transport).complete(_spec(), _request(), None)
    assert "127.0.0.1:9" in str(e.value) and "api.openai.com" not in str(e.value)


def test_a_backend_location_must_be_an_http_url() -> None:
    with pytest.raises(ConfigError, match=r"models\.q\.base_url"):
        build_runtime(
            {
                "models": {
                    "q": {
                        "backend": "openai-compat",
                        "model": "m",
                        "family": "x",
                        "base_url": "localhost:8080/v1",
                    }
                }
            }
        )


# --- the wire: statuses, redirects, transport failures ---------------------------------


@pytest.mark.parametrize("backend", [OpenAICompatBackend, AnthropicBackend])
@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "rate_limit"),
        (408, "timeout"),
        (500, "unavailable"),
        (502, "unavailable"),
        (503, "unavailable"),
        (504, "unavailable"),
        (529, "unavailable"),
        # No retry can change these. 501 says the server does not implement the request
        # and 505 that it refuses the HTTP version: the row points somewhere that cannot serve it.
        (501, "bad_request"),
        (505, "bad_request"),
        (400, "bad_request"),
        (404, "bad_request"),
    ],
)
async def test_both_protocols_classify_a_status_alike(
    backend: type[OpenAICompatBackend | AnthropicBackend], status: int, reason: str
) -> None:
    wire = _answer({"type": "error", "error": {"type": "x", "message": "no"}}, status=status)
    with pytest.raises(BackendError) as e:
        await backend(wire.transport).complete(_spec(), _request(), "k")
    assert e.value.reason == reason
    assert f"HTTP {status}" in str(e.value) and "no" in str(e.value)


async def test_retry_after_header_is_read() -> None:
    wire = _answer({"error": {"message": "slow down"}}, status=429, **{"retry-after": "7"})
    with pytest.raises(BackendError) as e:
        await OpenAICompatBackend(wire.transport).complete(_spec(), _request(), None)
    assert (e.value.reason, e.value.retry_after) == ("rate_limit", 7.0)


async def test_a_redirect_is_reported_not_followed() -> None:
    # Following it would send the row's key to wherever the Location points. Changed requirement
    # A redirect is the row's location to correct, so a config error (exit 3) whose fix
    # names where to point it, not `unknown` (exit 1, "report a bug").
    moved = "https://127.0.0.1:9/v1/chat/completions"
    wire = Wire(lambda _: httpx2.Response(308, headers={"location": moved}))
    with pytest.raises(ConfigError) as e:
        await OpenAICompatBackend(wire.transport).complete(_spec(), _request(), "k")
    assert len(wire.requests) == 1
    assert moved in str(e.value) and "models.q.base_url" in str(e.value)
    assert e.value.fix == "set models.q.base_url to 'https://127.0.0.1:9/v1'"
    payload, code = error_payload(e.value)
    assert code == 3 and payload["error"]["fix"] == e.value.fix


async def test_a_redirect_elsewhere_names_the_location_in_its_fix() -> None:
    wire = Wire(lambda _: httpx2.Response(301, headers={"location": "http://elsewhere.example/"}))
    with pytest.raises(ConfigError) as e:
        await AnthropicBackend(wire.transport).complete(_spec(backend="anthropic"), _request(), "k")
    assert e.value.fix is not None and "http://elsewhere.example/" in e.value.fix


# --- a proxy is named by the row, never taken from the environment -----------------


@pytest.mark.parametrize("proxy", [None, "http://proxy.local:3128"])
async def test_a_row_names_its_proxy(proxy: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    # trust_env=False rightly ignores HTTPS_PROXY, which left a user behind a proxy no route.
    from hardline.adapters._out import _http

    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.example:1")
    built: list[str | None] = []
    wire = _answer(_chat())

    def transport(proxy: str | None = None) -> httpx2.AsyncBaseTransport:
        built.append(proxy)
        return wire.transport

    monkeypatch.setattr(_http, "network_transport", transport)
    await OpenAICompatBackend().complete(_spec(proxy=proxy), _request(), None)
    assert built == [proxy]


@pytest.mark.parametrize(
    ("backend", "name"), [(OpenAICompatBackend, "openai-compat"), (AnthropicBackend, "anthropic")]
)
async def test_a_backend_given_no_transport_reaches_the_workspace_guard(
    backend: Callable[[], ModelBackend], name: str, refused_live_calls: list[str]
) -> None:
    """The workspace guard refuses ``_http.network_transport``. This holds that a backend's
    request is carried by that very name, so the guard is in its path: a backend
    that bound the transport some other way would reach the provider past it. The row points
    at a closed loopback port all the same."""
    with pytest.raises(AssertionError, match="a live model session"):
        await backend().complete(_spec(backend=name), _request(), "k")
    assert refused_live_calls == ["hardline.adapters._out._http.network_transport"]
    refused_live_calls.clear()


@pytest.mark.parametrize(
    ("proxy", "problem"),
    [
        ("proxy.local:3128", "http:// or https://"),
        ("socks5://proxy.local:1080", "http:// or https://"),
        ("http://user:secret@proxy.local:3128", "credentials"),
    ],
)
def test_a_proxy_the_row_cannot_use_fails_composition(proxy: str, problem: str) -> None:
    with pytest.raises(ConfigError, match=rf"models\.q\.proxy: .*{problem}") as e:
        build_runtime(
            {
                "models": {
                    "q": {"backend": "openai-compat", "model": "m", "family": "x", "proxy": proxy}
                }
            }
        )
    assert "secret" not in str(e.value)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx2.ReadTimeout("read timed out"), "timeout"),
        (httpx2.ConnectTimeout("connect timed out"), "timeout"),
        (httpx2.ConnectError("refused"), "unavailable"),
        (httpx2.RemoteProtocolError("server hung up"), "unavailable"),
    ],
)
async def test_transport_failures_are_transient(error: Exception, reason: str) -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise error

    with pytest.raises(BackendError) as e:
        await OpenAICompatBackend(httpx2.MockTransport(fail)).complete(_spec(), _request(), None)
    assert e.value.reason == reason and e.value.retryable


@pytest.mark.parametrize(
    "reply",
    [
        httpx2.Response(200, text="<html>gateway</html>"),
        httpx2.Response(200, json=["not", "an", "object"]),
        httpx2.Response(200, json={"choices": "not a list"}),
    ],
)
async def test_a_reply_that_is_not_the_protocol_is_reported(reply: httpx2.Response) -> None:
    wire = Wire(lambda _: reply)
    with pytest.raises(BackendError) as e:
        await OpenAICompatBackend(wire.transport).complete(_spec(), _request(), None)
    assert e.value.reason == "unknown" and "127.0.0.1:9" in str(e.value)


def test_each_built_in_has_a_timeout_of_its_rows_choosing() -> None:
    # httpx2's own default is 5 s for every phase, far below a model's reply time.
    from hardline.adapters._out._http import client_timeout

    timeout = client_timeout(_spec(timeout_s=90.0))
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (90.0,) * 4


# --- why a reply ended, and a refusal kept apart from an answer -----------------


@pytest.mark.parametrize(
    ("native", "finish_reason"),
    [
        ("stop", "stop"),
        ("length", "length"),
        ("content_filter", "content_filter"),
        ("tool_calls", "tool_call"),
        ("function_call", "tool_call"),
        ("eos", "other"),
        (None, None),
    ],
)
async def test_openai_compat_reports_why_the_reply_ended(
    native: str | None, finish_reason: str | None
) -> None:
    # Before: finish_reason was dropped and `raw` excluded `choices`, so a reply cut at
    # max_tokens looked like a whole one.
    wire = _answer(_chat("partial", finish_reason=native))
    raw = await OpenAICompatBackend(wire.transport).complete(_spec(), _request(), None)
    assert (raw.finish_reason, raw.native_finish_reason) == (finish_reason, native)
    assert raw.refusal is None
    assert raw.raw["choices"] == [{"index": 0, "finish_reason": native}]  # no content


async def test_openai_compat_keeps_a_refusal_apart_from_the_text() -> None:
    # Before: text was `content or ""`, so a refusal arrived as an empty answer with no trace.
    wire = _answer(_chat(None, message={"role": "assistant", "content": None, "refusal": "No."}))
    raw = await OpenAICompatBackend(wire.transport).complete(_spec(), _request(), None)
    assert (raw.text, raw.refusal) == ("", "No.")
    assert (raw.finish_reason, raw.native_finish_reason) == ("refusal", "stop")


@pytest.mark.parametrize(
    ("native", "finish_reason"),
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("model_context_window_exceeded", "length"),
        ("tool_use", "tool_call"),
        ("pause_turn", "other"),
        ("refusal", "refusal"),
    ],
)
async def test_anthropic_reports_why_the_reply_ended(native: str, finish_reason: str) -> None:
    # Before: stop_reason survived only inside the untyped `raw`.
    wire = _answer(_message(stop_reason=native))
    raw = await AnthropicBackend(wire.transport).complete(
        _spec(backend="anthropic"), _request(), "k"
    )
    assert (raw.finish_reason, raw.native_finish_reason) == (finish_reason, native)
    assert (raw.refusal is not None) == (native == "refusal")
    assert raw.raw["stop_reason"] == native


async def test_the_mock_reports_a_finish_reason_a_row_chooses() -> None:
    spec = _spec(backend="mock", options={"finish_reason": "length"})
    raw = await MockBackend().complete(spec, _request(), None)
    assert raw.finish_reason == "length"
    assert (
        await MockBackend().complete(_spec(backend="mock"), _request(), None)
    ).finish_reason == ("stop")
