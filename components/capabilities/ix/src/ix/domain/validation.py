"""Validation: every problem with an experiment, found in one pass, nothing run.

:func:`parse` turns a file's data into an :class:`Experiment` or the problems that stop it.
:func:`check` holds a loaded experiment against its genre, the kinds it names and its
probes. Both report everything they find; neither raises on a wrong experiment.

One rule here is not a genre's to relax: **a model-judged metric never decides.** Whether a
metric is judged is declared by the sensor that provides it, never by the experiment. In a
genre with a verdict, a judged metric is refused as the primary and as a guardrail; it may
still be measured and reported.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ix.domain import schema
from ix.domain.design import assign_roles, kind_uses, resolve, select_sensors
from ix.domain.experiment import ESTIMATORS, STATISTICS, Experiment
from ix.domain.genre import CONTROL
from ix.domain.kind import NOTE_PREFIX, placeholders
from ix.domain.plan import count_trials, effects, estimate_spend
from ix.domain.problem import Problem, Validation, from_validation_error, problem

if TYPE_CHECKING:
    from ix.domain.design import Design, Instruments
    from ix.domain.experiment import Guardrail, MetricRef, ProbeSet, Threshold
    from ix.domain.genre import Genre, Genres
    from ix.domain.kind import Kinds, Metric

_MUST_SET_FIX = {
    "rationale": "rationale: <why you expect it, in a sentence>",
    "primary": "primary: {metric: <a metric a sensor provides>}",
    "threshold": 'threshold: {op: ">=", value: 0.9}',
    "margin": "margin: 5%",
    "comparison": "comparison: <one of the genre's comparisons>",
    "guardrails": "guardrails: [{metric: <name>, margin: 5%}]",
    "repeats": "repeats: 5",
    "seed": "seed: 1",
    "spend": "spend: {ceiling: 5.00}",
}


def parse(data: Any) -> tuple[Experiment | None, list[Problem]]:
    """An experiment from a file's data, or every reason it is not one."""
    if not isinstance(data, Mapping):
        return None, [
            problem(
                "",
                "wrong_shape",
                "an experiment file is a YAML mapping",
                fix="start with name:, question:, genre:, subjects:, probes:",
            )
        ]
    # Two slips an author makes with the shape, each with the sentence that fixes it;
    # pydantic's own account of them points at the wrong key.
    slips: list[Problem] = []
    if isinstance(data.get("subjects"), list):
        slips.append(
            problem(
                "subjects",
                "wrong_shape",
                "subjects is a mapping of each subject's name to its kind and options, not a list",
                fix="subjects: {<name>: {kind: <subject kind>, ...}}",
            )
        )
    probes = data.get("probes")
    if isinstance(probes, Mapping) and "source" not in probes:
        slips.append(
            problem(
                "probes",
                "wrong_shape",
                "probes is a list of probes, or one mapping that names a source; this "
                "mapping names no source",
                fix="probes: [{kind: <probe kind>, ...}]  (a list, each item starting with '-')"
                if "kind" in probes
                else "probes: {source: directory, path: probes}",
            )
        )
    try:
        return Experiment.model_validate(dict(data)), []
    except ValidationError as error:
        slipped = tuple(slip.path for slip in slips)
        rest = [
            found
            for found in from_validation_error(error)
            if not any(found.path == key or found.path.startswith(key + ".") for key in slipped)
        ]
        return None, slips + rest


@dataclass(frozen=True)
class Checked:
    """What :func:`check` found, and the design when the experiment is valid."""

    problems: tuple[Problem, ...]
    warnings: tuple[Problem, ...]
    genre: Genre | None
    design: Design | None

    @property
    def valid(self) -> bool:
        return not self.problems


def _genre(experiment: Experiment, genres: Genres) -> tuple[Genre | None, list[Problem]]:
    genre = genres.get(experiment.genre)
    if genre is not None:
        return genre, []
    known = genres.names()
    refused = genres.rejected.get(schema.url("genre", experiment.genre))
    if refused:
        return None, [
            problem(
                "genre",
                "genre_unknown",
                f"genre {experiment.genre!r} is declared by a genre file ix refused",
                fix="fix the genre file (its problems follow), or use one of: "
                f"{', '.join(known) or '(none)'}",
                allowed=known,
                got=experiment.genre,
            ),
            *refused,
        ]
    return None, [
        problem(
            "genre",
            "genre_unknown",
            f"no genre named {experiment.genre!r}",
            allowed=known,
            got=experiment.genre,
        )
    ]


def _against_genre(experiment: Experiment, genre: Genre) -> list[Problem]:
    problems: list[Problem] = []
    label = schema.short(genre.name)

    if genre.factors == "forbidden" and experiment.factors:
        problems.append(
            problem(
                "factors",
                "genre_factors",
                f"genre {label} takes no factors",
                fix="remove 'factors', or use a genre that varies conditions",
            )
        )
    if genre.factors == "required" and not experiment.factors:
        problems.append(
            problem(
                "factors",
                "genre_factors",
                f"genre {label} varies at least one factor and none is declared",
                fix="factors: {<name>: [<level>, <level>]}",
            )
        )

    comparisons = tuple(genre.comparisons)
    for field in genre.must_set:
        value = getattr(experiment, field)
        supplied = bool(value) or value == 0
        if field == "comparison" and genre.default_comparison is not None:
            supplied = True
        if not supplied:
            problems.append(
                problem(
                    field,
                    "must_set",
                    f"genre {label} needs {field!r} and the experiment does not set it",
                    fix=f"comparison: one of {', '.join(comparisons)}"
                    if field == "comparison"
                    else _MUST_SET_FIX[field],
                    allowed=comparisons if field == "comparison" else (),
                )
            )

    if experiment.comparison is not None and experiment.comparison not in genre.comparisons:
        problems.append(
            problem(
                "comparison",
                "comparison_not_allowed",
                f"genre {label} makes no comparison between subjects"
                if not comparisons
                else f"genre {label} does not make the comparison {experiment.comparison!r}",
                fix=None if comparisons else "remove 'comparison'",
                allowed=comparisons,
                got=experiment.comparison,
            )
        )
    comparison = experiment.comparison or genre.default_comparison
    if (
        genre.needs_margin(comparison)
        and experiment.margin is None
        and "margin" not in genre.must_set
    ):
        problems.append(
            problem(
                "margin",
                "margin_required",
                f"comparison {comparison} is stated in terms of a margin: the largest loss "
                "that still counts as no worse",
                fix="margin: 5%",
            )
        )

    floor = genre.design.repeats.min
    if experiment.repeats is not None and experiment.repeats < floor:
        problems.append(
            problem(
                "repeats",
                "repeats_below_min",
                f"genre {label} needs at least {floor} repeats to carry its finding; "
                f"{experiment.repeats} given",
                fix=f"repeats: {floor}, or leave 'repeats' out for the genre's "
                f"{genre.design.repeats.default}",
                got=experiment.repeats,
            )
        )

    if genre.role(CONTROL) is None:
        for index, guardrail in enumerate(experiment.guardrails):
            if guardrail.margin is not None:
                problems.append(
                    problem(
                        f"guardrails.{index}.margin",
                        "guardrail_form",
                        f"genre {label} has no control to be within a margin of; a "
                        "guardrail here is an absolute threshold",
                        fix='threshold: {op: "<", value: <bound>}',
                    )
                )
    return problems


def _fit(experiment: Experiment, kinds: Kinds, probes: ProbeSet | None) -> list[Problem]:
    """A probe kind must be offered what it needs by every subject's kind."""
    if probes is None:
        return []
    problems: list[Problem] = []
    inline = isinstance(experiment.probes, tuple)
    reported: set[tuple[str, str]] = set()
    for index, probe in enumerate(probes.probes):
        probe_kind = kinds.get("probe", probe.kind)
        if probe_kind is None:
            continue
        for name, subject in experiment.subjects.items():
            subject_kind = kinds.get("subject", subject.kind)
            if subject_kind is None:
                continue
            lacking = sorted(set(probe_kind.needs) - set(subject_kind.offers))
            if not lacking or (probe.kind, name) in reported:
                continue
            reported.add((probe.kind, name))
            fitting = [
                schema.short(kind.name)
                for kind in kinds.of("subject")
                if set(probe_kind.needs) <= set(kind.offers)
            ]
            problems.append(
                problem(
                    f"probes.{index}.kind" if inline else "probes",
                    "kind_unfit",
                    f"probe kind {probe.kind} needs {', '.join(lacking)} and subject {name} "
                    f"(kind {subject.kind}) offers "
                    f"{', '.join(subject_kind.offers) or 'nothing'}",
                    fix=f"apply {probe.kind} to a subject of kind "
                    f"{' or '.join(fitting) or '(none installed)'}, or use a probe kind that "
                    f"needs only {', '.join(subject_kind.offers) or 'nothing'}",
                    allowed=fitting,
                    got=subject.kind,
                )
            )
    return problems


def _threshold_unit(path: str, threshold: Threshold | None, metric: Metric) -> list[Problem]:
    if threshold is None or not threshold.unit or threshold.unit == metric.unit:
        return []
    return [
        problem(
            f"{path}.value",
            "unit_mismatch",
            f"the threshold is in {threshold.unit!r} and {metric.name} is in "
            f"{metric.unit or '(no unit)'!r}",
            fix=f"state the threshold in {metric.unit}"
            if metric.unit
            else "state the threshold as a bare number",
            allowed=[metric.unit] if metric.unit else [],
            got=threshold.unit,
        )
    ]


def _reads(
    ref: MetricRef | Guardrail,
    path: str,
    instruments: Instruments,
    *,
    decides: bool,
    directional: bool,
) -> list[Problem]:
    """Check one metric a decision reads: legal reductions, and fit to decide."""
    metric = instruments.metrics.get(ref.metric)
    if metric is None:
        return []  # unprovided or ambiguous: reported where the sensor was sought
    problems: list[Problem] = []
    statistics, estimators = STATISTICS[metric.shape], ESTIMATORS[metric.shape]
    if ref.statistic is not None and ref.statistic not in statistics:
        hint = (
            f"; to combine trials, use estimator: {ref.statistic}"
            if ref.statistic in estimators
            else ""
        )
        problems.append(
            problem(
                f"{path}.statistic",
                "statistic_illegal",
                f"{metric.name} is a {metric.shape}; {ref.statistic!r} does not reduce one "
                f"trial's {metric.shape} reading{hint}",
                fix=(f"remove 'statistic' and write estimator: {ref.statistic}" if hint else None),
                allowed=statistics,
                got=ref.statistic,
            )
        )
    if ref.estimator is not None and ref.estimator not in estimators:
        problems.append(
            problem(
                f"{path}.estimator",
                "estimator_illegal",
                f"{metric.name} is a {metric.shape}; {ref.estimator!r} does not combine its trials",
                allowed=estimators,
                got=ref.estimator,
            )
        )
    if decides and metric.judged:
        problems.append(
            problem(
                f"{path}.metric",
                "judged_gate",
                f"{metric.name} is judged by a model (its sensor declares it), and a "
                "model-judged reading never decides",
                fix=f"decide on a metric no model judges, and keep {metric.name} under "
                "'metrics:' to report it",
                got=metric.name,
            )
        )
    if decides and directional and metric.direction == "neither":
        problems.append(
            problem(
                f"{path}.metric",
                "metric_direction",
                f"{metric.name} has direction 'neither', so 'better' and 'no worse' have no "
                "meaning for it",
                fix="decide on a metric whose direction is higher or lower, or bound "
                f"{metric.name} with a threshold",
                got=metric.name,
            )
        )
    return problems


def _decision(
    experiment: Experiment, genre: Genre | None, instruments: Instruments
) -> list[Problem]:
    problems: list[Problem] = []
    decides = genre is not None and genre.decides
    compares = genre is not None and (experiment.comparison or genre.default_comparison) is not None
    if experiment.primary is not None:
        problems += _reads(
            experiment.primary, "primary", instruments, decides=decides, directional=compares
        )
        metric = instruments.metrics.get(experiment.primary.metric)
        if metric is not None:
            problems += _threshold_unit("threshold", experiment.threshold, metric)
    for index, guardrail in enumerate(experiment.guardrails):
        path = f"guardrails.{index}"
        problems += _reads(
            guardrail, path, instruments, decides=decides, directional=guardrail.margin is not None
        )
        metric = instruments.metrics.get(guardrail.metric)
        if metric is not None:
            problems += _threshold_unit(f"{path}.threshold", guardrail.threshold, metric)
    return problems


def _placeholders(experiment: Experiment) -> list[Problem]:
    """An inline probe's ``{name}`` must be a factor, or an option every subject sets."""
    if not isinstance(experiment.probes, tuple):
        return []
    shared = [set(subject.options) for subject in experiment.subjects.values()]
    bound = sorted(set(experiment.factors) | (set.intersection(*shared) if shared else set()))
    problems: list[Problem] = []
    for index, probe in enumerate(experiment.probes):
        for key, value in probe.options.items():
            for name in sorted(set(placeholders(value))):
                if name not in bound:
                    problems.append(
                        problem(
                            f"probes.{index}.{key}",
                            "unbound_placeholder",
                            f"{{{name}}} is bound to nothing",
                            fix=f"declare factors: {{{name}: [...]}}, set {name!r} on every "
                            "subject, or write the value out",
                            allowed=bound,
                            got=name,
                        )
                    )
    return problems


def _probes(
    experiment: Experiment, probes: ProbeSet | None, kinds: Kinds, instruments: Instruments
) -> list[Problem]:
    if probes is None:
        return []  # the source could not be read; its own problem says why
    if not probes.probes:
        return [
            problem(
                "probes",
                "probes_empty",
                "the experiment has no probes, so a run would apply no stimulus",
                fix="add at least one probe",
            )
        ]
    repeated = sorted({probe_id for probe_id in probes.ids if probes.ids.count(probe_id) > 1})
    problems = [
        problem(
            "probes",
            "probe_duplicate_id",
            f"probe id {probe_id!r} is used more than once; readings join on it",
            fix="give each probe its own id",
            got=probe_id,
        )
        for probe_id in repeated
    ]
    read = sorted(
        {
            key
            for sensor in instruments.sensors
            if (kind := kinds.kinds.get(sensor.kind)) is not None
            for key in kind.truth
        }
    )
    inline = isinstance(experiment.probes, tuple)
    for index, probe in enumerate(probes.probes):
        for key in probe.truth:
            if key in read or key.startswith(NOTE_PREFIX):
                continue
            problems.append(
                problem(
                    f"probes.{index if inline else probe.id}.truth.{key}",
                    "truth_unclaimed",
                    f"no sensor in use reads the truth key {key!r}, so nothing would be "
                    "measured against it",
                    fix=f"correct its spelling, name a metric whose sensor reads it, or "
                    f"prefix a note with {NOTE_PREFIX!r}",
                    allowed=read,
                    got=key,
                )
            )
    return problems


def _spend(experiment: Experiment, design: Design, kinds: Kinds, probes: ProbeSet) -> list[Problem]:
    trials = count_trials(experiment, design, len(probes.probes))
    estimate = estimate_spend(experiment.spend, trials.total)
    problems: list[Problem] = []
    spenders = sorted(
        {e.source for e in effects(experiment, design, kinds, probes) if e.effect == "spend"}
    )
    if spenders and estimate.ceiling is None:
        problems.append(
            problem(
                "spend.ceiling",
                "spend_unbounded",
                f"a run would spend money ({', '.join(spenders)}) and the experiment sets no "
                "ceiling",
                fix="spend: {ceiling: <the most this run may cost>, per_trial: <your estimate>}",
            )
        )
    if (
        estimate.estimated is not None
        and estimate.ceiling is not None
        and estimate.estimated > estimate.ceiling
    ):
        problems.append(
            problem(
                "spend.ceiling",
                "spend_over_ceiling",
                f"the run is estimated at {estimate.estimated:g} {estimate.currency} "
                f"({estimate.basis}), over the ceiling of {estimate.ceiling:g}",
                fix="lower repeats, probes or cells, or raise spend.ceiling to at least "
                f"{estimate.estimated:g}",
                got=estimate.ceiling,
            )
        )
    return problems


def _parity(experiment: Experiment, control: str | None) -> list[Problem]:
    """Warn when a variant differs from the control in more than one option: the effects of
    the differences cannot be told apart."""
    if control is None:
        return []
    base = experiment.subjects[control]
    base_options = {"kind": base.kind, **base.options}
    warnings: list[Problem] = []
    for name, subject in experiment.subjects.items():
        if name == control:
            continue
        options = {"kind": subject.kind, **subject.options}
        differing = sorted(
            key
            for key in base_options.keys() | options.keys()
            if base_options.get(key) != options.get(key)
        )
        if len(differing) > 1:
            warnings.append(
                problem(
                    f"subjects.{name}",
                    "subjects_differ_in",
                    f"{name} differs from the control {control} in {', '.join(differing)}; a "
                    "difference in the result cannot be attributed to one of them",
                    fix="change one option per variant, or accept the confound and say so in "
                    "the report",
                    got=differing,
                )
            )
    return warnings


def check(experiment: Experiment, genres: Genres, kinds: Kinds, probes: ProbeSet | None) -> Checked:
    """Every problem with a loaded experiment. ``probes`` is None when they could not be
    read (the caller reports why)."""
    genre, problems = _genre(experiment, genres)
    control: str | None = None
    roles_fit = False
    if genre is not None:
        _, control, role_problems = assign_roles(experiment, genre)
        roles_fit = not role_problems
        problems += role_problems
        problems += _against_genre(experiment, genre)

    _, kind_problems = kind_uses(experiment, kinds, probes)
    problems += kind_problems
    problems += _fit(experiment, kinds, probes)
    instruments = select_sensors(experiment, kinds, probes)
    problems += instruments.problems
    problems += _decision(experiment, genre, instruments)
    problems += _placeholders(experiment)
    problems += _probes(experiment, probes, kinds, instruments)

    design = (
        resolve(experiment, genre, kinds, probes, instruments=instruments)
        if genre is not None and roles_fit
        else None
    )
    if design is not None and probes is not None:
        problems += _spend(experiment, design, kinds, probes)

    warnings = _parity(experiment, control if genre and genre.role(CONTROL) else None)
    used = schema.url("genre", experiment.genre)
    for name, refused in genres.rejected.items():
        if name != used:
            warnings.append(
                problem(
                    "",
                    "genre_rejected",
                    f"genre file {name} was refused ({len(refused)} problem(s)) and is not "
                    "among the genres",
                    fix="`ix genres` shows why",
                    source=refused[0].source,
                )
            )
    for name, refused in kinds.rejected.items():
        warnings.append(
            problem(
                "",
                "kind_rejected",
                f"kind file {name} was refused ({len(refused)} problem(s)) and is not among "
                "the kinds",
                fix="`ix kinds` shows why",
                source=refused[0].source,
            )
        )
    return Checked(
        problems=tuple(problems),
        warnings=tuple(warnings),
        genre=genre,
        design=design if not problems else None,
    )


def validation(
    *,
    source: str,
    problems: tuple[Problem, ...],
    warnings: tuple[Problem, ...] = (),
    experiment: str = "",
    genre: Genre | None = None,
    complete: bool = True,
) -> Validation:
    """The validation document for what was found."""
    return Validation(
        experiment=experiment,
        source=source,
        genre=schema.short(genre.name) if genre else "",
        genre_url=genre.name if genre else "",
        valid=not problems,
        complete=complete,
        problems=problems,
        warnings=warnings,
    )
