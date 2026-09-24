"""ModelBackend — the one port a provider adapter implements.

Two members. ``structured_modes`` lets composition reject a registry row that asks a
backend for a structured mode it cannot honour, before any call is made. ``complete``
runs one attempt; retries, schema validation and provenance belong to the runtime.

Structural typing: an adapter satisfies this without importing modelrt's classes, the
same way a matrix Component does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from modelrt.domain.types import ModelSpec, RawCompletion, Request


@runtime_checkable
class ModelBackend(Protocol):
    @property
    def structured_modes(self) -> frozenset[str]: ...

    async def complete(
        self, spec: ModelSpec, request: Request, api_key: str | None
    ) -> RawCompletion: ...
