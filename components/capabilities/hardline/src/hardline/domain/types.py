"""hardline types — the port's vocabulary.

Messages and an optional output schema go in; text (or a validated object) comes out,
tagged with the family that answered, what it cost, and enough provenance to ledger.
Stateless: the conversation belongs to the caller.

Type URLs follow the matrix convention ``<namespace>.v<version>/<resource>`` (xDS
TypedExtensionConfig), so a composition root can key on them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NAMESPACE = "hardline.v1"
COMPLETION_TYPE_URL = f"{NAMESPACE}/completion"


def backend_type_url(name: str) -> str:
    """Registry key for a backend short name, e.g. ``openai-compat``."""
    return f"{NAMESPACE}/backend.{name}"


Role = Literal["system", "user", "assistant"]

#: How structured output is requested from the provider. Validation happens in the
#: runtime regardless — these only decide how hard the provider is asked.
#:   prompt       the schema is stated in an instruction; works with every backend
#:   json_object  the provider is asked for JSON (OpenAI ``response_format`` json_object)
#:   json_schema  the provider is handed the schema (OpenAI ``response_format`` json_schema)
StructuredMode = Literal["prompt", "json_object", "json_schema"]

_SLUG = r"^[a-z0-9][a-z0-9._-]*$"
_SECRET_SCHEMES = ("env:", "file:")


class Message(BaseModel):
    """One turn of a conversation the caller owns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str


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
    api_key: str | None = None
    local: bool = False
    structured: StructuredMode = "prompt"
    max_tokens: int = Field(default=1024, gt=0)
    temperature: float | None = Field(default=None, ge=0.0)
    timeout_s: float = Field(default=120.0, gt=0.0)
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


class Request(BaseModel):
    """What the runtime hands a backend for one attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    messages: tuple[Message, ...] = Field(min_length=1)
    json_schema: dict[str, Any] | None = None
    max_tokens: int = Field(gt=0)
    temperature: float | None = None


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class RawCompletion(BaseModel):
    """What a backend returns for one attempt. The port's declared output shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    model: str
    usage: Usage = Usage()
    request_id: str | None = None
    raw: dict[str, Any] = {}


class Completion(BaseModel):
    """What a caller gets back. Every field needed to ledger the call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type_url: str = COMPLETION_TYPE_URL
    name: str
    text: str
    family: str
    model: str
    backend: str
    local: bool
    usage: Usage
    request_id: str | None
    latency_ms: int
    attempts: int
    raw: dict[str, Any] = {}


@dataclass(frozen=True)
class Structured[T: BaseModel]:
    """A validated object plus the completion that produced it."""

    value: T
    completion: Completion
