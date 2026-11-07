"""The credential-reference grammar every source kind uses (ruling A1).

A credential is never written in a mission: the mission names where it is. ``env:NAME`` is
the environment variable ``NAME``, which the mission thereby declares for the collector types
whose runs use the source; ``file:PATH`` is a file, a relative path being relative to the
mission's directory, which its group and others must not be able to read. A source may
instead name a ``headers_helper``, a command whose standard output is a JSON object of the
headers to send, run once a survey. An ``api`` or ``sse`` source says so in ``auth`` (``token``,
``headers_helper``), an ``mcp`` source in its own keys (``token``, ``headers_helper``, and a
stdio server's ``env`` values); one adapter resolves them all (``recon.adapters._out.
credentials``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, Literal, Protocol

if TYPE_CHECKING:
    from recon.core.domain.errors import CollectionError
    from recon.core.domain.models import SourceEntry

#: Where a reference says the credential is.
Scheme = Literal["env", "file"]

_REFERENCE: Final = re.compile(r"^(env|file):(.+)$")


def reference(written: str) -> tuple[Scheme, str] | None:
    """``(scheme, name)`` of a reference (``env:NAME``, ``file:PATH``); None for a value that
    is not one."""
    found = _REFERENCE.match(written)
    if found is None:
        return None
    scheme: Scheme = "env" if found.group(1) == "env" else "file"
    return scheme, found.group(2)


def env_name(written: str) -> str | None:
    """The variable an ``env:NAME`` reference names; None for anything else."""
    found = reference(written)
    return found[1] if found is not None and found[0] == "env" else None


#: What a token that is not a reference is told: the fix every kind's check gives.
NOT_A_REFERENCE = "a token is a reference, never the credential itself"
REFERENCE_FIX = "token: env:NAME (a variable recon reads) or token: file:PATH"


class Credentials(Protocol):
    """A source's credential, resolved the one way every source kind's is: the ``credentials``
    need a collector type is built with (``recon.core.domain.collector.NEEDS``). Each is
    resolved on first use and kept for the act, so a file is read and a helper run once."""

    def token(self, source: SourceEntry) -> str | None:
        """The secret the source's ``auth.token`` names, with its ``auth.prefix``; None when it
        names none or its variable is unset."""
        ...

    def helped(self, source: SourceEntry) -> dict[str, str]:
        """The headers the source's ``auth.headers_helper`` prints; none without one."""
        ...

    def resolve(self, written: str, where: str) -> str | None:
        """The value an ``env:NAME`` or ``file:PATH`` reference names (a relative ``file:``
        being the mission's), None for an unset variable; ``where`` names the key in an
        error."""
        ...

    def helper(self, command: str, source: SourceEntry) -> dict[str, str]:
        """The headers a helper command prints, run within the source's ``timeout`` and the
        act's deadline: a kind's own ``headers_helper`` key."""
        ...

    def secrets(self, source: SourceEntry) -> list[str]:
        """What of the source's credentials was resolved this act, to keep out of what is
        captured and of an error's text."""
        ...

    def redacted(self, source: SourceEntry, error: CollectionError) -> CollectionError:
        """``error``, its message with the source's credentials redacted."""
        ...
