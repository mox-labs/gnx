"""OpenAICompatBackend — the OpenAI chat-completions wire protocol, over HTTP.

One adapter for every server that speaks it: mlx_lm.server, ollama, vLLM, llama.cpp,
Gemini's compatibility endpoint, OpenAI itself. A model is a registry row naming a
``base_url`` and a ``model``; nothing here knows which server is on the other end. A row with
no ``base_url`` is given ``default_base_url`` by the composition root, so the row records where
it sends.

Row options this adapter reads, and no others::

    options:
      token_param: max_tokens | max_completion_tokens   # default max_tokens (local servers)

The request is a POST to ``<base_url>/chat/completions``. A key, when the row names one, is
sent as ``Authorization: Bearer``; a row with no key sends no such header, which a local server
ignores and a hosted endpoint rejects loudly. Nothing is read from the environment. HTTP,
timeouts and connection lifetimes are ``_http``'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from hardline.adapters._out._http import Http, location_problems
from hardline.adapters._out._options import option_problems, read_options
from hardline.domain.errors import BackendError
from hardline.domain.types import ModelSpec, RawCompletion, Request, Usage

if TYPE_CHECKING:
    from hardline.adapters._out._http import Transport
    from hardline.domain.types import FinishReason

#: The protocol's finish reasons, in hardline's vocabulary. Anything else is ``other``.
_FINISH: dict[str, FinishReason] = {
    "stop": "stop",
    "length": "length",
    "content_filter": "content_filter",
    "tool_calls": "tool_call",
    "function_call": "tool_call",
}

#: Keys of a choice that carry the reply's content, which ``raw`` never holds.
_CONTENT_KEYS = frozenset({"message", "delta", "text", "logprobs"})


class _Options(BaseModel):
    """The ``options`` this backend reads; any other key is refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"


# The reply's shape, as far as hardline reads it. Compatible servers omit or null many fields
# (mlx_lm and ollama often send no usage details), so everything is optional.


class _Message(BaseModel):
    content: str | list[Any] | None = None
    refusal: str | None = None


class _Choice(BaseModel):
    message: _Message = _Message()
    finish_reason: str | None = None


class _PromptDetails(BaseModel):
    cached_tokens: int | None = None


class _Usage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    prompt_tokens_details: _PromptDetails | None = None


class _Reply(BaseModel):
    id: str | None = None
    model: str | None = None
    choices: list[_Choice] = []
    usage: _Usage | None = None


class OpenAICompatBackend:
    structured_modes = frozenset({"prompt", "json_object", "json_schema"})
    default_base_url = "https://api.openai.com/v1"

    def __init__(self, transport: Transport | None = None) -> None:
        self._http = Http(transport)

    def check(self, spec: ModelSpec) -> list[str]:
        return location_problems(spec) + option_problems(_Options, spec)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(
        self, spec: ModelSpec, request: Request, api_key: str | None
    ) -> RawCompletion:
        options = read_options(_Options, spec)
        body: dict[str, Any] = {
            "model": spec.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            options.token_param: request.max_tokens,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.json_schema is not None and spec.structured == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif request.json_schema is not None and spec.structured == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": request.json_schema},
            }
        headers = {"authorization": f"Bearer {api_key}"} if api_key else {}

        data = await self._http.post(spec, "chat/completions", body, headers, api_key)
        try:
            reply = _Reply.model_validate(data)
        except ValidationError as e:
            raise BackendError(
                f"model {spec.name!r} ({spec.backend} → {spec.base_url}): the reply is not a "
                f"chat completion: {e.error_count()} problem(s), first at "
                f"{'.'.join(map(str, e.errors()[0]['loc']))}",
                reason="unknown",
            ) from None

        choice = reply.choices[0] if reply.choices else _Choice()
        native = choice.finish_reason
        refusal = choice.message.refusal
        usage = reply.usage or _Usage()
        details = usage.prompt_tokens_details or _PromptDetails()
        return RawCompletion(
            text=_text(choice.message.content),
            model=reply.model or spec.model,
            usage=Usage(
                input_tokens=usage.prompt_tokens or 0,
                output_tokens=usage.completion_tokens or 0,
                cache_read_tokens=details.cached_tokens or 0,
            ),
            request_id=reply.id,
            finish_reason=(
                "refusal"
                if refusal is not None
                else _FINISH.get(native, "other")
                if native is not None
                else None
            ),
            native_finish_reason=native,
            refusal=refusal,
            raw=_metadata(data),
        )


def _metadata(data: dict[str, Any]) -> dict[str, Any]:
    """The reply without its content: each choice keeps its index and finish reason."""
    raw = dict(data)
    choices = raw.get("choices")
    if isinstance(choices, list):
        raw["choices"] = [
            {key: value for key, value in c.items() if key not in _CONTENT_KEYS}
            for c in choices
            if isinstance(c, dict)
        ]
    return raw


def _text(content: str | list[Any] | None) -> str:
    """A message's text. Some compatible servers send a list of parts instead of a string."""
    if isinstance(content, list):
        return "".join(
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )
    return content or ""
