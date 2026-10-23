"""A model call: one request to one model, and the record of what answered it.

A model call is not an agent session. It has no loop, no tools and no turns: a prompt goes
in, a reply comes out. So it has its own request, its own record and its own port
(:class:`~matrix.domain.ports._out.model_runtime.ModelRuntime`), beside the agent's.

* **ModelRequest** is what a caller asks: a prompt, optionally a system prompt, a model name
  the runtime resolves, and an ``output`` type when the reply must be that structure.
* **ModelCall** is what came back: the reply (``text``, and ``value`` when an ``output`` type
  was asked for) and what the runtime knows of how it was answered: why the reply ended
  (``finish_reason``, with the provider's own value and any refusal), the model that answered,
  which a fallback may make differ from the one asked for (``fallback_from``), usage with
  cache tokens, the provider's request id, attempts, retries and latency, what was sent
  (``params``, ``turns_digest``), every attempt made (``attempt_log``) and the registry row
  that answered (``row``). A fact the runtime does not report is None, never a constant.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

#: Why a reply ended, in one vocabulary across runtimes (hardline's). The provider's own value
#: is kept beside it (``native_finish_reason``).
#:   stop            the model ended its turn
#:   length          cut off at max_tokens (or the context window)
#:   content_filter  the provider withheld or cut the reply
#:   refusal         the model refused; ``refusal`` holds what it said
#:   tool_call       the model asked for a tool, which a model call does not run
#:   other           a reason the provider gave that none of these names
FinishReason = Literal["stop", "length", "content_filter", "refusal", "tool_call", "other"]


class TokenUsage(BaseModel):
    """Tokens one call cost, as the provider counted them. Cache fields are 0 where the
    provider reports none.

    ``input_tokens`` is every input token, those read from and written to the cache included,
    as the GenAI conventions count ``gen_ai.usage.input_tokens`` and as an agent session's
    ``tokens_input`` counts them; the cache fields say how many of them, and are never added
    to it again."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Every input token, the cache's included.
    input_tokens: int = 0
    output_tokens: int = 0
    #: Of ``input_tokens``, those served from the provider's prompt cache.
    cache_read_tokens: int = 0
    #: Of ``input_tokens``, those written to the provider's prompt cache.
    cache_write_tokens: int = 0


class RequestParams(BaseModel):
    """What was sent to the model that answered, after its registry row's defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_tokens: int
    temperature: float | None = None
    #: How structured output was asked for (``prompt``, ``json_object``, ``json_schema``);
    #: None for a plain call.
    structured: str | None = None


class CallAttempt(BaseModel):
    """One backend call made for a model call: a retry, a fallback or a schema repair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The registry row asked.
    name: str
    #: Why it failed (``rate_limit``, ``unavailable``, ...); None for the call that answered.
    reason: str | None = None
    #: What the provider reported; a failed call reports none.
    usage: TokenUsage | None = None
    latency_ms: int


class RegistryRow(BaseModel):
    """The registry row that answered, as it was configured: the digest of its whole spec
    (``sha256:<hex>`` over canonical JSON), and for each field a configuration set, the tier
    that set it (``matrix.models``, ``cwd ./hardline.yaml``, ``default of backend ...``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    digest: str
    rungs: dict[str, str] = {}


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
    """The runtime-neutral record of one model call: what a runtime returns.

    On a reply row a model step provides, the facts the session row holds are moved there:
    the row keeps the reply and what a consumer acts on (``text``, ``value``,
    ``finish_reason``, ``refusal``, ``family``, ``prompt``) and ``session``, the position of
    its row in ``matrix.v1.session``; every field the session row holds is None
    (:meth:`as_reply`).
    """

    model_config = ConfigDict(frozen=True)

    #: The reply as the model wrote it.
    text: str = ""
    #: The reply validated against the request's ``output`` type, as JSON data. None when
    #: the request named none.
    value: Any = None
    #: Why the reply ended; ``stop`` only when the model ended its turn. None when the
    #: runtime does not say.
    finish_reason: FinishReason | None = None
    #: The provider's own value (``finish_reason``, ``stop_reason``), verbatim.
    native_finish_reason: str | None = None
    #: What the model said when it refused; None when it did not refuse.
    refusal: str | None = None
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
    #: Summed over every backend call made (``attempt_log``) that reported usage.
    usage: TokenUsage | None = None
    #: The provider's id for the request that answered, when it gave one.
    request_id: str | None = None
    #: Calls made for a structured reply: 1, plus each repair after a reply that did not fit.
    attempts: int | None = None
    #: Transient failures retried, across every attempt and model.
    retries: int | None = None
    #: The model asked for, when a fallback answered instead.
    fallback_from: str | None = None
    latency_ms: int | None = None
    #: What was sent to the model that answered.
    params: RequestParams | None = None
    #: Every backend call made, in order, with why each failed.
    attempt_log: tuple[CallAttempt, ...] | None = None
    #: The digest of the turns the answering call was sent (``sha256:<hex>``): the schema
    #: instruction and any repair turns included.
    turns_digest: str | None = None
    #: The registry row that answered.
    row: RegistryRow | None = None
    #: The prompt as sent: a ``model-step`` records it here, after filling its inputs.
    prompt: str | None = None
    #: The position of this call's row in the run's ``matrix.v1.session`` table, when a run
    #: recorded it (for a reused row, the run ``reused_from`` names). None outside a run.
    session: int | None = None

    def as_reply(self) -> ModelCall:
        """This call as a reply row: with ``session`` set, the facts its session row holds
        are moved off. Without it nothing else holds them, and they stay."""
        if self.session is None:
            return self
        return self.model_copy(update=dict.fromkeys(_SESSION_FACTS))


#: The facts of a model call its session row holds, and a reply row does not repeat.
_SESSION_FACTS = (
    "native_finish_reason",
    "name",
    "model",
    "backend",
    "provider",
    "usage",
    "request_id",
    "attempts",
    "retries",
    "fallback_from",
    "latency_ms",
    "params",
    "attempt_log",
    "turns_digest",
    "row",
)
