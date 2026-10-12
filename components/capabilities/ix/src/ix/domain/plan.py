"""The plan: what a run of an experiment would do, computed without running anything.

A plan is a pure function of the experiment, its resolved design and its probe set. It lists
the subjects with their roles, the cells, the probes, the repeats, the order rule, the
effects the file declares and the estimated spend, and it is identified by a digest of all of
that: the same file gives the same plan, and a plan that changed has another digest.
"""

from __future__ import annotations

from itertools import product
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ix.domain import schema
from ix.domain.design import Setting, SubjectRole
from ix.domain.experiment import Level
from ix.domain.schema import Record

if TYPE_CHECKING:
    from ix.domain.design import ResolvedDesign
    from ix.domain.experiment import Experiment, ProbeSet, Spend

#: Said by every plan until a host is attached: the file is the only source of effects.
EFFECTS_NOTE = (
    "effects are those the file declares; what each subject, probe and environment kind does "
    "on its own is added when a host is attached"
)


class Order(Record):
    """The rule a run orders its trials by. The seed fixes the order it produces."""

    rule: Literal["interleaved", "sequential"]
    seed: int
    description: str


class Effect(Record):
    """Something a run would do to the world, and the part of the file that declares it."""

    effect: str
    declared_by: str


class PlannedProbes(Record):
    count: int
    digest: str
    ids: tuple[str, ...]


class Calibration(Record):
    """The A/A pass: the control run against itself, to measure the noise floor."""

    aa: bool
    subject: str | None
    trials: int


class SpendEstimate(Record):
    estimated: float | None
    ceiling: float | None
    currency: str
    basis: str


class Plan(Record):
    """What a run would do. ``plan`` is the digest of every other field."""

    schema_: Literal["ix.v2.plan"] = Field(default=schema.PLAN, alias="schema")
    plan: str
    experiment: str
    experiment_digest: str
    genre: str
    subjects: tuple[SubjectRole, ...]
    control: str | None
    factors: dict[str, tuple[Level, ...]]
    cells: tuple[dict[str, Level], ...]
    probes: PlannedProbes
    repeats: int
    calibration: Calibration
    #: Subject trials plus calibration trials.
    trials: int
    order: Order
    effects: tuple[Effect, ...]
    spend: SpendEstimate
    verdict: Literal["three-way", "none"]
    #: Every design value and where it came from; see :class:`ix.domain.design.Setting`.
    settings: tuple[Setting, ...]
    notes: tuple[str, ...]


def cells(factors: dict[str, tuple[Level, ...]]) -> tuple[dict[str, Level], ...]:
    """Every combination of factor levels, the first factor varying slowest. No factors:
    one cell with no levels."""
    names = list(factors)
    return tuple(
        dict(zip(names, levels, strict=True))
        for levels in product(*(factors[name] for name in names))
    )


def trial_counts(experiment: Experiment, design: ResolvedDesign, probes: int) -> tuple[int, int]:
    """(subject trials, calibration trials) of one run."""
    per_subject = len(cells(experiment.factors)) * probes * design.repeats
    calibration = per_subject if design.aa and design.control is not None else 0
    return per_subject * len(design.subjects), calibration


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


def _effects(experiment: Experiment) -> tuple[Effect, ...]:
    found: list[Effect] = [
        Effect(effect=effect, declared_by="environment")
        for effect in experiment.environment.effects
    ]
    for name, subject in experiment.subjects.items():
        found += [
            Effect(effect=effect, declared_by=f"subjects.{name}") for effect in subject.effects
        ]
    if isinstance(experiment.probes, tuple):
        for index, probe in enumerate(experiment.probes):
            found += [
                Effect(effect=effect, declared_by=f"probes.{index}") for effect in probe.effects
            ]
    return tuple(found)


def plan(experiment: Experiment, design: ResolvedDesign, probes: ProbeSet) -> Plan:
    """The plan of a valid experiment. Runs nothing and reads nothing."""
    subject_trials, calibration_trials = trial_counts(experiment, design, len(probes.probes))
    trials = subject_trials + calibration_trials
    order = (
        Order(
            rule="interleaved",
            seed=design.seed,
            description="within each repeat, cell and probe the subjects run in a random "
            "order drawn from the seed",
        )
        if design.interleave
        else Order(
            rule="sequential",
            seed=design.seed,
            description="subjects run one after another in the order written",
        )
    )
    notes = [EFFECTS_NOTE]
    if calibration_trials:
        notes.append(
            f"calibration: the control {design.control!r} also runs against itself (A/A) to "
            "measure the noise floor"
        )
    body = Plan(
        plan="",
        experiment=experiment.name,
        experiment_digest=experiment.digest(),
        genre=design.genre,
        subjects=design.subjects,
        control=design.control,
        factors=experiment.factors,
        cells=cells(experiment.factors),
        probes=PlannedProbes(count=len(probes.probes), digest=probes.digest, ids=probes.ids),
        repeats=design.repeats,
        calibration=Calibration(aa=design.aa, subject=design.control, trials=calibration_trials),
        trials=trials,
        order=order,
        effects=_effects(experiment),
        spend=estimate_spend(experiment.spend, trials),
        verdict=design.verdict,
        settings=design.settings,
        notes=tuple(notes),
    )
    document = body.document()
    del document["plan"]
    return body.model_copy(update={"plan": schema.digest(document)})
