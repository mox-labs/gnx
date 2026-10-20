"""The plan: what a run of an experiment would do, and the decision it would inform, computed
without running anything.

A plan is a pure function of the experiment, its design, its probe set and the kinds it
names. It states the decision first (the question, the hypothesis rendered from the genre,
the guardrails, the form of the verdict), then what a run costs: the trials with their
arithmetic, the estimated duration, the effects and the spend.

**The digest.** ``digest`` identifies what a run would do. It is over the experiment's
operative digest, the design, the probe set, the effects, the trial count and the list of
trials in the order a run carries them out (``schedule_digest``). It is stable against a
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
    kind_uses,
    metric_text,
    missing_adapters,
)
from ix.domain.experiment import Level, cells
from ix.domain.genre import Calibration, Order, VerdictForm
from ix.domain.kind import EFFECTS, fill, seconds
from ix.domain.problem import Problem
from ix.domain.schema import Record, canonical

if TYPE_CHECKING:
    from ix.domain.design import Design
    from ix.domain.experiment import Experiment, ProbeSet, Spend
    from ix.domain.genre import Genre
    from ix.domain.kind import Kinds

#: The share of runs in which a true effect of the detectable size is shown.
POWER = 0.8

_ALPHA = round(1 - CONFIDENCE, 12)

VERDICTS: dict[str, str] = {
    "three-way": "holds / fails / inconclusive",
    "none": "none: estimates only",
}


class OrderPlan(Record):
    """How a run orders its trials. The seed fixes the order it produces."""

    scheme: Order
    seed: int
    description: str


class Effect(Record):
    """Something a run would do to the world, and what declares it: the kind an entry names,
    or the author on top of that."""

    effect: str
    detail: str = ""
    #: The entry in the experiment this effect belongs to (``subjects.buffered``).
    source: str
    declared_by: Literal["kind", "experiment"]


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


class SpendEstimate(Record):
    """What a run is expected to spend, the ceiling it is held to, and where each came from.

    ``estimated`` is what the trials of the latest finished run of the experiment cost on
    average times the trials (``source: measured``), or, before any run reported a cost, the
    experiment's ``per_trial`` times the trials (``declared``): a cost measured on this
    experiment is better evidence than a guess made before it ran. ``ceiling_source`` is
    ``experiment`` (its ``spend.ceiling``) or ``settings`` (ix's ``spend_cap``, for a run
    that spends and whose experiment sets none).
    """

    estimated: float | None
    ceiling: float | None
    currency: str
    basis: str
    ceiling_source: Literal["experiment", "settings", ""] = ""
    #: What ``estimated`` rests on: ``measured`` (an earlier run), ``declared``
    #: (``spend.per_trial``), or nothing.
    source: Literal["measured", "declared", ""] = ""


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
    effects: tuple[Effect, ...]
    spend: SpendEstimate
    registration: RegistrationStatus = RegistrationStatus()
    #: The smallest effect a run of this plan can detect, when an earlier run says how much
    #: the primary varies. Not part of the digest: it changes with the runs, not the plan.
    sensitivity: Sensitivity = Sensitivity()
    #: Every design value and where it came from.
    provenance: tuple[SourcedValue, ...]
    #: Kinds the experiment uses that this ix has no adapter for: it can be checked, planned
    #: and registered, and ``ix run`` would refuse it.
    unimplemented: tuple[str, ...] = ()
    #: Subjects this host cannot start as written (``subjects.<name>: <why>``): they depend
    #: on the host, so registration is not refused, and ``ix run`` here would refuse them.
    unstartable: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    #: What ``validate`` warns of (subjects that differ in more than one thing, a kind with
    #: no adapter here, a refused genre or kind file): problems that do not stop the plan.
    warnings: tuple[Problem, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def runnable(self) -> bool:
        """Whether ``ix run`` on this host would start: no kind without an adapter and no
        subject it cannot start. False is not an error; ``ix plan`` still exits 0."""
        return not self.unimplemented and not self.unstartable

    @property
    def verdict(self) -> str:
        """The form of the verdict, in words."""
        return VERDICTS[self.verdict_form]


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


def estimate_spend(
    spend: Spend | None,
    trials: int,
    *,
    spend_cap: float | None = None,
    observed: tuple[float, str] | None = None,
) -> SpendEstimate:
    """The estimated cost of ``trials`` trials: from ``observed``, the mean cost of a trial
    in an earlier run and that run's id, or else from the author's per-trial figure. The
    ceiling is the experiment's, or ``spend_cap`` when it sets none."""
    currency = spend.currency if spend is not None else "USD"
    ceiling = spend.ceiling if spend is not None else None
    source: Literal["experiment", "settings", ""] = "experiment" if ceiling is not None else ""
    if ceiling is None and spend_cap is not None:
        ceiling, source = spend_cap, "settings"
    per_trial = spend.per_trial if spend is not None else None
    estimated: float | None = None
    rests_on: Literal["measured", "declared", ""] = ""
    if observed is not None:
        mean, run_id = observed
        estimated, rests_on = round(mean * trials, 6), "measured"
        basis = f"{trials} trials x {mean:.4g}, the mean cost of a trial measured in run {run_id}"
        if per_trial is not None:
            basis += f"; the declared per_trial of {per_trial:g} is set aside"
    elif per_trial is not None:
        estimated, rests_on = round(per_trial * trials, 6), "declared"
        basis = (
            f"{trials} trials x {per_trial:g} declared per trial, a guess until a run measures "
            "it: pilot with `ix run --exploratory` and plan again"
        )
    else:
        basis = "no per_trial cost declared and no earlier run that reported one"
    return SpendEstimate(
        estimated=estimated, ceiling=ceiling, currency=currency, basis=basis,
        ceiling_source=source, source=rests_on,
    )  # fmt: skip


def effects(
    experiment: Experiment, design: Design, kinds: Kinds, probes: ProbeSet | None
) -> tuple[Effect, ...]:
    """What a run would do to the world: the effects of each kind in use, then what the author
    added to them. The author cannot remove one."""
    found: list[Effect] = []
    used, _ = kind_uses(experiment, kinds, probes)
    for use in used:
        found += [
            Effect(effect=effect, detail=EFFECTS[effect], source=use.path, declared_by="kind")
            for effect in use.kind.effects
        ]
        found += [
            Effect(effect=text, source=use.path, declared_by="experiment") for text in use.effects
        ]
    for sensor in design.sensors:
        selected = kinds.kinds.get(sensor.kind)
        if sensor.source == "ix" and selected is not None:
            found += [
                Effect(
                    effect=effect,
                    detail=EFFECTS[effect],
                    source=f"sensor {selected.label}",
                    declared_by="kind",
                )
                for effect in selected.effects
            ]
    unique: dict[tuple[str, str], Effect] = {}
    for effect in found:
        unique.setdefault((effect.effect, effect.source), effect)
    return tuple(unique.values())


def estimate_duration(
    experiment: Experiment, design: Design, kinds: Kinds, probes: ProbeSet
) -> Duration:
    """Probe time over the whole run, from the durations probe kinds declare."""
    shared = [set(subject.options) for subject in experiment.subjects.values()]
    common = set.intersection(*shared) if shared else set()
    first = next(iter(experiment.subjects.values()))
    passes = len(design.roles) + (1 if design.calibration == "aa" and design.control else 0)
    total = 0.0
    for probe in probes.probes:
        kind = kinds.get("probe", probe.kind)
        if kind is None or not kind.duration:
            return Duration(seconds=None, basis=f"probe kind {probe.kind} declares no duration")
        for cell in cells(experiment.factors):
            bound: dict[str, Any] = {name: first.options[name] for name in common} | cell
            for option in kind.duration:
                if option not in probe.options:
                    continue
                length = seconds(fill(probe.options[option], bound))
                if length is None:
                    return Duration(
                        seconds=None,
                        basis=f"{probe.id}: {option} is not a duration once filled in",
                    )
                total += length * design.repeats * passes
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
    return OrderPlan(scheme=design.order, seed=design.seed, description=description)


def plan_digest(
    experiment: Experiment,
    design: Design,
    probes: ProbeSet,
    plan_effects: tuple[Effect, ...],
    trials: int,
    schedule_digest: str,
) -> str:
    """The digest of what a run would do; see the module docstring for what it is stable
    against."""
    resolved = canonical(design, exclude={"hypotheses", "provenance"})
    for metric in resolved.get("metrics", []):
        metric.pop("description", None)
    return schema.digest(
        {
            "experiment": experiment.digest(operative=True),
            "design": resolved,
            "probes": probes.digest,
            "effects": sorted([effect.effect, effect.source] for effect in plan_effects),
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
    spend_cap: float | None = None,
    observed_cost: tuple[float, str] | None = None,
) -> Plan:
    """The plan of a valid experiment. Runs nothing and reads nothing.

    ``adapters`` is the kinds (type URLs) a run could carry out here; None when the caller
    has no run path to ask.
    """
    trials = count_trials(experiment, design, len(probes.probes))
    plan_effects = effects(experiment, design, kinds, probes)
    alternating = len(design.roles) + (1 if trials.calibration else 0)
    schedule_digest = run.schedule_digest(run.schedule(experiment, design, probes))
    missing = () if adapters is None else missing_adapters(experiment, design, probes, adapters)
    return Plan(
        digest=plan_digest(experiment, design, probes, plan_effects, trials.total, schedule_digest),
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
        effects=plan_effects,
        spend=estimate_spend(
            experiment.spend, trials.total, spend_cap=spend_cap, observed=observed_cost
        ),
        registration=registration or RegistrationStatus(),
        provenance=design.provenance,
        unimplemented=tuple(
            f"{schema.family_of(kind) or 'kind'} kind {schema.short(kind)} ({where})"
            for where, kind in missing
        ),
        notes=(genre.design.note,),
        sensitivity=sensitivity(design, design.repeats * len(probes.probes), spread),
    )
