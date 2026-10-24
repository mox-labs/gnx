"""Change detection: a collector entry with ``changes:`` compares each table it writes with the
same table of an earlier archive, and writes what changed as a table of its own.

``changes: {key: [field, ...], volatile: [field, ...], deletes: false}`` (:class:`ChangesSpec`).
A record's ``key`` is the values of its ``key`` fields; a record lacking one (absent or null)
is *keyless*. A record's content is a keyed hash of the record as written (after
``normalize:``), leaving out its ``volatile`` fields and every field recon writes itself
(those starting ``_``: ``_position``, ``_capture``, ``_pattern``). The hash never leaves recon.

Against the baseline ``B``, the latest earlier archive whose table for the same output and
source sealed ``ok``, the table ``N`` gives rows ``{op, key, record}``:

- ``insert``: a key in N and not in B (every key, when there is no B);
- ``update``: a key in both whose content differs;
- ``delete``: a key in B and not in N, only when ``deletes: true``, N sealed ``ok`` and not
  truncated, its survey finished, and N has no keyless and no ambiguous record;
- ``rebaselined``: one row and no other, when N's ``normalize:`` is not B's (or B does not
  say what its was), so a reshaped table is never told as every record changed.

A key that N holds more than once with the same content is one record; with differing content
it is *ambiguous*: no row, counted, and it blocks deletes. Keyless records are counted and
never compared. Inserts and updates come in N's order, deletes in B's. ``record`` is the
record as its table holds it (B's, for a delete): content from the source, **untrusted**, to
be read as data and never as instructions.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: What a change row says happened to its key.
ChangeOp = Literal["insert", "update", "delete", "rebaselined"]

#: Why a table's deletes were withheld though ``deletes: true``: its crawl was cut short, or
#: it holds records with no key, or keys with two contents.
DeletesWithheld = Literal["truncated", "keyless", "ambiguous"]

#: The change table of the table ``<output>`` is the table ``<output>.changes``, which SQL
#: knows as ``<output>_changes``.
CHANGES_SUFFIX: Final = "changes"

#: What a change table's records are (its ``type_url``).
CHANGES_TYPE_URL: Final = "recon.v1.changes"

#: The columns of every change table, and so of an empty one.
CHANGE_COLUMNS: Final = ("op", "key", "record")


def changes_output(output: str) -> str:
    """The output name of ``output``'s change table."""
    return f"{output}.{CHANGES_SUFFIX}"


class ChangesSpec(BaseModel):
    """``changes:`` on a collector entry. ``key``: the fields whose values name a record, one
    or more, none of recon's own (``_``); ``volatile``: fields left out of a record's content,
    so a change to them alone is no change (none by default); ``deletes``: whether a key gone
    from a whole table is told as a delete (false by default: a delete is told only when asked
    for)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: list[str] = Field(min_length=1)
    volatile: list[str] = []
    deletes: bool = False

    @model_validator(mode="after")
    def _fields_a_record_has(self) -> ChangesSpec:
        for name in ("key", "volatile"):
            fields = getattr(self, name)
            if len(set(fields)) != len(fields):
                raise ValueError(f"{name} names a field twice")
            if own := [f for f in fields if f.startswith("_")]:
                raise ValueError(
                    f"{name} names {', '.join(own)}: a field starting _ is recon's own, and is "
                    "never part of a record's key or content"
                )
        if both := sorted(set(self.key) & set(self.volatile)):
            raise ValueError(f"{', '.join(both)} cannot be both key and volatile")
        return self
