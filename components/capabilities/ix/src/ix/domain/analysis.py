"""Analysis: what a run's readings say about the decision the experiment declared.

A pure function of the design and the three tables a run records (trials, interactions,
readings). It runs outside the run, so it can be repeated over a stored run with no host.
:data:`METHOD` is this docstring in the words a report prints; the judgments behind each
choice, with their sources and the simulations that check them, are in
``docs/design/2026-10-19-record.md``.

**Two reductions, then an interval.** Each trial's reading is reduced to one number by the
decision's ``statistic`` (the p99 of that trial's latencies). The trials' numbers are combined
by its ``estimator`` (their mean). The decision is made on a 95% interval for the estimate,
never on the estimate alone. The method follows the measurement
(:mod:`ix.domain.intervals` holds each):

* a ``mean`` gets Student's t interval; a difference of means is the t interval of the
  per-block differences (a **block** is one repeat of one probe in one cell, where every
  subject ran side by side), and a relative difference is Fieller's interval for the ratio
  of the two means, measured in the same blocks;
* a ``median`` gets the order-statistic interval, of the per-block differences for a
  comparison (so a median compares by the median of the per-block differences);
* a ``boolean`` metric is a rate over trials, with a Wilson score interval; a ``count``
  metric (events out of units, as failed requests out of requests sent) is a rate pooled
  over trials, with the Wilson interval at Korn and Graubard's effective sample size, the
  trial as the cluster. A difference of two rates combines their intervals (Newcombe's
  method 10). Neither interval has zero width, whatever was counted;
* ``min`` and ``max`` have no interval ix can stand behind: they are estimated and never
  decide.

**The comparison.** With the control ``c`` and a variant ``v``, the *gain* is ``v - c`` turned
so that positive is better (the metric's direction decides), on the margin's basis: percent
of the control, percentage points, or the metric's own unit. ``superiority``, which has no
margin, uses percent of the control, and points for a rate. For the interval ``[lo, hi]`` of
the gain:

* ``superiority`` holds when ``lo > 0`` and fails when ``hi < 0``;
* ``non-inferiority`` with margin ``m`` holds when ``lo > -m`` and fails when ``hi < -m``:
  a one-sided test at 2.5%, the convention of FDA's 2016 guidance;
* ``equivalence`` holds when the interval lies inside ``(-m, m)`` and fails when it lies
  wholly outside: two one-sided tests at 2.5% each, stricter than the 5% of a 90% interval;
* a ``threshold`` holds when the subject's own interval lies wholly on the right side of it,
  and fails when wholly on the wrong side.

Anything else is ``inconclusive``. Two identical subjects are therefore inconclusive under
``superiority`` (no difference was shown either way), and hold under ``non-inferiority``
once the interval is narrower than the margin (a loss beyond the margin was ruled out).

**More than one decision.** The verdict holds only if every decision (each variant, in each
cell, on the primary and on each guardrail) holds. That is an intersection-union test
(Berger 1982): each decision is read at the full level, and requiring all of them adds no
chance of a false ``holds``, so the ``holds`` side takes no correction. The verdict fails if
any decision fails, so the ``fails`` side is a union, and Holm's step-down procedure holds
the family to the level there: the decision at step ``i`` of ``m``, in order of level, is
allowed ``alpha / (m - i + 1)``. The family is fixed by the design before any reading is
looked at: a decision that could not be computed stays in it, undecided, so missing data
never loosens the correction for the rest. A model-judged decision is outside it. A
decision's ``decided_at`` is the smallest two-sided level at which it would be made: its
test's p-value where the interval inverts a test, otherwise searched down to 1e-6.

**Calibration is a check.** Under A/A calibration the control runs twice in every block. The
control's second sample is compared with its first, with the same method, at the level
:data:`CHECK_LEVEL` shared out over every such check (Bonferroni), so a stable environment
fails the check in at most one run in a hundred. A check that finds the control differing
from itself, or that cannot be made, makes the run inconclusive and says why. It never
changes an interval.

**Failures.** A trial the harness failed is missing data: it is left out and counted. A trial
the subject failed counts against the subject: a subject that failed any trial of a cell
fails that cell. Under a comparison, a control that failed one, in either of its turns, makes
the cell inconclusive instead, whatever the variant did.

**A model-judged metric never decides.** ``validate`` refuses one as a primary or a
guardrail; if a design holds one anyway, its decision is inconclusive.

Limits:

* Fewer than :data:`MIN_BLOCKS` usable blocks (trials, for one subject) decide nothing; a
  median needs :data:`~ix.domain.intervals.MEDIAN_MIN`.
* Every trial reading the same number gives no spread to make a t interval from: such a
  decision is inconclusive, never certain.
* A one-sample t interval under-covers when the per-trial numbers are strongly skewed; a
  ``median`` is the remedy. A paired difference is far less sensitive.
* A relative difference has no bounded interval when the control's mean is not
  distinguishable from zero, and none at all when it is zero.
* The interval of a difference of rates does not use the blocks' pairing: it is wider, not
  narrower, for that.
* A rate whose rare events arrive in bursts concentrated in a few trials is under-covered:
  too few trials carry them to estimate their spread.
* The A/A check sees a shift between the control's two samples, not every kind of
  instability, and has little power with few blocks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from ix.domain import intervals
from ix.domain.errors import ConfigError
from ix.domain.experiment import ESTIMATORS, STATISTICS, Level
from ix.domain.genre import CONTROL
from ix.domain.intervals import MEDIAN_MIN, Span
from ix.domain.run import CALIBRATION, cell_key, turns
from ix.domain.schema import Record

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ix.domain.design import Design
    from ix.domain.experiment import Guardrail, Margin, MetricRef, Threshold
    from ix.domain.genre import AnalysisRef
    from ix.domain.kind import Metric
    from ix.domain.run import Interaction, Reading, Trial

Outcome = Literal["holds", "fails", "inconclusive"]

#: The confidence each decision is held to, and the level of every reported interval.
CONFIDENCE = 0.95
#: The fewest usable blocks (or trials, for one subject) an interval is computed from.
MIN_BLOCKS = 3
#: The chance that a stable environment fails the A/A check in one run, shared out over
#: every check the run makes.
CHECK_LEVEL = 0.01

#: The method in the words a report prints.
METHOD: tuple[str, ...] = (
    "Each trial is reduced by the statistic; trials combine by the estimator.",
    f"Intervals are {CONFIDENCE:.0%}: Student's t for a mean (of the per-block differences "
    "in a comparison; Fieller's for a relative one), order statistics for a median, a "
    "Wilson score interval for a rate (pooled over trials, the trial as cluster, for a "
    "count) and Newcombe's for a difference of rates. min and max never decide.",
    "The verdict holds only if every decision holds, so each is read at the full level "
    "(an intersection-union test); it fails if any fails, so the fails side is corrected "
    "by Holm's procedure.",
    "Under A/A calibration the control is compared with itself as a check: if it differs "
    "from itself, or cannot be checked, the run is inconclusive. It never widens an "
    "interval.",
    f"Fewer than {MIN_BLOCKS} usable blocks on a side decide nothing ({MEDIAN_MIN} for a "
    "median); every trial reading the same number decides nothing.",
)

_ALPHA = round(1 - CONFIDENCE, 12)

#: The analyses ix has, by the id a genre names. None of them takes a parameter.
ANALYSES: frozenset[str] = frozenset(
    {"ix.v1.analysis.evaluate", "ix.v1.analysis.compare", "ix.v1.analysis.characterize"}
)


class Interval(Record):
    low: float
    high: float


class Estimate(Record):
    """One subject's estimate of one metric in one cell."""

    subject: str
    role: str
    cell: dict[str, Level] = {}
    metric: str
    statistic: str
    estimator: str
    unit: str = ""
    #: Usable trials behind the estimate.
    n: int
    value: float | None = None
    #: At :data:`CONFIDENCE`. None when the method has none (too few trials, no spread, a
    #: ``min`` or ``max``).
    interval: Interval | None = None


class CalibrationCheck(Record):
    """The control against its own second sample: whether the environment held still enough
    to compare in."""

    cell: dict[str, Level] = {}
    metric: str
    statistic: str
    estimator: str
    #: ``%`` of the control, ``pp`` or the metric's unit.
    unit: str
    #: Blocks in which both samples of the control have a usable trial.
    n: int
    #: The second sample minus the first.
    difference: float | None = None
    #: At ``confidence``, the check's own level.
    interval: Interval | None = None
    confidence: float
    #: False when the interval excludes zero; None when the check could not be made.
    stable: bool | None = None
    reason: str = ""


Form = Literal["superiority", "non-inferiority", "equivalence", "threshold"]


class Decision(Record):
    """One thing the verdict depends on: the primary, or one guardrail, for one subject in
    one cell."""

    part: Literal["primary", "guardrail"]
    metric: str
    statistic: str
    estimator: str
    form: Form
    #: What must be true, in words: ``at most 5% worse than buffered``.
    claim: str
    #: ``%`` of the control, ``pp`` or the metric's unit.
    unit: str = ""
    #: Which way is better for this metric.
    better: str = ""
    #: The interval method, in words: ``t``, ``fieller``, ``order statistics``, ``wilson``,
    #: ``clustered wilson``, ``newcombe``.
    method: str = ""
    #: The subject minus the control (for a threshold: the subject's own estimate).
    difference: float | None = None
    #: At :data:`CONFIDENCE`, unadjusted: Holm's procedure adjusts the level a ``fails``
    #: is allowed, not the interval printed.
    interval: Interval | None = None
    #: Usable blocks (for a threshold: trials).
    n: int = 0
    #: The smallest two-sided significance level at which this decision would be made. None
    #: when no level makes it.
    decided_at: float | None = None
    #: The level it was allowed: the full level for ``holds``, Holm's for ``fails``.
    allowed: float | None = None
    outcome: Outcome = "inconclusive"
    reason: str = ""


class Finding(Record):
    """The outcome for one subject in one cell: it holds only if every decision does."""

    subject: str
    cell: dict[str, Level] = {}
    outcome: Outcome
    reason: str = ""
    decisions: tuple[Decision, ...] = ()


class FailureCount(Record):
    subject: str
    role: str
    owner: str
    reason: str
    count: int


class Analysis(Record):
    """What the readings say. ``outcome`` is None for a genre that gives estimates only."""

    id: str
    confidence: float = CONFIDENCE
    method: tuple[str, ...] = METHOD
    #: How many decisions Holm's procedure corrected together on the ``fails`` side.
    family: int = 0
    #: Holds only if every finding holds; fails if any fails; inconclusive, with ``reason``,
    #: when the calibration check does not pass.
    outcome: Outcome | None = None
    reason: str = ""
    findings: tuple[Finding, ...] = ()
    estimates: tuple[Estimate, ...] = ()
    calibration: tuple[CalibrationCheck, ...] = ()
    failures: tuple[FailureCount, ...] = ()
    notes: tuple[str, ...] = ()


# --- the run's data ----------------------------------------------------------------------


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


_COMBINE: dict[str, Callable[[Sequence[float]], float]] = {
    "mean": _mean,
    "rate": _mean,
    "median": _median,
    "min": min,
    "max": max,
}

type Block = tuple[int, str, str]


@dataclass(frozen=True, kw_only=True)
class _SubjectInCell:
    """Whose trials are read: one subject, in one role, in one cell (by its key)."""

    subject: str
    role: str
    cell: str


class _Data:
    """A run's readings, arranged for the questions the analysis asks."""

    def __init__(
        self,
        design: Design,
        trials: Sequence[Trial],
        interactions: Sequence[Interaction],
        readings: Sequence[Reading],
    ) -> None:
        self.design = design
        self.trials = {trial.seq: trial for trial in trials}
        self.interactions = {interaction.seq: interaction for interaction in interactions}
        self.readings: dict[tuple[int, str], Reading] = {
            (reading.seq, reading.metric): reading for reading in readings
        }
        self.cells: dict[str, dict[str, Level]] = {}
        for trial in trials:
            self.cells.setdefault(cell_key(trial.cell), dict(trial.cell))

    def _belongs(self, trial: Trial, who: _SubjectInCell) -> bool:
        return (trial.subject, trial.role, cell_key(trial.cell)) == (
            who.subject,
            who.role,
            who.cell,
        )

    def _usable(self, who: _SubjectInCell, metric: str) -> dict[Block, Reading]:
        return {
            trial.block: reading
            for trial in self.trials.values()
            if self._belongs(trial, who)
            and (reading := self.readings.get((trial.seq, metric))) is not None
            and reading.failure is None
        }

    def sample(self, who: _SubjectInCell, metric: str, statistic: str) -> dict[Block, float]:
        """The usable trials of one subject in one cell, by block, as numbers."""
        found: dict[Block, float] = {}
        for block, reading in self._usable(who, metric).items():
            number = reading.number(statistic)
            if number is not None and math.isfinite(number):
                found[block] = number
        return found

    def counts(self, who: _SubjectInCell, metric: str) -> dict[Block, tuple[int, int]]:
        """The usable trials of one subject in one cell, by block, as (events, units)."""
        return {
            block: counted
            for block, reading in self._usable(who, metric).items()
            if (counted := reading.counted()) is not None
        }

    def subject_failures(self, who: _SubjectInCell) -> tuple[int, int]:
        """(trials the subject failed, trials carried out) for one subject in one cell."""
        failed = carried = 0
        for interaction in self.interactions.values():
            trial = self.trials.get(interaction.seq)
            if trial is None or not self._belongs(trial, who):
                continue
            carried += 1
            if interaction.failure is not None and interaction.failure.owner == "subject":
                failed += 1
        return failed, carried

    def failure_counts(self) -> tuple[FailureCount, ...]:
        counts: dict[tuple[str, str, str, str], int] = {}
        for interaction in self.interactions.values():
            if interaction.failure is not None:
                key = (
                    interaction.subject,
                    interaction.role,
                    interaction.failure.owner,
                    interaction.failure.reason,
                )
                counts[key] = counts.get(key, 0) + 1
        for reading in self.readings.values():
            if reading.failure is not None:
                key = (reading.subject, reading.role, reading.failure.owner, reading.failure.reason)
                counts[key] = counts.get(key, 0) + 1
        return tuple(
            FailureCount(subject=subject, role=role, owner=owner, reason=reason, count=count)
            for (subject, role, owner, reason), count in sorted(counts.items())
        )


def _reductions(ref: MetricRef | Guardrail, metric: Metric) -> tuple[str, str]:
    statistic = ref.statistic or STATISTICS[metric.shape][0]
    estimator = ref.estimator or ESTIMATORS[metric.shape][0]
    return statistic, estimator


@dataclass(frozen=True)
class _Measure:
    """What a decision is about: the part of the hypothesis it is, the metric, and how the
    metric's trials are reduced."""

    part: Literal["primary", "guardrail"]
    metric: Metric
    statistic: str
    estimator: str

    @property
    def rate(self) -> bool:
        """A boolean or a count: a share of something, decided on a score interval."""
        return self.metric.shape in ("boolean", "count")


def _measure(
    part: Literal["primary", "guardrail"], ref: MetricRef | Guardrail, metric: Metric
) -> _Measure:
    statistic, estimator = _reductions(ref, metric)
    return _Measure(part=part, metric=metric, statistic=statistic, estimator=estimator)


# --- intervals ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Estimated:
    """An estimate, its interval at any level, the method that gave it, and why there is
    no interval when there is none."""

    n: int
    value: float | None = None
    span: Span | None = None
    method: str = ""
    problem: str = ""


_UNDECIDABLE = "min and max have no interval ix decides on; use mean or median"
_FLAT = "every trial read the same number, so there is no spread to make an interval from"


def _too_few(n: int, needed: int, what: str = "usable trials") -> str:
    return f"too few {what} ({n}; {needed} needed)"


def _own(data: _Data, who: _SubjectInCell, measure: _Measure) -> _Estimated:
    """One subject's estimate in one cell, and its interval."""
    name = measure.metric.name
    if measure.metric.shape == "count":
        counts = list(data.counts(who, name).values())
        n = len(counts)
        if not counts:
            return _Estimated(0)
        events, units = [e for e, _ in counts], [u for _, u in counts]
        value = sum(events) / sum(units)
        if n < MIN_BLOCKS:
            return _Estimated(n, value, problem=_too_few(n, MIN_BLOCKS))
        return _Estimated(n, value, intervals.clustered_rate(events, units), "clustered wilson")

    values = list(data.sample(who, name, measure.statistic).values())
    n = len(values)
    if not values:
        return _Estimated(0)
    value = _COMBINE[measure.estimator](values)
    if measure.metric.shape == "boolean":
        if n < MIN_BLOCKS:
            return _Estimated(n, value, problem=_too_few(n, MIN_BLOCKS))
        return _Estimated(n, value, intervals.wilson(math.fsum(values), n), "wilson")
    return _numbers(values, measure.estimator, value)


def _numbers(values: list[float], estimator: str, value: float) -> _Estimated:
    """The interval of a mean or a median of plain numbers."""
    n = len(values)
    if estimator in ("min", "max"):
        return _Estimated(n, value, problem=_UNDECIDABLE)
    needed = MEDIAN_MIN if estimator == "median" else MIN_BLOCKS
    if n < needed:
        return _Estimated(n, value, problem=_too_few(n, needed))
    if min(values) == max(values):
        return _Estimated(n, value, problem=_FLAT)
    if estimator == "median":
        return _Estimated(n, value, intervals.median(values), "order statistics")
    return _Estimated(n, value, intervals.mean(values), "t")


_UNITS = {"relative": "%", "points": "pp"}


def _basis(measure: _Measure, margin: Margin | None) -> str:
    """The basis a difference is on: the margin's, else percent of the control, or points
    for a rate."""
    if margin is not None:
        return margin.basis
    return "points" if measure.rate else "relative"


def _rate_gap(
    data: _Data, who: _SubjectInCell, control: _SubjectInCell, measure: _Measure, scale: float
) -> _Estimated:
    """The difference of two rates, in the blocks both sides have."""
    name = measure.metric.name
    if measure.metric.shape == "count":
        ours, theirs = data.counts(who, name), data.counts(control, name)
        blocks = sorted(ours.keys() & theirs.keys())
        n = len(blocks)
        if n < MIN_BLOCKS:
            return _Estimated(n, problem=_too_few(n, MIN_BLOCKS, "usable blocks"))

        def side(counted: dict[Block, tuple[int, int]]) -> tuple[float, Span]:
            events = [counted[block][0] for block in blocks]
            units = [counted[block][1] for block in blocks]
            return sum(events) / sum(units), intervals.clustered_rate(events, units)

        method = "newcombe (clustered wilson)"
        (mine, mine_span), (base, base_span) = side(ours), side(theirs)
    else:
        a, b = data.sample(who, name, "value"), data.sample(control, name, "value")
        blocks = sorted(a.keys() & b.keys())
        n = len(blocks)
        if n < MIN_BLOCKS:
            return _Estimated(n, problem=_too_few(n, MIN_BLOCKS, "usable blocks"))
        ones, zeros = (
            math.fsum(a[block] for block in blocks),
            math.fsum(b[block] for block in blocks),
        )
        method = "newcombe"
        mine, mine_span = ones / n, intervals.wilson(ones, n)
        base, base_span = zeros / n, intervals.wilson(zeros, n)
    span = intervals.difference(mine_span, base_span, mine, base).scaled(scale)
    return _Estimated(n, (mine - base) * scale, span, method)


def _gap(
    data: _Data, who: _SubjectInCell, control: _SubjectInCell, measure: _Measure, basis: str
) -> _Estimated:
    """The difference between ``who`` and ``control`` on ``basis``: ``who`` minus the
    control, in percent of the control, in percentage points or in the metric's unit."""
    scale = 100.0 if basis in ("relative", "points") else 1.0
    if measure.rate:
        if basis == "relative":
            return _Estimated(
                0, problem="a rate is compared in percentage points or as a share, not in percent"
            )
        return _rate_gap(data, who, control, measure, scale)

    name, statistic = measure.metric.name, measure.statistic
    ours, theirs = data.sample(who, name, statistic), data.sample(control, name, statistic)
    blocks = sorted(ours.keys() & theirs.keys())
    mine = [ours[block] for block in blocks]
    base = [theirs[block] for block in blocks]
    n = len(blocks)
    if measure.estimator in ("min", "max"):
        return _Estimated(n, problem=_UNDECIDABLE)
    needed = MEDIAN_MIN if measure.estimator == "median" else MIN_BLOCKS
    if n < needed:
        return _Estimated(n, problem=_too_few(n, needed, "usable blocks"))

    if basis != "relative":
        differences = [(a - b) * scale for a, b in zip(mine, base, strict=True)]
        return _numbers(differences, measure.estimator, _COMBINE[measure.estimator](differences))
    if measure.estimator == "median":
        if any(b == 0 for b in base):
            return _Estimated(n, problem="the control read 0 in a block, so there is no "
                              "relative difference there")  # fmt: skip
        relative = [(a - b) / abs(b) * 100 for a, b in zip(mine, base, strict=True)]
        return _numbers(relative, "median", _median(relative))

    control_mean = _mean(base)
    if control_mean == 0:
        return _Estimated(n, problem="the control's estimate is 0, so there is no relative "
                          "difference")  # fmt: skip
    value = (_mean(mine) - control_mean) / abs(control_mean) * 100
    ratio = _mean(mine) / control_mean
    if all(a == ratio * b for a, b in zip(mine, base, strict=True)):
        return _Estimated(n, value, problem=_FLAT)
    # The relative difference is (R - 1), turned when the control's mean is negative.
    span = intervals.ratio(mine, base).scaled(100.0, -100.0)
    return _Estimated(n, value, span if control_mean > 0 else span.negated(), "fieller")


# --- deciding ----------------------------------------------------------------------------

_JUDGED = "judged by a model: it never decides"


@dataclass(frozen=True, eq=False)
class _Pending:
    """A decision before the family is settled: what it would be, and the smallest level at
    which it would be made. ``level`` is None only for a decision outside the family (judged
    by a model), and ``math.inf`` when no level makes it or it could not be computed."""

    decision: Decision
    candidate: Outcome = "inconclusive"
    level: float | None = None


def _settle(pending: Sequence[_Pending]) -> dict[_Pending, Decision]:
    """Each decision as the family allows it. ``holds`` is read at the full level (the
    verdict needs every decision to hold); ``fails`` by Holm's step-down procedure over the
    whole family (the verdict needs only one to fail): in order of level, the decision at
    step ``i`` of ``m`` is allowed ``alpha / (m - i + 1)``, and once one is not decided none
    after it is."""
    family = [item for item in pending if item.level is not None]
    size = len(family)
    settled = {item: item.decision for item in pending}

    def fails_level(item: _Pending) -> float:
        return item.level if item.candidate == "fails" and item.level is not None else math.inf

    #: For each decision whose candidate is ``fails``: its Holm level, and whether the
    #: step-down reached it before stopping.
    holm: dict[_Pending, tuple[float, bool]] = {}
    going = True
    for index, item in enumerate(sorted(family, key=fails_level)):
        step = _ALPHA / (size - index)
        going = going and fails_level(item) <= step
        holm[item] = (step, going)

    for item in family:
        level = item.level if item.level is not None else math.inf
        if item.candidate == "fails":
            permitted, decided = holm[item]
        else:
            permitted, decided = _ALPHA, level <= _ALPHA
        if decided or item.decision.reason:
            reason = item.decision.reason
        elif level > _ALPHA:
            reason = "the interval crosses the boundary"
        else:
            reason = f"not decided once corrected with {size - 1} other decision(s)"
        settled[item] = item.decision.model_copy(
            update={
                "decided_at": level if math.isfinite(level) else None,
                "allowed": permitted,
                "outcome": item.candidate if decided else "inconclusive",
                "reason": reason,
            }
        )
    return settled


def _nearer(holds: float, fails: float) -> tuple[Outcome, float]:
    """Which of holds and fails the interval is nearer deciding, and at what level."""
    return ("holds", holds) if holds <= fails else ("fails", fails)


def _threshold_levels(threshold: Threshold, span: Span) -> tuple[float, float]:
    """(holds, fails): the levels at which an interval lies wholly on the right side of the
    threshold, and wholly on the wrong side."""
    bound = threshold.value
    if threshold.op in (">", ">="):
        return span.above(bound), span.below(bound)
    return span.below(bound), span.above(bound)


def _against_threshold(
    data: _Data, who: _SubjectInCell, measure: _Measure, threshold: Threshold
) -> _Pending:
    metric = measure.metric
    own = _own(data, who, measure)
    decision = Decision(
        part=measure.part,
        metric=metric.name,
        statistic=measure.statistic,
        estimator=measure.estimator,
        form="threshold",
        claim=threshold.text(),
        unit=metric.unit,
        better=metric.direction,
        method=own.method,
        difference=own.value,
        n=own.n,
    )
    if metric.judged:
        return _Pending(decision.model_copy(update={"reason": _JUDGED}))
    if own.span is None:
        reason = own.problem or _too_few(own.n, MIN_BLOCKS)
        return _Pending(decision.model_copy(update={"reason": reason}), level=math.inf)
    low, high = own.span(_ALPHA)
    candidate, level = _nearer(*_threshold_levels(threshold, own.span))
    return _Pending(
        decision.model_copy(update={"interval": Interval(low=low, high=high)}), candidate, level
    )


def _claim(form: Form, control: str, margin: Margin | None) -> str:
    within = margin.text() if margin else "0"
    match form:
        case "superiority":
            return f"better than {control}"
        case "non-inferiority":
            return f"at most {within} worse than {control}"
        case "equivalence":
            return f"within {within} of {control}"
    return form


def _comparison_levels(form: Form, limit: float, gain: Span) -> tuple[float, float]:
    """(holds, fails) for the interval of the gain, read against zero or the margin."""
    match form:
        case "superiority":
            return gain.above(0.0), gain.below(0.0)
        case "non-inferiority":
            return gain.above(-limit), gain.below(-limit)
    return (
        max(gain.above(-limit), gain.below(limit)),
        min(gain.below(-limit), gain.above(limit)),
    )


def _control(data: _Data, cell: str, role: str = CONTROL) -> _SubjectInCell:
    return _SubjectInCell(subject=data.design.control or "", role=role, cell=cell)


def _against_control(
    data: _Data,
    who: _SubjectInCell,
    measure: _Measure,
    *,
    form: Form,
    margin: Margin | None,
) -> _Pending:
    metric = measure.metric
    basis = _basis(measure, margin)
    decision = Decision(
        part=measure.part,
        metric=metric.name,
        statistic=measure.statistic,
        estimator=measure.estimator,
        form=form,
        claim=_claim(form, data.design.control or "", margin),
        unit=_UNITS.get(basis, metric.unit),
        better=metric.direction,
    )

    def undecided(reason: str, **update: object) -> _Pending:
        return _Pending(decision.model_copy(update={"reason": reason, **update}), level=math.inf)

    if metric.judged:
        return _Pending(decision.model_copy(update={"reason": _JUDGED}))
    if metric.direction not in ("higher", "lower"):
        return undecided("the metric has no better direction")
    if form != "superiority" and margin is None:
        return undecided(f"{form} needs a margin and the design has none")

    gap = _gap(data, who, _control(data, who.cell), measure, basis)
    measured: dict[str, object] = {"n": gap.n, "difference": gap.value, "method": gap.method}
    if gap.span is None:
        return undecided(gap.problem, **measured)
    low, high = gap.span(_ALPHA)
    gain = gap.span if metric.direction == "higher" else gap.span.negated()
    limit = margin.value if margin is not None else 0.0
    candidate, level = _nearer(*_comparison_levels(form, limit, gain))
    return _Pending(
        decision.model_copy(update={**measured, "interval": Interval(low=low, high=high)}),
        candidate,
        level,
    )


def _combine_outcomes(outcomes: Sequence[Outcome]) -> Outcome:
    if any(outcome == "fails" for outcome in outcomes):
        return "fails"
    if outcomes and all(outcome == "holds" for outcome in outcomes):
        return "holds"
    return "inconclusive"


# --- the calibration check ---------------------------------------------------------------


@dataclass(frozen=True)
class _Checked:
    """What the A/A check is asked about: one measure, in one cell, on one basis."""

    cell: str
    measure: _Measure
    basis: str


def _check(data: _Data, asked: _Checked, level: float) -> CalibrationCheck:
    measure = asked.measure
    gap = _gap(
        data,
        _control(data, asked.cell, CALIBRATION),
        _control(data, asked.cell),
        measure,
        asked.basis,
    )
    check = CalibrationCheck(
        cell=data.cells[asked.cell],
        metric=measure.metric.name,
        statistic=measure.statistic,
        estimator=measure.estimator,
        unit=_UNITS.get(asked.basis, measure.metric.unit),
        n=gap.n,
        difference=gap.value,
        confidence=1 - level,
    )
    if gap.span is None:
        return check.model_copy(update={"reason": f"the check could not be made: {gap.problem}"})
    low, high = gap.span(level)
    stable = min(gap.span.above(0.0), gap.span.below(0.0)) > level
    reason = "" if stable else "the control differs from its own second sample"
    return check.model_copy(
        update={"interval": Interval(low=low, high=high), "stable": stable, "reason": reason}
    )


def _calibration(data: _Data, asked: Sequence[_Checked]) -> tuple[CalibrationCheck, ...]:
    """Every check the comparisons ask for, once each, at :data:`CHECK_LEVEL` shared out
    over them."""
    unique: dict[tuple[str, str, str, str, str], _Checked] = {}
    for item in asked:
        key = (
            item.cell,
            item.measure.metric.name,
            item.measure.statistic,
            item.measure.estimator,
            item.basis,
        )
        unique.setdefault(key, item)
    level = CHECK_LEVEL / max(1, len(unique))
    return tuple(_check(data, item, level) for item in unique.values())


def _unsettled(checks: Sequence[CalibrationCheck]) -> str:
    """Why the calibration makes the run inconclusive; empty when every check passed."""
    for check in checks:
        if check.stable is not True:
            where = cell_key(check.cell)
            return (
                f"calibration: on {check.metric} {check.statistic} in cell {where}, "
                f"{check.reason}; the environment did not hold still enough to read a "
                "difference in"
            )
    return ""


# --- the analyses ------------------------------------------------------------------------


def analysis_unsupported(analysis: AnalysisRef) -> str | None:
    """Why this ix cannot carry out ``analysis``, in words; None when it can."""
    if analysis.id not in ANALYSES:
        return f"this ix has no analysis {analysis.id}"
    if analysis.params:
        return f"analysis {analysis.id} takes no parameters, and the genre gives " + ", ".join(
            sorted(analysis.params)
        )
    return None


def _estimates(data: _Data) -> tuple[Estimate, ...]:
    """An estimate of every metric the experiment names, for every subject in every cell."""
    design = data.design
    measures: dict[tuple[str, str, str], _Measure] = {}
    decided: list[MetricRef | Guardrail] = [*([design.primary] if design.primary else [])]
    for ref in [*decided, *design.guardrails]:
        metric = design.metric(ref.metric)
        if metric is not None:
            measured = _measure("primary", ref, metric)
            measures.setdefault((metric.name, measured.statistic, measured.estimator), measured)
    for metric in design.metrics:
        if not any(name == metric.name for name, _, _ in measures):
            defaults = (STATISTICS[metric.shape][0], ESTIMATORS[metric.shape][0])
            measures[(metric.name, *defaults)] = _Measure("primary", metric, *defaults)
    found = []
    for cell in data.cells:
        for subject, role in turns(design):
            who = _SubjectInCell(subject=subject, role=role, cell=cell)
            for measure in measures.values():
                own = _own(data, who, measure)
                interval = None
                if own.span is not None:
                    low, high = own.span(_ALPHA)
                    interval = Interval(low=low, high=high)
                found.append(
                    Estimate(
                        subject=subject,
                        role=role,
                        cell=data.cells[cell],
                        metric=measure.metric.name,
                        statistic=measure.statistic,
                        estimator=measure.estimator,
                        unit=measure.metric.unit,
                        n=own.n,
                        value=own.value,
                        interval=interval,
                    )
                )
    return tuple(found)


def _compares(design: Design) -> bool:
    return design.analysis.id == "ix.v1.analysis.compare" and design.control is not None


def _form(comparison: str | None) -> Literal["superiority", "non-inferiority", "equivalence"]:
    match comparison or "superiority":
        case "superiority" | "non-inferiority" | "equivalence" as form:
            return form
    raise ConfigError(
        f"this analysis makes no comparison named {comparison!r}",
        fix="it makes: superiority, non-inferiority, equivalence",
    )


def _decided(data: _Data) -> list[_SubjectInCell]:
    """Each subject a finding is about, in each cell: every subject but the control when the
    analysis compares."""
    design = data.design
    return [
        _SubjectInCell(subject=assigned.subject, role=assigned.role, cell=cell)
        for cell in data.cells
        for assigned in design.roles
        if not (_compares(design) and assigned.role == CONTROL)
    ]


@dataclass
class _Asked:
    """What the decisions ask beyond themselves: the calibration checks."""

    checks: list[_Checked] = field(default_factory=list)


def _decisions(
    data: _Data, who: _SubjectInCell, primary: _Measure, asked: _Asked
) -> tuple[_Pending, ...]:
    """Every decision a subject's finding in one cell depends on, before the family is
    settled."""
    design = data.design
    found: list[_Pending] = []

    def compare(measure: _Measure, form: Form, margin: Margin | None) -> None:
        if design.calibration == "aa":
            asked.checks.append(_Checked(who.cell, measure, _basis(measure, margin)))
        found.append(_against_control(data, who, measure, form=form, margin=margin))

    if _compares(design):
        compare(primary, _form(design.comparison), design.margin)
    elif design.threshold is not None:
        found.append(_against_threshold(data, who, primary, design.threshold))
    for guardrail in design.guardrails:
        metric = design.metric(guardrail.metric)
        if metric is None:
            continue
        measure = _measure("guardrail", guardrail, metric)
        if guardrail.threshold is not None:
            found.append(_against_threshold(data, who, measure, guardrail.threshold))
        elif _compares(design):
            compare(measure, "non-inferiority", guardrail.margin)
    return tuple(found)


def _forced(data: _Data, who: _SubjectInCell) -> tuple[Outcome, str] | None:
    """The outcome failures force, whatever the decisions say. A control that failed a trial,
    in either of its turns, leaves nothing sound to compare with; a subject that failed one
    fails."""
    design = data.design
    if _compares(design) and design.control is not None:
        roles = (CONTROL, CALIBRATION) if design.calibration == "aa" else (CONTROL,)
        failed = carried = 0
        for role in roles:
            failed_here, carried_here = data.subject_failures(_control(data, who.cell, role))
            failed, carried = failed + failed_here, carried + carried_here
        if failed:
            return "inconclusive", f"the control failed {failed} of {carried} trial(s)"
    failed, carried = data.subject_failures(who)
    if failed:
        return "fails", f"the subject failed {failed} of {carried} trial(s)"
    return None


def analyze(
    design: Design,
    trials: Sequence[Trial],
    interactions: Sequence[Interaction],
    readings: Sequence[Reading],
) -> Analysis:
    """What the readings say about the decision ``design`` declares.

    Pure and repeatable: the same design and tables give the same analysis. Raises
    :class:`ConfigError` when the design names an analysis this ix cannot carry out.
    """
    unsupported = analysis_unsupported(design.analysis)
    if unsupported is not None:
        raise ConfigError(
            unsupported,
            fix="read the run with an ix that has it; this one has " + ", ".join(sorted(ANALYSES)),
        )
    data = _Data(design, trials, interactions, readings)
    base = Analysis(
        id=design.analysis.id, estimates=_estimates(data), failures=data.failure_counts()
    )
    if design.verdict_form == "none" or design.primary is None:
        return base
    metric = design.metric(design.primary.metric)
    if metric is None:
        return base.model_copy(
            update={"notes": (f"no sensor provides the primary {design.primary.metric}",)}
        )

    primary = _measure("primary", design.primary, metric)
    asked = _Asked()
    drafts = [
        (who, _decisions(data, who, primary, asked), _forced(data, who)) for who in _decided(data)
    ]
    settled = _settle([item for _, pending, _ in drafts for item in pending])
    findings: list[Finding] = []
    for who, pending, forced in drafts:
        decisions = tuple(settled[item] for item in pending)
        outcome, reason = forced or (_combine_outcomes([d.outcome for d in decisions]), "")
        findings.append(
            Finding(
                subject=who.subject,
                cell=data.cells[who.cell],
                outcome=outcome,
                reason=reason,
                decisions=decisions,
            )
        )
    checks = _calibration(data, asked.checks)
    unsettled = _unsettled(checks)
    return base.model_copy(
        update={
            "family": sum(1 for item in settled if item.level is not None),
            "outcome": "inconclusive"
            if unsettled
            else _combine_outcomes([finding.outcome for finding in findings]),
            "reason": unsettled,
            "findings": tuple(findings),
            "calibration": checks,
        }
    )


# --- the spread an earlier run shows -----------------------------------------------------


class Spread(Record):
    """How much the primary's per-block numbers varied in a run: what a plan needs to say
    how small an effect a run of a given size can detect."""

    run: str = ""
    metric: str
    statistic: str
    #: ``%`` of the control, ``pp`` or the metric's unit.
    unit: str
    #: The standard deviation of the per-block differences from the control (of the
    #: per-trial numbers, for one subject); the largest over variants and cells.
    sd: float
    #: The blocks it was measured over.
    n: int


def spread(
    design: Design,
    trials: Sequence[Trial],
    interactions: Sequence[Interaction],
    readings: Sequence[Reading],
    *,
    run: str = "",
) -> Spread | None:
    """The spread of the primary in a run under ``design``; None when the run has too few
    usable trials of it, or every one read the same number (a spread of 0 says nothing of
    how much the primary varies). A count's per-trial number is its rate."""
    if design.primary is None:
        return None
    metric = design.metric(design.primary.metric)
    if metric is None:
        return None
    measure = _measure("primary", design.primary, metric)
    statistic = "rate" if metric.shape == "count" else measure.statistic
    data = _Data(design, trials, interactions, readings)
    basis = _basis(measure, design.margin)
    found: list[tuple[float, int]] = []
    for who in _decided(data):
        mine = data.sample(who, metric.name, statistic)
        if _compares(design):
            theirs = data.sample(_control(data, who.cell), metric.name, statistic)
            blocks = sorted(mine.keys() & theirs.keys())
            if len(blocks) < 2:
                continue
            control_mean = _mean([theirs[block] for block in blocks])
            if basis == "relative" and control_mean == 0:
                continue
            divisor = abs(control_mean) / 100 if basis == "relative" else 1.0
            scale = 100.0 if basis == "points" else 1.0
            values = [(mine[block] - theirs[block]) * scale / divisor for block in blocks]
        else:
            values = list(mine.values())
        if len(values) >= 2:
            found.append((intervals.spread(values), len(values)))
    if not found:
        return None
    sd, n = max(found)
    if sd == 0:
        return None
    unit = _UNITS.get(basis, metric.unit) if _compares(design) else metric.unit
    return Spread(run=run, metric=metric.name, statistic=statistic, unit=unit, sd=sd, n=n)
