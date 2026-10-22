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

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ix.domain import schema
from ix.domain.analysis import ANALYSES, MIN_BLOCKS, analysis_unsupported
from ix.domain.design import (
    assign_roles,
    kind_uses,
    missing_adapters,
    resolve,
    select_sensors,
)
from ix.domain.experiment import ESTIMATORS, STATISTICS, Experiment, default_estimator
from ix.domain.genre import CONTROL
from ix.domain.intervals import MEDIAN_MIN
from ix.domain.kind import NOTE_PREFIX, placeholders
from ix.domain.problem import Problem, Validation, from_validation_error, problem

if TYPE_CHECKING:
    from ix.domain.design import Design, Instruments
    from ix.domain.experiment import Guardrail, Margin, MetricRef, ProbeSet, Threshold
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
}


def parse(data: Any) -> tuple[Experiment | None, list[Problem]]:
    """An experiment from data already read (an object built in memory, or a mapping), or
    every reason it is not one."""
    if not isinstance(data, Mapping):
        return None, [
            problem(
                "",
                kind="wrong_shape",
                message="an experiment is a mapping of keys to values",
                fix="start with name:, question:, genre:, subjects:, probes:",
            )
        ]
    try:
        return Experiment.model_validate(dict(data)), []
    except ValidationError as error:
        return None, from_validation_error(error)


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
                kind="name_unknown",
                message=f"genre {experiment.genre!r} is declared by a genre file ix refused",
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
            kind="name_unknown",
            message=f"no genre named {experiment.genre!r}",
            allowed=known,
            got=experiment.genre,
        )
    ]


def _against_genre(experiment: Experiment, genre: Genre) -> list[Problem]:
    return [
        *_factors(experiment, genre),
        *_must_set(experiment, genre),
        *_comparison(experiment, genre),
        *_repeats(experiment, genre),
        *_guardrail_forms(experiment, genre),
    ]


def _factors(experiment: Experiment, genre: Genre) -> list[Problem]:
    label = schema.short(genre.name)
    if genre.factors == "forbidden" and experiment.factors:
        return [
            problem(
                "factors",
                kind="genre_factors",
                message=f"genre {label} takes no factors",
                fix="remove 'factors', or use a genre that varies conditions",
            )
        ]
    if genre.factors == "required" and not experiment.factors:
        return [
            problem(
                "factors",
                kind="genre_factors",
                message=f"genre {label} varies at least one factor and none is declared",
                fix="factors: {<name>: [<level>, <level>]}",
            )
        ]
    return []


def _must_set(experiment: Experiment, genre: Genre) -> list[Problem]:
    label = schema.short(genre.name)
    comparisons = tuple(genre.comparisons)
    problems: list[Problem] = []
    for field in genre.must_set:
        value = getattr(experiment, field)
        supplied = bool(value) or value == 0
        if field == "comparison" and genre.default_comparison is not None:
            supplied = True
        if not supplied:
            problems.append(
                problem(
                    field,
                    kind="must_set",
                    message=f"genre {label} needs {field!r} and the experiment does not set it",
                    fix=f"comparison: one of {', '.join(comparisons)}"
                    if field == "comparison"
                    else _MUST_SET_FIX[field],
                    allowed=comparisons if field == "comparison" else (),
                )
            )
    return problems


def _comparison(experiment: Experiment, genre: Genre) -> list[Problem]:
    """A comparison the genre does not make, and a margin its comparison needs."""
    label = schema.short(genre.name)
    comparisons = tuple(genre.comparisons)
    problems: list[Problem] = []
    if experiment.comparison is not None and experiment.comparison not in genre.comparisons:
        problems.append(
            problem(
                "comparison",
                kind="comparison_not_allowed",
                message=f"genre {label} makes no comparison between subjects"
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
                kind="margin_required",
                message=f"comparison {comparison} is stated in terms of a margin: the largest loss "
                "that still counts as no worse",
                fix="margin: 5%",
            )
        )
    return problems


def _repeats(experiment: Experiment, genre: Genre) -> list[Problem]:
    floor = genre.design.repeats.min
    if experiment.repeats is None or experiment.repeats >= floor:
        return []
    return [
        problem(
            "repeats",
            kind="repeats_below_min",
            message=f"genre {schema.short(genre.name)} needs at least {floor} repeats to carry "
            f"its finding; {experiment.repeats} given",
            fix=f"repeats: {floor}, or leave 'repeats' out for the genre's "
            f"{genre.design.repeats.default}",
            got=experiment.repeats,
        )
    ]


def _guardrail_forms(experiment: Experiment, genre: Genre) -> list[Problem]:
    """A guardrail with a margin, in a genre with no control to be within a margin of."""
    if genre.role(CONTROL) is not None:
        return []
    return [
        problem(
            f"guardrails.{index}.margin",
            kind="guardrail_form",
            message=f"genre {schema.short(genre.name)} has no control to be within a margin "
            "of; a guardrail here is an absolute threshold",
            fix='threshold: {op: "<", value: <bound>}',
        )
        for index, guardrail in enumerate(experiment.guardrails)
        if guardrail.margin is not None
    ]


def _fit(experiment: Experiment, kinds: Kinds, probes: ProbeSet | None) -> list[Problem]:
    """A probe kind must be offered what it needs by every subject's kind."""
    if probes is None:
        return []
    problems: list[Problem] = []
    inline = bool(experiment.probes)
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
                    kind="kind_unfit",
                    message=f"probe kind {probe.kind} needs {', '.join(lacking)} and subject "
                    f"{name} (kind {subject.kind}) offers "
                    f"{', '.join(subject_kind.offers) or 'nothing'}",
                    fix=f"apply {probe.kind} to a subject of kind "
                    f"{' or '.join(fitting) or '(none installed)'}, or use a probe kind that "
                    f"needs only {', '.join(subject_kind.offers) or 'nothing'}",
                    allowed=fitting,
                    got=subject.kind,
                )
            )
    return problems


def _share_fix(value: float, unit: str) -> str:
    """How to write ``value`` as the share a rate is compared on."""
    return f"a share: {value / 100:g}" if unit in ("%", "pp") else "a share, such as 0.05"


def _threshold_value(path: str, threshold: Threshold | None, metric: Metric) -> list[Problem]:
    """A threshold in another unit than its metric's; on a rate, one outside (0, 1), which a
    rate always passes or never can."""
    if threshold is None:
        return []
    rate = metric.shape in ("boolean", "count")
    if threshold.unit and threshold.unit != metric.unit:
        if rate:
            fix = f"state the threshold as {_share_fix(threshold.value, threshold.unit)}"
        elif metric.unit:
            fix = f"state the threshold in {metric.unit}"
        else:
            fix = "state the threshold as a bare number"
        return [
            problem(
                f"{path}.value",
                kind="unit_mismatch",
                message=f"the threshold is in {threshold.unit!r} and {metric.name} is in "
                f"{metric.unit or '(no unit)'!r}",
                fix=fix,
                allowed=[metric.unit] if metric.unit else [],
                got=threshold.unit,
            )
        ]
    if rate and not 0 < threshold.value < 1:
        return [
            problem(
                f"{path}.value",
                kind="invalid_value",
                message=f"{metric.name} is a rate, a share between 0 and 1, so a threshold of "
                f"{threshold.value:g} is passed by every rate or by none",
                fix="state the threshold as a share between 0 and 1, such as 0.05",
                got=threshold.value,
            )
        ]
    return []


@dataclass(frozen=True)
class _Deciding:
    """What a decision's checks need from the genre and the probes: whether the genre
    decides, the estimator it supplies, and the blocks per cell a run would have (None
    while the probes are unknown)."""

    decides: bool
    estimator: str
    blocks: int | None


def _reads(
    ref: MetricRef | Guardrail,
    path: str,
    instruments: Instruments,
    deciding: _Deciding,
    *,
    directional: bool,
    margin: tuple[str, Margin] | None = None,
) -> list[Problem]:
    """Check one metric a decision reads: legal reductions, and fit to decide. ``margin`` is
    the margin the decision is read against, with its path."""
    metric = instruments.metrics.get(ref.metric)
    if metric is None:
        return []  # unprovided or ambiguous: reported where the sensor was sought
    decides = deciding.decides
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
                kind="statistic_illegal",
                message=f"{metric.name} is a {metric.shape}; {ref.statistic!r} does not reduce one "
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
                kind="estimator_illegal",
                message=f"{metric.name} is a {metric.shape}; {ref.estimator!r} does not combine "
                "its trials",
                allowed=estimators,
                got=ref.estimator,
            )
        )
    if decides and metric.judged:
        problems.append(
            problem(
                f"{path}.metric",
                kind="judged_gate",
                message=f"{metric.name} is judged by a model (its sensor declares it), and a "
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
                kind="metric_direction",
                message=f"{metric.name} has direction 'neither', so 'better' and 'no worse' have "
                "no meaning for it",
                fix="decide on a metric whose direction is higher or lower, or bound "
                f"{metric.name} with a threshold",
                got=metric.name,
            )
        )
    if decides:
        problems += _decidable(ref, path, metric, deciding, margin)
    return problems


#: A rate's whole range on each basis a margin is stated in: a share, or percentage points.
_WHOLE = {"absolute": 1.0, "points": 100.0, "relative": math.inf}


def _decidable(
    ref: MetricRef | Guardrail,
    path: str,
    metric: Metric,
    deciding: _Deciding,
    margin: tuple[str, Margin] | None,
) -> list[Problem]:
    """What the interval methods cannot decide on: a min or a max, a rate in percent, and a
    median with fewer blocks than its interval needs."""
    problems: list[Problem] = []
    rate = metric.shape in ("boolean", "count")
    estimator = ref.estimator or default_estimator(metric.shape, deciding.estimator)
    if estimator in ("min", "max") and estimator in ESTIMATORS[metric.shape]:
        problems.append(
            problem(
                f"{path}.estimator",
                kind="estimator_illegal",
                message=f"{estimator} has no interval ix can stand behind at these trial "
                "counts, so it never decides"
                + ("" if ref.estimator else "; the genre supplies it"),
                fix="estimator: mean, or estimator: median; keep the "
                f"{estimator} under 'metrics:' to report it",
                allowed=("mean", "median"),
                got=estimator,
            )
        )
    if rate and margin is not None and margin[1].basis == "relative":
        where, given = margin
        problems.append(
            problem(
                where,
                kind="unit_mismatch",
                message=f"{metric.name} is a rate, and a margin in percent of a rate near 0 "
                f"is unstable; ix compares rates in percentage points or as a share",
                fix=f"margin: {given.value:g}pp (percentage points) or a share such as 0.05",
                allowed=("pp", "a bare share"),
                got=given.text(),
            )
        )
    elif rate and margin is not None and margin[1].value >= _WHOLE[margin[1].basis]:
        where, given = margin
        problems.append(
            problem(
                where,
                kind="invalid_value",
                message=f"{metric.name} is a rate, a share between 0 and 1, so a margin of "
                f"{given.text()} is the whole range or more and rules no loss out",
                fix="margin: a share below 1, such as 0.05, or percentage points below 100, "
                "such as 5pp",
                got=given.text(),
            )
        )
    if (
        estimator == "median"
        and deciding.blocks is not None
        and MIN_BLOCKS <= deciding.blocks < MEDIAN_MIN
    ):
        problems.append(
            problem(
                "repeats",
                kind="repeats_below_min",
                message=f"a median's interval needs {MEDIAN_MIN} blocks (repeats x probes) in a "
                f"cell; this run would have {deciding.blocks}",
                fix=f"raise repeats so repeats x probes is at least {MEDIAN_MIN}, or decide on "
                "the mean",
                got=deciding.blocks,
            )
        )
    return problems


def _decision(
    experiment: Experiment, genre: Genre | None, instruments: Instruments, probes: ProbeSet | None
) -> list[Problem]:
    problems: list[Problem] = []
    compares = genre is not None and (experiment.comparison or genre.default_comparison) is not None
    repeats = experiment.repeats or (genre.design.repeats.default if genre else 1)
    deciding = _Deciding(
        decides=genre is not None and genre.decides,
        estimator=genre.design.estimator if genre else "mean",
        blocks=None if probes is None else repeats * len(probes.probes),
    )
    if deciding.decides and deciding.blocks is not None and deciding.blocks < MIN_BLOCKS:
        problems.append(
            problem(
                "repeats",
                kind="repeats_below_min",
                message=f"an interval needs {MIN_BLOCKS} blocks (repeats x probes) in a cell; "
                f"this run would have {deciding.blocks}, so no decision could be made",
                fix=f"raise repeats so repeats x probes is at least {MIN_BLOCKS}",
                got=deciding.blocks,
            )
        )
    if experiment.primary is not None:
        problems += _reads(
            experiment.primary,
            "primary",
            instruments,
            deciding,
            directional=compares,
            margin=("margin", experiment.margin) if compares and experiment.margin else None,
        )
        metric = instruments.metrics.get(experiment.primary.metric)
        if metric is not None:
            problems += _threshold_value("threshold", experiment.threshold, metric)
    for index, guardrail in enumerate(experiment.guardrails):
        path = f"guardrails.{index}"
        problems += _reads(
            guardrail,
            path,
            instruments,
            deciding,
            directional=guardrail.margin is not None,
            margin=(f"{path}.margin", guardrail.margin) if guardrail.margin else None,
        )
        metric = instruments.metrics.get(guardrail.metric)
        if metric is not None:
            problems += _threshold_value(f"{path}.threshold", guardrail.threshold, metric)
    return problems


def _placeholders(experiment: Experiment) -> list[Problem]:
    """An inline probe's ``{name}`` must be a factor, or an option every subject sets."""
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
                            kind="unbound_placeholder",
                            message=f"{{{name}}} is bound to nothing",
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
                kind="probes_empty",
                message="the experiment has no probes, so a run would apply no stimulus",
                fix="add at least one probe",
            )
        ]
    repeated = sorted({probe_id for probe_id in probes.ids if probes.ids.count(probe_id) > 1})
    problems = [
        problem(
            "probes",
            kind="name_taken",
            message=f"probe id {probe_id!r} is used more than once; readings join on it",
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
    inline = bool(experiment.probes)
    for index, probe in enumerate(probes.probes):
        declared = kinds.get("probe", probe.kind)
        options = declared.options if declared is not None else {}
        for key in probe.truth:
            if key in read or key.startswith(NOTE_PREFIX):
                continue
            path = f"probes.{index if inline else probe.id}.truth.{key}"
            if key in options:
                problems.append(_misplaced_option(path, key, probe.truth[key], inline=inline))
                continue
            problems.append(
                problem(
                    path,
                    kind="truth_unclaimed",
                    message=f"no sensor in use reads the truth key {key!r}, so nothing would be "
                    "measured against it",
                    fix=f"correct its spelling, name a metric whose sensor reads it, or "
                    f"prefix a note with {NOTE_PREFIX!r}",
                    allowed=read,
                    got=key,
                )
            )
    return problems


def _misplaced_option(path: str, key: str, value: Any, *, inline: bool) -> Problem:
    """A truth key no sensor reads that is an option of the probe's kind: written where the
    probe reads truth, so the probe never sees it."""
    shown = json.dumps(value) if isinstance(value, str) else "..."
    if inline:
        where = f"write it beside `kind`, out of `truth:` (`{key}: {shown}`)"
    else:
        where = (
            f"write it under `options:` in the probe file's frontmatter (`options: {{{key}: "
            f"{shown}}}`); every other frontmatter key but id, kind and effects is truth"
        )
    return problem(
        path,
        kind="truth_unclaimed",
        message=f"{key!r} is an option of the probe's kind, written where the probe reads "
        "truth: no sensor reads it there and the probe never sees it",
        fix=where,
        got=key,
    )


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
                    kind="subjects_differ_in",
                    message=f"{name} differs from the control {control} in {', '.join(differing)}; "
                    "a difference in the result cannot be attributed to one of them",
                    fix="change one option per variant, or accept the confound and say so in "
                    "the report",
                    got=differing,
                )
            )
    return warnings


def _unimplemented(where: str, kind: str, kinds: Kinds) -> Problem:
    """The warning for a kind in use that has no adapter here, or one that cannot be built."""
    named = f"{schema.family_of(kind) or 'kind'} kind {schema.short(kind)}"
    unable = "the experiment can be checked, planned and registered, and `ix run` would refuse it"
    broken = kinds.unbuildable.get(kind)
    if broken is not None:
        return problem(
            where,
            kind="kind_unimplemented",
            message=f"the adapter for {named} cannot be built here ({broken}): {unable}",
            fix="fix or uninstall the extension that registered it; `ix kinds` lists every "
            "adapter that cannot be built, with why",
            got=schema.short(kind),
        )
    elsewhere = (
        f"; an extension failed to load and may be the one that registers it "
        f"({'; '.join(kinds.quarantined)}): `matrix catalog` shows why"
        if kinds.quarantined
        else ""
    )
    return problem(
        where,
        kind="kind_unimplemented",
        message=f"this ix has no adapter for {named}: {unable}",
        fix="use a kind `ix kinds` marks runnable, or run it where an adapter for "
        f"{schema.short(kind)} is installed{elsewhere}",
        got=schema.short(kind),
    )


def _unrunnable(
    experiment: Experiment,
    design: Design,
    probes: ProbeSet,
    adapters: frozenset[str],
    kinds: Kinds,
) -> list[Problem]:
    """A warning for each kind in use with no adapter here, or one that cannot be built, and
    for an analysis this ix does not have."""
    warnings = [
        _unimplemented(where, kind, kinds)
        for where, kind in missing_adapters(experiment, design, probes, adapters)
    ]
    unsupported = analysis_unsupported(design.analysis)
    if unsupported is not None:
        warnings.append(
            problem(
                "genre",
                kind="analysis_unimplemented",
                message=f"{unsupported}: `ix run` would refuse the experiment",
                fix="use a genre whose analysis is one of: " + ", ".join(sorted(ANALYSES)),
                allowed=sorted(ANALYSES),
                got=design.analysis.id,
            )
        )
    return warnings


def _refused(experiment: Experiment, genres: Genres, kinds: Kinds) -> list[Problem]:
    """A warning for each genre file and kind file that was refused, but the genre the
    experiment names (a problem already says why)."""
    used = schema.url("genre", experiment.genre)
    warnings = [
        problem(
            "",
            kind="genre_rejected",
            message=f"genre file {name} was refused ({len(refused)} problem(s)) and is not among "
            "the genres",
            fix="`ix genres` shows why",
            source=refused[0].source,
        )
        for name, refused in genres.rejected.items()
        if name != used
    ]
    warnings += [
        problem(
            "",
            kind="kind_rejected",
            message=f"kind file {name} was refused ({len(refused)} problem(s)) and is not among "
            "the kinds",
            fix="`ix kinds` shows why",
            source=refused[0].source,
        )
        for name, refused in kinds.rejected.items()
    ]
    return warnings


def check(
    experiment: Experiment,
    genres: Genres,
    kinds: Kinds,
    probes: ProbeSet | None,
    *,
    adapters: frozenset[str] | None = None,
) -> Checked:
    """Every problem with a loaded experiment. ``probes`` is None when they could not be
    read (the caller reports why). ``adapters`` is the kinds (type URLs) a run could carry
    out here; a kind in use that is not among them is a warning, since the experiment is
    still a valid declaration."""
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
    problems += _decision(experiment, genre, instruments, probes)
    problems += _placeholders(experiment)
    problems += _probes(experiment, probes, kinds, instruments)

    design = (
        resolve(experiment, genre, kinds, probes, instruments=instruments)
        if genre is not None and roles_fit
        else None
    )
    warnings = _parity(experiment, control if genre and genre.role(CONTROL) else None)
    # Said of a valid experiment only: an invalid one cannot run for reasons already given.
    if not problems and design is not None and probes is not None and adapters is not None:
        warnings += _unrunnable(experiment, design, probes, adapters, kinds)
    warnings += _refused(experiment, genres, kinds)
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
