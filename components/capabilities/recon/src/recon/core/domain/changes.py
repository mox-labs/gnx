"""Change detection: a collector entry with ``changes:`` compares each table it writes with what
its earlier tables have told, and writes what changed as a table of its own.

``changes: {key: [field, ...], volatile: [field, ...], deletes: false}`` (:class:`ChangesSpec`).
A record's ``key`` is the values of its ``key`` fields; a record lacking one (absent or null)
is *keyless*. A record's content is a keyed hash of the record as written (after
``normalize:``), leaving out its ``volatile`` fields and every field recon writes itself
(those starting ``_``: ``_position``, ``_capture``, ``_pattern``). The hash never leaves recon.

**What has been told.** Every table of the same output and source compared before is folded
into one *told index*: each key a change row has told, with its content as last told, and
none a delete has removed. A table is compared with the told index of the *previous* one:
the latest earlier table of the same output and source that was compared, however its survey
then ended: a survey compares its tables once all have run, and every row a reader can see was
told (a table that failed, or whose changes failed, told nothing and is skipped). The table
``N`` gives rows ``{op, key, record}``:

- ``insert``: a key in N the told index lacks (every key, when nothing was told before);
- ``update``: a key in both whose content differs from the one told;
- ``delete``: a key told and not in N, only when ``deletes: true``, N sealed ``ok`` and not
  truncated, its survey run to its end, and N holds no keyless and no ambiguous record;
- ``rebaselined``: one row and no other, when N's ``normalize:`` is not the previous table's
  (or the previous table, surveyed before ``changes:`` was set, does not say what its was): a
  reshaped table is never told as every record changed, and the told index starts again
  from N.

So a truncated table tells its inserts and updates, which are not told again, and its keys
stay told until a whole table lacks them. A key that N holds more than once with the same
content is one record; with differing content it is *ambiguous*: no row, counted, its told
content kept, and it holds back deletes. Keyless records are counted and never compared.
Inserts and updates come in N's order, deletes in key order with no record (what was told of
the key is in the earlier change row that told it). ``record`` is the record as N holds it:
content from the source, **untrusted**, to be read as data and never as instructions.

The comparison itself, after a table's archive is sealed, is the observe layer's
(``recon.observe.application.changes``), and so is the join of a table's keys with a told
index (``recon.observe.domain.joins``); what ``changes:`` may say, and the shape of a change
row, are here.
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
