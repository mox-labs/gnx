"""ConstructStore: keeps a run's record as it is made, and gives it back.

The executor opens a run in the store before anything starts, appends each row as it lands,
and finishes the run when it ends. So the record is on the store's side of the process
boundary the whole time: a run can be read while it is going, and a run that was killed
leaves everything it had recorded.

Contract:

* ``open(run)`` creates the run's record with status ``running`` and returns its writer. A
  run id that already exists is a StoreError: a record is never reopened by a writer. A
  store may finish a record whose writer is gone (the JSONL store, on the first read of a
  killed run, marks it ``interrupted``). It keeps every row whose ``append`` returned; it
  may set the status and the counts of rows, and move a cut-short last line aside, never
  drop it.
* ``RunWriter.append(kind, row)`` is durable when it returns. The executor appends each
  row's ledger entry (kind ``matrix.v1.ledger``) and then the row itself; a store keeps that
  order and adds nothing of its own.
* ``RunWriter.finish(run)`` records how the run ended, atomically: a reader sees the record
  as it was while running, or the finished one, never a mixture. Returns where it is.
* ``RunWriter.events()``, optional, is an observer that keeps the run's events beside its
  record (the JSONL store writes ``events.jsonl``), or None. The executor attaches it for the
  run and, when it has a ``close()``, closes it once the run's events have drained, after
  ``finish``. It is telemetry like any observer: lossy, never data. A writer without it keeps
  no events.
* ``load(run_id)`` returns the Construct as recorded, for a run in any state. Rows come back
  as stored data, not as the objects that produced them. A ledger entry whose row never
  landed is reported in ``Construct.torn``.
* ``describe(run_id)`` is the run's document (``matrix.v1.run``): status, times, how each
  component ended, and each table's kind and size. A run that never finished reads
  ``running``, or ``interrupted`` when the store can tell its writer is gone.
* ``runs()`` lists run ids, oldest first. A missing run is a NotFoundError naming those
  that exist. Anything the store cannot write or read back is a StoreError.
* ``location(run_id)``, optional, is where the run's record is (a path, or the id), whether
  or not it exists yet: what a caller prints. A store without it is located by the run id.
* ``damaged()``, optional, names each record the store found and ``runs()`` skipped, as
  ``(where, why)``: where as ``location`` gives it, why as a sentence. A store without it
  says nothing of what it skipped.

The optional members were added after the others: a store written before them keeps working,
and :func:`events_of`, :func:`location_of` and :func:`damaged_of` read them for a caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matrix.domain.construct import Construct
    from matrix.domain.executor import Run
    from matrix.domain.observer import Observer


@runtime_checkable
class RunWriter(Protocol):
    def append(self, kind: str, row: Any) -> None:
        """Add ``row`` to ``kind``'s table. Durable before it returns."""
        ...

    def finish(self, run: Run) -> str:
        """Record the run's final state; return where the record is (a path, or the id)."""
        ...


@runtime_checkable
class ConstructStore(Protocol):
    def open(self, run: Run) -> RunWriter: ...

    def load(self, run_id: str) -> Construct: ...

    def describe(self, run_id: str) -> dict[str, Any]: ...

    def runs(self) -> list[str]: ...


def events_of(writer: RunWriter) -> Observer | None:
    """The writer's event sink, when it keeps one."""
    events = getattr(writer, "events", None)
    sink: Observer | None = events() if callable(events) else None
    return sink


def location_of(store: ConstructStore, run_id: str) -> str:
    """Where ``store`` keeps the run ``run_id``: its own answer, or the run id."""
    location = getattr(store, "location", None)
    return str(location(run_id)) if callable(location) else run_id


def damaged_of(store: ConstructStore) -> list[tuple[str, str]]:
    """The records ``store`` skipped when it listed its runs, each ``(where, why)``: its own
    answer, or none."""
    damaged = getattr(store, "damaged", None)
    return [(str(where), str(why)) for where, why in damaged()] if callable(damaged) else []
