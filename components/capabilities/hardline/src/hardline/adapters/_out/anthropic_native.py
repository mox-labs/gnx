"""AnthropicBackend — the Anthropic Messages API, natively, over HTTP.

The request is a POST to ``<base_url>/v1/messages`` with the row's key in ``x-api-key``. A row
with no ``base_url`` is given ``default_base_url`` by the composition root, so the row records
where it sends. A row with no ``api_key`` is refused when the runtime is composed: the registry
row is the only place a credential may come from, and nothing reads ``ANTHROPIC_API_KEY`` or
``ANTHROPIC_BASE_URL``.

System turns are lifted into the ``system`` parameter, as the API requires. Structured output
is ``prompt`` mode only here: the schema is stated in an instruction and the runtime validates
the reply. A registry row asking this backend for ``json_schema`` is rejected at composition,
not silently downgraded. HTTP, timeouts and connection lifetimes are ``_http``'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

from hardline.adapters._out._http import Http, location_problems
from hardline.adapters._out._options import option_problems, read_options
from hardline.domain.errors import BackendError, ConfigError
from hardline.domain.types import ModelSpec, RawCompletion, Request, Usage

if TYPE_CHECKING:
    import httpx2

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


class _Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


class _Reply(BaseModel):
    id: str | None = None
    model: str | None = None
    content: list[_Block] = []
    usage: _Usage | None = None


class AnthropicBackend:
    structured_modes = frozenset({"prompt"})
    default_base_url = "https://api.anthropic.com"

    def __init__(self, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._http = Http(transport)

    def check(self, spec: ModelSpec) -> list[str]:
        return (
            location_problems(spec)
            + option_problems(_Options, spec)
            + ([] if spec.api_key else [_NO_KEY])
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(
        self, spec: ModelSpec, request: Request, api_key: str | None
    ) -> RawCompletion:
        read_options(_Options, spec)
        if not api_key:
            raise ConfigError(f"models.{spec.name}.{_NO_KEY}")
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        body: dict[str, Any] = {
            "model": spec.model,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in request.messages
                if m.role != "system"
            ],
        }
        if system:
            body["system"] = system
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
        return RawCompletion(
            text="".join(block.text or "" for block in reply.content if block.type == "text"),
            model=reply.model or spec.model,
            usage=Usage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ),
            request_id=reply.id,
            raw={key: value for key, value in data.items() if key != "content"},
        )
