"""Tool calling: a request offers tools, a completion carries the calls the model asked for.

hardline runs no tool. It offers the definitions a caller hands it, returns the calls the model
asked for, and sends the caller's results back as ``tool`` turns, on both wire protocols: the
OpenAI chat-completions ``tools`` and ``tool_calls``, and the Messages API's ``tools`` and
``tool_use`` blocks. Each protocol is tested on the wire, over an httpx2 ``MockTransport``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from hardline_helpers import DictSecrets, mock_runtime
from pydantic import ValidationError

from hardline import (
    Completion,
    ConfigError,
    Endpoint,
    Message,
    ModelSpec,
    RawCompletion,
    Request,
    RequestParams,
    ToolCall,
    ToolSpec,
    Usage,
    backend_problems,
    build_runtime,
)
from hardline.adapters._out.anthropic_native import AnthropicBackend
from hardline.adapters._out.mock import MockBackend
from hardline.adapters._out.openai_compat import OpenAICompatBackend
from hardline.domain.digests import digest, turns_digest

LOOKUP = ToolSpec(
    name="lookup",
    description="Find a word in the dictionary.",
    parameters={"type": "object", "properties": {"word": {"type": "string"}}},
)

#: A conversation that has been through one tool call: asked, called, answered.
CALLED = (
    Message(role="system", content="sys"),
    Message(role="user", content="What does ossify mean?"),
    Message(
        role="assistant",
        content="Let me look.",
        tool_calls=(ToolCall(id="c1", name="lookup", arguments='{"word": "ossify"}'),),
    ),
    Message(role="tool", tool_call_id="c1", content="to turn into bone"),
)


class Wire:
    """A MockTransport that records each request body and answers with ``body``."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.transport = httpx2.MockTransport(self._handle)
        self._body = body

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=self._body)


def _spec(backend: str, base_url: str) -> Endpoint:
    """A registry row, as the runtime hands it to a backend."""
    return ModelSpec(name="q", backend=backend, model="m", family="x", base_url=base_url).endpoint()


# --- the OpenAI chat-completions protocol --------------------------------------------------


async def test_openai_compat_offers_tools_sends_tool_turns_and_reads_the_calls_asked_for() -> None:
    wire = Wire(
        {
            "id": "chatcmpl-2",
            "model": "served",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c2",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"word": "bone"}'},
                            },
                            # Some compatible servers send the arguments as an object, and no id.
                            {"type": "function", "function": {"name": "lookup", "arguments": {}}},
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 30, "completion_tokens": 9},
        }
    )
    request = Request(messages=CALLED, tools=(LOOKUP,), max_tokens=64)
    raw = await OpenAICompatBackend(wire.transport).complete(
        _spec("openai-compat", "http://127.0.0.1:9/v1"), request, None
    )
    body = wire.bodies[-1]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Find a word in the dictionary.",
                "parameters": LOOKUP.parameters,
            },
        }
    ]
    assert body["messages"][2:] == [
        {
            "role": "assistant",
            "content": "Let me look.",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"word": "ossify"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "to turn into bone"},
    ]
    assert raw.tool_calls == (
        ToolCall(id="c2", name="lookup", arguments='{"word": "bone"}'),
        ToolCall(id="call-1", name="lookup", arguments="{}"),
    )
    assert (raw.text, raw.finish_reason, raw.native_finish_reason) == (
        "",
        "tool_call",
        "tool_calls",
    )
    assert "lookup" not in json.dumps(raw.raw)  # the calls are content; raw never holds them


async def test_openai_compat_sends_no_tools_key_when_none_are_offered() -> None:
    wire = Wire({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    request = Request(messages=(Message(role="user", content="hi"),), max_tokens=8)
    await OpenAICompatBackend(wire.transport).complete(
        _spec("openai-compat", "http://127.0.0.1:9/v1"), request, None
    )
    assert "tools" not in wire.bodies[-1]


# --- the Anthropic Messages API --------------------------------------------------------------


async def test_anthropic_offers_tools_sends_results_as_one_user_turn_and_reads_tool_use() -> None:
    wire = Wire(
        {
            "id": "msg_2",
            "model": "claude-x",
            "content": [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "toolu_2", "name": "lookup", "input": {"word": "b"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 20, "output_tokens": 6},
        }
    )
    two = (
        *CALLED[:2],
        Message(
            role="assistant",
            tool_calls=(
                ToolCall(id="c1", name="lookup", arguments='{"word": "ossify"}'),
                ToolCall(id="c2", name="lookup", arguments='{"word": ""}'),
            ),
        ),
        Message(role="tool", tool_call_id="c1", content="to turn into bone"),
        Message(role="tool", tool_call_id="c2", content="no word given", is_error=True),
    )
    request = Request(messages=two, tools=(LOOKUP,), max_tokens=64)
    raw = await AnthropicBackend(wire.transport).complete(
        _spec("anthropic", "http://127.0.0.1:9"), request, "k"
    )
    body = wire.bodies[-1]
    assert body["tools"] == [
        {
            "name": "lookup",
            "description": "Find a word in the dictionary.",
            "input_schema": LOOKUP.parameters,
        }
    ]
    assert body["system"] == "sys"
    assert body["messages"] == [
        {"role": "user", "content": "What does ossify mean?"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "c1", "name": "lookup", "input": {"word": "ossify"}},
                {"type": "tool_use", "id": "c2", "name": "lookup", "input": {"word": ""}},
            ],
        },
        # Every result answering one assistant turn leads the next user turn, together.
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": "to turn into bone"},
                {
                    "type": "tool_result",
                    "tool_use_id": "c2",
                    "content": "no word given",
                    "is_error": True,
                },
            ],
        },
    ]
    assert raw.tool_calls == (ToolCall(id="toolu_2", name="lookup", arguments='{"word": "b"}'),)
    assert (raw.text, raw.finish_reason, raw.native_finish_reason) == (
        "Checking.",
        "tool_call",
        "tool_use",
    )
    assert "content" not in raw.raw


async def test_anthropic_refuses_arguments_that_are_not_a_json_object() -> None:
    # The Messages API takes a tool call's input as an object. A call whose arguments are not
    # one (a model on another protocol wrote bad JSON) cannot be sent back as it was asked.
    bad = (
        Message(role="user", content="hi"),
        Message(role="assistant", tool_calls=(ToolCall(id="c", name="lookup", arguments="{"),)),
        Message(role="tool", tool_call_id="c", content="?"),
    )
    with pytest.raises(ConfigError, match=r"tool call 'c'"):
        await AnthropicBackend(Wire({}).transport).complete(
            _spec("anthropic", "http://127.0.0.1:9"), Request(messages=bad, max_tokens=8), "k"
        )


# --- the runtime ----------------------------------------------------------------------------


def _scripted(*script: Any) -> Any:
    return mock_runtime(
        {"q": {"backend": "mock", "model": "m", "family": "x", "options": {"script": list(script)}}}
    )


async def test_a_tool_call_is_a_whole_answer_when_tools_were_offered() -> None:
    rt = _scripted({"tool_calls": [{"name": "lookup", "arguments": '{"word": "bone"}'}]})
    c = await rt.complete("q", "What is bone?", tools=[LOOKUP])
    assert c.tool_calls == (ToolCall(id="mock-q-1-0", name="lookup", arguments='{"word": "bone"}'),)
    assert (c.finish_reason, c.text) == ("tool_call", "")
    assert c.params is not None and c.params.tools == ("lookup",)
    assert c.answered


async def test_complete_keeps_its_promise_a_tool_call_unasked_for_is_no_answer() -> None:
    # The promise for plain `complete`: a reply that ended on a tool call is not a whole
    # answer. It is one only when the caller offered the tools.
    unasked = Completion(
        name="q", text="", family="x", model="m", backend="b", local=False, usage=Usage(),
        request_id=None, latency_ms=1, attempts=1, finish_reason="tool_call",
        tool_calls=(ToolCall(id="c", name="lookup"),),
        params=RequestParams(max_tokens=8, temperature=None, structured=None),
    )  # fmt: skip
    assert not unasked.answered
    offered = unasked.model_copy(
        update={"params": unasked.params.model_copy(update={"tools": ("lookup",)})}  # type: ignore[union-attr]
    )
    assert offered.answered
    assert not offered.model_copy(update={"tool_calls": ()}).answered  # offered, none asked for
    # Some compatible servers end a turn that asks for tools with `stop`: still an answer.
    assert offered.model_copy(update={"finish_reason": "stop"}).answered
    assert not unasked.model_copy(update={"finish_reason": "stop"}).answered  # no text either
    rt = _scripted("plain")
    assert (await rt.complete("q", "hi")).params.tools == ()


async def test_a_tool_turn_round_trips_through_the_runtime_and_its_digest_covers_it() -> None:
    rt = _scripted("done")
    c = await rt.complete("q", CALLED, tools=[LOOKUP])
    assert c.text == "done" and c.answered
    assert c.turns_digest == turns_digest(CALLED)
    other = CALLED[:2] + (
        CALLED[2].model_copy(
            update={"tool_calls": (ToolCall(id="c1", name="lookup", arguments="{}"),)}
        ),
        CALLED[3],
    )
    assert turns_digest(other) != turns_digest(CALLED)
    # Turns without tool parts keep the digest a reader recomputes as [[role, content], ...].
    plain = (Message(role="user", content="hi"),)
    assert turns_digest(plain) == digest([["user", "hi"]])


class _Textual:
    """A third-party backend written before tool calling: it declares no ``supports_tools``."""

    structured_modes = frozenset({"prompt"})

    def __init__(self) -> None:
        self.sent: list[Request] = []

    async def complete(
        self, spec: Endpoint, request: Request, api_key: str | None
    ) -> RawCompletion:
        self.sent.append(request)
        return RawCompletion(text="t", model=spec.model, finish_reason="stop")


async def test_a_backend_that_cannot_call_tools_is_refused_before_anything_is_sent() -> None:
    textual = _Textual()
    rt = build_runtime(
        {
            "models": {
                "q": {"backend": "mock", "model": "m", "family": "x", "fallbacks": ["t"]},
                "t": {"backend": "textual", "model": "t", "family": "x"},
            }
        },
        backends={"mock": MockBackend(), "textual": textual},
        secrets=DictSecrets(),
    )
    with pytest.raises(ConfigError, match=r"backend 'textual'") as e:
        await rt.complete("q", "hi", tools=[LOOKUP])
    assert e.value.problems[0].path == "models.t.backend"
    with pytest.raises(ConfigError):
        await rt.complete("t", CALLED)  # tool turns need a backend that can send them
    assert textual.sent == []
    with pytest.raises(ConfigError):
        rt.check_tools("q")
    assert (await rt.complete("t", "hi")).text == "t"  # without tools it serves as before


def test_tool_parts_belong_to_their_roles() -> None:
    with pytest.raises(ValidationError, match="tool_calls"):
        Message(role="user", content="x", tool_calls=(ToolCall(id="c", name="n"),))
    with pytest.raises(ValidationError, match="tool_call_id"):
        Message(role="tool", content="x")
    with pytest.raises(ValidationError, match="tool_call_id"):
        Message(role="user", content="x", tool_call_id="c")
    with pytest.raises(ValidationError, match="is_error"):
        Message(role="assistant", content="x", is_error=True)
    with pytest.raises(ValidationError, match="name"):
        ToolSpec(name="has space")


def test_supports_tools_is_part_of_the_checked_contract() -> None:
    assert backend_problems(MockBackend()) == []

    class Claims(_Textual):
        supports_tools = "yes"

    assert backend_problems(Claims()) == ["supports_tools: present but not a bool"]
