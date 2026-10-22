"""MockBackend — deterministic, offline, and a faithful implementation of the port.

Row options (all optional)::

    options:
      responses: {"<user message>": "<text>"}   # keyed on the last user turn, else the first
      script: ["first attempt", {error: rate_limit}, "third"]   # in order; last repeats
      # a script entry may ask for tools: {tool_calls: [{name: lookup, arguments: '{"w": 1}'}]}
      default: "fallback text"
      finish_reason: length          # why every reply ends; default stop
      refusal: "I can't help."        # every reply is a refusal saying this

With nothing configured it echoes ``mock:<model>:<first user message>``, which is enough
to prove wiring. Each call's ``request_id`` is ``mock-<row>-<n>``, ``n`` counting the row's
calls from 1. ``script`` exists to plant failures: a first attempt that is not JSON and a
second that is proves the runtime's repair path; an ``{error: <reason>}`` entry raises a
BackendError with that reason, which proves retries and fallbacks — all without a network.
A ``{tool_calls: [...]}`` entry is a reply that asks for those tools (``finish_reason:
tool_call``, no text), each call's id ``mock-<row>-<n>-<i>`` unless the entry gives one: with
it a caller's tool loop runs offline. The options are checked when the runtime is composed,
like any backend's.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from hardline.adapters._out._options import option_problems, read_options
from hardline.domain.errors import BackendError, Reason
from hardline.domain.types import (
    FinishReason,
    ModelSpec,
    RawCompletion,
    Request,
    ToolCall,
    Usage,
)


class _ScriptedError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    error: Reason


class _ScriptedCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    arguments: str = "{}"
    id: str | None = None


class _ScriptedToolCalls(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_calls: list[_ScriptedCall]


class _Options(BaseModel):
    """The ``options`` this backend reads; any other key is refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    responses: dict[str, str] = {}
    script: list[str | _ScriptedError | _ScriptedToolCalls] = []
    default: str | None = None
    finish_reason: FinishReason = "stop"
    refusal: str | None = None


class MockBackend:
    structured_modes = frozenset({"prompt", "json_object", "json_schema"})
    supports_tools = True

    def check(self, spec: ModelSpec) -> list[str]:
        return option_problems(_Options, spec)

    def __init__(self) -> None:
        self._cursor: dict[str, int] = {}

    async def complete(
        self, spec: ModelSpec, request: Request, api_key: str | None
    ) -> RawCompletion:
        call = self._cursor.get(spec.name, 0) + 1  # this call's ordinal, before _pick counts it
        last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        picked = self._pick(spec, request, last_user)
        opts = read_options(_Options, spec)
        usage = Usage(input_tokens=sum(len(m.content.split()) for m in request.messages))
        if isinstance(picked, _ScriptedToolCalls):
            calls = tuple(
                ToolCall(
                    id=asked.id or f"mock-{spec.name}-{call}-{i}",
                    name=asked.name,
                    arguments=asked.arguments,
                )
                for i, asked in enumerate(picked.tool_calls)
            )
            return RawCompletion(
                text="",
                model=spec.model,
                finish_reason="tool_call",
                native_finish_reason="tool_call",
                tool_calls=calls,
                usage=usage.model_copy(update={"output_tokens": len(calls)}),
                request_id=f"mock-{spec.name}-{call}",
            )
        return RawCompletion(
            text=picked,
            model=spec.model,
            finish_reason="refusal" if opts.refusal is not None else opts.finish_reason,
            native_finish_reason=opts.finish_reason,
            refusal=opts.refusal,
            usage=usage.model_copy(update={"output_tokens": len(picked.split())}),
            request_id=f"mock-{spec.name}-{call}",
        )

    def _pick(self, spec: ModelSpec, request: Request, last_user: str) -> str | _ScriptedToolCalls:
        opts = read_options(_Options, spec)
        if opts.script:
            i = self._cursor.get(spec.name, 0)
            self._cursor[spec.name] = i + 1
            entry = opts.script[min(i, len(opts.script) - 1)]
            if isinstance(entry, _ScriptedError):
                raise BackendError(
                    f"model {spec.name!r} (mock): scripted {entry.error}", reason=entry.error
                )
            return entry
        self._cursor[spec.name] = self._cursor.get(spec.name, 0) + 1
        # Keyed on the first user turn too, so a structured call, which appends a schema
        # instruction after it, still finds its canned reply.
        first_user = next((m.content for m in request.messages if m.role == "user"), "")
        for key in (last_user, first_user):
            if key in opts.responses:
                return opts.responses[key]
        if opts.default is not None:
            return opts.default
        return f"mock:{spec.model}:{first_user}"
