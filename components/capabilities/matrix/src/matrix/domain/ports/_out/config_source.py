"""ConfigSource — reads raw config from a single tier.

Port contract: return a dict (empty when the source is absent), no validation, no schema
awareness. ``describe()`` names the source so a validation error can point at the file the
bad value came from. Tiers compose by deep merge before validation.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ConfigSource(Protocol):
    def read(self) -> dict[str, Any]:
        """Read config from this source. Empty dict if absent."""
        ...

    def describe(self) -> str:
        """Where this tier reads from, for error messages."""
        ...
