"""Ports — the contracts the dao domain depends on, and nothing more.

Hexagonal discipline: the domain declares what it needs as a Protocol; adapters satisfy
it from outside. Structural typing (`@runtime_checkable Protocol`) rather than base
classes, so an adapter never imports the domain to be compatible with it — the same
choice matrix made for its `Component` protocol.

Three ports, one per direction of dependency the domain actually has:

- `CorpusReader`  — where grounded domains come from (dao-corpus today; a registry,
                    a database, or a hand-written list tomorrow).
- `ProjectionTarget` — where an organization gets written, and read back for audit.
                    Filed as a port because `dao check` must work against a target it
                    did not create, and against one that is not a local filesystem.
- `IndependentGate` — the out-of-family check M1-H2 narrowed component #7 down to.
                    A port precisely because the whole finding is that the gate must
                    come from a *different distribution* than the maker: an
                    implementation that calls the same model family cannot satisfy the
                    contract, and typing it as a port makes that substitution visible
                    instead of silent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from dao.domain.models import CorpusDomain, GateVerdict, Seat


@runtime_checkable
class CorpusReader(Protocol):
    """Reads grounded domains that a bench can be projected from."""

    def domains(self) -> list[CorpusDomain]:
        """Every domain the corpus holds. Empty when the corpus is absent."""
        ...

    def for_project(self, project: str) -> list[CorpusDomain]:
        """Domains whose `informs` edge names this project."""
        ...


@runtime_checkable
class ProjectionTarget(Protocol):
    """A project an organization is written into, and audited from."""

    @property
    def root(self) -> Path:
        """The project root the eleven components are relative to."""
        ...

    def read_text(self, relative: str) -> str:
        """Contents of a path relative to the root; empty string when absent."""
        ...

    def exists(self, relative: str) -> bool: ...

    def list_bench(self) -> list[Seat]:
        """Seats currently seated in this target."""
        ...


@runtime_checkable
class IndependentGate(Protocol):
    """An externally-anchored check on work the maker produced.

    `is_out_of_family` is part of the contract, not metadata — but as a RECORD, not a
    ranking. M1-H2 measured a two-pass cold re-read as sufficient on everything it tested and
    refuted the broad justification for a separate gate; it did not measure out-of-family as
    superior. That comparison is run-03 in `verifier-error-correlation/DESIGN.md`, unrun, and
    its refutation condition explicitly permits "rung 2 buys nothing".

    The flag travels so a stored verdict cannot lose its provenance, and so the eventual
    measurement has something to correlate against. Nothing here should be read as scoring
    one kind of gate above the other.
    """

    @property
    def is_out_of_family(self) -> bool:
        """True only when this gate runs on a different model distribution."""
        ...

    def review(self, subject: str, criteria: list[str]) -> GateVerdict: ...
