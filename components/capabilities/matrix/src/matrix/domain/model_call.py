"""A model call: one request to one model, and the record of what answered it.

A model call is not an agent session. It has no loop, no tools and no turns: a prompt goes
in, a reply comes out. So it has its own request, its own record and its own port
(:class:`~matrix.domain.ports._out.model_runtime.ModelRuntime`), beside the agent's.

* **ModelRequest** is what a caller asks: a prompt, optionally a system prompt, a model name
  the runtime resolves, and an ``output`` type when the reply must be that structure.
* **ModelCall** is what came back: the reply (``text``, and ``value`` when an ``output`` type
  was asked for) and what the runtime knows of how it was answered: the model that answered,
  which a fallback may make differ from the one asked for (``fallback_from``), usage with
  cache tokens, the provider's request id, attempts, retries and latency. A fact the runtime
  does not report is None, never a constant: ``finish_reason`` and ``refusal`` stay None
  until the runtime reports them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class TokenUsage(BaseModel):
    """Tokens one call cost, as the provider counted them. Cache fields are 0 where the
    provider reports none."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    #: Input tokens served from the provider's prompt cache.
    cache_read_tokens: int = 0
    #: Input tokens written to the provider's prompt cache.
    cache_write_tokens: int = 0


class ModelRequest(BaseModel):
    """One model call, as asked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt: str
    #: A name the runtime resolves (a hardline registry name). ``None``: its default.
    model: str | None = None
    system: str | None = None
    #: The structure the reply must have. The runtime validates the reply against it and
    #: returns the result as :attr:`ModelCall.value`, or raises.
    output: type[BaseModel] | None = None


class ModelCall(BaseModel):
    """The runtime-neutral record of one model call."""

    model_config = ConfigDict(frozen=True)

    #: The reply as the model wrote it.
    text: str = ""
    #: The reply validated against the request's ``output`` type, as JSON data. None when
    #: the request named none.
    value: Any = None
    #: The registry name of the model that answered.
    name: str | None = None
    #: The provider's id of the model that answered.
    model: str | None = None
    #: The family of the model that answered (``claude``, ``qwen``, ``gemini``).
    family: str | None = None
    #: The backend that served it (hardline's ``anthropic``, ``openai-compat``, ...).
    backend: str | None = None
    #: Who served it, as the GenAI conventions name providers (``anthropic``), when known.
    provider: str | None = None
    usage: TokenUsage = TokenUsage()
    #: The provider's id for the request that answered, when it gave one.
    request_id: str | None = None
    #: Calls made for a structured reply: 1, plus each repair after a reply that did not fit.
    attempts: int = 1
    #: Transient failures retried, across every attempt and model.
    retries: int = 0
    #: The model asked for, when a fallback answered instead.
    fallback_from: str | None = None
    latency_ms: int = 0
    #: Why the model stopped writing, as the provider said. None until the runtime reports it.
    finish_reason: str | None = None
    #: The model's refusal, when it declined. None until the runtime reports it.
    refusal: str | None = None
    #: The prompt as sent: a ``model-step`` records it here, after filling its inputs.
    prompt: str | None = None
