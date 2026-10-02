"""AnthropicBackend — the Anthropic Messages API, natively.

System turns are lifted into the ``system`` parameter, as the API requires. Structured
output is ``prompt`` mode only here: the schema is stated in an instruction and the
runtime validates the reply. A registry row asking this backend for ``json_schema`` is
rejected at composition, not silently downgraded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hardline.adapters._out._scrub import scrub
from hardline.domain.errors import BackendError
from hardline.domain.types import ModelSpec, RawCompletion, Request, Usage

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


class AnthropicBackend:
    structured_modes = frozenset({"prompt"})

    def __init__(self) -> None:
        self._clients: dict[tuple[str | None, str | None, float], AsyncAnthropic] = {}

    def _client(self, spec: ModelSpec, api_key: str | None) -> AsyncAnthropic:
        from anthropic import AsyncAnthropic

        if not api_key:
            # Refuse rather than let the SDK fall back to ANTHROPIC_API_KEY: the registry
            # row is the only place a credential is allowed to come from.
            raise BackendError(
                f"model {spec.name!r} (anthropic): no api_key reference in its registry row. "
                "Add api_key: env:ANTHROPIC_API_KEY or api_key: file:~/.secrets/claude-api"
            )
        key = (spec.base_url, api_key, spec.timeout_s)
        client = self._clients.get(key)
        if client is None:
            client = AsyncAnthropic(api_key=api_key, base_url=spec.base_url, timeout=spec.timeout_s)
            self._clients[key] = client
        return client

    async def complete(
        self, spec: ModelSpec, request: Request, api_key: str | None
    ) -> RawCompletion:
        import anthropic

        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        turns = [
            {"role": m.role, "content": m.content} for m in request.messages if m.role != "system"
        ]
        kwargs: dict[str, Any] = {
            "model": spec.model,
            "max_tokens": request.max_tokens,
            "messages": turns,
        }
        if system:
            kwargs["system"] = system
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        client = self._client(spec, api_key)
        try:
            response = await client.messages.create(**kwargs)
        except anthropic.AnthropicError as e:
            raise BackendError(
                f"model {spec.name!r} (anthropic): {type(e).__name__}: {scrub(str(e), api_key)}"
            ) from None

        text = "".join(block.text for block in response.content if block.type == "text")
        return RawCompletion(
            text=text,
            model=response.model,
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            request_id=response.id,
            raw=response.model_dump(mode="json", exclude={"content"}),
        )
