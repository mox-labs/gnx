"""Kinds: what a subject, a probe, a sensor or an environment of a given kind is, as data.

A kind's declaration says which options it takes (their types, which are required), what
using it does to the world (``effects``), which facets it ``offers`` and which it ``needs``,
and, for a sensor, the metrics it provides (with whether a model judges them) and the probe
truth it reads. ``validate`` holds an experiment to these declarations and ``plan`` reports
their effects, so neither depends on the author asserting them.

A declaration starts nothing and measures nothing. The code that does comes with the run
path and will carry the same record.

**Facets** are how kinds fit together without knowing each other. A subject kind offers how
it can be reached (``url``, ``pid``, ``agent``); a probe kind needs some of those and offers
what applying it leaves behind (``load``, ``response``, ``usage``); a sensor needs what it
measures from.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from ix.domain import schema
from ix.domain.experiment import Direction, Name, Shape
from ix.domain.problem import Problem, from_validation_error, problem
from ix.domain.schema import Record

Family = Literal["subject", "probe", "sensor", "environment"]
KIND_FAMILIES: tuple[str, ...] = ("subject", "probe", "sensor", "environment")

#: What using a kind can do to the world. A closed set, so a plan can be read at a glance
#: and a rule can act on one (a kind that spends needs a ceiling).
EFFECTS: dict[str, str] = {
    "process": "starts a process on this machine",
    "network": "sends requests over the network",
    "spend": "spends money",
    "tools": "lets a model call tools",
    "code": "executes generated code",
    "containers": "starts containers",
    "filesystem": "writes files outside the run's own record",
}
Effect = Literal["process", "network", "spend", "tools", "code", "containers", "filesystem"]

OptionType = Literal["string", "integer", "number", "boolean", "duration", "list", "mapping", "any"]

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_DURATION = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m|h)\s*$")
_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

#: The prefix of a key that is a note: it sits beside a kind's options and is not checked.
NOTE_PREFIX = "x-"


def placeholders(value: Any) -> list[str]:
    """Every ``{name}`` in the strings of ``value``, in order of appearance."""
    if isinstance(value, str):
        return _PLACEHOLDER.findall(value)
    if isinstance(value, Mapping):
        return [name for item in value.values() for name in placeholders(item)]
    if isinstance(value, list | tuple):
        return [name for item in value for name in placeholders(item)]
    return []


def fill(value: Any, bound: Mapping[str, Any]) -> Any:
    """``value`` with each ``{name}`` that ``bound`` knows replaced by its level."""
    if isinstance(value, str):
        return _PLACEHOLDER.sub(
            lambda match: str(bound[match.group(1)]) if match.group(1) in bound else match.group(0),
            value,
        )
    return value


def seconds(value: Any) -> float | None:
    """A duration option as seconds: ``60s``, ``2m``, ``500ms``, ``1h`` or a bare number of
    seconds. None when it is not a duration."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        match = _DURATION.match(value)
        if match:
            return float(match.group(1)) * _SECONDS[match.group(2)]
    return None


class Metric(Record):
    """A measurable a sensor provides: its unit, which way is better, and the shape of one
    reading. ``judged`` is true when a model produces the reading."""

    name: Name
    unit: str = ""
    direction: Direction
    shape: Shape
    judged: bool = False
    description: str = ""


class Option(Record):
    """One option a kind takes."""

    type: OptionType = "string"
    required: bool = False
    #: The legal values, where the option is a closed choice.
    allowed: tuple[str, ...] = ()
    description: str = ""


class Kind(Record):
    """One kind's declaration."""

    schema_: Literal["ix.v1.kind"] = Field(default=schema.KIND, alias="schema")
    #: Type URL: ``<namespace>.v<N>.<family>.<name>``.
    name: str
    summary: str = Field(min_length=1)
    options: dict[Name, Option] = {}
    effects: tuple[Effect, ...] = ()
    offers: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    #: Sensors only: the metrics this sensor provides.
    metrics: tuple[Metric, ...] = ()
    #: Sensors only: the probe truth keys this sensor reads.
    truth: tuple[str, ...] = ()
    #: Probe kinds only: the duration options that add up to the length of one trial.
    duration: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _coherent(self) -> Kind:
        family = schema.family_of(self.name)
        if family not in KIND_FAMILIES:
            raise ValueError(
                f"{self.name!r} is not a kind's type URL; write "
                f"<namespace>.v1.<{'|'.join(KIND_FAMILIES)}>.<name>"
            )
        if family != "sensor" and (self.metrics or self.truth):
            raise ValueError("only a sensor kind declares metrics or truth")
        if family == "sensor" and not self.metrics:
            raise ValueError("a sensor kind declares at least one metric")
        names = [metric.name for metric in self.metrics]
        if len(set(names)) != len(names):
            raise ValueError("a metric is declared more than once")
        if self.duration and family != "probe":
            raise ValueError("only a probe kind declares duration")
        for option in self.duration:
            if option not in self.options or self.options[option].type != "duration":
                raise ValueError(f"duration names {option!r}, which is not a duration option")
        return self

    @property
    def family(self) -> str:
        return schema.family_of(self.name) or ""

    @property
    def label(self) -> str:
        """The name an experiment writes: short for a built-in, the type URL otherwise."""
        return schema.short(self.name)

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(name for name, option in self.options.items() if option.required)


# --- options -----------------------------------------------------------------------------


def _fits(value: Any, option: Option) -> bool:
    if placeholders(value):
        return True  # bound per cell; its type is known only then
    match option.type:
        case "string":
            ok = isinstance(value, str)
        case "integer":
            ok = isinstance(value, int) and not isinstance(value, bool)
        case "number":
            ok = isinstance(value, int | float) and not isinstance(value, bool)
        case "boolean":
            ok = isinstance(value, bool)
        case "duration":
            ok = seconds(value) is not None
        case "list":
            ok = isinstance(value, list | tuple)
        case "mapping":
            ok = isinstance(value, Mapping)
        case _:
            ok = True
    return ok and (not option.allowed or str(value) in option.allowed)


def check_options(kind: Kind, options: Mapping[str, Any], path: str) -> list[Problem]:
    """Hold the options written beside a kind to its declaration: unknown keys, missing
    required ones, wrong types. ``path`` is the entry (``subjects.buffered``)."""
    label = schema.short(kind.name)
    known = tuple(kind.options)
    problems: list[Problem] = []
    for key, value in options.items():
        if key.startswith(NOTE_PREFIX):
            continue
        option = kind.options.get(key)
        if option is None:
            problems.append(
                problem(
                    f"{path}.{key}",
                    "option_unknown",
                    f"{kind.family} kind {label} takes no option {key!r}"
                    + (f"; it takes {', '.join(known)}" if known else "; it takes none"),
                    fix=f"remove {key!r} or correct its spelling"
                    + (f" (options: {', '.join(known)})" if known else "")
                    + f"; prefix a note with {NOTE_PREFIX!r}",
                    allowed=known,
                    got=key,
                )
            )
        elif not _fits(value, option):
            expected = (
                f"one of {', '.join(option.allowed)}" if option.allowed else f"a {option.type}"
            )
            problems.append(
                problem(
                    f"{path}.{key}",
                    "option_invalid",
                    f"{label} option {key!r} is {expected}; got {value!r}",
                    fix=f"{key}: {option.allowed[0]}"
                    if option.allowed
                    else f"give {key!r} {expected}"
                    + (", such as 60s or 2m" if option.type == "duration" else ""),
                    allowed=option.allowed,
                    got=value if isinstance(value, str | int | float | bool) else None,
                )
            )
    for key in kind.required:
        if key not in options:
            problems.append(
                problem(
                    f"{path}.{key}",
                    "option_missing",
                    f"{kind.family} kind {label} requires option {key!r}"
                    + (
                        f": {kind.options[key].description}"
                        if kind.options[key].description
                        else ""
                    ),
                    fix=f"add {key!r} beside kind: {label}",
                )
            )
    return problems


# --- the set of kinds --------------------------------------------------------------------


@dataclass(frozen=True)
class KindFile:
    """One kind file as read: where it is, and its data or why it could not be read."""

    origin: str
    data: Mapping[str, Any] | None = None
    error: str = ""


class Kinds(Record):
    """The kinds an experiment may name, and the files that were refused, with reasons."""

    schema_: Literal["ix.v1.kinds"] = Field(default=schema.KINDS, alias="schema")
    kinds: dict[str, Kind] = {}
    #: Refused kind files, by kind name (or by origin when the file gave no name).
    rejected: dict[str, tuple[Problem, ...]] = {}

    def get(self, family: str, name: str) -> Kind | None:
        return self.kinds.get(schema.url(family, name))

    def of(self, family: str) -> tuple[Kind, ...]:
        return tuple(kind for kind in self.kinds.values() if kind.family == family)

    def names(self, family: str) -> tuple[str, ...]:
        return tuple(schema.short(kind.name) for kind in self.of(family))


def kinds(files: Iterable[KindFile]) -> Kinds:
    """Build the set of kinds from kind files. A file with any problem is refused whole and
    the others are unaffected; two files declaring one name are both refused."""
    rejected: dict[str, list[Problem]] = {}
    accepted: dict[str, tuple[str, Kind]] = {}
    duplicated: set[str] = set()

    for file in files:
        if file.data is None:
            rejected.setdefault(file.origin, []).append(
                problem(
                    "",
                    "kind_invalid",
                    f"the kind file could not be read: {file.error}",
                    fix="make the file a YAML mapping with the keys of ix.v1.kind",
                    source=file.origin,
                )
            )
            continue
        name = file.data.get("name")
        key = name if isinstance(name, str) and name else file.origin
        try:
            kind = Kind.model_validate(dict(file.data))
        except ValidationError as error:
            rejected.setdefault(key, []).extend(
                found.model_copy(update={"kind": "kind_invalid"})
                if found.kind == "invalid_value"
                else found
                for found in from_validation_error(error, source=file.origin)
            )
            continue
        if kind.name in accepted or kind.name in duplicated:
            duplicated.add(kind.name)
            first = accepted.pop(kind.name, None)
            for origin in [file.origin] + ([first[0]] if first else []):
                rejected.setdefault(kind.name, []).append(
                    problem(
                        "name",
                        "kind_duplicate",
                        f"{kind.name} is declared by more than one kind file",
                        fix="give one of them another name; ix refuses both so that neither "
                        "silently wins",
                        source=origin,
                    )
                )
            continue
        accepted[kind.name] = (file.origin, kind)

    return Kinds(
        kinds={name: kind for name, (_, kind) in sorted(accepted.items())},
        rejected={name: tuple(found) for name, found in sorted(rejected.items())},
    )
