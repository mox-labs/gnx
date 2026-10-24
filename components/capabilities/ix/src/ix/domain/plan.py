"""The plan: what a run of an experiment would do, and the decision it would inform, computed
without running anything.

A plan is a pure function of the experiment, its design, its probe set and the kinds it
names. It states the decision first (the question, the hypothesis rendered from the genre,
the guardrails, the form of the verdict), then what a run takes: the trials with their
arithmetic and the estimated duration. What the run needs of this host, and whether each
need holds here, is added by the caller that can ask the adapters (``requirements``): it
depends on the host, not the experiment, so it is in no digest.

**The digest.** ``digest`` identifies what a run would do. It is over the experiment's
operative digest, the design, the probe set, the trial count and the list of trials in the
order a run carries them out (``schedule_digest``). It is stable against a
comment, key order, a default written out, a reworded question, note or description, and
a field a later ix adds with a default. It changes when a value a run
would use changes, including one the genre supplied.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, computed_field

from ix.domain import intervals, run, schema
from ix.domain.analysis import CONFIDENCE, Spread
from ix.domain.design import (
    RoleAssignment,
    SelectedSensor,
    SourcedValue,
    metric_text,
    missing_adapters,
)
from ix.domain.experiment import Level, cells
from ix.domain.genre import Calibration, Order, VerdictForm
from ix.domain.kind import UNFROZEN_METRIC_FIELDS, fill, seconds
from ix.domain.problem import Problem
from ix.domain.schema import Record, canonical

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ix.domain.design import Design
    from ix.domain.experiment import Experiment, ProbeSet
    from ix.domain.genre import Genre
    from ix.domain.kind import Kinds
    from ix.domain.ports import Requirement

#: The share of runs in which a true effect of the detectable size is shown.
POWER = 0.8

_ALPHA = round(1 - CONFIDENCE, 12)

VERDICTS: dict[str, str] = {
    "three-way": "holds / fails / inconclusive",
    "none": "none: estimates only",
}


class OrderPlan(Record):
    """How a run orders its trials, and how many it carries out at once. The seed fixes the
    order it produces; trials start in that order, and at a width above one they finish in
    whatever order they finish in."""

    scheme: Order
    seed: int
    description: str
    trials_in_flight: int = 1


class Required(Record):
    """Something a run needs of this host, whether it holds here, and the entries that need
    it: what an adapter's preflight said, the same need from several entries said once."""

    need: str
    holds: bool
    #: What was found, when it does not hold.
    why: str = ""
    #: How to make it hold, when it does not.
    fix: str = ""
    #: The entries that need it: ``subjects.buffered``, ``probes.0``, ``sensors.1``,
    #: ``environment``.
    needed_by: tuple[str, ...]

    def text(self) -> str:
        """The need in one line, with why it does not hold and its fix when it does not."""
        said = f"{', '.join(self.needed_by)}: {self.need}"
        if self.holds:
            return said
        return said + "".join(f"; {part}" for part in (self.why, self.fix) if part)


class PlannedProbes(Record):
    count: int
    digest: str
    ids: tuple[str, ...]


class Trials(Record):
    """The trial count, with the arithmetic that gives it."""

    subjects: int
    cells: int
    probes: int
    repeats: int
    #: Trials of the subjects: subjects x cells x probes x repeats.
    measured: int
    #: Trials of the A/A pass: the control once more, in every cell, probe and repeat.
    calibration: int
    total: int

    def text(self) -> str:
        def n(count: int, noun: str) -> str:
            return f"{count} {noun}{'' if count == 1 else 's'}"

        parts = (
            f"{n(self.subjects, 'subject')} x {n(self.cells, 'cell')} x "
            f"{n(self.probes, 'probe')} x {n(self.repeats, 'repeat')} = {self.measured}"
        )
        if self.calibration:
            parts += f", + {self.calibration} A/A = {self.total}"
        return parts


class Duration(Record):
    """How long the probes alone would take, when every probe kind declares its duration."""

    seconds: float | None
    basis: str

    def text(self) -> str:
        if self.seconds is None:
            return f"not estimated ({self.basis})"
        minutes = round(self.seconds / 60)
        human = (
            f"{self.seconds:g}s"
            if self.seconds < 90
            else f"{minutes}m"
            if minutes < 90
            else f"{minutes // 60}h {minutes % 60:02d}m"
        )
        return f"about {human} ({self.basis})"


class RegistrationStatus(Record):
    """Where the experiment stands against its registration."""

    status: Literal["none", "matches", "deviations"] = "none"
    deviations: int = 0
    location: str = ""

    def text(self) -> str:
        if self.status == "deviations":
            return f"{self.deviations} deviation{'' if self.deviations == 1 else 's'}"
        return self.status


class Sensitivity(Record):
    """How small an effect a run of this size can detect, from the spread an earlier run of
    the experiment showed; or why that is not known.

    ``detectable`` is the true difference (the true value's distance beyond the threshold,
    for a threshold) that the run shows ``power`` of the time: ``(t(0.975) + t(power)) *
    sd / sqrt(n)`` with ``n`` the blocks per cell and ``n - 1`` degrees of freedom. A normal
    approximation for a rate.
    """

    detectable: float | None = None
    #: ``%`` of the control, ``pp`` or the metric's unit.
    unit: str = ""
    power: float = POWER
    #: Blocks per cell a run of this plan has: repeats x probes.
    blocks: int = 0
    #: The run whose spread it rests on; empty when none did.
    source: str = ""
    #: The whole of it in one sentence.
    text: str = ""


class Plan(Record):
    """What a run would do. ``digest`` identifies it; see the module docstring."""

    schema_: Literal["ix.v1.plan"] = Field(default=schema.PLAN, alias="schema")
    digest: str
    experiment: str
    experiment_digest: str
    #: The genre as the author writes it, and its type URL.
    genre: str
    genre_url: str

    question: str
    #: The hypothesis the verdict is about: one sentence per variant, rendered from the genre.
    decides: tuple[str, ...]
    comparison: str | None
    guardrails: tuple[str, ...]
    verdict_form: VerdictForm

    subjects: tuple[RoleAssignment, ...]
    control: str | None
    factors: dict[str, tuple[Level, ...]]
    probes: PlannedProbes
    calibration: Calibration
    trials: Trials
    #: The digest of the list of trials, in order. A run of this plan records the same one.
    schedule_digest: str = ""
    duration: Duration
    order: OrderPlan
    sensors: tuple[SelectedSensor, ...]
    #: What a run needs of this host, each with whether it holds here now. Not part of the
    #: digest: it changes with the host, not the plan.
    requirements: tuple[Required, ...] = ()
    registration: RegistrationStatus = RegistrationStatus()
    #: The smallest effect a run of this plan can detect, when an earlier run says how much
    #: the primary varies. Not part of the digest: it changes with the runs, not the plan.
    sensitivity: Sensitivity = Sensitivity()
    #: Every design value and where it came from.
    provenance: tuple[SourcedValue, ...]
    #: Kinds the experiment uses that this ix has no adapter for, or whose adapter cannot be
    #: built here (with why): it can be checked, planned and registered, and ``ix run`` would
    #: refuse it.
    unimplemented: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    #: What the caller changed from the experiment as written for this plan (``ix plan
    #: --trials-in-flight N``), worded as the run that tries it reports it
    #: (``Report.overridden``). Such a run is exploratory or dry: one that gives a verdict
    #: refuses the change.
    overridden: tuple[str, ...] = ()
    #: What ``validate`` warns of (subjects that differ in more than one thing, a kind with
    #: no adapter here, a refused genre or kind file): problems that do not stop the plan.
    warnings: tuple[Problem, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def runnable(self) -> bool:
        """Whether ``ix run`` on this host would start: no kind without an adapter that
        builds, and every requirement holds. False is not an error; ``ix plan`` still exits
        0."""
        return not self.unimplemented and not self.unmet

    @property
    def unmet(self) -> tuple[Required, ...]:
        """The requirements that do not hold here: they depend on the host, so registration
        is not refused, and ``ix run`` here would refuse them."""
        return tuple(required for required in self.requirements if not required.holds)

    @property
    def verdict(self) -> str:
        """The form of the verdict, in words."""
        return VERDICTS[self.verdict_form]


def required(found: Iterable[tuple[str, Requirement]]) -> tuple[Required, ...]:
    """Each requirement an adapter's preflight gave, as (the entry it was given for, it), said
    once with every entry that needs it, in the order first needed."""
    merged: dict[Requirement, list[str]] = {}
    for where, requirement in found:
        entries = merged.setdefault(requirement, [])
        if where not in entries:
            entries.append(where)
    return tuple(
        Required(
            need=requirement.need,
            holds=requirement.holds,
            why=requirement.why,
            fix=requirement.fix,
            needed_by=tuple(entries),
        )
        for requirement, entries in merged.items()
    )


def count_trials(experiment: Experiment, design: Design, probes: int) -> Trials:
    """The trials of one run."""
    n_cells = len(cells(experiment.factors))
    per_subject = n_cells * probes * design.repeats
    measured = per_subject * len(design.roles)
    calibration = per_subject if design.calibration == "aa" and design.control else 0
    return Trials(
        subjects=len(design.roles),
        cells=n_cells,
        probes=probes,
        repeats=design.repeats,
        measured=measured,
        calibration=calibration,
        total=measured + calibration,
    )


def estimate_duration(
    experiment: Experiment, design: Design, kinds: Kinds, probes: ProbeSet
) -> Duration:
    """Probe time over the whole run, from the durations probe kinds declare. A probe's
    options are filled as each trial fills them: with the cell's levels and the options of
    the subject whose turn it is."""
    total = 0.0
    for probe in probes.probes:
        kind = kinds.get("probe", probe.kind)
        if kind is None or not kind.duration:
            return Duration(seconds=None, basis=f"probe kind {probe.kind} declares no duration")
        for cell in cells(experiment.factors):
            for subject, _ in run.turns(design):
                options = experiment.subjects[subject].options
                bound: dict[str, Any] = {**fill(options, cell), **cell}
                for option in kind.duration:
                    if option not in probe.options:
                        continue
                    length = seconds(fill(probe.options[option], bound))
                    if length is None:
                        return Duration(
                            seconds=None,
                            basis=f"{probe.id}: {option} is not a duration once filled in",
                        )
                    total += length * design.repeats
    trials = count_trials(experiment, design, len(probes.probes)).total
    return Duration(
        seconds=total, basis=f"{trials} trials, probe time only; setup and teardown add to it"
    )


def _guardrail_text(design: Design) -> tuple[str, ...]:
    lines = []
    for guardrail in design.guardrails:
        if guardrail.threshold is not None:
            lines.append(f"{metric_text(guardrail)} {guardrail.threshold.text()}")
        elif guardrail.margin is not None:
            lines.append(
                f"{metric_text(guardrail)} at most {guardrail.margin.text()} worse than "
                f"{design.control or 'the control'}"
            )
    return tuple(lines)


def _order(design: Design, alternating: int) -> OrderPlan:
    if design.order == "sequential":
        description = "trials run in the order written: repeats, then cells, probes, subjects"
    elif alternating > 1:
        description = (
            "within each repeat, cell and probe, the subjects run in a random order drawn "
            "from the seed"
        )
    else:
        description = "one subject and no A/A pass: there is nothing to interleave"
    width = design.trials_in_flight
    description += (
        "; one at a time"
        if width == 1
        else f"; {width} trials in flight, each started in that order as another finishes"
    )
    return OrderPlan(
        scheme=design.order, seed=design.seed, description=description, trials_in_flight=width
    )


def plan_digest(
    experiment: Experiment,
    design: Design,
    probes: ProbeSet,
    trials: int,
    schedule_digest: str,
) -> str:
    """The digest of what a run would do; see the module docstring for what it is stable
    against."""
    resolved = canonical(design, exclude={"hypotheses", "provenance"})
    for metric in resolved.get("metrics", []):
        for field in UNFROZEN_METRIC_FIELDS:
            metric.pop(field, None)
    return schema.digest(
        {
            "experiment": experiment.digest(operative=True),
            "design": resolved,
            "probes": probes.digest,
            "trials": trials,
            "schedule": schedule_digest,
        }
    )


def _number(value: float, unit: str) -> str:
    return f"{value:.3g}{unit if unit in ('%', 'pp') else ' ' + unit if unit else ''}"


def best_case(design: Design, blocks: int) -> str:
    """For a rate held to a threshold: what the run shows if every trial goes the right way,
    and the fewest blocks that could show the threshold; empty when that suffices."""
    threshold, primary = design.threshold, design.primary
    metric = design.metric(primary.metric) if primary else None
    if threshold is None or metric is None or metric.shape != "boolean" or blocks < 1:
        return ""
    upward = threshold.op in (">", ">=")

    def shown(n: int) -> float:
        low, high = intervals.wilson(n if upward else 0, n)(_ALPHA)
        return low if upward else high

    def holds(n: int) -> bool:
        return shown(n) > threshold.value if upward else shown(n) < threshold.value

    if holds(blocks):
        return ""
    needed = next((n for n in range(blocks + 1, 100_000) if holds(n)), None)
    if needed is None:
        return (
            f"This run cannot hold: no number of trials makes a rate's interval lie "
            f"{threshold.text()}."
        )
    return (
        f"This run cannot hold: with {blocks} trials per cell, even every trial reading "
        f"{'true' if upward else 'false'} shows only "
        f"{'>=' if upward else '<='} {shown(blocks):.3g}; it takes {needed} trials per cell "
        f"(repeats x probes) to show {threshold.text()}."
    )


def sensitivity(design: Design, blocks: int, spread: Spread | None) -> Sensitivity:
    """How small an effect a run of ``blocks`` blocks per cell can detect, from ``spread``."""
    if design.verdict_form == "none":
        return Sensitivity(blocks=blocks, text="not applicable: the genre gives estimates only")
    best = best_case(design, blocks)
    if spread is None or blocks < 2:
        unknown = (
            "unknown: no earlier run of this experiment shows how much the primary varies; "
            "run it once with `ix run --exploratory` and plan again"
        )
        return Sensitivity(blocks=blocks, text=" ".join(filter(None, (best, unknown))))
    df = blocks - 1
    detectable = (
        (intervals.t_quantile(1 - _ALPHA / 2, df) + intervals.t_quantile(POWER, df))
        * spread.sd
        / math.sqrt(blocks)
    )
    size, chance = _number(detectable, spread.unit), f"{POWER:.0%} of the time"
    margin = design.margin.value if design.margin else 0.0
    match design.comparison if design.control else None:
        case "superiority":
            found = f"shows a true difference of {size} {chance}"
        case "non-inferiority":
            found = (
                f"holds {chance} when the true difference is at least {size} better than "
                f"-{_number(margin, spread.unit)}"
            )
        case "equivalence" if margin > detectable:
            found = (
                f"holds {chance} when the true difference is within "
                f"{_number(margin - detectable, spread.unit)} of zero"
            )
        case "equivalence":
            found = f"rarely holds: the margin is inside the {size} it can detect"
        case _:
            found = f"holds {chance} when the true value is at least {size} past the threshold"
    source = f"from the spread in run {spread.run}" if spread.run else "from an earlier run"
    text = f"with {blocks} blocks per cell it {found} ({source}, {spread.n} blocks)."
    return Sensitivity(
        detectable=detectable,
        unit=spread.unit,
        blocks=blocks,
        source=spread.run,
        text=" ".join(filter(None, (best, text[0].upper() + text[1:]))),
    )


def plan(
    experiment: Experiment,
    genre: Genre,
    design: Design,
    probes: ProbeSet,
    kinds: Kinds,
    *,
    registration: RegistrationStatus | None = None,
    adapters: frozenset[str] | None = None,
    spread: Spread | None = None,
) -> Plan:
    """The plan of a valid experiment. Runs nothing and reads nothing.

    ``adapters`` is the kinds (type URLs) a run could carry out here; None when the caller
    has no run path to ask.
    """
    trials = count_trials(experiment, design, len(probes.probes))
    alternating = len(design.roles) + (1 if trials.calibration else 0)
    schedule_digest = run.schedule_digest(run.schedule(experiment, design, probes))
    missing = () if adapters is None else missing_adapters(experiment, design, probes, adapters)
    return Plan(
        digest=plan_digest(experiment, design, probes, trials.total, schedule_digest),
        experiment=experiment.name,
        experiment_digest=experiment.digest(),
        genre=schema.short(genre.name),
        genre_url=genre.name,
        question=experiment.question,
        decides=design.hypotheses,
        comparison=design.comparison,
        guardrails=_guardrail_text(design),
        verdict_form=design.verdict_form,
        subjects=design.roles,
        control=design.control,
        factors=experiment.factors,
        probes=PlannedProbes(count=len(probes.probes), digest=probes.digest, ids=probes.ids),
        calibration=design.calibration if trials.calibration else "none",
        trials=trials,
        schedule_digest=schedule_digest,
        duration=estimate_duration(experiment, design, kinds, probes),
        order=_order(design, alternating),
        sensors=design.sensors,
        registration=registration or RegistrationStatus(),
        provenance=design.provenance,
        unimplemented=tuple(
            f"{schema.family_of(kind) or 'kind'} kind {schema.short(kind)} ({where})"
            + (
                f", only one that cannot be built: {kinds.unbuildable[kind]}"
                if kind in kinds.unbuildable
                else ""
            )
            for where, kind in missing
        ),
        notes=(genre.design.note,),
        sensitivity=sensitivity(design, design.repeats * len(probes.probes), spread),
    )
