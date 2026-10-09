"""ConstructStore — keeps finished runs, and gives back their Constructs.

Adapters: in memory (tests, one process), and a directory of typed JSONL tables (one file per
topic, plus ``run.json`` with the run's status and each table's type), readable with ``jq``
or SQL without matrix installed.

Contract: ``save`` never rewrites an earlier run; ``load`` returns the Construct exactly as
saved (values come back as JSON data, not as the Python objects that produced them);
``runs`` lists saved run ids, oldest first. A missing run is a NotFoundError naming the
runs that exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matrix.domain.construct import Construct
    from matrix.domain.executor import Run


@runtime_checkable
class ConstructStore(Protocol):
    def save(self, run: Run) -> str:
        """Persist ``run``; return where it went (a path, or the run id)."""
        ...

    def load(self, run_id: str) -> Construct: ...

    def describe(self, run_id: str) -> dict[str, Any]:
        """The run's record: flow, status, times, failures, and each table's type and size."""
        ...

    def runs(self) -> list[str]: ...
