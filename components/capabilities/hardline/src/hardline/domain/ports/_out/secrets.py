"""SecretResolver — turns a reference (``env:NAME`` / ``file:PATH``) into a value.

A port rather than a function in the domain because resolving is I/O, and because a
deployment may want a different store (a keychain, a vault) without touching the runtime.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretResolver(Protocol):
    def resolve(self, reference: str) -> str: ...
