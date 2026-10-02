"""MockBackend — deterministic, offline, and a faithful implementation of the port.

Row options (all optional)::

    options:
      responses: {"<last user message>": "<text>"}   # keyed replies
      script: ["first attempt", {error: rate_limit}, "third"]   # in order; last repeats
      default: "fallback text"

With nothing configured it echoes ``mock:<model>:<last user message>``, which is enough
to prove wiring. ``script`` exists to plant failures: a first attempt that is not JSON
and a second that is proves the runtime's repair path; an ``{error: <reason>}`` entry
raises a BackendError with that reason, which proves retries and fallbacks — all without a
network.
"""

from __future__ import annotations

from typing import Any, get_args

from hardline.domain.errors import BackendError, ConfigError, Reason
from hardline.domain.types import ModelSpec, RawCompletion, Request, Usage

_REASONS = frozenset(get_args(Reason))


class MockBackend:
    structured_modes = frozenset({"prompt", "json_object", "json_schema"})

    def __init__(self) -> None:
        self._cursor: dict[str, int] = {}

    async def complete(
        self, spec: ModelSpec, request: Request, api_key: str | None
    ) -> RawCompletion:
        last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        text = self._pick(spec, request, last_user)
        return RawCompletion(
            text=text,
            model=spec.model,
            usage=Usage(
                input_tokens=sum(len(m.content.split()) for m in request.messages),
                output_tokens=len(text.split()),
            ),
            request_id=f"mock-{spec.name}-{self._cursor.get(spec.name, 0)}",
        )

    def _pick(self, spec: ModelSpec, request: Request, last_user: str) -> str:
        opts = spec.options
        script = opts.get("script")
        if isinstance(script, list) and script:
            i = self._cursor.get(spec.name, 0)
            self._cursor[spec.name] = i + 1
            return _scripted(spec, script[min(i, len(script) - 1)])
        self._cursor[spec.name] = self._cursor.get(spec.name, 0) + 1
        responses = opts.get("responses")
        # Keyed on the first user turn so a structured call, which appends a schema
        # instruction after it, still finds its canned reply.
        first_user = next((m.content for m in request.messages if m.role == "user"), "")
        if isinstance(responses, dict):
            for key in (last_user, first_user):
                if key in responses:
                    return str(responses[key])
        if "default" in opts:
            return str(opts["default"])
        return f"mock:{spec.model}:{first_user}"


def _scripted(spec: ModelSpec, entry: Any) -> str:
    if isinstance(entry, dict) and "error" in entry:
        reason = entry["error"]
        if reason not in _REASONS:
            raise ConfigError(
                f"models.{spec.name}.options.script: error {reason!r}. "
                f"Legal: {', '.join(sorted(_REASONS))}"
            )
        raise BackendError(f"model {spec.name!r} (mock): scripted {reason}", reason=reason)
    return str(entry)
