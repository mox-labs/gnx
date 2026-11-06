"""The join of a table's keys with a told index (:mod:`recon.observe.application.changes`): a
port, in memory for a small index and in DuckDB for a large one, each giving the same results.

What a mission's ``changes:`` may say, and what a change row is, are the config's and the
documents' (:class:`recon.core.ChangesSpec`, ``recon.core.ChangeOp``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from recon.core import ChangeOp


class Joined(Protocol):
    """A table's keys joined with a told index (:meth:`KeyJoin.join`). ``ambiguous``: the keys
    the table holds with two contents. Each listing may be read once; :meth:`close` lets go
    of what the join holds."""

    ambiguous: int

    def ops(self) -> Iterator[tuple[int, ChangeOp]]:
        """Each unambiguous key the told index lacks (``insert``) or told with another content
        (``update``), as the ordinal of its first record in the table, in table order."""
        ...

    def gone(self) -> Iterator[str]:
        """Each key told and not in the table, sorted."""
        ...

    def told(self, *, deletes: bool) -> Iterator[tuple[str, str]]:
        """The told index once this table is told, sorted by key: each unambiguous key of the
        table with its content, and, unless ``deletes`` (told only of a table with no
        ambiguous key), each other key told before, as told."""
        ...

    def told_count(self, *, deletes: bool) -> int:
        """How many keys :meth:`told` gives."""
        ...

    def close(self) -> None: ...


class KeyJoin(Protocol):
    """Joins a table's keys with a told index. ``told`` is ``(key, content)`` sorted by key;
    ``new`` is ``(ordinal, key, content)`` for each keyed record of the table, in table order.
    Both are read through before :meth:`join` returns. A key is the canonical JSON of its
    values; a content is a hash. It runs no SQL a mission wrote. Where a join keeps what it
    spills is its own: a join is built for one mission, and keeps it private to that mission,
    each join's removed when it closes."""

    def join(
        self, told: Iterable[tuple[str, str]], new: Iterable[tuple[int, str, str]]
    ) -> Joined: ...
