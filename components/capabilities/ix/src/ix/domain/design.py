"""Resolution: an experiment, its genre and the kinds it names become one design.

The experiment says what its author chose; the genre supplies the rest; the kinds say which
sensor provides each metric. The design holds every value a run would use and, in
``provenance``, where each came from: the ``experiment``, the ``genre`` or ``ix`` (a rule of
the core, the same under every genre). It is what a registration freezes.

**Metrics are provided, not asserted.** For each metric the experiment names, exactly one
sensor must provide it. A sensor the author lists under ``sensors:`` wins; otherwise ix
selects the one installed sensor that provides the metric and whose needs the subjects and
probes meet. None is ``metric_unprovided``; two are ``metric_ambiguous``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ix.domain import schema
from ix.domain.experiment import (
    ESTIMATORS,
    STATISTICS,
    Guardrail,
    Level,
    Margin,
    MetricRef,
    Threshold,
)
from ix.domain.genre import CONTROL, AnalysisRef, Calibration, Order, VerdictForm
from ix.domain.kind import Metric, check_options
from ix.domain.problem import InvalidExperimentError, Problem, problem
from ix.domain.schema import Record

if TYPE_CHECKING:
    from ix.domain.experiment import Experiment, ProbeSet
    from ix.domain.genre import Genre
    from ix.domain.kind import Kind, Kinds

Source = Literal["experiment", "genre", "ix"]


class SourcedValue(Record):
    """One resolved value of the design and where it came from."""

    path: str
    value: Level | None
    source: Source
    note: str = ""


class RoleAssignment(Record):
    """A subject and the role the genre gives it."""

    subject: str
    role: str
    kind: str


class SelectedSensor(Record):
    """A sensor a run would use, the metrics it is there for, and who chose it."""

    kind: str
    metrics: tuple[str, ...]
    options: dict[str, Any] = {}
    source: Source = "ix"

    @property
    def label(self) -> str:
        return schema.short(self.kind)


class Design(Record):
    """Everything a run of the experiment would use, with nothing left to convention."""

    schema_: Literal["ix.v1.design"] = Field(default=schema.DESIGN, alias="schema")
    experiment: str
    #: The genre's type URL.
    genre: str
    roles: tuple[RoleAssignment, ...]
    #: The control subject, where the genre has that role.
    control: str | None = None
    #: The hypothesis, rendered from the genre: one sentence per variant.
    hypotheses: tuple[str, ...] = ()
    primary: MetricRef | None = None
    threshold: Threshold | None = None
    comparison: str | None = None
    margin: Margin | None = None
    guardrails: tuple[Guardrail, ...] = ()
    #: The declaration of every metric the experiment names, from the sensor providing it.
    metrics: tuple[Metric, ...] = ()
    sensors: tuple[SelectedSensor, ...] = ()
    repeats: int
    seed: int
    order: Order
    calibration: Calibration
    analysis: AnalysisRef
    verdict_form: VerdictForm
    report: tuple[str, ...]
    provenance: tuple[SourcedValue, ...] = ()

    def metric(self, name: str) -> Metric | None:
        return next((metric for metric in self.metrics if metric.name == name), None)


# --- roles -------------------------------------------------------------------------------


def _count(low: int, high: int | None) -> str:
    if high == low:
        return f"exactly {low}"
    return f"{low} or more" if high is None else f"{low} to {high}"


def assign_roles(
    experiment: Experiment, genre: Genre
) -> tuple[tuple[RoleAssignment, ...], str | None, list[Problem]]:
    """Give every subject its role. Returns the roles, the control's name, and what is wrong.

    The genre's ``control`` role goes to the subject ``control:`` names, or to the first
    subject. Every other subject takes the genre's other role.
    """
    problems: list[Problem] = []
    names = list(experiment.subjects)
    has_control = genre.role(CONTROL) is not None
    other = next((role.name for role in genre.roles if role.name != CONTROL), None)
    label = schema.short(genre.name)

    control: str | None = None
    if has_control:
        control = experiment.control or names[0]
        if control not in experiment.subjects:
            problems.append(
                problem(
                    "control",
                    "control_unknown",
                    f"control names {control!r}, which is not a subject",
                    allowed=names,
                    got=control,
                )
            )
            control = None
    elif experiment.control is not None:
        problems.append(
            problem(
                "control",
                "control_not_applicable",
                f"genre {label} has no control role; its roles are "
                f"{', '.join(role.name for role in genre.roles)}",
                fix="remove 'control', or use a genre that compares against a control",
                got=experiment.control,
            )
        )

    roles = tuple(
        RoleAssignment(
            subject=name,
            role=CONTROL if name == control else (other or CONTROL),
            kind=experiment.subjects[name].kind,
        )
        for name in names
    )

    for role in genre.roles:
        if role.name == CONTROL and has_control and control is None:
            continue  # already reported: the named control is not a subject
        count = sum(1 for assigned in roles if assigned.role == role.name)
        if count < role.min or (role.max is not None and count > role.max):
            problems.append(
                problem(
                    "subjects",
                    "genre_roles",
                    f"genre {label} needs {_count(role.min, role.max)} subject(s) in role "
                    f"{role.name!r}; {count} given ({len(names)} subject(s) in all)",
                    fix=f"give {_count(*genre.subject_bounds)} subject(s), or choose a genre "
                    f"that fits {len(names)}",
                    got=len(names),
                )
            )
    return roles, control, problems


# --- sensors and metrics -----------------------------------------------------------------


@dataclass(frozen=True)
class Instruments:
    """Which sensor provides each metric the experiment names, and what stood in the way."""

    sensors: tuple[SelectedSensor, ...]
    metrics: dict[str, Metric]
    problems: tuple[Problem, ...]


def named_metrics(experiment: Experiment) -> list[tuple[str, str]]:
    """(path, metric name) for every metric the experiment names, first mention first."""
    named: list[tuple[str, str]] = []
    if experiment.primary is not None:
        named.append(("primary.metric", experiment.primary.metric))
    named += [
        (f"guardrails.{index}.metric", guardrail.metric)
        for index, guardrail in enumerate(experiment.guardrails)
    ]
    named += [(f"metrics.{index}", metric.name) for index, metric in enumerate(experiment.metrics)]
    return named


def select_sensors(experiment: Experiment, kinds: Kinds, probes: ProbeSet | None) -> Instruments:
    """Resolve every named metric to the one sensor that provides it."""
    problems: list[Problem] = []

    subject_kinds = [kinds.get("subject", subject.kind) for subject in experiment.subjects.values()]
    probe_kinds = (
        None if probes is None else [kinds.get("probe", probe.kind) for probe in probes.probes]
    )
    # What sensors may need: facets every subject offers, and what any probe leaves behind.
    # Unknown while any kind in play is unknown (that is reported where it is named).
    offered: set[str] | None = None
    if probe_kinds is not None and all(subject_kinds) and all(probe_kinds):
        offered = set.intersection(*(set(kind.offers) for kind in subject_kinds if kind))
        offered |= {facet for kind in probe_kinds if kind for facet in kind.offers}

    def fits(sensor: Kind) -> bool:
        return offered is None or set(sensor.needs) <= offered

    listed: list[tuple[int, Kind]] = []
    for index, selection in enumerate(experiment.sensors):
        sensor = kinds.get("sensor", selection.kind)
        if sensor is None:
            continue  # kind_unknown is reported where the kind is named
        listed.append((index, sensor))
        if not fits(sensor):
            lacking = sorted(set(sensor.needs) - (offered or set()))
            problems.append(
                problem(
                    f"sensors.{index}.kind",
                    "kind_unfit",
                    f"sensor {schema.short(sensor.name)} needs {', '.join(lacking)}, which "
                    "the subjects and probes of this experiment do not offer "
                    f"(they offer: {', '.join(sorted(offered or ())) or 'nothing'})",
                    fix="remove this sensor, or use subject and probe kinds that offer "
                    f"{', '.join(lacking)} (`ix kinds` lists what each offers)",
                    got=selection.kind,
                )
            )

    chosen: dict[str, tuple[Kind, Source, dict[str, Any]]] = {}
    for path, name in named_metrics(experiment):
        if name in chosen:
            continue
        explicit = [(i, s) for i, s in listed if any(m.name == name for m in s.metrics)]
        installed = [s for s in kinds.of("sensor") if any(m.name == name for m in s.metrics)]
        if len(explicit) == 1:
            index, sensor = explicit[0]
            chosen[name] = (sensor, "experiment", dict(experiment.sensors[index].options))
        elif len(explicit) > 1:
            problems.append(
                problem(
                    path,
                    "metric_ambiguous",
                    f"metric {name!r} is provided by more than one sensor listed under 'sensors'",
                    fix=f"list only one sensor that provides {name!r}",
                    allowed=[schema.short(s.name) for _, s in explicit],
                    got=name,
                )
            )
        else:
            fitting = [sensor for sensor in installed if fits(sensor)]
            if len(fitting) == 1:
                sensor = fitting[0]
                chosen[name] = (sensor, "ix", {})
                for required in sensor.required:
                    label = schema.short(sensor.name)
                    problems.append(
                        problem(
                            "sensors",
                            "option_missing",
                            f"metric {name!r} comes from sensor {label}, which requires "
                            f"option {required!r}",
                            fix=f"sensors: [{{kind: {label}, {required}: <value>}}]",
                        )
                    )
            elif len(fitting) > 1:
                labels = [schema.short(sensor.name) for sensor in fitting]
                problems.append(
                    problem(
                        path,
                        "metric_ambiguous",
                        f"metric {name!r} is provided by {len(fitting)} sensors: "
                        f"{', '.join(labels)}",
                        fix=f"say which: sensors: [{{kind: {labels[0]}}}]",
                        allowed=labels,
                        got=name,
                    )
                )
            else:
                available = sorted(
                    {m.name for s in kinds.of("sensor") if fits(s) for m in s.metrics}
                )
                if installed:
                    sensor = installed[0]
                    lacking = sorted(set(sensor.needs) - (offered or set()))
                    message = (
                        f"metric {name!r} comes from sensor {schema.short(sensor.name)}, "
                        f"which needs {', '.join(lacking)}; the subjects and probes here "
                        "do not offer that"
                    )
                else:
                    message = f"no installed sensor provides a metric named {name!r}"
                problems.append(
                    problem(
                        path,
                        "metric_unprovided",
                        message,
                        fix="name a metric a sensor provides here: "
                        f"{', '.join(available) or '(none)'} (`ix kinds` lists them)",
                        allowed=available,
                        got=name,
                    )
                )

    for index, expected in enumerate(experiment.metrics):
        if expected.name not in chosen:
            continue
        declared = next(m for m in chosen[expected.name][0].metrics if m.name == expected.name)
        for field in ("unit", "direction", "shape"):
            stated = getattr(expected, field)
            actual = getattr(declared, field)
            if stated is not None and stated != actual:
                problems.append(
                    problem(
                        f"metrics.{index}.{field}",
                        "metric_mismatch",
                        f"{expected.name} is expected with {field} {stated!r}; sensor "
                        f"{schema.short(chosen[expected.name][0].name)} provides it with "
                        f"{field} {actual!r}",
                        fix=f"{field}: {actual}" if actual else f"remove {field!r}",
                        allowed=[actual] if actual else [],
                        got=stated,
                    )
                )

    by_sensor: dict[str, SelectedSensor] = {}
    for index, sensor in listed:  # a listed sensor is used even when no decision reads it
        by_sensor[sensor.name] = SelectedSensor(
            kind=sensor.name,
            metrics=(),
            options=dict(experiment.sensors[index].options),
            source="experiment",
        )
    for name, (sensor, source, options) in chosen.items():
        current = by_sensor.get(sensor.name)
        by_sensor[sensor.name] = SelectedSensor(
            kind=sensor.name,
            metrics=(*(current.metrics if current else ()), name),
            options=current.options if current else options,
            source=current.source if current else source,
        )
    metrics = {
        name: next(m for m in sensor.metrics if m.name == name)
        for name, (sensor, _, _) in chosen.items()
    }
    return Instruments(sensors=tuple(by_sensor.values()), metrics=metrics, problems=tuple(problems))


def kind_uses(
    experiment: Experiment, kinds: Kinds, probes: ProbeSet | None
) -> tuple[list[tuple[str, Kind, dict[str, Any], tuple[str, ...]]], list[Problem]]:
    """Every use of a kind in the experiment as (path, kind, options, author's effects),
    and the problems with them: unknown kinds, and options the kind does not take."""
    found: list[tuple[str, Kind, dict[str, Any], tuple[str, ...]]] = []
    problems: list[Problem] = []

    unknown: set[tuple[str, str]] = set()

    def bind(
        path: str, family: str, name: str, options: dict[str, Any], effects: tuple[str, ...]
    ) -> None:
        kind = kinds.get(family, name)
        if kind is None:
            if (family, name) not in unknown:  # once per kind, however many probes name it
                unknown.add((family, name))
                problems.append(
                    problem(
                        f"{path}.kind",
                        "kind_unknown",
                        f"no {family} kind named {name!r}",
                        allowed=kinds.names(family),
                        got=name,
                    )
                )
            return
        found.append((path, kind, options, effects))
        problems.extend(check_options(kind, options, path))

    environment = experiment.environment
    bind("environment", "environment", environment.kind, environment.options, environment.effects)
    for name, subject in experiment.subjects.items():
        bind(f"subjects.{name}", "subject", subject.kind, subject.options, subject.effects)
    if isinstance(experiment.probes, tuple):
        for index, probe in enumerate(experiment.probes):
            bind(f"probes.{index}", "probe", probe.kind, probe.options, probe.effects)
    elif probes is not None:
        for probe in probes.probes:
            bind(f"probes.{probe.id}", "probe", probe.kind, probe.options, probe.effects)
    for index, selection in enumerate(experiment.sensors):
        bind(f"sensors.{index}", "sensor", selection.kind, selection.options, selection.effects)
    return found, problems


# --- the hypothesis ----------------------------------------------------------------------


def metric_text(ref: MetricRef | Guardrail) -> str:
    """``ttfb p99``; a statistic of ``value`` adds nothing and is left out."""
    return ref.metric if ref.statistic in (None, "value") else f"{ref.metric} {ref.statistic}"


def _hypotheses(experiment: Experiment, genre: Genre, design: Design) -> tuple[str, ...]:
    form = (
        genre.comparisons.get(design.comparison or "", "") if genre.comparisons else ""
    ) or genre.hypothesis_form
    if not form:
        return ()
    shared = {
        "metric": metric_text(design.primary) if design.primary else "(no primary metric)",
        "margin": design.margin.text() if design.margin else "(no margin)",
        "threshold": design.threshold.text() if design.threshold else "(no threshold)",
        "factors": " x ".join(experiment.factors) or "(no factors)",
    }
    other = next((role.name for role in genre.roles if role.name != CONTROL), None)
    players: list[str | None] = [a.subject for a in design.roles if a.role == other]
    sentences = []
    for player in players or [None]:
        values = dict(shared)
        if design.control is not None:
            values[CONTROL] = design.control
        if other and player:
            values[other] = player
        sentences.append(form.format_map(_Blank(values)))
    return tuple(sentences)


class _Blank(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return f"({key})"


# --- resolve -----------------------------------------------------------------------------


def _derived_seed(experiment: Experiment) -> int:
    """A seed for an experiment that names none: fixed by its name, so a plan repeats."""
    return int(schema.digest(experiment.name)[:8], 16)


def resolve(
    experiment: Experiment,
    genre: Genre,
    kinds: Kinds,
    probes: ProbeSet | None,
    *,
    instruments: Instruments | None = None,
) -> Design:
    """The design of ``experiment`` under ``genre``.

    Raises :class:`InvalidExperimentError` when the subjects cannot take the genre's roles.
    Other problems (a missing margin, an unprovided metric) do not stop resolution;
    ``validate`` reports them.
    """
    roles, control, problems = assign_roles(experiment, genre)
    if problems:
        raise InvalidExperimentError(
            f"{experiment.name}: the subjects do not fit genre {schema.short(genre.name)}",
            tuple(problems),
            fix="ix validate reports every problem with its fix",
        )
    instruments = instruments or select_sensors(experiment, kinds, probes)
    provenance: list[SourcedValue] = []

    def sourced(path: str, value: Level | None, source: Source, note: str = "") -> None:
        provenance.append(SourcedValue(path=path, value=value, source=source, note=note))

    if control is not None:
        if experiment.control is not None:
            sourced("control", control, "experiment")
        else:
            sourced("control", control, "ix", "the first subject is the control")

    repeats = experiment.repeats if experiment.repeats is not None else genre.design.repeats.default
    sourced("repeats", repeats, "experiment" if experiment.repeats is not None else "genre")

    if experiment.seed is not None:
        seed = experiment.seed
        sourced("seed", seed, "experiment")
    else:
        seed = _derived_seed(experiment)
        sourced("seed", seed, "ix", "derived from the experiment's name; state seed: to pin it")

    sourced("order", genre.design.order, "genre")
    sourced("calibration", genre.design.calibration, "genre")

    comparison = experiment.comparison
    if comparison is not None:
        sourced("comparison", comparison, "experiment")
    elif genre.default_comparison is not None:
        comparison = genre.default_comparison
        sourced("comparison", comparison, "genre")

    def reductions(path: str, ref: MetricRef | Guardrail) -> tuple[str | None, str | None]:
        metric = instruments.metrics.get(ref.metric)
        statistic, estimator = ref.statistic, ref.estimator
        if statistic is not None:
            sourced(f"{path}.statistic", statistic, "experiment")
        elif metric is not None:
            statistic = STATISTICS[metric.shape][0]
            sourced(
                f"{path}.statistic", statistic, "ix", f"the first statistic of a {metric.shape}"
            )
        if estimator is not None:
            sourced(f"{path}.estimator", estimator, "experiment")
        elif metric is not None and metric.shape == "boolean":
            estimator = ESTIMATORS["boolean"][0]
            sourced(f"{path}.estimator", estimator, "ix", "booleans combine as a rate")
        elif metric is not None:
            estimator = genre.design.estimator
            sourced(f"{path}.estimator", estimator, "genre")
        return statistic, estimator

    primary = experiment.primary
    if primary is not None:
        statistic, estimator = reductions("primary", primary)
        primary = MetricRef(metric=primary.metric, statistic=statistic, estimator=estimator)

    guardrails = []
    for index, guardrail in enumerate(experiment.guardrails):
        statistic, estimator = reductions(f"guardrails.{index}", guardrail)
        guardrails.append(
            guardrail.model_copy(update={"statistic": statistic, "estimator": estimator})
        )

    design = Design(
        experiment=experiment.name,
        genre=genre.name,
        roles=roles,
        control=control,
        primary=primary,
        threshold=experiment.threshold,
        comparison=comparison,
        margin=experiment.margin,
        guardrails=tuple(guardrails),
        metrics=tuple(instruments.metrics.values()),
        sensors=instruments.sensors,
        repeats=repeats,
        seed=seed,
        order=genre.design.order,
        calibration=genre.design.calibration,
        analysis=genre.analysis,
        verdict_form=genre.verdict_form,
        report=genre.report,
        provenance=tuple(provenance),
    )
    return design.model_copy(update={"hypotheses": _hypotheses(experiment, genre, design)})
