"""The genre: the shape of a decision, as data.

A genre says which roles subjects play, what an experiment must set, which comparisons it
may make, how a run is laid out, which analysis decides and what the report contains. It
names no kind, sensor or tool, so genres and kinds add up instead of multiplying.

**The hypothesis is rendered, not written.** Each comparison carries the sentence it claims;
a genre without comparisons has one ``hypothesis_form``. A sentence that mentions
``{margin}`` needs a margin: that is the only place the rule is stated.

**Extension is narrowing.** A child ``extends`` one parent, one level deep, and may only
tighten it, so a finding under the child is still one under the parent.
:func:`narrowing_violations` is that check.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from ix.domain import schema
from ix.domain.kind import claim, placeholders, unreadable
from ix.domain.problem import Problem, from_validation_error, problem
from ix.domain.schema import DeclarationFile, Record

#: The role filled by ``control:`` in the experiment, or by the first subject.
CONTROL = "control"

FactorPolicy = Literal["forbidden", "allowed", "required"]
Order = Literal["interleaved", "sequential"]
Calibration = Literal["aa", "none"]
VerdictForm = Literal["three-way", "none"]

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
    #: ``aa``: the control also runs against itself, a check that the environment held still.
    calibration: Calibration
    #: How the trials' numbers combine when the experiment names no estimator. A boolean
    #: or a count metric always combines as a rate.
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
        if self.verdict_form != "none" and self.design.estimator in ("min", "max"):
            raise ValueError(
                f"estimator {self.design.estimator} has no interval ix decides on, and this "
                "genre gives a verdict; use mean or median"
            )
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


#: One way a child loosens its parent: (path, what is wrong, the fix).
type _Loosened = tuple[str, str, str]


def _roles_loosened(parent: Genre, child: Genre) -> list[_Loosened]:
    was_roles = {role.name: role for role in parent.roles}
    now_roles = {role.name: role for role in child.roles}
    found: list[_Loosened] = [
        (
            f"roles.{name}",
            f"role {name!r} is not a role of the parent",
            f"use the parent's roles: {sorted(was_roles)}",
        )
        for name in sorted(now_roles.keys() - was_roles.keys())
    ]
    found += [
        (
            f"roles.{name}",
            f"the parent's role {name!r} is missing",
            f"keep role {name!r}; narrow its min and max instead of removing it",
        )
        for name in sorted(was_roles.keys() - now_roles.keys())
    ]
    for name in sorted(was_roles.keys() & now_roles.keys()):
        was, now = was_roles[name], now_roles[name]
        if now.min < was.min:
            found.append(
                (
                    f"roles.{name}.min",
                    f"min {now.min} is below the parent's {was.min}",
                    f"min: {was.min} or more",
                )
            )
        if was.max is not None and (now.max is None or now.max > was.max):
            above = now.max if now.max is not None else "unbounded"
            found.append(
                (
                    f"roles.{name}.max",
                    f"max {above} is above the parent's {was.max}",
                    f"max: {was.max} or fewer",
                )
            )
    return found


def _choices_loosened(parent: Genre, child: Genre) -> list[_Loosened]:
    found: list[_Loosened] = []
    if parent.factors != "allowed" and child.factors != parent.factors:
        found.append(
            (
                "factors",
                f"the parent says factors are {parent.factors}; {child.factors} reopens it",
                f"factors: {parent.factors}",
            )
        )
    dropped = sorted(set(parent.must_set) - set(child.must_set))
    if dropped:
        found.append(
            (
                "must_set",
                f"the parent requires {dropped} and the child does not",
                f"keep {dropped} in must_set",
            )
        )
    added = sorted(set(child.comparisons) - set(parent.comparisons))
    if added:
        found.append(
            (
                "comparisons",
                f"comparisons {added} are not comparisons of the parent {list(parent.comparisons)}",
                f"choose from {list(parent.comparisons)}",
            )
        )
    if parent.comparisons and not child.comparisons:
        found.append(
            (
                "comparisons",
                "the parent decides by a comparison and the child names none",
                f"keep at least one of {list(parent.comparisons)}",
            )
        )
    return found


def _design_loosened(parent: Genre, child: Genre) -> list[_Loosened]:
    was, now = parent.design, child.design
    found: list[_Loosened] = []
    if now.repeats.min < was.repeats.min:
        found.append(
            (
                "design.repeats.min",
                f"min {now.repeats.min} is below the parent's {was.repeats.min}",
                f"min: {was.repeats.min} or more",
            )
        )
    for field, scale in (("order", _ORDER_STRICTNESS), ("calibration", _CALIBRATION_STRICTNESS)):
        strict, relaxed = getattr(was, field), getattr(now, field)
        if scale.index(relaxed) < scale.index(strict):
            found.append(
                (
                    f"design.{field}",
                    f"the parent sets {field} {strict} and the child relaxes it to {relaxed}",
                    f"{field}: {strict}",
                )
            )
    if now.estimator != was.estimator:
        found.append(
            (
                "design.estimator",
                f"estimator {now.estimator!r} replaces the parent's "
                f"{was.estimator!r}, which changes what the finding means",
                f"estimator: {was.estimator}",
            )
        )
    return found


def _analysis_loosened(parent: Genre, child: Genre) -> list[_Loosened]:
    was, now = parent.analysis, child.analysis
    found: list[_Loosened] = []
    if now.id != was.id:
        found.append(
            (
                "analysis.id",
                f"analysis {now.id!r} replaces the parent's {was.id!r}; a child adds no mechanism",
                f"id: {was.id}",
            )
        )
    for key in sorted(now.params.keys() | was.params.keys()):
        fixed, given = was.params.get(key), now.params.get(key)
        if key not in was.params:
            found.append(
                (
                    f"analysis.params.{key}",
                    f"parameter {key}={given!r} is not a parameter of "
                    "the parent's analysis; a new parameter can change what the finding means",
                    f"remove {key!r}",
                )
            )
        elif given != fixed:
            change = (
                "and the child drops it" if key not in now.params else f"and {given!r} changes it"
            )
            found.append(
                (
                    f"analysis.params.{key}",
                    f"the parent fixes {key}={fixed!r} {change}",
                    f"{key}: {fixed}",
                )
            )
    return found


def _outputs_loosened(parent: Genre, child: Genre) -> list[_Loosened]:
    found: list[_Loosened] = []
    if child.verdict_form != parent.verdict_form:
        found.append(
            (
                "verdict_form",
                f"verdict form {child.verdict_form!r} differs from the parent's "
                f"{parent.verdict_form!r}",
                f"verdict_form: {parent.verdict_form}",
            )
        )
    missing = [item for item in parent.report if item not in child.report]
    if missing:
        found.append(
            (
                "report",
                f"the parent's report sections {missing} are missing",
                f"keep {missing} and add to them",
            )
        )
    return found


def narrowing_violations(parent: Genre, child: Genre, *, source: str = "") -> list[Problem]:
    """Every place ``child`` loosens ``parent`` or adds a mechanism. Empty: it only narrows."""
    loosened = [
        *_roles_loosened(parent, child),
        *_choices_loosened(parent, child),
        *_design_loosened(parent, child),
        *_analysis_loosened(parent, child),
        *_outputs_loosened(parent, child),
    ]
    return [
        problem(
            path,
            kind="genre_narrowing",
            message=f"{child.name} extends {child.extends} and may only tighten it: {message}",
            fix=fix,
            source=source,
        )
        for path, message, fix in loosened
    ]


# --- the set of genres -----------------------------------------------------------------


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


def _invalid(origin: str, *, message: str, fix: str, path: str = "") -> Problem:
    return problem(path, kind="genre_invalid", message=message, fix=fix, source=origin)


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


type _Rejected = dict[str, list[Problem]]
type _Raw = dict[str, tuple[DeclarationFile, dict[str, Any]]]


def _named(files: Iterable[DeclarationFile], rejected: _Rejected) -> _Raw:
    """Each readable genre file by the name it declares; the rest go to ``rejected``."""
    raw: _Raw = {}
    duplicated: set[str] = set()
    for file in files:
        if file.data is None:
            rejected.setdefault(file.origin, []).extend(file.problems or unreadable(file.origin))
            continue
        name = file.data.get("name")
        if not isinstance(name, str) or not name:
            rejected.setdefault(file.origin, []).append(
                _invalid(
                    file.origin,
                    message="the genre file has no name",
                    fix="name: <namespace>.v1.genre.<name>",
                    path="name",
                )
            )
            continue
        claim(name, file, dict(file.data), raw, duplicated, rejected, noun="genre")
    return raw


def _child(
    name: str, origin: str, data: dict[str, Any], raw: _Raw, accepted: dict[str, Genre]
) -> tuple[Genre | None, list[Problem]]:
    """A genre that extends another, merged onto its parent and held to narrow it."""
    parent_name = schema.url("genre", str(data["extends"]))
    if parent_name not in raw:
        return None, [
            problem(
                "extends",
                kind="name_unknown",
                message=f"{name} extends {parent_name}, which is not a genre ix can see",
                allowed=sorted(raw),
                got=parent_name,
                source=origin,
            )
        ]
    parent_data = raw[parent_name][1]
    if parent_data.get("extends") is not None:
        grandparent = schema.url("genre", str(parent_data["extends"]))
        return None, [
            problem(
                "extends",
                kind="genre_depth",
                message=f"{name} extends {parent_name}, which itself extends {grandparent}; a "
                "genre extends at most one level",
                fix=f"extend {grandparent} directly",
                source=origin,
            )
        ]
    parent = accepted.get(parent_name)
    if parent is None:
        return None, [
            _invalid(
                origin,
                message=f"{name} extends {parent_name}, which was refused",
                fix=f"fix {parent_name} first",
                path="extends",
            )
        ]
    merged = {**_merged(parent_data, data), "extends": parent_name}
    genre, problems = _build(merged, origin)
    if genre is None:
        return None, problems
    violations = narrowing_violations(parent, genre, source=origin)
    return (None, violations) if violations else (genre, [])


def genres(files: Iterable[DeclarationFile], *, grammar: schema.Grammar) -> Genres:
    """Build the set of genres from genre files, resolving ``extends`` and checking narrowing.

    A file with any problem is refused whole and the others are unaffected; a name
    ``grammar`` refuses is a problem; a child of a refused parent is refused with it. When
    two files declare one name, a file that does not own it is refused alone if the other
    does, and both are refused otherwise.
    """
    rejected: _Rejected = {}
    raw = _named(files, rejected)
    for name, (file, _) in list(raw.items()):
        misnamed = grammar(name)
        if misnamed is not None:
            rejected.setdefault(name, []).append(
                _invalid(
                    file.origin,
                    message=misnamed,
                    fix=f"rename the genre: {schema.NAMING}",
                    path="name",
                )
            )
            del raw[name]
    accepted: dict[str, Genre] = {}
    for name, (file, data) in raw.items():
        if data.get("extends") is None:
            genre, problems = _build(data, file.origin)
            if genre is None:
                rejected.setdefault(name, []).extend(problems)
            else:
                accepted[name] = genre
    for name, (file, data) in raw.items():
        if data.get("extends") is not None:
            genre, problems = _child(name, file.origin, data, raw, accepted)
            if genre is None:
                rejected.setdefault(name, []).extend(problems)
            else:
                accepted[name] = genre

    return Genres(
        genres=dict(sorted(accepted.items())),
        rejected={name: tuple(found) for name, found in sorted(rejected.items())},
    )
