"""The genre: the shape of a decision, as data.

A genre says which roles an experiment's subjects play, what the experiment must set, which
comparisons it may make, how a run is laid out (repeats, order, calibration, estimator),
which analysis decides, and what the report must contain. It is a record in a YAML file. It
holds no code, and it has no field that can name a probe kind, a sensor, a subject kind or a
tool: it ranges over the observation table, so genres and kinds add up instead of
multiplying.

**The hypothesis is rendered, not written.** Each comparison carries the sentence it claims
(``"{variant} is no worse than {control} by more than {margin} on {metric}"``); a genre
without comparisons has one ``hypothesis_form``. A comparison whose sentence mentions
``{margin}`` needs a margin: that is the only place the rule is stated.

**Extension is narrowing.** A genre may ``extends`` one other, one level deep. The child
starts from the parent's record and may only tighten it: raise a minimum, lower a maximum,
require more, allow fewer comparisons, add report sections. It may not loosen the parent,
touch the analysis or its parameters, change the verdict form or the estimator, or add a
role, so a finding under the child is still a valid finding under the parent.
:func:`narrowing_violations` is that check.

**The design is provisional.** Every genre's ``design`` carries a ``note`` saying where it
stands; the built-in ones say they wait on the experimentation canon.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from ix.domain import schema
from ix.domain.kind import placeholders
from ix.domain.problem import Problem, from_validation_error, problem
from ix.domain.schema import Record

#: The role filled by ``control:`` in the experiment, or by the first subject.
CONTROL = "control"

FactorPolicy = Literal["forbidden", "allowed", "required"]
Order = Literal["interleaved", "sequential"]
Calibration = Literal["aa", "none"]
VerdictForm = Literal["three-way", "none"]

#: The experiment fields a genre may require.
MUST_SETTABLE: frozenset[str] = frozenset(
    {
        "rationale",
        "primary",
        "threshold",
        "margin",
        "comparison",
        "guardrails",
        "repeats",
        "seed",
        "spend",
    }
)

#: What a hypothesis sentence may refer to besides the genre's role names.
FORM_PLACEHOLDERS: frozenset[str] = frozenset({"metric", "margin", "threshold", "factors"})


class Role(Record):
    """A role subjects play in the design, and how many may play it. No ``max``: unbounded."""

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    min: int = Field(default=1, ge=0)
    max: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Role:
        if self.max is not None and self.max < self.min:
            raise ValueError(f"role {self.name!r}: max {self.max} is below min {self.min}")
        return self


class Repeats(Record):
    """How many passes over every (subject × cell × probe): the default, and the fewest that
    can carry the genre's finding."""

    default: int = Field(ge=1)
    min: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Repeats:
        if self.default < self.min:
            raise ValueError(f"default repeats {self.default} is below min {self.min}")
        return self


class GenreDesign(Record):
    """How a run of this genre is laid out."""

    repeats: Repeats
    #: ``interleaved``: subjects alternate in a seeded random order, so drift spreads over
    #: all of them. ``sequential``: in the order written.
    order: Order
    #: ``aa``: the control also runs against itself, to measure the noise floor.
    calibration: Calibration
    #: How the trials' numbers combine when the experiment names no estimator. A boolean
    #: metric always combines as a rate.
    estimator: Literal["mean", "median", "min", "max"] = "mean"
    #: The standing of these choices, printed once under a plan.
    note: str = Field(min_length=1)


class AnalysisRef(Record):
    """The analysis that decides, by id, and its fixed parameters."""

    id: str = Field(min_length=1)
    params: dict[str, bool | int | float | str] = {}


class Genre(Record):
    """One genre, complete: a child's record already includes what it took from its parent."""

    schema_: Literal["ix.v1.genre"] = Field(default=schema.GENRE, alias="schema")
    name: str
    extends: str | None = None
    #: The typified question, one sentence.
    question: str = Field(min_length=1)
    #: The hypothesis sentence of a genre that makes no comparison.
    hypothesis_form: str = ""
    #: The roles subjects play. ``control`` is filled by name or position; the other role
    #: takes every remaining subject.
    roles: tuple[Role, ...] = Field(min_length=1)
    factors: FactorPolicy
    #: Experiment fields that must be set (``comparison`` is met by ``default_comparison``).
    must_set: tuple[str, ...] = ()
    #: The comparisons an experiment of this genre may name, each with the sentence it claims.
    comparisons: dict[str, str] = {}
    #: The comparison when the experiment names none.
    default_comparison: str | None = None
    design: GenreDesign
    analysis: AnalysisRef
    #: ``three-way``: holds, fails or inconclusive. ``none``: estimates only.
    verdict_form: VerdictForm
    #: Section ids the report must contain.
    report: tuple[str, ...] = Field(min_length=1)
    #: How to write an experiment of this genre: what to decide first, what would falsify it.
    guidance: str = Field(min_length=1)

    @model_validator(mode="after")
    def _coherent(self) -> Genre:
        if schema.family_of(self.name) != "genre":
            raise ValueError(
                f"{self.name!r} is not a genre's type URL; write <namespace>.v1.genre.<name>"
            )
        names = [role.name for role in self.roles]
        if len(set(names)) != len(names):
            raise ValueError("a role is declared more than once")
        if len([name for name in names if name != CONTROL]) > 1:
            raise ValueError(
                f"at most one role besides {CONTROL!r}: ix has one rule for who plays which "
                f"(the control by name or position, the rest by the other role); got {names}"
            )
        unknown = sorted(set(self.must_set) - MUST_SETTABLE)
        if unknown:
            raise ValueError(
                f"must_set names {unknown}, which are not experiment fields a genre may "
                f"require; legal: {sorted(MUST_SETTABLE)}"
            )
        if self.default_comparison is not None and self.default_comparison not in self.comparisons:
            raise ValueError(
                f"default_comparison {self.default_comparison!r} is not among this genre's "
                f"comparisons {list(self.comparisons)}"
            )
        if not self.comparisons and not self.hypothesis_form:
            raise ValueError("a genre has comparisons, or a hypothesis_form, to render")
        if self.design.calibration == "aa" and CONTROL not in names:
            raise ValueError(
                f"calibration aa runs the control against itself; add a {CONTROL} role"
            )
        legal = FORM_PLACEHOLDERS | set(names)
        for label, form in {"hypothesis_form": self.hypothesis_form, **self.comparisons}.items():
            stray = sorted(set(placeholders(form)) - legal)
            if stray:
                raise ValueError(
                    f"{label} refers to {stray}; a hypothesis sentence may refer to {sorted(legal)}"
                )
        return self

    @property
    def label(self) -> str:
        """The name an experiment writes: short for a built-in, the type URL otherwise."""
        return schema.short(self.name)

    @property
    def decides(self) -> bool:
        """Whether a finding of this genre carries a verdict."""
        return self.verdict_form != "none"

    def role(self, name: str) -> Role | None:
        return next((role for role in self.roles if role.name == name), None)

    def needs_margin(self, comparison: str | None) -> bool:
        """Whether ``comparison`` is stated in terms of a margin."""
        return comparison is not None and "margin" in placeholders(
            self.comparisons.get(comparison, "")
        )

    @property
    def author_must_set(self) -> tuple[str, ...]:
        """What an author of this genre has to write: ``must_set`` without what a default
        already meets, plus the margin a comparison is stated in."""
        fields = [
            field
            for field in self.must_set
            if not (field == "comparison" and self.default_comparison is not None)
        ]
        needing = [name for name in self.comparisons if self.needs_margin(name)]
        if needing and "margin" not in fields:
            fields.append(
                "margin"
                if len(needing) == len(self.comparisons)
                else f"margin (for {', '.join(needing)})"
            )
        return tuple(fields)

    @property
    def subject_bounds(self) -> tuple[int, int | None]:
        """Fewest and most subjects an experiment of this genre may have."""
        low = sum(role.min for role in self.roles)
        high = (
            None
            if any(role.max is None for role in self.roles)
            else sum(role.max or 0 for role in self.roles)
        )
        return low, high


# --- narrowing -------------------------------------------------------------------------

#: Stricter comes later: a child may move right, never left.
_ORDER_STRICTNESS = ("sequential", "interleaved")
_CALIBRATION_STRICTNESS = ("none", "aa")


def narrowing_violations(parent: Genre, child: Genre, *, source: str = "") -> list[Problem]:
    """Every place ``child`` loosens ``parent`` or adds a mechanism. Empty: it only narrows."""

    def bad(path: str, message: str, fix: str) -> Problem:
        return problem(
            path,
            "genre_narrowing",
            f"{child.name} extends {child.extends} and may only tighten it: {message}",
            fix=fix,
            source=source,
        )

    problems: list[Problem] = []

    parent_roles = {role.name: role for role in parent.roles}
    child_roles = {role.name: role for role in child.roles}
    for name in sorted(child_roles.keys() - parent_roles.keys()):
        problems.append(
            bad(
                f"roles.{name}",
                f"role {name!r} is not a role of the parent",
                f"use the parent's roles: {sorted(parent_roles)}",
            )
        )
    for name in sorted(parent_roles.keys() - child_roles.keys()):
        problems.append(
            bad(
                f"roles.{name}",
                f"the parent's role {name!r} is missing",
                f"keep role {name!r}; narrow its min and max instead of removing it",
            )
        )
    for name in sorted(parent_roles.keys() & child_roles.keys()):
        was, now = parent_roles[name], child_roles[name]
        if now.min < was.min:
            problems.append(
                bad(
                    f"roles.{name}.min",
                    f"min {now.min} is below the parent's {was.min}",
                    f"min: {was.min} or more",
                )
            )
        if was.max is not None and (now.max is None or now.max > was.max):
            problems.append(
                bad(
                    f"roles.{name}.max",
                    f"max {now.max if now.max is not None else 'unbounded'} is above the "
                    f"parent's {was.max}",
                    f"max: {was.max} or fewer",
                )
            )

    if parent.factors != "allowed" and child.factors != parent.factors:
        problems.append(
            bad(
                "factors",
                f"the parent says factors are {parent.factors}; {child.factors} reopens it",
                f"factors: {parent.factors}",
            )
        )

    dropped = sorted(set(parent.must_set) - set(child.must_set))
    if dropped:
        problems.append(
            bad(
                "must_set",
                f"the parent requires {dropped} and the child does not",
                f"keep {dropped} in must_set",
            )
        )

    added = sorted(set(child.comparisons) - set(parent.comparisons))
    if added:
        problems.append(
            bad(
                "comparisons",
                f"comparisons {added} are not comparisons of the parent {list(parent.comparisons)}",
                f"choose from {list(parent.comparisons)}",
            )
        )
    if parent.comparisons and not child.comparisons:
        problems.append(
            bad(
                "comparisons",
                "the parent decides by a comparison and the child names none",
                f"keep at least one of {list(parent.comparisons)}",
            )
        )

    if child.design.repeats.min < parent.design.repeats.min:
        problems.append(
            bad(
                "design.repeats.min",
                f"min {child.design.repeats.min} is below the parent's {parent.design.repeats.min}",
                f"min: {parent.design.repeats.min} or more",
            )
        )
    for field, scale in (("order", _ORDER_STRICTNESS), ("calibration", _CALIBRATION_STRICTNESS)):
        was, now = getattr(parent.design, field), getattr(child.design, field)
        if scale.index(now) < scale.index(was):
            problems.append(
                bad(
                    f"design.{field}",
                    f"the parent sets {field} {was} and the child relaxes it to {now}",
                    f"{field}: {was}",
                )
            )
    if child.design.estimator != parent.design.estimator:
        problems.append(
            bad(
                "design.estimator",
                f"estimator {child.design.estimator!r} replaces the parent's "
                f"{parent.design.estimator!r}, which changes what the finding means",
                f"estimator: {parent.design.estimator}",
            )
        )

    if child.analysis.id != parent.analysis.id:
        problems.append(
            bad(
                "analysis.id",
                f"analysis {child.analysis.id!r} replaces the parent's {parent.analysis.id!r}; "
                "a child adds no mechanism",
                f"id: {parent.analysis.id}",
            )
        )
    for key in sorted(child.analysis.params.keys() | parent.analysis.params.keys()):
        fixed = parent.analysis.params.get(key)
        given = child.analysis.params.get(key)
        if key not in parent.analysis.params:
            problems.append(
                bad(
                    f"analysis.params.{key}",
                    f"parameter {key}={given!r} is not a parameter of the parent's analysis; "
                    "a new parameter can change what the finding means",
                    f"remove {key!r}",
                )
            )
        elif given != fixed:
            change = (
                "and the child drops it"
                if key not in child.analysis.params
                else f"and {given!r} changes it"
            )
            problems.append(
                bad(
                    f"analysis.params.{key}",
                    f"the parent fixes {key}={fixed!r} {change}",
                    f"{key}: {fixed}",
                )
            )

    if child.verdict_form != parent.verdict_form:
        problems.append(
            bad(
                "verdict_form",
                f"verdict form {child.verdict_form!r} differs from the parent's "
                f"{parent.verdict_form!r}",
                f"verdict_form: {parent.verdict_form}",
            )
        )

    missing = [item for item in parent.report if item not in child.report]
    if missing:
        problems.append(
            bad(
                "report",
                f"the parent's report sections {missing} are missing",
                f"keep {missing} and add to them",
            )
        )
    return problems


# --- the set of genres -----------------------------------------------------------------


@dataclass(frozen=True)
class GenreFile:
    """One genre file as read: where it is, and its data or why it could not be read."""

    origin: str
    data: Mapping[str, Any] | None = None
    error: str = ""


class Genres(Record):
    """The genres an experiment may name, and the files that were refused, with reasons."""

    schema_: Literal["ix.v1.genres"] = Field(default=schema.GENRES, alias="schema")
    genres: dict[str, Genre] = {}
    #: Refused genre files, by genre name (or by origin when the file gave no name).
    rejected: dict[str, tuple[Problem, ...]] = {}

    def get(self, name: str) -> Genre | None:
        return self.genres.get(schema.url("genre", name))

    def names(self) -> tuple[str, ...]:
        return tuple(schema.short(name) for name in self.genres)


def _merged(parent: Mapping[str, Any], child: Mapping[str, Any]) -> dict[str, Any]:
    """The child's record over the parent's: a field the child omits is the parent's.

    ``design`` merges key by key (and ``design.repeats`` within it), so a child can restate
    one choice and keep the rest.
    """
    merged: dict[str, Any] = {**parent, **child}
    was, now = parent.get("design"), child.get("design")
    if isinstance(was, Mapping) and isinstance(now, Mapping):
        design = {**was, **now}
        if isinstance(was.get("repeats"), Mapping) and isinstance(now.get("repeats"), Mapping):
            design["repeats"] = {**was["repeats"], **now["repeats"]}
        merged["design"] = design
    return merged


def _invalid(origin: str, message: str, fix: str, path: str = "") -> Problem:
    return problem(path, "genre_invalid", message, fix=fix, source=origin)


def _build(data: Mapping[str, Any], origin: str) -> tuple[Genre | None, list[Problem]]:
    try:
        return Genre.model_validate(dict(data)), []
    except ValidationError as error:
        return None, [
            found.model_copy(update={"kind": "genre_invalid"})
            if found.kind == "invalid_value"
            else found
            for found in from_validation_error(error, source=origin)
        ]


def genres(files: Iterable[GenreFile]) -> Genres:
    """Build the set of genres from genre files, resolving ``extends`` and checking narrowing.

    A file with any problem is refused whole and the others are unaffected; a child of a
    refused parent is refused with it.
    """
    rejected: dict[str, list[Problem]] = {}
    raw: dict[str, tuple[str, dict[str, Any]]] = {}
    duplicated: set[str] = set()

    for file in files:
        if file.data is None:
            rejected.setdefault(file.origin, []).append(
                _invalid(
                    file.origin,
                    f"the genre file could not be read: {file.error}",
                    "make the file a YAML mapping with the keys of ix.v1.genre",
                )
            )
            continue
        name = file.data.get("name")
        if not isinstance(name, str) or not name:
            rejected.setdefault(file.origin, []).append(
                _invalid(
                    file.origin,
                    "the genre file has no name",
                    "name: <namespace>.v1.genre.<name>",
                    "name",
                )
            )
            continue
        if name in raw or name in duplicated:
            duplicated.add(name)
            first = raw.pop(name, None)
            for origin in [file.origin] + ([first[0]] if first else []):
                rejected.setdefault(name, []).append(
                    problem(
                        "name",
                        "genre_duplicate",
                        f"{name} is declared by more than one genre file",
                        fix="give one of them another name; ix refuses both so that "
                        "neither silently wins",
                        source=origin,
                    )
                )
            continue
        raw[name] = (file.origin, dict(file.data))

    accepted: dict[str, Genre] = {}
    children: list[str] = []
    for name, (origin, data) in raw.items():
        if data.get("extends") is not None:
            children.append(name)
            continue
        genre, problems = _build(data, origin)
        if genre is None:
            rejected.setdefault(name, []).extend(problems)
        else:
            accepted[name] = genre

    for name in children:
        origin, data = raw[name]
        parent_name = schema.url("genre", str(data["extends"]))
        if parent_name not in raw:
            rejected.setdefault(name, []).append(
                problem(
                    "extends",
                    "genre_extends_unknown",
                    f"{name} extends {parent_name}, which is not a genre ix can see",
                    allowed=sorted(raw),
                    got=parent_name,
                    source=origin,
                )
            )
            continue
        if raw[parent_name][1].get("extends") is not None:
            grandparent = schema.url("genre", str(raw[parent_name][1]["extends"]))
            rejected.setdefault(name, []).append(
                problem(
                    "extends",
                    "genre_depth",
                    f"{name} extends {parent_name}, which itself extends {grandparent}; a "
                    "genre extends at most one level",
                    fix=f"extend {grandparent} directly",
                    source=origin,
                )
            )
            continue
        parent = accepted.get(parent_name)
        if parent is None:
            rejected.setdefault(name, []).append(
                _invalid(
                    origin,
                    f"{name} extends {parent_name}, which was refused",
                    f"fix {parent_name} first",
                    "extends",
                )
            )
            continue
        merged = _merged(raw[parent_name][1], data)
        merged["extends"] = parent_name
        genre, problems = _build(merged, origin)
        if genre is not None:
            problems = narrowing_violations(parent, genre, source=origin)
        if problems or genre is None:
            rejected.setdefault(name, []).extend(problems)
        else:
            accepted[name] = genre

    return Genres(
        genres=dict(sorted(accepted.items())),
        rejected={name: tuple(found) for name, found in sorted(rejected.items())},
    )
