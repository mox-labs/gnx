"""Resolution: an experiment and its genre become one resolved design.

The experiment says what its author chose; the genre supplies the rest. The resolved design
holds every value a run would use and, in ``settings``, where each came from: the
``experiment``, the genre (by name, with the genre's note on the default) or ``ix`` (a
convention of the core). Nothing a run does is decided anywhere else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ix.domain import schema
from ix.domain.experiment import DEFAULT_STATISTIC, Bar, Guard, Level, Margin, MetricRef
from ix.domain.genre import CONTROL, AnalysisRef
from ix.domain.problem import InvalidExperimentError, Problem
from ix.domain.schema import Record

if TYPE_CHECKING:
    from ix.domain.experiment import Experiment
    from ix.domain.genre import Genre

#: The source of a value the author wrote.
EXPERIMENT = "experiment"
#: The source of a value that is a convention of the core, the same under every genre.
CORE = "ix"


class Setting(Record):
    """One resolved value of the design and where it came from."""

    field: str
    value: Level | None
    source: str
    note: str = ""


class SubjectRole(Record):
    """A subject and the role the genre gives it."""

    name: str
    role: str
    kind: str


class ResolvedDesign(Record):
    """Everything a run of the experiment would use, with nothing left to convention."""

    schema_: Literal["ix.v1.design"] = Field(default=schema.DESIGN, alias="schema")
    experiment: str
    genre: str
    subjects: tuple[SubjectRole, ...]
    #: The control subject, where the genre has that role.
    control: str | None
    primary: MetricRef | None
    bar: Bar | None
    margin: Margin | None
    rule: str | None
    guards: tuple[Guard, ...]
    repeats: int
    seed: int
    interleave: bool
    aa: bool
    analysis: AnalysisRef
    verdict: Literal["three-way", "none"]
    report: tuple[str, ...]
    settings: tuple[Setting, ...]

    @property
    def defaults(self) -> tuple[Setting, ...]:
        """The settings the author did not write."""
        return tuple(setting for setting in self.settings if setting.source != EXPERIMENT)


def assign_roles(
    experiment: Experiment, genre: Genre
) -> tuple[tuple[SubjectRole, ...], str | None, list[Problem]]:
    """Give every subject its role. Returns the roles, the control's name, and what is wrong.

    The genre's ``control`` role goes to the subject ``control:`` names, or to the first
    subject. Every other subject takes the genre's other role.
    """
    problems: list[Problem] = []
    names = list(experiment.subjects)
    has_control = genre.role(CONTROL) is not None
    other = next((role.name for role in genre.subjects if role.name != CONTROL), None)
    label = schema.short(genre.name)

    control: str | None = None
    if has_control:
        control = experiment.control or names[0]
        if control not in experiment.subjects:
            problems.append(
                Problem(
                    path="control",
                    kind="control_unknown",
                    message=f"control names {control!r}, which is not a subject. "
                    f"Subjects: {', '.join(names)}",
                    fix=f"control: {names[0]}",
                )
            )
            control = None
    elif experiment.control is not None:
        problems.append(
            Problem(
                path="control",
                kind="control_not_applicable",
                message=f"genre {label} has no control role; its roles are "
                f"{[role.name for role in genre.subjects]}",
                fix="remove 'control', or use a genre that compares against a control",
            )
        )

    roles: list[SubjectRole] = []
    for name in names:
        role = CONTROL if name == control else (other or CONTROL)
        roles.append(SubjectRole(name=name, role=role, kind=experiment.subjects[name].kind))

    for rule in genre.subjects:
        if rule.name == CONTROL and has_control and control is None:
            continue  # already reported: the named control is not a subject
        count = sum(1 for assigned in roles if assigned.role == rule.name)
        if count < rule.min or (rule.max is not None and count > rule.max):
            bound = (
                f"exactly {rule.min}"
                if rule.max == rule.min
                else f"at least {rule.min}"
                if rule.max is None
                else f"{rule.min} to {rule.max}"
            )
            low, high = genre.subject_bounds
            total = (
                f"exactly {low}"
                if high == low
                else f"{low} or more"
                if high is None
                else f"{low} to {high}"
            )
            problems.append(
                Problem(
                    path="subjects",
                    kind="genre_subjects",
                    message=f"genre {label} needs {bound} subject(s) in role {rule.name!r}; "
                    f"{count} given ({len(names)} subject(s) in all)",
                    fix=f"give {total} subject(s), or choose a genre that fits {len(names)}",
                )
            )
    return tuple(roles), control, problems


def _derived_seed(experiment: Experiment) -> int:
    """A seed for an experiment that names none: fixed by its name, so a plan repeats."""
    return int(schema.digest(experiment.name)[:8], 16)


def _statistic(experiment: Experiment, ref: MetricRef) -> tuple[str | None, str]:
    """The statistic a reference reads and where it came from."""
    if ref.statistic is not None:
        return ref.statistic, EXPERIMENT
    metric = experiment.metric(ref.metric)
    if metric is None:
        return None, CORE
    return DEFAULT_STATISTIC.get(metric.shape), CORE


def resolve(experiment: Experiment, genre: Genre) -> ResolvedDesign:
    """The resolved design of ``experiment`` under ``genre``.

    Raises :class:`InvalidExperimentError` when the subjects cannot take the genre's roles.
    Other problems (a missing margin, an undeclared metric) do not stop resolution;
    ``validate`` reports them.
    """
    roles, control, problems = assign_roles(experiment, genre)
    if problems:
        raise InvalidExperimentError(
            f"{experiment.name}: the subjects do not fit genre {schema.short(genre.name)}",
            tuple(problems),
            fix="ix validate reports every problem with its fix",
        )
    defaults = genre.defaults
    settings: list[Setting] = []

    def setting(field: str, value: Level | None, source: str, note: str = "") -> None:
        settings.append(Setting(field=field, value=value, source=source, note=note))

    if control is not None:
        if experiment.control is not None:
            setting("control", control, EXPERIMENT)
        else:
            setting("control", control, CORE, "the first subject is the control")

    if experiment.repeats is not None:
        repeats = experiment.repeats
        setting("repeats", repeats, EXPERIMENT)
    else:
        repeats = defaults.repeats.value
        setting("repeats", repeats, genre.name, defaults.repeats.note)

    if experiment.seed is not None:
        seed = experiment.seed
        setting("seed", seed, EXPERIMENT)
    else:
        seed = _derived_seed(experiment)
        setting("seed", seed, CORE, "derived from the experiment's name; state seed: to pin it")

    setting("interleave", defaults.interleave.value, genre.name, defaults.interleave.note)
    setting("aa", defaults.aa.value, genre.name, defaults.aa.note)

    rule = experiment.rule
    if rule is not None:
        setting("rule", rule, EXPERIMENT)
    elif defaults.rule is not None:
        rule = defaults.rule.value
        setting("rule", rule, genre.name, defaults.rule.note)

    primary = experiment.primary
    if primary is not None:
        statistic, source = _statistic(experiment, primary)
        primary = MetricRef(metric=primary.metric, statistic=statistic)
        setting(
            "primary.statistic",
            statistic,
            source,
            "" if source == EXPERIMENT else "the default statistic for the metric's shape",
        )

    guards: list[Guard] = []
    for index, guard in enumerate(experiment.guards):
        statistic, source = _statistic(
            experiment, MetricRef(metric=guard.metric, statistic=guard.statistic)
        )
        guards.append(Guard(metric=guard.metric, statistic=statistic, margin=guard.margin))
        setting(
            f"guards.{index}.statistic",
            statistic,
            source,
            "" if source == EXPERIMENT else "the default statistic for the metric's shape",
        )

    return ResolvedDesign(
        experiment=experiment.name,
        genre=genre.name,
        subjects=roles,
        control=control,
        primary=primary,
        bar=experiment.bar,
        margin=experiment.margin,
        rule=rule,
        guards=tuple(guards),
        repeats=repeats,
        seed=seed,
        interleave=defaults.interleave.value,
        aa=defaults.aa.value,
        analysis=genre.analysis,
        verdict=genre.verdict,
        report=genre.report,
        settings=tuple(settings),
    )
