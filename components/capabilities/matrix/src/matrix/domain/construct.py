"""The Construct: one run's record. A table per kind, and a ledger of what landed when.

A **kind** is the type URL of some data, and it is the schema of its table: the rows of
``acme.v1.review`` are reviews, the records themselves, with nothing wrapped around them.
Provenance is one more table, the **ledger** (``matrix.v1.ledger``): every row in every other
table has exactly one :class:`LedgerEntry`.

**The write order** is ledger entry first, then the row; both are written through to the
run's store before :meth:`Construct.append` returns. So a process killed between the two
leaves an entry pointing one past the end of its table, never a row that nothing accounts
for. A reader detects that: :meth:`Construct.restore` keeps every entry whose row exists and
reports the others as :attr:`Construct.torn`. The two writes are consecutive and nothing is
written after one fails, so a torn tail is the ledger's last entry and nothing else.

Rows are append-only and the executor is their only writer. Components never receive the
Construct; they get :class:`~matrix.domain.component.Inputs` holding the kinds they require.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from matrix.domain.component import LEDGER
from matrix.domain.errors import NotFoundError, StoreError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

#: The component recorded for a row supplied to the run from outside (a run input).
INPUT = "$input"


class LedgerEntry(BaseModel):
    """One row's provenance: a row of the ``matrix.v1.ledger`` table. Immutable.

    It points at its row by ``(kind, row)``: the table, and the row's 0-based position in
    it. Each kind has one provider and its table is append-only, so a position never changes
    and nothing is added to a record to make it addressable."""

    model_config = ConfigDict(frozen=True)

    #: The row's place in the run's order, from 0. Order across kinds is arrival order.
    seq: int
    #: The table the row is in.
    kind: str
    #: The row's 0-based position in that table.
    row: int
    #: The name of the component that handed it over, or ``$input`` for a run input.
    component: str
    time: datetime


class Construct:
    """Append-only typed tables, one per kind, plus the ledger of every row in order."""

    def __init__(self, kinds: Iterable[str] = ()) -> None:
        self._rows: dict[str, list[Any]] = {kind: [] for kind in kinds}
        self._ledger: list[LedgerEntry] = []
        self._sink: Callable[[str, Any], None] | None = None
        #: What the sink raised, once it has: every later append is refused with it.
        self._broken: BaseException | None = None
        #: Ledger entries whose row never landed: a run killed between the two writes.
        #: Always empty for a run in this process; set by :meth:`restore`.
        self.torn: tuple[LedgerEntry, ...] = ()

    def write_through(self, sink: Callable[[str, Any], None]) -> None:
        """Send every row appended from now on to ``sink(kind, row)`` (a store's writer).

        If the sink raises, that row is not recorded, the error propagates, and every later
        append raises StoreError: a record that failed once is not appended to again, and a
        row it cannot keep is not kept in memory either.
        """
        self._sink = sink

    def append(self, kind: str, row: Any, *, component: str) -> LedgerEntry:
        """Add ``row`` to ``kind``'s table: ledger entry first, then the row.

        Only the executor calls this. Returns the entry, once both are written through.
        """
        if kind == LEDGER:
            raise ValueError(f"{LEDGER} is written by the Construct itself")
        if self._broken is not None:
            raise StoreError(
                f"the record failed earlier and keeps no more rows: "
                f"{type(self._broken).__name__}: {self._broken}",
                table=kind,
            )
        table = self._rows.setdefault(kind, [])
        entry = LedgerEntry(
            seq=len(self._ledger),
            kind=kind,
            row=len(table),
            component=component,
            time=datetime.now(UTC),
        )
        if self._sink is not None:
            try:
                self._sink(LEDGER, entry)
                self._sink(kind, row)
            except BaseException as e:
                self._broken = e
                raise
        self._ledger.append(entry)
        table.append(row)
        return entry

    @classmethod
    def restore(cls, tables: Mapping[str, Sequence[Any]]) -> Construct:
        """Rebuild a Construct from stored tables, the ledger's among them.

        Entries whose row is missing are left out and reported in :attr:`torn`. A row with
        no entry, or entries out of order, cannot come from the write order above: that is a
        damaged record, and a StoreError.
        """
        construct = cls(kind for kind in tables if kind != LEDGER)
        torn: list[LedgerEntry] = []
        for raw in tables.get(LEDGER, ()):
            entry = raw if isinstance(raw, LedgerEntry) else LedgerEntry.model_validate(raw)
            stored = tables.get(entry.kind, ())
            table = construct._rows.setdefault(entry.kind, [])
            if entry.row >= len(stored):
                torn.append(entry)
                continue
            if entry.row != len(table):
                raise StoreError(
                    f"the ledger is out of order at seq {entry.seq}: it points at row "
                    f"{entry.row} of {entry.kind!r} where row {len(table)} comes next",
                    table=entry.kind,
                )
            construct._ledger.append(entry)
            table.append(stored[entry.row])
        for kind, stored in tables.items():
            if kind != LEDGER and len(stored) > len(construct._rows[kind]):
                raise StoreError(
                    f"table {kind!r} holds {len(stored)} rows but the ledger accounts for "
                    f"{len(construct._rows[kind])}",
                    table=kind,
                )
        construct.torn = tuple(torn)
        return construct

    def rows(self, kind: str) -> tuple[Any, ...]:
        """Every row of ``kind``, in the order it landed. Empty if none.

        ``rows("matrix.v1.ledger")`` is the ledger: provenance is a table like the others.
        """
        if kind == LEDGER:
            return tuple(self._ledger)
        return tuple(self._rows.get(kind, ()))

    def last(self, kind: str) -> Any:
        """The most recent row of ``kind``. Raises NotFoundError listing the kinds with rows."""
        rows = self._rows.get(kind)
        if not rows:
            available = ", ".join(sorted(k for k, v in self._rows.items() if v)) or "(none)"
            raise NotFoundError(
                f"no row of kind {kind!r}. Kinds with rows: {available}", table=kind
            )
        return rows[-1]

    def provenance(self, kind: str) -> tuple[LedgerEntry, ...]:
        """The ledger entries for ``kind``'s rows, in row order."""
        return tuple(e for e in self._ledger if e.kind == kind)

    def kinds(self) -> tuple[str, ...]:
        """Every table the run has, the ledger's last."""
        return (*self._rows, LEDGER)

    @property
    def ledger(self) -> tuple[LedgerEntry, ...]:
        """Every row's entry, in the order the rows were appended."""
        return tuple(self._ledger)

    def __contains__(self, kind: str) -> bool:
        return bool(self._rows.get(kind))

    def __len__(self) -> int:
        return len(self._ledger)

    def __repr__(self) -> str:
        sizes = ", ".join(f"{kind}: {len(rows)}" for kind, rows in self._rows.items() if rows)
        return f"Construct({sizes})" if sizes else "Construct(empty)"
