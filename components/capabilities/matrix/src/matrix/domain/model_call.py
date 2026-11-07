"""A model call: one request to one model, and the reply.

A model call is not an agent session. It has no loop: it may carry a conversation, offer the
model tools and come back asking for them, but it runs none, and whoever holds the loop (an
agent runtime) runs them and calls again. So it has its own request, its own reply and its
own port (:class:`~matrix.domain.ports._out.model_runtime.ModelRuntime`), beside the agent's.

* **ModelRequest** is one call in full: the ``turns`` (system, user, assistant with the tool
  calls it asked for, and each tool's result), the ``tools`` offered, an ``output`` type when
  the reply must be that structure, a model name the runtime resolves, and the two overrides
  every chat-completion provider takes, ``max_tokens`` and ``temperature``.
  :meth:`ModelRequest.from_prompt` is a request of one user turn.
* **ModelCall** is what came back: the reply (``text``, the ``tool_calls`` the model asked
  for, and ``value`` when an ``output`` type was asked for) and what any model runtime can
  say of how it was answered: why the reply ended (``finish_reason``, with the provider's own
  value and any refusal), the name, model, family and provider that answered, usage with
  cache tokens, the provider's request id and the latency. A fact the runtime does not report
  is None, never a constant.

What only one runtime knows of a call (its retries, its fallbacks, its own record of what it
sent) is not a field here, since every runtime knows different things: it is a document in
``provenance``, keyed by its type URL (``hardline.v1.call``), whose schema the runtime's
package registers (``register_payload``), so a reader dispatches on the URL and validates
against the schema.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from matrix.domain.ids import versioned_problem

#: Why a reply ended, in matrix's own vocabulary; each runtime maps its provider's onto it
#: and keeps the provider's own value beside it (``native_finish_reason``).
#:   stop            the model ended its turn
#:   length          cut off at max_tokens (or the context window)
#:   content_filter  the provider withheld or cut the reply
#:   refusal         the model refused; ``refusal`` holds what it said
#:   tool_call       the model asked for a tool, which a model call does not run
#:   other           a reason the provider gave that none of these names
FinishReason = Literal["stop", "length", "content_filter", "refusal", "tool_call", "other"]

#: Who speaks a turn.
Role = Literal["system", "user", "assistant", "tool"]

#: What a call may ask of a runtime beyond a reply in text, as ``check`` is asked about it:
#: ``tools`` (tools offered, or tool turns sent) and ``output`` (a reply validated against
#: a structure).
Use = Literal["tools", "output"]


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


class ToolSpec(BaseModel):
    """A tool offered to the model: its name, what it does, and its parameters as a JSON
    schema. The model may ask for it; nothing in a model call runs it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    """A tool the model asked for: the id its result answers, the tool's name, and its
    arguments as the JSON text the model wrote (the caller validates them)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: str = "{}"


class Turn(BaseModel):
    """One turn of the conversation a call carries.

    An ``assistant`` turn may carry the ``tool_calls`` its model asked for; a ``tool`` turn is
    the result of one of them, naming it by ``tool_call_id``, with ``is_error`` when the tool
    failed or its arguments were refused. Text only: no image or document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    is_error: bool = False

    @model_validator(mode="after")
    def _tool_parts_fit_the_role(self) -> Turn:
        if self.tool_calls and self.role != "assistant":
            raise ValueError("only an assistant turn carries tool_calls")
        if (self.role == "tool") != (self.tool_call_id is not None):
            raise ValueError("a tool turn, and only a tool turn, names the tool_call_id it answers")
        if self.is_error and self.role != "tool":
            raise ValueError("only a tool turn is_error")
        return self


class ModelRequest(BaseModel):
    """One model call, as asked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turns: tuple[Turn, ...] = Field(min_length=1)
    #: A name the runtime resolves. ``None``: its default.
    model: str | None = None
    #: The tools the model may ask for. None is run by the call.
    tools: tuple[ToolSpec, ...] = ()
    #: The structure the reply must have. The runtime validates the reply against it and
    #: returns the result as :attr:`ModelCall.value`, or raises.
    output: type[BaseModel] | None = None
    #: Overrides of the runtime's own: the most tokens the reply may have, and the sampling
    #: temperature. ``None``: the runtime's (its config's, else its model's).
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0)

    @classmethod
    def from_prompt(
        cls,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        output: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ModelRequest:
        """A request of one user turn, ``prompt``, after a ``system`` turn when one is given."""
        turns = [Turn(role="user", content=prompt)]
        if system is not None:
            turns.insert(0, Turn(role="system", content=system))
        return cls(
            turns=tuple(turns),
            model=model,
            output=output,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    @property
    def prompt(self) -> str | None:
        """The text of the one user turn, when the request is a prompt (system turns and one
        user turn); None for a conversation."""
        users = [turn for turn in self.turns if turn.role == "user"]
        if len(users) == 1 and all(turn.role in ("system", "user") for turn in self.turns):
            return users[0].content
        return None

    @property
    def uses(self) -> frozenset[Use]:
        """What the request asks of the runtime beyond text: what ``check`` is asked about."""
        found: set[Use] = set()
        if self.tools or any(turn.tool_calls or turn.role == "tool" for turn in self.turns):
            found.add("tools")
        if self.output is not None:
            found.add("output")
        return frozenset(found)


class ModelCall(BaseModel):
    """The runtime-neutral record of one model call: what a runtime returns.

    On a reply row a model step provides, the facts the session row holds are moved there:
    the row keeps the reply and what a consumer acts on (``text``, ``value``, ``tool_calls``,
    ``finish_reason``, ``refusal``, ``family``, ``prompt``) and ``session``, the position of
    its row in ``matrix.v1.session``; every field the session row holds is None or empty
    (:meth:`as_reply`).
    """

    model_config = ConfigDict(frozen=True)

    #: The reply as the model wrote it.
    text: str = ""
    #: The reply validated against the request's ``output`` type, as JSON data. None when
    #: the request named none.
    value: Any = None
    #: The tools the model asked for, none of them run.
    tool_calls: tuple[ToolCall, ...] = ()
    #: Why the reply ended; ``stop`` only when the model ended its turn. None when the
    #: runtime does not say.
    finish_reason: FinishReason | None = None
    #: The provider's own value (``finish_reason``, ``stop_reason``), verbatim.
    native_finish_reason: str | None = None
    #: What the model said when it refused; None when it did not refuse.
    refusal: str | None = None
    #: The runtime's name for the model that answered, which a fallback may make differ from
    #: the one asked for.
    name: str | None = None
    #: The provider's id of the model that answered.
    model: str | None = None
    #: The family of the model that answered (``claude``, ``qwen``, ``gemini``).
    family: str | None = None
    #: Who served it, as the GenAI conventions name providers (``anthropic``), when known.
    provider: str | None = None
    #: What the call cost, summed over every request the runtime made for it.
    usage: TokenUsage | None = None
    #: The provider's id for the request that answered, when it gave one.
    request_id: str | None = None
    latency_ms: int | None = None
    #: What only the runtime that made the call knows of it: type URL -> the document, JSON
    #: data that the schema registered for that type URL validates. Empty: nothing beyond
    #: these fields.
    provenance: dict[str, dict[str, Any]] = {}
    #: The prompt as sent: a ``model-step`` records it here, after filling its inputs.
    prompt: str | None = None
    #: The position of this call's row in the run's ``matrix.v1.session`` table, when a run
    #: recorded it (for a reused row, the run ``reused_from`` names). None outside a run.
    session: int | None = None

    @field_validator("provenance")
    @classmethod
    def _keyed_by_type_url(cls, value: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        for url in value:
            reason = versioned_problem(url)
            if reason is not None:
                raise ValueError(f"provenance key {url!r} is not a type URL: {reason}")
        return value

    def as_reply(self) -> ModelCall:
        """This call as a reply row: with ``session`` set, the facts its session row holds
        are moved off. Without it nothing else holds them, and they stay."""
        if self.session is None:
            return self
        return self.model_copy(update={**dict.fromkeys(_SESSION_FACTS), "provenance": {}})


#: The facts of a model call its session row holds, and a reply row does not repeat.
_SESSION_FACTS = (
    "native_finish_reason",
    "name",
    "model",
    "provider",
    "usage",
    "request_id",
    "latency_ms",
)
