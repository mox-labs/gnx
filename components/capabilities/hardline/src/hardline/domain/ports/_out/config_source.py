"""ConfigSource — one tier of configuration, as a raw mapping.

``describe()`` exists so a validation error can name the file the bad value came from.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ConfigSource(Protocol):
    def read(self) -> dict[str, Any]: ...

    def describe(self) -> str: ...
