"""OpenAICompatBackend — the OpenAI chat-completions wire protocol.

One adapter for every server that speaks it: mlx_lm.server, ollama, vLLM, llama.cpp,
Gemini's compatibility endpoint, OpenAI itself. A model is a registry row naming a
``base_url`` and a ``model``; nothing here knows which server is on the other end.

Row options this adapter reads::

    options:
      token_param: max_tokens | max_completion_tokens   # default max_tokens (local servers)

Retries are the runtime's (``retries`` on the row), not the SDK's: the client is built with
``max_retries=0``, so a transient failure is retried by one policy rather than compounded by two.
A client is pooled within one event loop and never shared across loops (``_clients``);
``aclose()`` closes the running loop's.

No ambient credentials: when a row names no ``api_key`` the client is given a placeholder
rather than being left to read ``OPENAI_API_KEY`` from the environment behind the
config's back. Local servers ignore it; a hosted endpoint rejects it loudly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from hardline.adapters._out._classify import classify
from hardline.adapters._out._clients import LoopClients
from hardline.adapters._out._options import option_problems, read_options
from hardline.adapters._out._scrub import scrub
from hardline.domain.errors import BackendError
from hardline.domain.types import ModelSpec, RawCompletion, Request, Usage

if TYPE_CHECKING:
    from openai import AsyncOpenAI

_PLACEHOLDER_KEY = "hardline-no-key"


class _Options(BaseModel):
    """The ``options`` this backend reads; any other key is refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"


class OpenAICompatBackend:
    structured_modes = frozenset({"prompt", "json_object", "json_schema"})

    def check(self, spec: ModelSpec) -> list[str]:
        return option_problems(_Options, spec)

    def __init__(self) -> None:
        self._clients: LoopClients[AsyncOpenAI] = LoopClients(lambda client: client.close())

    def _client(self, spec: ModelSpec, api_key: str | None) -> AsyncOpenAI:
        from openai import AsyncOpenAI

        return self._clients.get(
            (spec.base_url, api_key or _PLACEHOLDER_KEY, spec.timeout_s),
            lambda: AsyncOpenAI(
                api_key=api_key or _PLACEHOLDER_KEY,
                base_url=spec.base_url,
                timeout=spec.timeout_s,
                max_retries=0,
            ),
        )

    async def aclose(self) -> None:
        await self._clients.aclose()

    async def complete(
        self, spec: ModelSpec, request: Request, api_key: str | None
    ) -> RawCompletion:
        import openai

        token_param = read_options(_Options, spec).token_param
        kwargs: dict[str, Any] = {
            "model": spec.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            token_param: request.max_tokens,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.json_schema is not None and spec.structured == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        elif request.json_schema is not None and spec.structured == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": request.json_schema},
            }

        try:
            response = await self._client(spec, api_key).chat.completions.create(**kwargs)
        except openai.OpenAIError as e:
            where = spec.base_url or "api.openai.com"
            reason, retry_after = classify(e)
            raise BackendError(
                f"model {spec.name!r} ({spec.backend} → {where}): "
                f"{type(e).__name__}: {scrub(str(e), api_key)}",
                reason=reason,
                retry_after=retry_after,
            ) from None

        text = ""
        if response.choices:
            text = response.choices[0].message.content or ""
        usage = response.usage
        # Compatible servers (mlx_lm, ollama) often omit the details block entirely.
        details = getattr(usage, "prompt_tokens_details", None)
        return RawCompletion(
            text=text,
            model=response.model or spec.model,
            usage=Usage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                cache_read_tokens=getattr(details, "cached_tokens", None) or 0,
            ),
            request_id=response.id,
            raw=response.model_dump(mode="json", exclude={"choices"}),
        )
