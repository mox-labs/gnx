"""Subscriptions, as a mission's config says them: who is told what changed in a mission's
outputs (round-4 rulings R2-R4).

A mission declares each in its config, ``subscriptions:``, a list as ``catalog:`` and
``collectors:`` are (:class:`SubscriptionSpec`): its ``name``; the ``output`` whose change rows
it reads (an output whose entry has ``changes:``); ``match:``, which of them; and ``deliver:``,
the command each is given to (:class:`DeliverSpec`), or none, for a subscriber that reads them
itself (``recon subscribe``). What a subscription has read and acked is kept beside the mission
by the observe layer (``recon.observe.domain.delivery``), never in the config.

**match:** maps a field of a change row to a glob, or a list of globs any of which it may
match, the shape of a ``capture`` collector's ``match:``: ``op``, ``key.<field>`` (a field of
``changes.key``) or ``record.<field>``. A field absent or null matches no glob (a delete's
``record`` is null). No SQL: a filter reads one row, never another table.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from recon.core.domain.missions import MISSION_NAME

#: A field of a change row that ``match:`` may name as it is; the others are named in it.
ROW_FIELDS = ("op",)

#: The parts of a change row whose fields ``match:`` names by ``<part>.<field>``.
ROW_PARTS = ("key", "record")

#: By default, the most rows one delivery gives a command one at a time; the rest are given as
#: one row that counts them.
MAX_PER_TICK = 10

#: By default, how many times a row is given to a command that fails it before it is set aside.
ATTEMPTS = 3

#: By default, the seconds one delivery of a subscription may take, every command it runs
#: included; and the seconds one run of the command may take.
DELIVER_DEADLINE_S = 30.0
DELIVER_TIMEOUT_S = 10.0

#: By default, the most characters a string of a row keeps in a delivery: a mail's subject
#: whole, a body's opening lines.
MAX_CHARS = 200


#: By default, the seconds a subscription keeps retention from removing an archive whose
#: change rows it has not acked: 30 days. Past it, the archive goes as ``retain:`` says, and
#: the subscriber is told (``retired_range``, ``changes_retired``): a subscription nobody
#: reads must not keep a mission growing for ever (Erlang, review F2).
MAX_LAG_S = 2_592_000.0


class DeliverSpec(BaseModel):
    """``deliver:`` on a subscription: give each row to ``command``.

    ``command`` is the program run for each row and ``args`` its arguments (none by default),
    the shape an ``mcp`` source's stdio server has: run as given, by no shell, the row on its
    standard input and in no argument. ``fields``: the record's fields
    a delivery carries besides the row's op, output and key (none by default: no body).
    ``max_per_tick``: the most rows one delivery gives one at a time; the rest it gives as one
    row counting them. ``attempts``: how many times a row is given to a command that fails it,
    one a delivery, before it is set aside as a dead letter, so it never holds the rows after
    it. ``deadline``: the seconds one delivery may take; ``timeout``: the seconds one run of the
    command may take. ``max_chars``: the most characters each string of a row keeps."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: str = Field(min_length=1)
    args: list[str] = []
    fields: list[str] = []
    max_per_tick: int = Field(default=MAX_PER_TICK, ge=1)
    attempts: int = Field(default=ATTEMPTS, ge=1)
    deadline: float = Field(default=DELIVER_DEADLINE_S, gt=0, allow_inf_nan=False)
    timeout: float = Field(default=DELIVER_TIMEOUT_S, gt=0, allow_inf_nan=False)
    max_chars: int = Field(default=MAX_CHARS, ge=1)

    @field_validator("args")
    @classmethod
    def _arguments(cls, value: list[str]) -> list[str]:
        if any("\0" in argument for argument in value):
            raise ValueError("an argument holds a NUL character, which no program is given")
        return value

    @field_validator("command")
    @classmethod
    def _a_program(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("the command holds a NUL character, which names no program")
        return value

    @property
    def argv(self) -> list[str]:
        """The argument list each run is: ``command``, then ``args``."""
        return [self.command, *self.args]

    @field_validator("fields")
    @classmethod
    def _record_fields(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("fields names a field twice")
        if own := [field for field in value if field.startswith("_")]:
            raise ValueError(f"fields names {', '.join(own)}: a field starting _ is recon's own")
        return value


class SubscriptionSpec(BaseModel):
    """One of a mission's ``subscriptions:``. ``name`` names it among the mission's, and is
    what ``recon subscribe`` takes; ``output`` is the output whose change rows it reads;
    ``match:`` chooses which (every row by default); ``deliver:`` gives each to a command (none
    by default: the subscriber reads them itself). ``max_lag``: the seconds it keeps ``retain:``
    from removing an archive whose rows it has not acked (30 days by default)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    output: str
    match: dict[str, str | list[str]] = {}
    deliver: DeliverSpec | None = None
    max_lag: float = Field(default=MAX_LAG_S, gt=0, allow_inf_nan=False)

    @field_validator("name")
    @classmethod
    def _a_name(cls, value: str) -> str:
        if not MISSION_NAME.match(value):
            raise ValueError(
                "a subscription's name is letters, digits, ., _ and -, starting with a letter "
                "or a digit"
            )
        return value

    @model_validator(mode="after")
    def _globs(self) -> SubscriptionSpec:
        for field, globs in self.match.items():
            if isinstance(globs, list) and not globs:
                raise ValueError(f"match.{field} is an empty list, which no row matches")
        return self


def match_field(field: str) -> tuple[str, str | None]:
    """A ``match:`` field as the part of a change row it reads and the field in it: ``op`` is
    ``("op", None)``, ``record.subject`` is ``("record", "subject")``; one of neither shape is
    ``("", None)``."""
    if field in ROW_FIELDS:
        return field, None
    part, _, name = field.partition(".")
    if part in ROW_PARTS and name:
        return part, name
    return "", None
