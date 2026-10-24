"""What a report says beside its verdict: confusion tables, flags and the probe screen.
Each is a pure function of the design and a run's tables, so a report computed again
from the record says the same.

* **Confusion.** A sensor whose kind declares a :class:`~ix.domain.kind.Confusion` names, in
  each reading's detail, what was expected and what it observed (the skill that should fire,
  the one that did). The table counts expected against observed for each subject, with
  precision and recall for each label and overall, the label that means none being the
  negative class.
* **Flags.** A boolean metric whose declaration carries a ``flag`` is a warning: when any
  trial reads true, the report prints the flag's sentence and how many trials raised it.
* **The screen.** Before a design is scaled up, the probes that cannot discriminate are
  the ones to fix: per probe, on the primary metric, ``floor`` when every reading sits at the
  lowest value the metric can take, ``ceiling`` at the highest, ``noisy`` when the readings
  of one subject on it spread by more than 0.35 of that range, and ``failed`` when no trial
  of it gave a reading. A metric with no
  range (a latency) is screened only for ``failed``.
"""

from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING, Literal

from ix.domain import schema
from ix.domain.run import CALIBRATION
from ix.domain.schema import Record

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ix.domain.design import Design
    from ix.domain.kind import Metric
    from ix.domain.run import Reading, Trial

#: The spread of one subject's readings on one probe, as a share of the metric's range,
#: above which the probe is ``noisy``.
NOISY = 0.35


class LabelScore(Record):
    """Precision and recall for one label: of the trials that observed it, the share that
    expected it; of those that expected it, the share that observed it."""

    label: str
    expected: int
    observed: int
    precision: float | None = None
    recall: float | None = None


class ConfusionTable(Record):
    """Expected against observed for one subject, as one sensor read them."""

    sensor: str
    subject: str
    negative: str
    #: ``counts[expected][observed]``: how many trials.
    counts: dict[str, dict[str, int]]
    labels: tuple[LabelScore, ...] = ()
    #: The negative label against the rest: a trial that expected a label and observed it
    #: is a true positive; one that observed a label it did not expect a false positive;
    #: one that expected a label and observed another, or none, a false negative.
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0
    precision: float | None = None
    recall: float | None = None


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _measured(readings: Sequence[Reading]) -> list[Reading]:
    """The readings of the subjects as measured: the A/A pass is a check, not a result."""
    return [reading for reading in readings if reading.role != CALIBRATION]


def confusion(design: Design, readings: Sequence[Reading]) -> tuple[ConfusionTable, ...]:
    """A table per subject for each sensor whose kind declares a confusion: the subjects in
    the order their first trial ran, each table's counts in the order of its trials. Read in
    ``seq`` order, so the order the readings were landed in (which trials in flight make the
    order they finished in) changes nothing."""
    tables: list[ConfusionTable] = []
    in_order = sorted(_measured(readings), key=lambda reading: reading.seq)
    for sensor in design.sensors:
        declared = sensor.confusion
        if declared is None:
            continue
        seen: dict[str, dict[int, tuple[str, str]]] = {}
        for reading in in_order:
            expected = reading.detail.get(declared.expected)
            observed = reading.detail.get(declared.observed)
            if reading.sensor != sensor.kind or expected is None or observed is None:
                continue
            seen.setdefault(reading.subject, {})[reading.seq] = (str(expected), str(observed))
        for subject, pairs in seen.items():
            tables.append(_table(schema.short(sensor.kind), subject, declared.negative, pairs))
    return tuple(tables)


def _table(
    sensor: str, subject: str, negative: str, pairs: dict[int, tuple[str, str]]
) -> ConfusionTable:
    counts: dict[str, dict[str, int]] = {}
    for expected, observed in pairs.values():
        row = counts.setdefault(expected, {})
        row[observed] = row.get(observed, 0) + 1
    names = sorted({name for pair in pairs.values() for name in pair} - {negative})
    labels = []
    for name in names:
        hit = counts.get(name, {}).get(name, 0)
        wanted = sum(counts.get(name, {}).values())
        seen = sum(row.get(name, 0) for row in counts.values())
        labels.append(
            LabelScore(
                label=name,
                expected=wanted,
                observed=seen,
                precision=_share(hit, seen),
                recall=_share(hit, wanted),
            )
        )
    tp = sum(1 for e, o in pairs.values() if e != negative and o == e)
    fp = sum(1 for e, o in pairs.values() if o != negative and o != e)
    fn = sum(1 for e, o in pairs.values() if e != negative and o != e)
    tn = sum(1 for e, o in pairs.values() if e == negative and o == negative)
    return ConfusionTable(
        sensor=sensor,
        subject=subject,
        negative=negative,
        counts=counts,
        labels=tuple(labels),
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        true_negative=tn,
        precision=_share(tp, tp + fp),
        recall=_share(tp, tp + fn),
    )


def flags(design: Design, readings: Sequence[Reading]) -> tuple[str, ...]:
    """The sentence of each flagged metric any trial raised, with how many did."""
    raised: list[str] = []
    for metric in design.metrics:
        if not metric.flag:
            continue
        read = [r for r in _measured(readings) if r.metric == metric.name and r.failure is None]
        count = sum(1 for reading in read if reading.value is True)
        if count:
            raised.append(f"{metric.name}: {metric.flag} ({count} of {len(read)} trials)")
    return tuple(raised)


Screen = Literal["ok", "floor", "ceiling", "noisy", "failed"]


class ProbeScreen(Record):
    """Whether one probe can discriminate, on the primary metric, over a run's trials."""

    probe: str
    metric: str
    #: Usable readings, of every subject.
    n: int
    mean: float | None = None
    #: The largest spread of one subject's readings on the probe.
    sd: float | None = None
    screen: Screen = "ok"


def _range(metric: Metric) -> tuple[float, float] | None:
    if metric.shape in ("boolean", "count"):
        return 0.0, 1.0
    return metric.bounds


def screen(
    design: Design, trials: Sequence[Trial], readings: Sequence[Reading]
) -> tuple[ProbeScreen, ...]:
    """The screen of every probe on the primary metric; empty without a primary."""
    primary = design.primary
    metric = design.metric(primary.metric) if primary is not None else None
    if primary is None or metric is None:
        return ()
    statistic = primary.statistic or "value"
    probe_of = {trial.seq: trial.probe for trial in trials if trial.role != CALIBRATION}
    numbers: dict[str, dict[str, list[float]]] = {probe: {} for probe in probe_of.values()}
    for reading in _measured(readings):
        if reading.metric != metric.name or reading.seq not in probe_of:
            continue
        number = reading.number(statistic)
        if number is not None and math.isfinite(number):
            subjects = numbers[probe_of[reading.seq]]
            subjects.setdefault(reading.subject, []).append(number)
    span = _range(metric)
    found = []
    for probe, by_subject in numbers.items():
        values = [value for read in by_subject.values() for value in read]
        if not values:
            found.append(ProbeScreen(probe=probe, metric=metric.name, n=0, screen="failed"))
            continue
        spread = max(
            (statistics.stdev(read) for read in by_subject.values() if len(read) > 1),
            default=0.0,
        )
        verdict: Screen = "ok"
        if span is not None:
            low, high = span
            if all(math.isclose(value, low) for value in values):
                verdict = "floor"
            elif all(math.isclose(value, high) for value in values):
                verdict = "ceiling"
            elif spread > NOISY * (high - low):
                verdict = "noisy"
        found.append(
            ProbeScreen(
                probe=probe,
                metric=metric.name,
                n=len(values),
                mean=statistics.fmean(values),
                sd=spread,
                screen=verdict,
            )
        )
    return tuple(found)
