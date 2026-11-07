"""The optional members any built extension may keep, whatever its point, each declared.

A port says what every implementation must do. What only some do is an optional member, and
each is a protocol of its own, checked with ``isinstance`` where it is read, so a type checker
sees it and nothing is found by its name. These three are read on runtimes of either kind;
an optional member of one port alone is declared in that port's module.

**The rule.** A member may be optional only when its absence has a stated meaning that
changes nothing another part computes: no warnings shown, a fingerprint of the config alone,
nothing to close. A fact whose absence would change a result is a required member, a
registration field or a port of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matrix.domain.problems import Problem


@runtime_checkable
class SupportsWarnings(Protocol):
    """What a reader of the extension's config should see but that is not an error (a
    permissive permission mode), which composition carries into every document the
    ``matrix`` command prints. Without it: none."""

    @property
    def warnings(self) -> tuple[Problem, ...]: ...


@runtime_checkable
class SupportsResolved(Protocol):
    """JSON data for what the extension settled when it was built, beyond its own config,
    that decides how it answers (a model registry it read), which composition digests into
    the reuse fingerprint of every component that uses it. Without it: its config alone."""

    @property
    def resolved(self) -> dict[str, Any]: ...


@runtime_checkable
class SupportsAclose(Protocol):
    """Closes the connections the extension's calls opened in the running event loop. A
    connection can be closed only in its own loop, so whoever made the calls awaits it there,
    once they are done. The extension stays usable after it. Without it: nothing to close."""

    async def aclose(self) -> None: ...
