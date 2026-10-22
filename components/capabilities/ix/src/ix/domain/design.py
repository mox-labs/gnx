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
    STATISTICS,
    Guardrail,
    Level,
    Margin,
    MetricRef,
    Threshold,
    default_estimator,
)
from ix.domain.genre import CONTROL, AnalysisRef, Calibration, Order, VerdictForm
from ix.domain.kind import Confusion, Metric, check_options
from ix.domain.problem import InvalidExperimentError, Problem, problem
from ix.domain.schema import Record

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ix.domain.experiment import (
        EnvironmentConfig,
        Experiment,
        ProbeConfig,
        ProbeSet,
        SensorConfig,
        SubjectConfig,
    )
    from ix.domain.genre import Genre
    from ix.domain.kind import Kind, Kinds

#: Where a resolved value came from.
Origin = Literal["experiment", "genre", "ix"]


class SourcedValue(Record):
    """One resolved value of the design and where it came from."""

    path: str
    value: Level | None
    source: Origin
    note: str = ""


class RoleAssignment(Record):
    """A subject and the role the genre gives it."""

    subject: str
    role: str
    kind: str


class SelectedSensor(Record):
    """A sensor a run would use, the metrics it is there for, and who chose it; and the
    confusion its kind declares, which a report tabulates."""

    kind: str
    metrics: tuple[str, ...]
    options: dict[str, Any] = {}
    source: Origin = "ix"
    confusion: Confusion | None = None

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
                    kind="name_unknown",
                    message=f"control names {control!r}, which is not a subject",
                    allowed=names,
                    got=control,
                )
            )
            control = None
    elif experiment.control is not None:
        problems.append(
            problem(
                "control",
                kind="control_not_applicable",
                message=f"genre {label} has no control role; its roles are "
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
                    kind="genre_roles",
                    message=f"genre {label} needs {_count(role.min, role.max)} subject(s) in role "
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


type _Choice = tuple[Kind, Origin, dict[str, Any]]


def _offered(experiment: Experiment, kinds: Kinds, probes: ProbeSet | None) -> set[str] | None:
    """The facets a sensor may need here: those every subject offers, and what any probe
    leaves behind. None while any kind in play is unknown (that is reported where it is
    named)."""
    subject_kinds = [kinds.get("subject", subject.kind) for subject in experiment.subjects.values()]
    if probes is None:
        return None
    probe_kinds = [kinds.get("probe", probe.kind) for probe in probes.probes]
    if not all(subject_kinds) or not all(probe_kinds):
        return None
    offered = set.intersection(*(set(kind.offers) for kind in subject_kinds if kind))
    return offered | {facet for kind in probe_kinds if kind for facet in kind.offers}


def _fits(sensor: Kind, offered: set[str] | None) -> bool:
    return offered is None or set(sensor.needs) <= offered


def _provides(sensor: Kind, metric: str) -> bool:
    return any(declared.name == metric for declared in sensor.metrics)


def _listed(
    experiment: Experiment, kinds: Kinds, offered: set[str] | None
) -> tuple[list[tuple[int, Kind]], list[Problem]]:
    """The sensors the experiment lists under ``sensors:``, by position, and each that the
    subjects and probes cannot feed."""
    listed: list[tuple[int, Kind]] = []
    problems: list[Problem] = []
    for index, selection in enumerate(experiment.sensors):
        sensor = kinds.get("sensor", selection.kind)
        if sensor is None:
            continue  # an unknown kind is reported where the kind is named
        listed.append((index, sensor))
        if not _fits(sensor, offered):
            lacking = sorted(set(sensor.needs) - (offered or set()))
            problems.append(
                problem(
                    f"sensors.{index}.kind",
                    kind="kind_unfit",
                    message=f"sensor {schema.short(sensor.name)} needs {', '.join(lacking)}, which "
                    "the subjects and probes of this experiment do not offer "
                    f"(they offer: {', '.join(sorted(offered or ())) or 'nothing'})",
                    fix="remove this sensor, or use subject and probe kinds that offer "
                    f"{', '.join(lacking)} (`ix kinds` lists what each offers)",
                    got=selection.kind,
                )
            )
    return listed, problems


def _chosen_by_ix(
    path: str, metric: str, kinds: Kinds, offered: set[str] | None
) -> tuple[_Choice | None, list[Problem]]:
    """The one installed sensor that provides ``metric`` and can be fed here."""
    installed = [sensor for sensor in kinds.of("sensor") if _provides(sensor, metric)]
    fitting = [sensor for sensor in installed if _fits(sensor, offered)]
    if len(fitting) == 1:
        (sensor,) = fitting
        label = schema.short(sensor.name)
        return (sensor, "ix", {}), [
            problem(
                "sensors",
                kind="option_missing",
                message=f"metric {metric!r} comes from sensor {label}, which requires option "
                f"{required!r}",
                fix=f"sensors: [{{kind: {label}, {required}: <value>}}]",
            )
            for required in sensor.required({})
        ]
    if len(fitting) > 1:
        labels = [schema.short(sensor.name) for sensor in fitting]
        return None, [
            problem(
                path,
                kind="metric_ambiguous",
                message=f"metric {metric!r} is provided by {len(fitting)} sensors: "
                + ", ".join(labels),
                fix=f"say which: sensors: [{{kind: {labels[0]}}}]",
                allowed=labels,
                got=metric,
            )
        ]
    return None, [_unprovided(path, metric, kinds, installed, offered)]


def _unprovided(
    path: str, metric: str, kinds: Kinds, installed: list[Kind], offered: set[str] | None
) -> Problem:
    available = sorted(
        {declared.name for sensor in kinds.of("sensor") if _fits(sensor, offered)
         for declared in sensor.metrics}
    )  # fmt: skip
    if installed:
        sensor = installed[0]
        lacking = sorted(set(sensor.needs) - (offered or set()))
        message = (
            f"metric {metric!r} comes from sensor {schema.short(sensor.name)}, which needs "
            f"{', '.join(lacking)}; the subjects and probes here do not offer that"
        )
    else:
        message = f"no installed sensor provides a metric named {metric!r}"
    return problem(
        path,
        kind="metric_unprovided",
        message=message,
        fix="name a metric a sensor provides here: "
        f"{', '.join(available) or '(none)'} (`ix kinds` lists them)",
        allowed=available,
        got=metric,
    )


def _choose(
    path: str,
    metric: str,
    experiment: Experiment,
    kinds: Kinds,
    listed: list[tuple[int, Kind]],
    offered: set[str] | None,
) -> tuple[_Choice | None, list[Problem]]:
    """The sensor that provides ``metric``: the one listed under ``sensors:`` that does,
    or else the one ix finds."""
    explicit = [(index, sensor) for index, sensor in listed if _provides(sensor, metric)]
    if len(explicit) == 1:
        ((index, sensor),) = explicit
        return (sensor, "experiment", dict(experiment.sensors[index].options)), []
    if len(explicit) > 1:
        return None, [
            problem(
                path,
                kind="metric_ambiguous",
                message=f"metric {metric!r} is provided by more than one sensor listed under "
                "'sensors'",
                fix=f"list only one sensor that provides {metric!r}",
                allowed=[schema.short(sensor.name) for _, sensor in explicit],
                got=metric,
            )
        ]
    return _chosen_by_ix(path, metric, kinds, offered)


def _declared(sensor: Kind, metric: str) -> Metric:
    return next(declared for declared in sensor.metrics if declared.name == metric)


def _mismatches(experiment: Experiment, chosen: dict[str, _Choice]) -> list[Problem]:
    """Each unit, direction or shape the experiment expects of a metric that its sensor
    declares otherwise."""
    problems: list[Problem] = []
    for index, expected in enumerate(experiment.metrics):
        if expected.name not in chosen:
            continue
        sensor = chosen[expected.name][0]
        declared = _declared(sensor, expected.name)
        for field in ("unit", "direction", "shape"):
            stated = getattr(expected, field)
            actual = getattr(declared, field)
            if stated is not None and stated != actual:
                problems.append(
                    problem(
                        f"metrics.{index}.{field}",
                        kind="metric_mismatch",
                        message=f"{expected.name} is expected with {field} {stated!r}; sensor "
                        f"{schema.short(sensor.name)} provides it with {field} {actual!r}",
                        fix=f"{field}: {actual}" if actual else f"remove {field!r}",
                        allowed=[actual] if actual else [],
                        got=stated,
                    )
                )
    return problems


def _selected(
    experiment: Experiment, listed: list[tuple[int, Kind]], chosen: dict[str, _Choice]
) -> tuple[SelectedSensor, ...]:
    """Each sensor a run would use: every listed one, even when no decision reads it, and
    every one chosen for a metric; each reading every metric it declares a flag for."""
    kinds = {sensor.name: sensor for _, sensor in listed}
    kinds |= {sensor.name: sensor for sensor, _, _ in chosen.values()}
    by_sensor = {
        sensor.name: SelectedSensor(
            kind=sensor.name,
            metrics=(),
            options=dict(experiment.sensors[index].options),
            source="experiment",
            confusion=sensor.confusion,
        )
        for index, sensor in listed
    }
    for metric, (sensor, origin, options) in chosen.items():
        current = by_sensor.get(sensor.name)
        by_sensor[sensor.name] = SelectedSensor(
            kind=sensor.name,
            metrics=(*(current.metrics if current else ()), metric),
            options=current.options if current else options,
            source=current.source if current else origin,
            confusion=sensor.confusion,
        )
    for name, selected in list(by_sensor.items()):
        flagged = [m.name for m in kinds[name].metrics if m.flag and m.name not in selected.metrics]
        if flagged:
            by_sensor[name] = selected.model_copy(update={"metrics": (*selected.metrics, *flagged)})
    return tuple(by_sensor.values())


def select_sensors(experiment: Experiment, kinds: Kinds, probes: ProbeSet | None) -> Instruments:
    """Resolve every named metric to the one sensor that provides it."""
    offered = _offered(experiment, kinds, probes)
    listed, problems = _listed(experiment, kinds, offered)
    chosen: dict[str, _Choice] = {}
    for path, metric in named_metrics(experiment):
        if metric in chosen:
            continue
        choice, found = _choose(path, metric, experiment, kinds, listed, offered)
        problems += found
        if choice is not None:
            chosen[metric] = choice
    problems += _mismatches(experiment, chosen)
    sensors = _selected(experiment, listed, chosen)
    declared = {metric: _declared(sensor, metric) for metric, (sensor, _, _) in chosen.items()}
    for _, sensor in listed:
        declared |= {m.name: m for m in sensor.metrics if m.flag and m.name not in declared}
    for sensor, _, _ in chosen.values():
        declared |= {m.name: m for m in sensor.metrics if m.flag and m.name not in declared}
    return Instruments(sensors=sensors, metrics=declared, problems=tuple(problems))


@dataclass(frozen=True)
class KindUse:
    """One place the experiment names a kind: where, which kind, and the options written
    beside it."""

    path: str
    kind: Kind
    options: dict[str, Any]


def kind_uses(
    experiment: Experiment, kinds: Kinds, probes: ProbeSet | None
) -> tuple[tuple[KindUse, ...], tuple[Problem, ...]]:
    """Every use of a kind in the experiment, and the problems with them: unknown kinds, and
    options the kind does not take."""
    found: list[KindUse] = []
    problems: list[Problem] = []
    unknown: set[tuple[str, str]] = set()

    def bind(
        path: str,
        *,
        family: str,
        entry: SubjectConfig | ProbeConfig | EnvironmentConfig | SensorConfig,
    ) -> None:
        kind = kinds.get(family, entry.kind)
        if kind is None:
            if (family, entry.kind) not in unknown:  # once per kind, however many name it
                unknown.add((family, entry.kind))
                problems.append(
                    problem(
                        f"{path}.kind",
                        kind="name_unknown",
                        message=f"no {family} kind named {entry.kind!r}",
                        allowed=kinds.names(family),
                        got=entry.kind,
                    )
                )
            return
        found.append(KindUse(path=path, kind=kind, options=entry.options))
        problems.extend(check_options(kind, entry.options, path))

    bind("environment", family="environment", entry=experiment.environment)
    for name, subject in experiment.subjects.items():
        bind(f"subjects.{name}", family="subject", entry=subject)
    if experiment.probes:
        for index, probe in enumerate(experiment.probes):
            bind(f"probes.{index}", family="probe", entry=probe)
    elif probes is not None:
        for probe in probes.probes:
            bind(f"probes.{probe.id}", family="probe", entry=probe)
    for index, selection in enumerate(experiment.sensors):
        bind(f"sensors.{index}", family="sensor", entry=selection)
    return tuple(found), tuple(problems)


def kinds_in_use(
    experiment: Experiment, design: Design, probes: ProbeSet
) -> tuple[tuple[str, str], ...]:
    """Each kind a run of the experiment uses, once, as (where it is first named, the kind's
    type URL)."""
    uses: list[tuple[str, str]] = [
        ("environment", schema.url("environment", experiment.environment.kind))
    ]
    uses += [
        (f"subjects.{name}", schema.url("subject", subject.kind))
        for name, subject in experiment.subjects.items()
    ]
    uses += [(f"probes.{probe.id}", schema.url("probe", probe.kind)) for probe in probes.probes]
    uses += [("sensors", sensor.kind) for sensor in design.sensors]
    first: dict[str, str] = {}
    for where, kind in uses:
        first.setdefault(kind, where)
    return tuple((where, kind) for kind, where in first.items())


def entries_in_use(
    experiment: Experiment, design: Design, probes: ProbeSet
) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
    """Every entry a run of the experiment carries out, as (where it is written, its kind's
    type URL, its options): the environment, each subject, each probe (``probes.<index>``
    when written inline, ``probes.<id>`` when read from a file) and each sensor the design
    uses (``sensors.<index>`` when listed, ``sensors`` when the genre chose it)."""
    found: list[tuple[str, str, Mapping[str, Any]]] = [
        (
            "environment",
            schema.url("environment", experiment.environment.kind),
            experiment.environment.options,
        )
    ]
    found += [
        (f"subjects.{name}", schema.url("subject", subject.kind), subject.options)
        for name, subject in experiment.subjects.items()
    ]
    inline = bool(experiment.probes)
    found += [
        (f"probes.{index if inline else probe.id}", schema.url("probe", probe.kind), probe.options)
        for index, probe in enumerate(probes.probes)
    ]
    listed = {
        schema.url("sensor", entry.kind): index for index, entry in enumerate(experiment.sensors)
    }
    found += [
        (
            "sensors" if sensor.kind not in listed else f"sensors.{listed[sensor.kind]}",
            sensor.kind,
            sensor.options,
        )
        for sensor in design.sensors
    ]
    return tuple(found)


def missing_adapters(
    experiment: Experiment, design: Design, probes: ProbeSet, adapters: frozenset[str]
) -> tuple[tuple[str, str], ...]:
    """Each kind in use that ``adapters`` (kind type URLs) does not hold, as
    :func:`kinds_in_use` gives it. A run needs every one."""
    return tuple(
        (where, kind)
        for where, kind in kinds_in_use(experiment, design, probes)
        if kind not in adapters
    )


# --- the hypothesis ----------------------------------------------------------------------


def metric_text(ref: MetricRef | Guardrail) -> str:
    """``ttfb p99``; a statistic of ``value`` (a boolean or a scalar) or ``rate`` (a count)
    adds nothing and is left out."""
    plain = ref.statistic in (None, "value", "rate")
    return ref.metric if plain else f"{ref.metric} {ref.statistic}"


def _hypotheses(experiment: Experiment, genre: Genre, design: Design) -> tuple[str, ...]:
    form = (
        genre.comparisons.get(design.comparison or "", "") if genre.comparisons else ""
    ) or genre.hypothesis_form
    if not form:
        return ()
    primary = design.metric(design.primary.metric) if design.primary else None
    shared = {
        "metric": metric_text(design.primary) if design.primary else "(no primary metric)",
        "margin": _margin_text(design.margin, primary) if design.margin else "(no margin)",
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


def _margin_text(margin: Margin, metric: Metric | None) -> str:
    """The margin as a sentence says it: ``5%``, ``10pp``, or a bare number in the
    primary's unit (``3 ms``)."""
    if margin.basis == "absolute" and metric is not None and metric.unit:
        return f"{margin.value:g} {metric.unit}"
    return margin.text()


class _Blank(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return f"({key})"


# --- resolve -----------------------------------------------------------------------------


def _derived_seed(experiment: Experiment) -> int:
    """A seed for an experiment that names none: fixed by its name, so a plan repeats."""
    return int(schema.digest(experiment.name)[:8], 16)


def _reductions(
    path: str, ref: MetricRef | Guardrail, metric: Metric | None, genre: Genre
) -> tuple[str | None, str | None, list[SourcedValue]]:
    """The statistic and estimator a decision on ``ref`` uses, and where each came from."""
    statistic, estimator = ref.statistic, ref.estimator
    sourced: list[SourcedValue] = []
    if statistic is not None:
        sourced.append(_from("experiment", path=f"{path}.statistic", value=statistic))
    elif metric is not None:
        statistic = STATISTICS[metric.shape][0]
        note = f"the first statistic of a {metric.shape}"
        sourced.append(_from("ix", path=f"{path}.statistic", value=statistic, note=note))
    if estimator is not None:
        sourced.append(_from("experiment", path=f"{path}.estimator", value=estimator))
    elif metric is not None and metric.shape in ("boolean", "count"):
        estimator = default_estimator(metric.shape)
        note = "booleans and counts combine as a rate"
        sourced.append(_from("ix", path=f"{path}.estimator", value=estimator, note=note))
    elif metric is not None:
        estimator = genre.design.estimator
        sourced.append(_from("genre", path=f"{path}.estimator", value=estimator))
    return statistic, estimator, sourced


def _from(origin: Origin, *, path: str, value: Level | None, note: str = "") -> SourcedValue:
    return SourcedValue(path=path, value=value, source=origin, note=note)


@dataclass(frozen=True)
class _Settings:
    """The run's settings the experiment may leave to the genre or to ix."""

    repeats: int
    seed: int
    comparison: str | None
    sourced: tuple[SourcedValue, ...]


def _settings(experiment: Experiment, genre: Genre, control: str | None) -> _Settings:
    sourced: list[SourcedValue] = []
    if control is not None and experiment.control is not None:
        sourced.append(_from("experiment", path="control", value=control))
    elif control is not None:
        sourced.append(
            _from("ix", path="control", value=control, note="the first subject is the control")
        )

    repeats = experiment.repeats if experiment.repeats is not None else genre.design.repeats.default
    sourced.append(
        _from(
            "experiment" if experiment.repeats is not None else "genre",
            path="repeats",
            value=repeats,
        )
    )

    if experiment.seed is not None:
        seed = experiment.seed
        sourced.append(_from("experiment", path="seed", value=seed))
    else:
        seed = _derived_seed(experiment)
        note = "derived from the experiment's name; state seed: to pin it"
        sourced.append(_from("ix", path="seed", value=seed, note=note))

    sourced.append(_from("genre", path="order", value=genre.design.order))
    sourced.append(_from("genre", path="calibration", value=genre.design.calibration))

    comparison = experiment.comparison
    if comparison is not None:
        sourced.append(_from("experiment", path="comparison", value=comparison))
    elif genre.default_comparison is not None:
        comparison = genre.default_comparison
        sourced.append(_from("genre", path="comparison", value=comparison))
    return _Settings(repeats=repeats, seed=seed, comparison=comparison, sourced=tuple(sourced))


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
    settings = _settings(experiment, genre, control)
    provenance = list(settings.sourced)

    if experiment.margin is not None:
        # The genres supply no margin: it is always the author's, and the plan says so.
        note = (
            "the largest difference either way still counted as none"
            if settings.comparison == "equivalence"
            else "the largest loss still counted as no worse"
        ) + "; the person's decision, made before the run"
        provenance.append(
            _from("experiment", path="margin", value=experiment.margin.text(), note=note)
        )

    primary = experiment.primary
    if primary is not None:
        metric = instruments.metrics.get(primary.metric)
        statistic, estimator, sourced = _reductions("primary", primary, metric, genre)
        provenance += sourced
        primary = MetricRef(metric=primary.metric, statistic=statistic, estimator=estimator)

    guardrails = []
    for index, guardrail in enumerate(experiment.guardrails):
        metric = instruments.metrics.get(guardrail.metric)
        statistic, estimator, sourced = _reductions(f"guardrails.{index}", guardrail, metric, genre)
        provenance += sourced
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
        comparison=settings.comparison,
        margin=experiment.margin,
        guardrails=tuple(guardrails),
        metrics=tuple(instruments.metrics.values()),
        sensors=instruments.sensors,
        repeats=settings.repeats,
        seed=settings.seed,
        order=genre.design.order,
        calibration=genre.design.calibration,
        analysis=genre.analysis,
        verdict_form=genre.verdict_form,
        report=genre.report,
        provenance=tuple(provenance),
    )
    return design.model_copy(update={"hypotheses": _hypotheses(experiment, genre, design)})
