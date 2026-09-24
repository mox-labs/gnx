"""DefinitionSource — where agent definitions come from (a directory, a plugin, a mapping)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matrix.domain.agent import AgentDefinition


@runtime_checkable
class DefinitionSource(Protocol):
    def load(self) -> list[AgentDefinition]: ...

    def describe(self) -> str: ...
