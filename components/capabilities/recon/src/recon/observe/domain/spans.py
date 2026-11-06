"""The span planner: which source time a ranged table's run asks for, after what its table
committed (owner rulings R1 and R5; the 11-04 design's D5). Pure: it reads no clock and no
store.

What a ``range:`` may say, the span an act is handed (:class:`recon.core.RangeRun`), how its
steps fill an entry's settings and how a range commits with its table are the core's
(``recon.core.domain.ranges``); which span each run asks for, given the checkpoint, is here.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import TYPE_CHECKING

from recon.core import Interval, RangeRun, aligned

if TYPE_CHECKING:
    from datetime import datetime

    from recon.core import Committed, RangeFrom, RangeSpec


def plan_range(
    spec: RangeSpec,
    now: datetime,
    request: str,
    checkpoint: Committed | None,
    *,
    override: Interval | None = None,
    rebase: bool = False,
    restart: bool = False,
) -> RangeRun:
    """The run of an entry whose ``range:`` is ``spec``, asked with ``request`` at ``now``,
    after ``checkpoint`` (the last range its table committed, of any request; None for
    none). Pure: it reads no clock and no store."""
    step = timedelta(seconds=spec.step_s)
    horizon = aligned(now.astimezone(UTC) - timedelta(seconds=spec.settle), spec.align)
    kind = spec.kind
    refused: list[tuple[str, str, str]] = []
    began: RangeFrom
    if override is not None:
        interval, began, moves = override, "override", False
    elif kind == "rolling":
        assert spec.last is not None
        start = aligned(horizon - timedelta(seconds=spec.last), spec.align)
        interval, began, moves = Interval(start, horizon), "default", False
    else:
        start, began = _first(spec, horizon, step, request, checkpoint, rebase, restart, refused)
        end = min(horizon, start + step * spec.max_steps)
        if spec.end is not None:
            end = min(end, spec.end)
        interval, moves = Interval(start, max(end, start)), True
    steps = _steps(interval, step)
    if (override is not None or kind == "rolling") and len(steps) > spec.max_steps:
        refused.append((
            "range_steps",
            f"the interval asked for is {len(steps)} step(s) of {spec.step_s:g}s, past "
            f"max_steps ({spec.max_steps})",
            "narrow the interval, or raise range.step or range.max_steps",
        ))  # fmt: skip
    horizon_end = horizon if spec.end is None else min(horizon, spec.end)
    complete = override is None and spec.end is not None and interval.start >= spec.end
    current = not steps and not complete
    behind = (horizon_end - interval.end).total_seconds() if moves else 0.0
    return RangeRun(kind=kind, interval=interval, steps=steps, began=began, request=request,
                    moves=moves, behind_s=max(behind, 0.0), complete=complete, current=current,
                    refused=refused)  # fmt: skip


def _first(
    spec: RangeSpec,
    horizon: datetime,
    step: timedelta,
    request: str,
    checkpoint: Committed | None,
    rebase: bool,
    restart: bool,
    refused: list[tuple[str, str, str]],
) -> tuple[datetime, RangeFrom]:
    """Where a continuing or fixed range's run begins, and why."""
    default: tuple[datetime, RangeFrom] = (
        (aligned(spec.start.astimezone(UTC), spec.align), "start")
        if spec.start is not None
        else (aligned(horizon - step, spec.align), "default")
    )
    if restart and checkpoint is not None:
        return default[0], "restart"
    if checkpoint is None:
        return default
    if rebase:
        return checkpoint.interval.end, "rebase"
    if checkpoint.request != request:
        refused.append((
            "checkpoint_request_changed",
            "the entry asks its source another question than the one its checkpoint was "
            "committed under, so going on from it may leave a gap or ask twice",
            "survey once with --rebase <output> to go on from the checkpoint under the new "
            "request, or --restart <output> to begin again from range.start",
        ))  # fmt: skip
    return checkpoint.interval.end, "checkpoint"


def _steps(interval: Interval, step: timedelta) -> tuple[Interval, ...]:
    """``interval`` cut into steps of ``step``, the last one short when it ends there."""
    steps = []
    at = interval.start
    while at < interval.end:
        end = min(at + step, interval.end)
        steps.append(Interval(at, end))
        at = end
    return tuple(steps)
