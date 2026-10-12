"""Validation: every problem with an experiment, found in one pass, nothing run.

:func:`parse` turns a file's data into an :class:`Experiment` or the problems that stop it.
:func:`check` holds a loaded experiment against its genre, its metrics and its probes. Both
report everything they find; neither raises on a wrong experiment.

One rule here is not a genre's to relax: **a model-judged metric never decides a gate.** In a
genre with a verdict, a metric declared ``judged: true`` is refused as the primary and as a
guard. It may still be measured and reported.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ix.domain import schema
from ix.domain.design import assign_roles, resolve
from ix.domain.experiment import STATISTICS, Experiment
from ix.domain.genre import CONTROL
from ix.domain.plan import estimate_spend, trial_counts
from ix.domain.problem import Problem, Validation, from_validation_error

if TYPE_CHECKING:
    from ix.domain.design import ResolvedDesign
    from ix.domain.experiment import Metric, ProbeSet
    from ix.domain.genre import Catalogue, Genre

#: Keys only the earlier harness's ``experiment.yaml`` has.
_EARLIER_KEYS = frozenset({"engine", "trials", "sensor", "sensors", "models"})

#: Rules that judge "no worse than by a margin", so cannot be stated without one.
MARGIN_RULES = frozenset({"non-inferiority", "equivalence"})

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}")

_REQUIRE_FIX = {
    "hypothesis": "hypothesis: <what you expect, in one sentence that could turn out false>",
    "primary": "primary: {metric: <name>, statistic: <statistic>}",
    "bar": 'bar: {op: ">=", value: 0.9}',
    "margin": "margin: 5%",
    "rule": "rule: <one of the genre's rules>",
    "guards": "guards: [{metric: <name>, margin: 5%}]",
    "repeats": "repeats: 5",
    "seed": "seed: 1",
    "spend": "spend: {ceiling: 5.00}",
}


def parse(data: Any, *, source: str = "") -> tuple[Experiment | None, list[Problem]]:
    """An experiment from a file's data, or every reason it is not one."""
    if not isinstance(data, Mapping):
        return None, [
            Problem(
                path="",
                kind="invalid_value",
                message="an experiment file is a YAML mapping",
                fix="start with name:, question:, genre:, subjects:, probes:",
            )
        ]
    earlier = sorted(_EARLIER_KEYS & data.keys())
    if "genre" not in data and (earlier or isinstance(data.get("subjects"), list)):
        found = earlier or ["subjects as a list"]
        return None, [
            Problem(
                path="",
                kind="earlier_format",
                message="this file is written for the earlier harness "
                f"({', '.join(found)}; no genre), which `ix experiment validate` and "
                "`ix run` read",
                fix="use `ix experiment validate <name> --lab <lab>` for this file; to write "
                "it for the experiment model, see `ix --skill`",
            )
        ]
    try:
        return Experiment.model_validate(dict(data)), []
    except ValidationError as error:
        return None, from_validation_error(error)


@dataclass(frozen=True)
class Checked:
    """What :func:`check` found, and the resolved design when the experiment has one."""

    problems: tuple[Problem, ...]
    warnings: tuple[Problem, ...]
    genre: Genre | None
    design: ResolvedDesign | None

    @property
    def valid(self) -> bool:
        return not self.problems


def _genre(experiment: Experiment, catalogue: Catalogue) -> tuple[Genre | None, list[Problem]]:
    genre = catalogue.get(experiment.genre)
    if genre is not None:
        return genre, []
    name = schema.genre(experiment.genre)
    refused = catalogue.rejected.get(name)
    known = ", ".join(schema.short(known) for known in catalogue.names()) or "(none)"
    if refused:
        return None, [
            Problem(
                path="genre",
                kind="genre_unknown",
                message=f"genre {experiment.genre!r} is declared by a genre file ix refused",
                fix=f"fix the genre file (its problems follow), or use one of: {known}",
            ),
            *refused,
        ]
    return None, [
        Problem(
            path="genre",
            kind="genre_unknown",
            message=f"no genre named {experiment.genre!r}. Genres: {known}",
            fix=f"genre: <one of {known}>",
        )
    ]


def _against_genre(experiment: Experiment, genre: Genre) -> list[Problem]:
    problems: list[Problem] = []
    label = schema.short(genre.name)

    if genre.factors == "forbidden" and experiment.factors:
        problems.append(
            Problem(
                path="factors",
                kind="genre_factors",
                message=f"genre {label} takes no factors",
                fix="remove 'factors', or use a genre that sweeps",
            )
        )
    if genre.factors == "required" and not experiment.factors:
        problems.append(
            Problem(
                path="factors",
                kind="genre_factors",
                message=f"genre {label} sweeps at least one factor and none is declared",
                fix="factors: {<name>: [<level>, <level>]}",
            )
        )

    default_rule = genre.defaults.rule
    for field in genre.requires:
        supplied = bool(getattr(experiment, field)) or getattr(experiment, field) == 0
        if field == "rule" and default_rule is not None:
            supplied = True
        if not supplied:
            problems.append(
                Problem(
                    path=field,
                    kind="genre_requires",
                    message=f"genre {label} needs {field!r} and the experiment does not set it",
                    fix=_REQUIRE_FIX[field],
                )
            )

    if experiment.rule is not None and experiment.rule not in genre.rules:
        problems.append(
            Problem(
                path="rule",
                kind="rule_not_allowed",
                message=f"genre {label} decides by {list(genre.rules)}; "
                f"{experiment.rule!r} is not one of them"
                if genre.rules
                else f"genre {label} decides by no rule; {experiment.rule!r} has no meaning here",
                fix=f"rule: {genre.rules[0]}" if genre.rules else "remove 'rule'",
            )
        )
    rule = experiment.rule or (default_rule.value if default_rule else None)
    if rule in MARGIN_RULES and experiment.margin is None and "margin" not in genre.requires:
        problems.append(
            Problem(
                path="margin",
                kind="rule_requires_margin",
                message=f"rule {rule} needs a margin: how much worse still counts as no worse",
                fix="margin: 5%",
            )
        )

    floor = genre.defaults.min_repeats
    if experiment.repeats is not None and experiment.repeats < floor.value:
        problems.append(
            Problem(
                path="repeats",
                kind="genre_min_repeats",
                message=f"genre {label} needs at least {floor.value} repeats to carry its "
                f"finding; {experiment.repeats} given",
                fix=f"repeats: {floor.value}",
            )
        )
    return problems


def _decision_metric(
    experiment: Experiment,
    metric_name: str,
    statistic: str | None,
    path: str,
    *,
    gated: bool,
    by_margin: bool,
) -> tuple[Metric | None, list[Problem]]:
    """Check one metric a decision reads: declared, a legal statistic, fit to decide."""
    metric = experiment.metric(metric_name)
    if metric is None:
        declared = ", ".join(m.name for m in experiment.metrics) or "(none)"
        return None, [
            Problem(
                path=f"{path}.metric",
                kind="metric_unknown",
                message=f"metric {metric_name!r} is not declared. Declared: {declared}",
                fix=f"add it under metrics: {{name: {metric_name}, unit: <unit>, "
                "direction: higher|lower|neither, shape: boolean|scalar|histogram|series}",
            )
        ]
    problems: list[Problem] = []
    legal = STATISTICS[metric.shape]
    if not legal:
        problems.append(
            Problem(
                path=f"{path}.metric",
                kind="statistic_illegal",
                message=f"{metric.name} is a {metric.shape}; a decision reads one number, and "
                f"no statistic of a {metric.shape} is defined",
                fix="decide on a scalar, boolean or histogram metric",
            )
        )
    elif statistic is not None and statistic not in legal:
        problems.append(
            Problem(
                path=f"{path}.statistic",
                kind="statistic_illegal",
                message=f"{metric.name} is a {metric.shape}; {statistic!r} is not a statistic "
                f"of one. Legal: {', '.join(legal)}",
                fix=f"statistic: {legal[0]}",
            )
        )
    if gated and metric.judged:
        problems.append(
            Problem(
                path=f"{path}.metric",
                kind="judged_gate",
                message=f"{metric.name} is judged by a model, and a model-judged reading never "
                "decides a gate",
                fix="decide on a metric that is not judged, and keep "
                f"{metric.name} as a reported metric",
            )
        )
    if gated and by_margin and metric.direction == "neither":
        problems.append(
            Problem(
                path=f"{path}.metric",
                kind="metric_direction",
                message=f"{metric.name} declares direction 'neither', so 'better' and 'no "
                "worse' have no meaning for it",
                fix=f"declare direction: higher or lower for {metric.name}",
            )
        )
    return metric, problems


def _decision(experiment: Experiment, genre: Genre | None) -> list[Problem]:
    problems: list[Problem] = []
    gated = genre is not None and genre.gated
    default_rule = genre.defaults.rule if genre is not None else None
    by_margin = (experiment.rule or (default_rule.value if default_rule else None)) is not None

    if experiment.primary is not None:
        metric, found = _decision_metric(
            experiment,
            experiment.primary.metric,
            experiment.primary.statistic,
            "primary",
            gated=gated,
            by_margin=by_margin,
        )
        problems += found
        bar = experiment.bar
        if metric is not None and bar is not None and bar.unit and bar.unit != metric.unit:
            problems.append(
                Problem(
                    path="bar.value",
                    kind="unit_mismatch",
                    message=f"the bar is in {bar.unit!r} and {metric.name} is in "
                    f"{metric.unit or '(no unit)'!r}",
                    fix=f"state the bar in {metric.unit!r}"
                    if metric.unit
                    else "state the bar as a bare number",
                )
            )
    for index, guard in enumerate(experiment.guards):
        _, found = _decision_metric(
            experiment,
            guard.metric,
            guard.statistic,
            f"guards.{index}",
            gated=gated,
            by_margin=True,
        )
        problems += found
    return problems


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list | tuple):
        return [text for item in value for text in _strings(item)]
    return []


def _bindings(experiment: Experiment) -> list[Problem]:
    """An inline probe's ``{name}`` must be a factor, or an option every subject sets."""
    if not isinstance(experiment.probes, tuple):
        return []
    subjects = list(experiment.subjects.values())
    shared = set(subjects[0].options) if subjects else set()
    for subject in subjects[1:]:
        shared &= set(subject.options)
    bound = set(experiment.factors) | shared
    problems: list[Problem] = []
    for index, probe in enumerate(experiment.probes):
        for key, value in probe.options.items():
            for name in sorted({n for text in _strings(value) for n in _PLACEHOLDER.findall(text)}):
                if name not in bound:
                    problems.append(
                        Problem(
                            path=f"probes.{index}.{key}",
                            kind="unbound_binding",
                            message=f"{{{name}}} is bound to nothing. Bound here: "
                            f"{', '.join(sorted(bound)) or '(nothing)'}",
                            fix=f"declare factors: {{{name}: [...]}}, set {name!r} on every "
                            "subject, or write the value out",
                        )
                    )
    return problems


def _probes(probes: ProbeSet | None) -> list[Problem]:
    if probes is None:
        return []  # the source could not be read; the reader's problem says why
    if not probes.probes:
        return [
            Problem(
                path="probes",
                kind="probes_empty",
                message="the experiment has no probes, so a run would apply no stimulus",
                fix="add at least one probe",
            )
        ]
    repeated = sorted({probe_id for probe_id in probes.ids if probes.ids.count(probe_id) > 1})
    return [
        Problem(
            path="probes",
            kind="probe_duplicate_id",
            message=f"probe id {probe_id!r} is used more than once; readings join on it",
            fix="give each probe its own id",
        )
        for probe_id in repeated
    ]


def _spend(experiment: Experiment, design: ResolvedDesign, probes: ProbeSet) -> list[Problem]:
    subject_trials, calibration = trial_counts(experiment, design, len(probes.probes))
    estimate = estimate_spend(experiment.spend, subject_trials + calibration)
    if (
        estimate.estimated is None
        or estimate.ceiling is None
        or estimate.estimated <= estimate.ceiling
    ):
        return []
    return [
        Problem(
            path="spend.ceiling",
            kind="spend_over_ceiling",
            message=f"the run is estimated at {estimate.estimated:g} {estimate.currency} "
            f"({estimate.basis}), over the ceiling of {estimate.ceiling:g}",
            fix="lower repeats, probes or cells, or raise spend.ceiling to at least "
            f"{estimate.estimated:g}",
        )
    ]


def _parity(experiment: Experiment, control: str | None) -> list[Problem]:
    """Warn when a variant differs from the control in more than one setting: the effects of
    the differences cannot be told apart."""
    if control is None:
        return []
    base = experiment.subjects[control]
    base_settings = {"kind": base.kind, **base.options}
    warnings: list[Problem] = []
    for name, subject in experiment.subjects.items():
        if name == control:
            continue
        settings = {"kind": subject.kind, **subject.options}
        differing = sorted(
            key
            for key in base_settings.keys() | settings.keys()
            if base_settings.get(key) != settings.get(key)
        )
        if len(differing) > 1:
            warnings.append(
                Problem(
                    path=f"subjects.{name}",
                    kind="subjects_differ_in",
                    message=f"{name} differs from the control {control} in "
                    f"{', '.join(differing)}; a difference in the result cannot be "
                    "attributed to one of them",
                    fix="change one setting per variant, or accept the confound and say so "
                    "in the report",
                )
            )
    return warnings


def check(experiment: Experiment, catalogue: Catalogue, probes: ProbeSet | None) -> Checked:
    """Every problem with a loaded experiment. ``probes`` is None when they could not be
    read (the caller reports why)."""
    genre, problems = _genre(experiment, catalogue)
    control: str | None = None
    roles_fit = False
    if genre is not None:
        _, control, role_problems = assign_roles(experiment, genre)
        roles_fit = not role_problems
        problems += role_problems
        problems += _against_genre(experiment, genre)
    problems += _decision(experiment, genre)
    problems += _bindings(experiment)
    problems += _probes(probes)

    design = resolve(experiment, genre) if genre is not None and roles_fit else None
    if design is not None and probes is not None:
        problems += _spend(experiment, design, probes)

    warnings = _parity(experiment, control if genre and genre.role(CONTROL) else None)
    used = schema.genre(experiment.genre)
    for name, refused in catalogue.rejected.items():
        if name != used:
            warnings.append(
                Problem(
                    path="",
                    kind="genre_rejected",
                    message=f"genre file {name} was refused ({len(refused)} problem(s)) and "
                    "is not in the catalogue",
                    fix="`ix genres` shows why",
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
    genre: str = "",
) -> Validation:
    """The validation document for what was found."""
    return Validation(
        experiment=experiment,
        source=source,
        genre=genre,
        valid=not problems,
        problems=problems,
        warnings=warnings,
    )
