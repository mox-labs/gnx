"""The plan: what a run of an experiment would do, and the decision it would inform, computed
without running anything.

A plan is a pure function of the experiment, its design, its probe set and the kinds it
names. It states the decision first (the question, the hypothesis rendered from the genre,
the guardrails, the form of the verdict), then what a run costs: the trials with their
arithmetic, the estimated duration, the effects and the spend.

**The digest.** ``digest`` identifies what a run would do. It is over the experiment's
operative digest, the design, the probe set, the effects and the trial count. It is stable
against a comment, key order, a default written out, a reworded question, note or
description, and a field a later ix adds with a default. It changes when a value a run
would use changes, including one the genre supplied.
"""

from __future__ import annotations

from itertools import product
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ix.domain import schema
from ix.domain.design import RoleAssignment, SelectedSensor, SourcedValue, kind_uses, metric_text
from ix.domain.experiment import Level
from ix.domain.genre import Calibration, Order, VerdictForm
from ix.domain.kind import EFFECTS, fill, seconds
from ix.domain.schema import Record, canonical

if TYPE_CHECKING:
    from ix.domain.design import Design
    from ix.domain.experiment import Experiment, ProbeSet, Spend
    from ix.domain.genre import Genre
    from ix.domain.kind import Kinds

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
    estimated: float | None
    ceiling: float | None
    currency: str
    basis: str


class RegistrationStatus(Record):
    """Where the experiment stands against its registration."""

    status: Literal["none", "matches", "deviations"] = "none"
    deviations: int = 0
    location: str = ""

    def text(self) -> str:
        if self.status == "deviations":
            return f"{self.deviations} deviation{'' if self.deviations == 1 else 's'}"
        return self.status


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
    duration: Duration
    order: OrderPlan
    sensors: tuple[SelectedSensor, ...]
    effects: tuple[Effect, ...]
    spend: SpendEstimate
    registration: RegistrationStatus = RegistrationStatus()
    #: Every design value and where it came from.
    provenance: tuple[SourcedValue, ...]
    notes: tuple[str, ...] = ()

    @property
    def verdict(self) -> str:
        """The form of the verdict, in words."""
        return VERDICTS[self.verdict_form]


def cells(factors: dict[str, tuple[Level, ...]]) -> tuple[dict[str, Level], ...]:
    """Every combination of factor levels, the first factor varying slowest. No factors:
    one cell with no levels."""
    names = list(factors)
    return tuple(
        dict(zip(names, levels, strict=True))
        for levels in product(*(factors[name] for name in names))
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


def estimate_spend(spend: Spend | None, trials: int) -> SpendEstimate:
    """The estimated cost of ``trials`` trials from the author's per-trial figure."""
    if spend is None:
        return SpendEstimate(
            estimated=None, ceiling=None, currency="USD", basis="no spend declared"
        )
    if spend.per_trial is None:
        return SpendEstimate(
            estimated=None,
            ceiling=spend.ceiling,
            currency=spend.currency,
            basis="no per_trial cost declared",
        )
    return SpendEstimate(
        estimated=round(spend.per_trial * trials, 6),
        ceiling=spend.ceiling,
        currency=spend.currency,
        basis=f"{trials} trials x {spend.per_trial:g} declared per trial",
    )


def effects(
    experiment: Experiment, design: Design, kinds: Kinds, probes: ProbeSet | None
) -> tuple[Effect, ...]:
    """What a run would do to the world: the effects of each kind in use, then what the author
    added to them. The author cannot remove one."""
    found: list[Effect] = []
    used, _ = kind_uses(experiment, kinds, probes)
    for path, kind, _, added in used:
        found += [
            Effect(effect=effect, detail=EFFECTS[effect], source=path, declared_by="kind")
            for effect in kind.effects
        ]
        found += [Effect(effect=text, source=path, declared_by="experiment") for text in added]
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
                f"{metric_text(guardrail)} no worse than {design.control or 'the control'} by "
                f"more than {guardrail.margin.text()}"
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
        }
    )


def plan(
    experiment: Experiment,
    genre: Genre,
    design: Design,
    probes: ProbeSet,
    kinds: Kinds,
    *,
    registration: RegistrationStatus | None = None,
) -> Plan:
    """The plan of a valid experiment. Runs nothing and reads nothing."""
    trials = count_trials(experiment, design, len(probes.probes))
    plan_effects = effects(experiment, design, kinds, probes)
    alternating = len(design.roles) + (1 if trials.calibration else 0)
    return Plan(
        digest=plan_digest(experiment, design, probes, plan_effects, trials.total),
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
        duration=estimate_duration(experiment, design, kinds, probes),
        order=_order(design, alternating),
        sensors=design.sensors,
        effects=plan_effects,
        spend=estimate_spend(experiment.spend, trials.total),
        registration=registration or RegistrationStatus(),
        provenance=design.provenance,
        notes=(genre.design.note,),
    )
