"""AnthropicBackend — the Anthropic Messages API, natively, over HTTP.

The request is a POST to ``<base_url>/v1/messages`` with the row's key in ``x-api-key``. A row
with no ``base_url`` is given ``default_base_url`` by the composition root, so the row records
where it sends. A row with no ``api_key`` is refused when the runtime is composed: the registry
row is the only place a credential may come from, and nothing reads ``ANTHROPIC_API_KEY`` or
``ANTHROPIC_BASE_URL``.

System turns are lifted into the ``system`` parameter, as the API requires. Tools are the API's
``tools`` (each with an ``input_schema``) and its ``tool_use`` and ``tool_result`` blocks: an
assistant turn's tool calls are ``tool_use`` blocks after its text, and the results that answer
them are ``tool_result`` blocks of one user turn, ahead of anything else that turn says, as the
API requires. A call's arguments must be a JSON object to be sent back. Structured output
is ``prompt`` mode only here: the schema is stated in an instruction and the runtime validates
the reply. A registry row asking this backend for ``json_schema`` is rejected at composition,
not silently downgraded. HTTP, timeouts and connection lifetimes are ``_http``'s.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

from hardline.adapters._out._http import Http, location_problems
from hardline.adapters._out._options import option_problems, read_options
from hardline.domain.errors import BackendError, ConfigError
from hardline.domain.types import Endpoint, RawCompletion, Request, ToolCall, Usage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hardline.adapters._out._http import Transport
    from hardline.domain.types import FinishReason, Message

#: The API's stop reasons, in hardline's vocabulary. Anything else is ``other``.
_FINISH: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_call",
    "refusal": "refusal",
}

#: The Messages API version this adapter is written against.
API_VERSION = "2023-06-01"

_NO_KEY = (
    "api_key: the anthropic backend needs a key reference. "
    "Add api_key: env:ANTHROPIC_API_KEY or api_key: file:~/.secrets/claude-api"
)


class _Options(BaseModel):
    """This backend reads no ``options``; any key is refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# The reply's shape, as far as hardline reads it.


class _Block(BaseModel):
    type: str
    text: str | None = None
    #: A ``tool_use`` block's.
    id: str | None = None
    name: str | None = None
    input: Any = None


class _Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


class _Reply(BaseModel):
    id: str | None = None
    model: str | None = None
    content: list[_Block] = []
    stop_reason: str | None = None
    usage: _Usage | None = None


class AnthropicBackend:
    structured_modes = frozenset({"prompt"})
    supports_tools = True
    default_base_url = "https://api.anthropic.com"

    def __init__(self, transport: Transport | None = None) -> None:
        self._http = Http(transport)

    def check(self, spec: Endpoint) -> list[str]:
        return (
            location_problems(spec)
            + option_problems(_Options, spec)
            + ([] if spec.has_key else [_NO_KEY])
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(
        self, spec: Endpoint, request: Request, api_key: str | None
    ) -> RawCompletion:
        read_options(_Options, spec)
        if not api_key:
            raise ConfigError(f"models.{spec.name}.{_NO_KEY}")
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        body: dict[str, Any] = {
            "model": spec.model,
            "max_tokens": request.max_tokens,
            "messages": _turns([m for m in request.messages if m.role != "system"]),
        }
        if system:
            body["system"] = system
        if request.tools:
            body["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in request.tools
            ]
        if request.temperature is not None:
            body["temperature"] = request.temperature
        headers = {"x-api-key": api_key, "anthropic-version": API_VERSION}

        data = await self._http.post(spec, "v1/messages", body, headers, api_key)
        try:
            reply = _Reply.model_validate(data)
        except ValidationError as e:
            raise BackendError(
                f"model {spec.name!r} ({spec.backend} → {spec.base_url}): the reply is not a "
                f"message: {e.error_count()} problem(s), first at "
                f"{'.'.join(map(str, e.errors()[0]['loc']))}",
                reason="unknown",
            ) from None

        usage = reply.usage or _Usage()
        text = "".join(block.text or "" for block in reply.content if block.type == "text")
        native = reply.stop_reason
        cache_read = usage.cache_read_input_tokens or 0
        cache_write = usage.cache_creation_input_tokens or 0
        return RawCompletion(
            text=text,
            model=reply.model or spec.model,
            usage=Usage(
                # The API's input_tokens leaves out the tokens read from and written to the
                # cache; hardline's counts every input token (see ``Usage``).
                input_tokens=(usage.input_tokens or 0) + cache_read + cache_write,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
            ),
            request_id=reply.id,
            finish_reason=_FINISH.get(native, "other") if native is not None else None,
            native_finish_reason=native,
            # The API marks a refusal by its stop reason and says nothing apart from the text.
            refusal=text if native == "refusal" else None,
            tool_calls=tuple(
                ToolCall(
                    id=block.id or f"call-{i}",
                    name=block.name or "",
                    arguments=json.dumps(block.input if block.input is not None else {}),
                )
                for i, block in enumerate(b for b in reply.content if b.type == "tool_use")
            ),
            raw={key: value for key, value in data.items() if key != "content"},
        )


def _turns(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """The conversation as the API takes it. A tool's result is a ``tool_result`` block of a
    user turn: the results answering one assistant turn are one user turn, and whatever the
    caller says next joins them there, after them. An assistant turn that says nothing (no
    text, no tool calls) is left out: the API refuses a text block with no text, and the user
    turns either side of it are combined by the API into one."""
    turns: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "assistant" and not message.content.strip() and not message.tool_calls:
            continue
        role = "assistant" if message.role == "assistant" else "user"
        last = turns[-1] if turns else None
        if role == "user" and last is not None and last["role"] == "user" and _results(last):
            last["content"] = [*last["content"], *_blocks(message)]
            continue
        plain = message.role != "tool" and not message.tool_calls
        turns.append({"role": role, "content": message.content if plain else _blocks(message)})
    return turns


def _blocks(message: Message) -> list[dict[str, Any]]:
    if message.role == "tool":
        result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": message.tool_call_id,
            "content": message.content,
        }
        if message.is_error:
            result["is_error"] = True
        return [result]
    # A text of whitespace alone is left out, as a turn that says nothing is: the API refuses
    # a text block with no text, and the tool calls beside it are the turn.
    blocks: list[dict[str, Any]] = (
        [{"type": "text", "text": message.content}] if message.content.strip() else []
    )
    for call in message.tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": _input(call)})
    return blocks


def _results(turn: dict[str, Any]) -> bool:
    """Whether a user turn carries tool results, which what the caller says next joins."""
    content = turn["content"]
    return isinstance(content, list) and any(b.get("type") == "tool_result" for b in content)


def _input(call: ToolCall) -> dict[str, Any]:
    """A call's arguments as the object the API takes."""
    try:
        value = json.loads(call.arguments)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise ConfigError(
            f"tool call {call.id!r} ({call.name}): its arguments are not a JSON object, which "
            "the Messages API needs to send it back",
            fix="send the call's arguments as a JSON object, or leave the call out of the turns",
        )
    return value
