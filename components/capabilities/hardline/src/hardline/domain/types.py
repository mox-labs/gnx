"""hardline types — the port's vocabulary.

Messages and an optional output schema go in; text (or a validated object) comes out,
tagged with the family that answered, what it cost, and enough provenance to ledger.
Stateless: the conversation belongs to the caller.

The one type URL, ``hardline.v1/completion``, follows the matrix convention
``<namespace>.v<version>/<resource>`` (xDS TypedExtensionConfig). A backend is keyed by its
entry-point name alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hardline.domain.errors import Reason  # noqa: TC001 -- pydantic reads it at runtime

NAMESPACE = "hardline.v1"
COMPLETION_TYPE_URL = f"{NAMESPACE}/completion"


#: ``tool`` is a tool's result, sent back to the model: the caller ran the tool, not hardline.
Role = Literal["system", "user", "assistant", "tool"]

#: How structured output is requested from the provider. Validation happens in the
#: runtime regardless — these only decide how hard the provider is asked.
#:   prompt       the schema is stated in an instruction; works with every backend
#:   json_object  the provider is asked for JSON (OpenAI ``response_format`` json_object)
#:   json_schema  the provider is handed the schema (OpenAI ``response_format`` json_schema)
StructuredMode = Literal["prompt", "json_object", "json_schema"]

#: Why a reply ended, in one vocabulary across providers. The provider's own value is kept
#: beside it (``native_finish_reason``).
#:   stop            the model ended its turn
#:   length          cut off at max_tokens (or the context window)
#:   content_filter  the provider withheld or cut the reply
#:   refusal         the model refused; ``refusal`` holds what it said
#:   tool_call       the model asked for a tool, which hardline does not run: a whole answer
#:                   when the caller offered tools, and never otherwise (``Completion.answered``)
#:   other           a reason the provider gave that none of these names
FinishReason = Literal["stop", "length", "content_filter", "refusal", "tool_call", "other"]

_SLUG = r"^[a-z0-9][a-z0-9._-]*$"
_SECRET_SCHEMES = ("env:", "file:")
#: What both protocols accept as a tool's name.
_TOOL_NAME = r"^[A-Za-z0-9_-]{1,64}$"


class ToolSpec(BaseModel):
    """A tool a request offers the model. hardline offers it and runs nothing: when the model
    asks for it, the call comes back on the completion (``tool_calls``) for the caller to run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_TOOL_NAME)
    description: str = ""
    #: A JSON Schema of the arguments, an object.
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    """A call the model asked for. The caller runs it and sends what it returned back as a
    ``tool`` turn naming ``id``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The provider's id for the call, or ``call-<n>`` (its position) when it gave none.
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    #: The arguments, as the JSON text the model wrote. It may not parse, or not fit the
    #: tool's schema: the caller validates it, and may tell the model what was wrong.
    arguments: str = "{}"


class Message(BaseModel):
    """One turn of a conversation the caller owns.

    An ``assistant`` turn may carry the ``tool_calls`` its model asked for; a ``tool`` turn is
    the result of one of them, naming it by ``tool_call_id``, with ``is_error`` when the tool
    failed or its arguments were refused.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    is_error: bool = False

    @model_validator(mode="after")
    def _tool_parts_fit_the_role(self) -> Message:
        if self.tool_calls and self.role != "assistant":
            raise ValueError("only an assistant turn carries tool_calls")
        if (self.role == "tool") != (self.tool_call_id is not None):
            raise ValueError("a tool turn, and only a tool turn, names the tool_call_id it answers")
        if self.is_error and self.role != "tool":
            raise ValueError("only a tool turn is_error")
        return self


#: The bounds a ``CallLimits`` can set, by key.
Limit = Literal["attempts", "seconds", "tokens"]


class CallLimits(BaseModel):
    """Bounds on one logical call: a ``complete`` or ``extract``, with every retry, fallback and
    schema repair it makes. Each is unset by default, which leaves the call bounded only by its
    chain: (schema repairs + 1) x the sum over the chain of (retries + 1) backend calls.

    The row asked for sets the bounds of the whole call; a fallback's own ``limits`` apply only
    to a call that asks for the fallback by name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Backend calls, at most: every retry, fallback and schema repair counts one.
    attempts: int | None = Field(default=None, ge=1)
    #: Wall time from the call's start, in seconds. An attempt still running when it passes is
    #: cut, and a wait for a retry that would pass it is not started.
    seconds: float | None = Field(default=None, gt=0)
    #: Tokens reported (input and output), summed over the call's backend calls. Checked
    #: between calls, so the call that crosses it completes; a failed call reports none.
    tokens: int | None = Field(default=None, ge=1)


class ModelSpec(BaseModel):
    """One registry row: a model is configuration, not a class.

    ``family`` is required and never inferred from the model id. It is the field an
    evaluation harness reads to decide whether a verifier is out of family, and a guessed
    family is worse than none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_SLUG)
    backend: str = Field(pattern=_SLUG)
    model: str = Field(min_length=1)
    family: str = Field(pattern=_SLUG)
    base_url: str | None = None
    #: An http:// or https:// proxy every request of this row goes through. Nothing in the
    #: environment (``HTTPS_PROXY``) is read: a row with no ``proxy`` connects directly.
    proxy: str | None = None
    api_key: str | None = None
    local: bool = False
    structured: StructuredMode = "prompt"
    max_tokens: int = Field(default=1024, gt=0)
    temperature: float | None = Field(default=None, ge=0.0)
    timeout_s: float = Field(default=120.0, gt=0.0)
    #: Retries of a *transient* failure (rate limit, timeout, unavailable) on this model,
    #: with exponential backoff and jitter, honouring a provider's Retry-After.
    retries: int = Field(default=2, ge=0, le=10)
    #: Registry names tried in order once this model's retries are exhausted on a transient
    #: failure. Never on a bad key or a malformed request — those are faults to fix, and
    #: another model would hide them. The Completion names the model that answered.
    fallbacks: tuple[str, ...] = ()
    #: Bounds on a call that asks for this row, across its retries, fallbacks and repairs.
    limits: CallLimits = CallLimits()
    options: dict[str, Any] = {}

    @field_validator("api_key")
    @classmethod
    def _secret_is_a_reference(cls, value: str | None) -> str | None:
        # A key written inline in a config file ends up in version control. Only a
        # reference to where the key lives is accepted.
        if value is not None and not value.startswith(_SECRET_SCHEMES):
            raise ValueError(
                "api_key must be a reference, not a key: use 'env:VAR_NAME' or 'file:~/path/to/key'"
            )
        return value

    @field_validator("fallbacks")
    @classmethod
    def _fallbacks_are_distinct(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # A repeated fallback would be asked again in full after it had just failed.
        repeated = sorted({name for name in value if value.count(name) > 1})
        if repeated:
            raise ValueError(f"names {', '.join(map(repr, repeated))} more than once")
        return value


class Request(BaseModel):
    """What the runtime hands a backend for one attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: tuple[Message, ...] = Field(min_length=1)
    json_schema: dict[str, Any] | None = None
    max_tokens: int = Field(gt=0)
    temperature: float | None = Field(default=None, ge=0.0)
    #: The tools the model may ask to call; none by default.
    tools: tuple[ToolSpec, ...] = ()


class Usage(BaseModel):
    """Tokens one call (or a sum of calls) cost. Cache fields are 0 where unreported.

    ``input_tokens`` is every input token, the ones read from and written to the provider's
    cache included, on every backend: the GenAI semantic conventions' meaning for
    ``gen_ai.usage.input_tokens``, whose cache counts "SHOULD be included" in it. The two cache
    fields say how many of ``input_tokens`` the cache served or took, and are never added to it
    again. A backend whose provider reports them apart (Anthropic's Messages API) adds them in.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Every input token, the cache's included.
    input_tokens: int = 0
    output_tokens: int = 0
    #: Of ``input_tokens``, those served from the provider's prompt cache.
    cache_read_tokens: int = 0
    #: Of ``input_tokens``, those written to the provider's prompt cache.
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class RawCompletion(BaseModel):
    """What a backend returns for one attempt. The port's declared output shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    model: str
    usage: Usage = Usage()
    request_id: str | None = None
    #: None when the provider did not say.
    finish_reason: FinishReason | None = None
    #: The provider's own value (``finish_reason``, ``stop_reason``), verbatim.
    native_finish_reason: str | None = None
    #: What the model said when it refused, when the provider marks a refusal; else None.
    refusal: str | None = None
    #: The calls the model asked for, in order; none when no tools were offered.
    tool_calls: tuple[ToolCall, ...] = ()
    raw: dict[str, Any] = {}


class RequestParams(BaseModel):
    """What was sent to the model that answered, after its row's defaults were applied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_tokens: int
    temperature: float | None
    #: How structured output was asked for; None for a plain completion.
    structured: StructuredMode | None
    #: The names of the tools offered; none for a call that offered none.
    tools: tuple[str, ...] = ()


class Attempt(BaseModel):
    """One backend call made for a logical call: a retry, a fallback or a schema attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The registry row asked.
    name: str
    #: Why the call failed; None for a call that answered.
    reason: Reason | None = None
    #: What the provider reported. A failed call reports none, so it costs nothing here.
    usage: Usage | None = None
    latency_ms: int


class RowRecord(BaseModel):
    """The registry row that answered, as it was configured.

    ``digest`` is that of the row's whole spec (``ModelSpec.model_dump(mode="json")``); see
    ``domain/digests.py``. ``rungs`` names, for each field a configuration set, the tier that
    set it: a file with how it was found (``cwd ./hardline.yaml``), a host's section
    (``matrix.models``), or the backend's default location. A field not named kept the model's
    default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    digest: str
    rungs: dict[str, str] = {}


class Completion(BaseModel):
    """What a caller gets back. Every field needed to ledger the call.

    ``name``, ``family``, ``model`` and ``backend`` describe the model that **answered**. When
    a fallback answered, ``fallback_from`` names the model that was asked for — the family
    may differ, and an out-of-family judgement must use the one that answered.

    A reply that is not a whole answer never looks like one. ``finish_reason`` is ``stop``
    only when the provider said the model ended its turn; a reply cut at ``max_tokens`` is
    ``length``, a refusal is ``refusal`` with ``refusal`` set, a withheld one
    ``content_filter``. ``answered`` is true only for a whole answer, and is what to check
    before taking ``text`` as one. A reply that asks for tools (``tool_call``, with
    ``tool_calls``) is a whole answer when the call offered tools, and only then.

    **Who upgrades first.** A Completion's JSON validates back into a Completion, and refuses a
    key it does not know (``extra="forbid"``). A minor release of hardline may add keys, each with
    a default; none is removed or renamed while ``type_url`` is ``hardline.v1/completion``. So a
    reader of stored Completion JSON upgrades first: it installs the newer hardline before any
    writer does, or it reads the JSON as a mapping rather than through this model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type_url: str = COMPLETION_TYPE_URL
    name: str
    text: str
    family: str
    model: str
    backend: str
    local: bool
    #: Summed over every backend call that reported usage: each of ``attempt_log``. A failed
    #: call reports none (the provider sends usage with an answer), so it adds nothing.
    usage: Usage
    request_id: str | None
    latency_ms: int
    #: Schema attempts (1 for a plain completion; up to 1 + schema_retries for extract).
    attempts: int
    #: Transient-failure retries spent across all attempts and models.
    retries: int = 0
    fallback_from: str | None = None
    #: Why the answering reply ended; see the class docstring. None when the provider did not say.
    finish_reason: FinishReason | None = None
    #: The provider's own value, verbatim.
    native_finish_reason: str | None = None
    #: What the model said when it refused; None when it did not refuse.
    refusal: str | None = None
    #: The calls the model asked for, in order: the caller runs them. None asked for, none here.
    tool_calls: tuple[ToolCall, ...] = ()
    #: What was sent to the model that answered.
    params: RequestParams | None = None
    #: Every backend call made, in order, with why each failed.
    attempt_log: tuple[Attempt, ...] = ()
    #: The digest of the turns the answering call was sent: for ``extract``, the schema
    #: instruction and any repair turns included. See ``domain/digests.py``.
    turns_digest: str | None = None
    #: The registry row that answered, its digest and the tier that set each field.
    row: RowRecord | None = None
    raw: dict[str, Any] = {}

    @property
    def answered(self) -> bool:
        """A whole answer: the model ended its turn, did not refuse, and said something; or,
        when the call offered tools, it asked for one or more of them.

        False for a reply cut at ``max_tokens``, refused, withheld, empty, one whose provider
        did not say why it ended, and one that ended on a tool call no tool was offered for.
        """
        if self.refusal is not None:
            return False
        offered = self.params is not None and bool(self.params.tools)
        asked = offered and bool(self.tool_calls)
        if self.finish_reason == "stop":
            return bool(self.text) or asked
        return self.finish_reason == "tool_call" and asked


class Problem(BaseModel):
    """One thing wrong with a configuration, or worth a warning about it.

    The problem record the capability CLIs share (``components/capabilities/error-envelope.json``
    in the workspace): ``path`` is the offending key, dotted from the document root
    (``models.a.fallbacks``); ``kind`` is a kind the tools share (``unknown_key``,
    ``invalid_value``, ...) or one of hardline's own (``shared_fate``); ``source`` is the tier
    the key came from, when known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    kind: str
    message: str
    fix: str | None = None
    allowed: tuple[str, ...] = ()
    got: str | int | float | bool | None = None
    source: str | None = None


class AttemptFailed(BaseModel):
    """A backend call of a logical call failed. ``retry_in_s`` is the wait before the same row
    is asked again; None when it will not be: the call falls back, or fails."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The row the logical call asked for.
    requested: str
    attempt: Attempt
    retry_in_s: float | None = None


class FellBack(BaseModel):
    """Row ``name`` spent its retries on a transient failure, and the call asks ``to`` next."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requested: str
    name: str
    to: str
    reason: Reason


class Answered(BaseModel):
    """A logical call answered: ``completion`` is what its caller gets, record and all."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requested: str
    completion: Completion


#: What a ``CallObserver`` is told about a logical call. An observer ignores a kind it does not
#: know, so a new kind of event breaks none.
CallEvent = AttemptFailed | FellBack | Answered


@dataclass(frozen=True)
class Structured[T: BaseModel]:
    """A validated object plus the completion that produced it."""

    value: T
    completion: Completion
