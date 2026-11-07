"""Ranges: what source time a collector entry's table covers, run after run (owner rulings R1,
R5 and R-TOMB; the 11-04 design's D3 to D5).

A ``range:`` on a snapshot entry asks for its source's time in **steps**: each step is one
request, the entry's settings filled with that step's bounds (``{range.start}``,
``{range.end}``, ``{range.last}``, :func:`fill`), for any collector type, and the run's steps
land in the run's one table. Three kinds, told apart by which keys are written:

======================  ===========================================  ===================
Keys                    Each run collects                            Moves the checkpoint
======================  ===========================================  ===================
neither ``end`` nor     ``[checkpoint or start, min(horizon,         yes, to the run's end
``last``: continuing    from + max_steps × step))``
``start`` and ``end``:  the same, never past ``end``                 yes; at ``end`` the
fixed                                                                range is complete
``last``: rolling       ``[horizon - last, horizon)``                no
======================  ===========================================  ===================

The **horizon** is ``now - settle``, to the whole second (and down to a multiple of
``align`` when it is set): an end nearer ``now`` would lose what the source has not yet
settled. Intervals are half-open, ``[start, end)``, so two steps that meet share no instant.

**The checkpoint** of a ranged table is the end of the last range it committed, under the
request it was asked with (the digest of the entry's own settings and its source). A range is
committed with its table's data, in one commit: the table sealed ``ok`` in an archive whose
meta is recorded with ``ended`` set (:mod:`recon.core.domain.archives`). A run that fails anywhere
commits nothing, so the next asks for the same interval again (a refetch, which ruling R1
accepts); nothing is ever asked for twice once committed, and no source time is left between
two committed ranges. Retention keeps each retired archive's committed ranges in its
tombstone, so it never loses the checkpoint. A table whose crawl was cut short
(``truncated``, past ``paginate.pages``) commits nothing either, for part of its time is
missing: the next run asks for the same interval, so a step too large for one crawl is
narrowed (``range.step``) or given more pages, never skipped.

**Overrides** (``--range START..END``) collect that interval and move no checkpoint; ``--rebase``
goes on from the checkpoint under a changed request, ``--restart`` begins again from ``start``.

Which span a run asks for is the observe layer's planner (``recon.observe.domain.spans``); the
span an act is handed (:class:`RangeRun`), the filling of its steps, and the checkpoint's
record, which commits with its table, are here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from recon.core.domain.errors import UsageError

if TYPE_CHECKING:
    from collections.abc import Mapping

#: By default, the seconds one step of a continuing or fixed range spans: a step is one
#: request, so it bounds what one request asks for.
DEFAULT_STEP_S = 3600.0

#: By default, the seconds a range's end stays behind now, for what its source has not yet
#: settled: an end nearer now silently loses late data; a later one only delays it.
DEFAULT_SETTLE_S = 60.0

#: By default, the most steps one run asks for: a long backlog is drained over several runs,
#: never one unbounded run.
DEFAULT_MAX_STEPS = 24

RangeKind = Literal["continuing", "fixed", "rolling"]

#: Where a run's interval began: the ``checkpoint`` committed, the entry's ``start``, the
#: ``default`` first step (one step back from the horizon), an ``override`` (``--range``),
#: a ``rebase`` (the checkpoint, under a changed request) or a ``restart`` (``start`` again).
RangeFrom = Literal["checkpoint", "start", "default", "override", "rebase", "restart"]

#: The formats a placeholder takes, ``{range.start:<format>}``: ``iso`` (the default),
#: ``epoch`` and ``epoch_ms`` (whole seconds and milliseconds since 1970, a number where the
#: placeholder is the whole value), and ``date`` (``YYYY-MM-DD``).
FORMATS = ("iso", "epoch", "epoch_ms", "date")

#: The bounds a placeholder names: ``{range.<bound>}``.
BOUNDS = ("start", "end", "last")

#: A range placeholder, which a literal brace before it (``{{``) keeps as text.
_PLACEHOLDER = re.compile(r"(?<!\{)\{range\.(start|end|last)(?::([a-z_]+))?\}(?!\})")
#: Anything written as one, a bound or format there is none of included.
_ANY = re.compile(r"(?<!\{)\{range\.([^{}:]*)(?::([^{}]*))?\}(?!\})")

#: The field each record of a ranged table carries: its step, ``{start, end}``. recon's own,
#: as every field beginning with ``_`` is.
RANGE_FIELD = "_range"

#: One unit of the ``iso`` and ``epoch`` formats, which ``{range.last}`` takes off the end.
_ONE = timedelta(seconds=1)

#: Where ``epoch`` counts from: ``align`` is a multiple of seconds since it.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class RangeSpec(BaseModel):
    """A collector entry's ``range:``. Which kind it is is which keys are written; every key
    left out is a default the plan reports with its origin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    last: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    step: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    settle: float = Field(default=DEFAULT_SETTLE_S, ge=0, allow_inf_nan=False)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1)
    align: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @property
    def kind(self) -> RangeKind:
        if self.last is not None:
            return "rolling"
        return "fixed" if self.end is not None else "continuing"

    @property
    def step_s(self) -> float:
        """The step in effect: as written, else a rolling range's whole interval, else an hour."""
        if self.step is not None:
            return self.step
        return self.last if self.last is not None else DEFAULT_STEP_S


@dataclass(frozen=True)
class Interval:
    """Source time, half-open: ``[start, end)``."""

    start: datetime
    end: datetime

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def document(self) -> dict[str, str]:
        return {"start": iso(self.start), "end": iso(self.end)}


def interval_of(start: datetime, end: datetime) -> Interval:
    """The interval ``[start, end)`` a caller asks for (``--range``), whole seconds. Raises
    UsageError for a bound with no time zone, or an end not after its start."""
    if start.tzinfo is None or end.tzinfo is None:
        raise UsageError(
            "a range's start and end must name their time zone: ISO 8601 with an offset",
            fix="give --range as 2026-10-01T00:00Z..2026-10-02T00:00Z",
        )
    interval = Interval(aligned(start, None), aligned(end, None))
    if interval.end <= interval.start:
        raise UsageError(
            f"a range's end ({iso(end)}) is not after its start ({iso(start)})",
            fix="give --range as START..END, END later than START",
        )
    return interval


@dataclass(frozen=True)
class Committed:
    """A range a table committed: its kind, its interval, the request it was asked with, and
    where its run began."""

    kind: RangeKind
    interval: Interval
    request: str
    began: RangeFrom


@dataclass(frozen=True)
class RangeRun:
    """What one run of a ranged entry asks for: its interval, cut into ``steps``, where it began
    (``began``), and what it finds worth saying: how far the run's end is behind the horizon
    (``behind_s``: a backlog for later runs), or that there was nothing to ask for
    (``complete``: a fixed range collected to its end; ``current``: nothing new past
    ``settle``). ``moves``: the run commits its interval as the checkpoint."""

    kind: RangeKind
    interval: Interval
    steps: tuple[Interval, ...]
    began: RangeFrom
    request: str
    moves: bool
    behind_s: float = 0.0
    complete: bool = False
    current: bool = False
    #: Problems that refuse the run (``range_steps``, ``checkpoint_request_changed``), as
    #: ``(kind, message, fix)``.
    refused: list[tuple[str, str, str]] = field(default_factory=list)

    def document(self) -> dict[str, Any]:
        return {**self.interval.document(), "kind": self.kind, "from": self.began,
                "steps": len(self.steps)}  # fmt: skip


def aligned(at: datetime, align: float | None) -> datetime:
    """``at`` to the whole second, and down to a multiple of ``align`` seconds since 1970
    when it is set."""
    seconds = int(at.timestamp())
    if align is not None:
        seconds -= seconds % int(align)
    return _EPOCH + timedelta(seconds=seconds)


def iso(at: datetime) -> str:
    """``at`` in UTC, ISO 8601 with ``Z``: the form every range bound is written in."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def uses_range(settings: Any) -> bool:
    """Whether any string in ``settings`` holds a ``{range.*}`` placeholder (an escaped one,
    ``{{range.start}}``, is text)."""
    return bool(_written(settings))


def unknown_placeholders(settings: Any) -> list[str]:
    """Each ``{range.*}`` in ``settings`` that names a bound none of :data:`BOUNDS`, or a
    format none of :data:`FORMATS`, as written: nothing would fill it."""
    return [m.group(0) for m in _written(settings)
            if m.group(1) not in BOUNDS or m.group(2) not in (None, *FORMATS)]  # fmt: skip


def _written(settings: Any) -> list[re.Match[str]]:
    if isinstance(settings, str):
        return list(_ANY.finditer(settings))
    if isinstance(settings, dict):
        return [m for value in settings.values() for m in _written(value)]
    if isinstance(settings, list):
        return [m for value in settings for m in _written(value)]
    return []


def fill(settings: Any, step: Interval) -> Any:
    """``settings`` with every ``{range.start}``, ``{range.end}`` and ``{range.last}`` (the
    end less one unit of its format, for an API whose end bound is inclusive) in every string
    filled with ``step``'s bounds, in the format each names. A string that is one placeholder
    in a number's format (``epoch``, ``epoch_ms``) becomes that number, so a JSON argument
    keeps the type its schema wants. ``{{`` before a placeholder keeps it as text."""
    if isinstance(settings, str):
        whole = _PLACEHOLDER.fullmatch(settings)
        if whole is not None and (whole.group(2) or "iso") in ("epoch", "epoch_ms"):
            return _value(whole.group(1), whole.group(2) or "iso", step)
        return _PLACEHOLDER.sub(
            lambda m: str(_value(m.group(1), m.group(2) or "iso", step)), settings
        )
    if isinstance(settings, dict):
        return {key: fill(value, step) for key, value in settings.items()}
    if isinstance(settings, list):
        return [fill(value, step) for value in settings]
    return settings


def _value(which: str, format_: str, step: Interval) -> str | int:
    at = step.start if which == "start" else step.end
    if which == "last":
        unit = {"epoch_ms": timedelta(milliseconds=1), "date": timedelta(days=1)}.get(format_, _ONE)
        at = step.end - unit
    if format_ == "epoch":
        return int(at.timestamp())
    if format_ == "epoch_ms":
        return int(at.timestamp() * 1000)
    if format_ == "date":
        return at.astimezone(UTC).strftime("%Y-%m-%d")
    return iso(at)


def committed_in(meta: Mapping[str, Any]) -> list[tuple[str, str | None, Committed]]:
    """Each range an archive's meta commits, as ``(output, source, range)``: a table's whose
    status is ``ok`` and whose crawl was whole (not ``truncated``: a step cut short holds part
    of its time), in a meta recorded with ``ended`` set, its run moving the checkpoint (not an
    override, not a rolling range). A meta recorded while its survey compared its tables
    (``ended`` null), and one never recorded, commit nothing."""
    if meta.get("format_version") is None or meta.get("ended") is None:
        return []
    found = []
    for table in meta.get("tables") or []:
        if not isinstance(table, dict) or table.get("status") != "ok":
            continue
        if table.get("truncated") is True:
            continue
        committed = _committed(table.get("range"))
        if committed is not None:
            found.append((str(table.get("name")), table.get("source"), committed))
    return found


def committed_kept(stone: Mapping[str, Any]) -> list[tuple[str, str | None, Committed]]:
    """Each range a retired archive's tombstone kept, as :func:`committed_in` gives them."""
    found = []
    for kept in stone.get("ranges") or []:
        if not isinstance(kept, dict):
            continue
        committed = _committed(kept)
        if committed is not None:
            found.append((str(kept.get("output")), kept.get("source"), committed))
    return found


def kept_in_tombstone(meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What a tombstone keeps of the ranges its archive committed, so a range goes on from
    where it was whatever retention removed."""
    return [
        {"output": output, "source": source, **_record(committed)}
        for output, source, committed in committed_in(meta)
    ]


def outcome(run: RangeRun) -> dict[str, Any]:
    """A run's range as its table's outcome records it: what :func:`committed_in` reads."""
    return {**run.interval.document(), "kind": run.kind, "from": run.began, "request": run.request,
            "steps": len(run.steps)}  # fmt: skip


def _record(committed: Committed) -> dict[str, Any]:
    return {**committed.interval.document(), "kind": committed.kind, "from": committed.began,
            "request": committed.request}  # fmt: skip


#: The kinds, and the beginnings, of a run that moves its checkpoint: an override moves none,
#: and a rolling range keeps none.
_MOVING_KINDS: dict[str, RangeKind] = {"continuing": "continuing", "fixed": "fixed"}
_MOVING_FROM: dict[str, RangeFrom] = {
    began: began for began in ("checkpoint", "start", "default", "rebase", "restart")
}


def _committed(recorded: Any) -> Committed | None:
    """A recorded range, when it moves a checkpoint; None for any other."""
    if not isinstance(recorded, dict):
        return None
    kind = _MOVING_KINDS.get(str(recorded.get("kind")))
    began = _MOVING_FROM.get(str(recorded.get("from")))
    if kind is None or began is None:
        return None
    try:
        start = datetime.fromisoformat(str(recorded["start"]))
        end = datetime.fromisoformat(str(recorded["end"]))
    except (KeyError, ValueError):
        return None
    if start.tzinfo is None or end.tzinfo is None:
        return None
    return Committed(kind, Interval(start, end), str(recorded.get("request")), began)
