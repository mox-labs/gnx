"""The genre: the shape of a decision, as data.

A genre says what an experiment of its kind must declare, which roles its subjects play, the
design defaults (repeats, interleaving, calibration), which analysis decides, and what the
report must contain. It is a record in a YAML file. It holds no code, and it has no field
that can name a probe kind, a sensor, a subject kind or a tool: it ranges over the
observation table, so genres and stimulus kinds add up instead of multiplying.

**Extension is narrowing.** A genre may ``extends`` one other, one level deep. The child
starts from the parent's record and may only tighten it: raise a minimum, lower a maximum,
require more, allow fewer rules, add report sections. It may not loosen a parent rule, change
the analysis or the verdict kind, or add a role, so a finding under the child is still a
valid finding under the parent. :func:`narrowing_violations` is that check.

**Defaults are provisional.** Every default carries a ``note`` saying where it stands; the
built-in ones say they wait on the experimentation canon.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from ix.domain import schema
from ix.domain.problem import Problem, from_validation_error
from ix.domain.schema import Record

#: The role filled by ``control:`` in the experiment, or by the first subject.
CONTROL = "control"

FactorRule = Literal["forbidden", "allowed", "required"]

#: The experiment fields a genre may require. ``rule`` and ``repeats`` are met by the genre's
#: own default when it has one.
REQUIRABLE: frozenset[str] = frozenset(
    {"hypothesis", "primary", "bar", "margin", "rule", "guards", "repeats", "seed", "spend"}
)


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


class Default[T](Record):
    """A value the genre supplies when the experiment is silent, and the standing of it."""

    value: T
    note: str = Field(min_length=1)


class DesignDefaults(Record):
    """How a run of this genre is laid out when the experiment does not say."""

    #: Passes over every (subject × cell × probe).
    repeats: Default[int]
    #: Fewer repeats than this cannot carry the genre's finding.
    min_repeats: Default[int]
    #: Subjects alternate in a seeded random order, so drift spreads over all of them.
    interleave: Default[bool]
    #: The harness runs the control against itself (A/A) to measure the noise floor.
    aa: Default[bool]
    #: The decision rule when the experiment names none.
    rule: Default[str] | None = None

    @model_validator(mode="after")
    def _coherent(self) -> DesignDefaults:
        if self.repeats.value < 1 or self.min_repeats.value < 1:
            raise ValueError("repeats and min_repeats are at least 1")
        if self.repeats.value < self.min_repeats.value:
            raise ValueError(
                f"default repeats {self.repeats.value} is below min_repeats "
                f"{self.min_repeats.value}"
            )
        return self


class AnalysisRef(Record):
    """The analysis that decides, by id, and its fixed parameters."""

    id: str = Field(min_length=1)
    params: dict[str, bool | int | float | str] = {}


class Genre(Record):
    """One genre, complete: a child's record already includes what it took from its parent."""

    schema_: Literal["ix.v1.genre"] = Field(default=schema.GENRE, alias="schema")
    name: str = Field(pattern=schema.GENRE_NAME_PATTERN)
    extends: str | None = None
    #: The typified question, one sentence.
    question: str = Field(min_length=1)
    #: The hypothesis as a template over role names, ``{metric}``, ``{margin}``, ``{bar}``.
    hypothesis_form: str = Field(min_length=1)
    #: The roles subjects play. ``control`` is filled by name or position; the other role
    #: takes every remaining subject.
    subjects: tuple[Role, ...] = Field(min_length=1)
    factors: FactorRule
    #: Experiment fields that must be set (or met by a default of this genre).
    requires: tuple[str, ...] = ()
    #: The decision rules an experiment of this genre may name. Empty: it names none.
    rules: tuple[str, ...] = ()
    defaults: DesignDefaults
    analysis: AnalysisRef
    #: ``three-way``: holds, fails or inconclusive, and a gate may read it. ``none``:
    #: estimates only.
    verdict: Literal["three-way", "none"]
    #: Section ids the report must contain.
    report: tuple[str, ...] = Field(min_length=1)
    #: Kinds of prior result this genre takes for granted. Stated, not yet checked.
    presupposes: tuple[str, ...] = ()
    #: How to write an experiment of this genre: what to decide first, what would falsify it.
    skill: str = Field(min_length=1)

    @model_validator(mode="after")
    def _coherent(self) -> Genre:
        names = [role.name for role in self.subjects]
        if len(set(names)) != len(names):
            raise ValueError("a role is declared more than once")
        if len([name for name in names if name != CONTROL]) > 1:
            raise ValueError(
                f"at most one role besides {CONTROL!r}: ix has one rule for who plays which "
                f"(the control by name or position, the rest by the other role); got {names}"
            )
        unknown = sorted(set(self.requires) - REQUIRABLE)
        if unknown:
            raise ValueError(
                f"requires names {unknown}, which are not experiment fields a genre may "
                f"require; legal: {sorted(REQUIRABLE)}"
            )
        rule = self.defaults.rule
        if rule is not None and rule.value not in self.rules:
            raise ValueError(
                f"default rule {rule.value!r} is not among this genre's rules {list(self.rules)}"
            )
        return self

    @property
    def gated(self) -> bool:
        """Whether a finding of this genre carries a verdict a gate may act on."""
        return self.verdict != "none"

    def role(self, name: str) -> Role | None:
        return next((role for role in self.subjects if role.name == name), None)

    @property
    def subject_bounds(self) -> tuple[int, int | None]:
        """Fewest and most subjects an experiment of this genre may have."""
        low = sum(role.min for role in self.subjects)
        high = (
            None
            if any(role.max is None for role in self.subjects)
            else sum(role.max or 0 for role in self.subjects)
        )
        return low, high


# --- narrowing -------------------------------------------------------------------------

#: How strict each factor rule is. ``allowed`` leaves the choice open; the other two close it.
_OPEN_FACTORS = "allowed"


def _violation(genre: Genre, path: str, message: str, fix: str, source: str) -> Problem:
    return Problem(
        path=path,
        kind="genre_narrowing",
        message=f"{genre.name} extends {genre.extends} and may only tighten it: {message}",
        fix=fix,
        source=source,
    )


def narrowing_violations(parent: Genre, child: Genre, *, source: str = "") -> list[Problem]:
    """Every place ``child`` loosens ``parent`` or adds a mechanism. Empty: it only narrows."""

    def bad(path: str, message: str, fix: str) -> Problem:
        return _violation(child, path, message, fix, source)

    problems: list[Problem] = []

    parent_roles = {role.name: role for role in parent.subjects}
    child_roles = {role.name: role for role in child.subjects}
    for name in sorted(child_roles.keys() - parent_roles.keys()):
        problems.append(
            bad(
                f"subjects.{name}",
                f"role {name!r} is not a role of the parent",
                f"use the parent's roles: {sorted(parent_roles)}",
            )
        )
    for name in sorted(parent_roles.keys() - child_roles.keys()):
        problems.append(
            bad(
                f"subjects.{name}",
                f"the parent's role {name!r} is missing",
                f"keep role {name!r}; narrow its min and max instead of removing it",
            )
        )
    for name in sorted(parent_roles.keys() & child_roles.keys()):
        was, now = parent_roles[name], child_roles[name]
        if now.min < was.min:
            problems.append(
                bad(
                    f"subjects.{name}.min",
                    f"min {now.min} is below the parent's {was.min}",
                    f"min: {was.min} or more",
                )
            )
        if was.max is not None and (now.max is None or now.max > was.max):
            problems.append(
                bad(
                    f"subjects.{name}.max",
                    f"max {now.max if now.max is not None else 'unbounded'} is above the "
                    f"parent's {was.max}",
                    f"max: {was.max} or fewer",
                )
            )

    if parent.factors != _OPEN_FACTORS and child.factors != parent.factors:
        problems.append(
            bad(
                "factors",
                f"the parent says factors are {parent.factors}; {child.factors} reopens it",
                f"factors: {parent.factors}",
            )
        )

    dropped = sorted(set(parent.requires) - set(child.requires))
    if dropped:
        problems.append(
            bad(
                "requires",
                f"the parent requires {dropped} and the child does not",
                f"keep {dropped} in requires",
            )
        )

    added_rules = sorted(set(child.rules) - set(parent.rules))
    if added_rules:
        problems.append(
            bad(
                "rules",
                f"rules {added_rules} are not rules of the parent {list(parent.rules)}",
                f"choose from {list(parent.rules)}",
            )
        )
    if parent.rules and not child.rules:
        problems.append(
            bad(
                "rules",
                "the parent decides by a rule and the child names none",
                f"keep at least one of {list(parent.rules)}",
            )
        )

    if child.defaults.min_repeats.value < parent.defaults.min_repeats.value:
        problems.append(
            bad(
                "defaults.min_repeats",
                f"min_repeats {child.defaults.min_repeats.value} is below the parent's "
                f"{parent.defaults.min_repeats.value}",
                f"min_repeats: {parent.defaults.min_repeats.value} or more",
            )
        )
    for field in ("interleave", "aa"):
        if getattr(parent.defaults, field).value and not getattr(child.defaults, field).value:
            problems.append(
                bad(
                    f"defaults.{field}",
                    f"the parent sets {field} and the child unsets it",
                    f"{field}: true",
                )
            )

    if child.analysis.id != parent.analysis.id:
        problems.append(
            bad(
                "analysis.id",
                f"analysis {child.analysis.id!r} replaces the parent's {parent.analysis.id!r}; "
                "a child fixes parameters of the parent's analysis and adds no mechanism",
                f"id: {parent.analysis.id}",
            )
        )
    for key, value in sorted(parent.analysis.params.items()):
        if key not in child.analysis.params:
            problems.append(
                bad(
                    f"analysis.params.{key}",
                    f"the parent fixes {key}={value!r} and the child drops it",
                    f"{key}: {value}",
                )
            )
        elif child.analysis.params[key] != value:
            problems.append(
                bad(
                    f"analysis.params.{key}",
                    f"the parent fixes {key}={value!r}; {child.analysis.params[key]!r} changes it",
                    f"{key}: {value}",
                )
            )

    if child.verdict != parent.verdict:
        problems.append(
            bad(
                "verdict",
                f"verdict {child.verdict!r} differs from the parent's {parent.verdict!r}",
                f"verdict: {parent.verdict}",
            )
        )

    for field in ("report", "presupposes"):
        missing = [item for item in getattr(parent, field) if item not in getattr(child, field)]
        if missing:
            problems.append(
                bad(
                    field,
                    f"the parent's {field} entries {missing} are missing",
                    f"keep {missing} and add to them",
                )
            )
    return problems


# --- the catalogue ---------------------------------------------------------------------


@dataclass(frozen=True)
class GenreRecord:
    """One genre file as a source hands it over: where it is, and its data or why not."""

    origin: str
    data: Mapping[str, Any] | None = None
    error: str = ""


@dataclass(frozen=True)
class Catalogue:
    """The genres an experiment may name, and the files that were refused, with reasons."""

    genres: Mapping[str, Genre]
    #: Refused genre files, by genre name (or by origin when the file gave no name).
    rejected: Mapping[str, tuple[Problem, ...]]

    def get(self, name: str) -> Genre | None:
        return self.genres.get(schema.genre(name))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.genres))

    def document(self) -> dict[str, Any]:
        """The catalogue as an ``ix.v1.genres`` document."""
        return {
            "schema": schema.GENRES,
            "genres": [genre.document() for genre in self.genres.values()],
            "rejected": [
                {"name": name, "problems": [problem.document() for problem in problems]}
                for name, problems in self.rejected.items()
            ],
        }


def _merged(parent: Mapping[str, Any], child: Mapping[str, Any]) -> dict[str, Any]:
    """The child's record over the parent's: a field the child omits is the parent's.

    ``defaults`` merges key by key, so a child can restate one default and keep the rest.
    """
    merged: dict[str, Any] = {**parent, **child}
    if isinstance(parent.get("defaults"), Mapping) and isinstance(child.get("defaults"), Mapping):
        merged["defaults"] = {**parent["defaults"], **child["defaults"]}
    return merged


def _invalid(origin: str, message: str, fix: str, path: str = "") -> Problem:
    return Problem(path=path, kind="genre_invalid", message=message, fix=fix, source=origin)


def _build(data: Mapping[str, Any], origin: str) -> tuple[Genre | None, list[Problem]]:
    try:
        return Genre.model_validate(dict(data)), []
    except ValidationError as error:
        problems = [
            problem.model_copy(update={"kind": "genre_invalid"})
            if problem.kind == "invalid_value"
            else problem
            for problem in from_validation_error(error, source=origin)
        ]
        return None, problems


def catalogue(records: Iterable[GenreRecord]) -> Catalogue:
    """Build the catalogue from genre files, resolving ``extends`` and checking narrowing.

    A file with any problem is refused whole and the others are unaffected; a child of a
    refused parent is refused with it.
    """
    rejected: dict[str, list[Problem]] = {}
    raw: dict[str, tuple[str, dict[str, Any]]] = {}
    duplicates: set[str] = set()

    for record in records:
        if record.data is None:
            rejected.setdefault(record.origin, []).append(
                _invalid(
                    record.origin,
                    f"the genre file could not be read: {record.error}",
                    "make the file a YAML mapping with the keys of ix.v1.genre",
                )
            )
            continue
        name = record.data.get("name")
        if not isinstance(name, str) or not name:
            rejected.setdefault(record.origin, []).append(
                _invalid(
                    record.origin,
                    "the genre file has no name",
                    "name: <namespace>.v1.genre.<name>",
                    "name",
                )
            )
            continue
        if name in raw or name in duplicates:
            duplicates.add(name)
            first = raw.pop(name, None)
            origins = [record.origin] + ([first[0]] if first else [])
            for origin in origins:
                rejected.setdefault(name, []).append(
                    Problem(
                        path="name",
                        kind="genre_duplicate",
                        message=f"{name} is declared by more than one genre file",
                        fix="give one of them another name; ix refuses both so that "
                        "neither silently wins",
                        source=origin,
                    )
                )
            continue
        raw[name] = (record.origin, dict(record.data))

    genres: dict[str, Genre] = {}
    children: list[str] = []
    for name, (origin, data) in raw.items():
        if data.get("extends") is not None:
            children.append(name)
            continue
        genre, problems = _build(data, origin)
        if genre is None:
            rejected.setdefault(name, []).extend(problems)
        else:
            genres[name] = genre

    for name in children:
        origin, data = raw[name]
        parent_name = schema.genre(str(data["extends"]))
        if parent_name not in raw:
            rejected.setdefault(name, []).append(
                Problem(
                    path="extends",
                    kind="genre_extends_unknown",
                    message=f"{name} extends {parent_name}, which is not a genre ix can see",
                    fix=f"extend one of: {', '.join(sorted(raw)) or '(none)'}",
                    source=origin,
                )
            )
            continue
        if raw[parent_name][1].get("extends") is not None:
            rejected.setdefault(name, []).append(
                Problem(
                    path="extends",
                    kind="genre_depth",
                    message=f"{name} extends {parent_name}, which itself extends "
                    f"{raw[parent_name][1]['extends']}; a genre extends at most one level",
                    fix=f"extend {schema.genre(str(raw[parent_name][1]['extends']))} directly",
                    source=origin,
                )
            )
            continue
        parent = genres.get(parent_name)
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
            genres[name] = genre

    return Catalogue(
        genres=dict(sorted(genres.items())),
        rejected={name: tuple(problems) for name, problems in sorted(rejected.items())},
    )
