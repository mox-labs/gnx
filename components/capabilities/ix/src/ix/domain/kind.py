"""Kinds: what a subject, a probe, a sensor or an environment of a given kind is, as data.

A kind's declaration says which options it takes (their types, which are required), which
facets it ``offers`` and which it ``needs``, and, for a sensor, the metrics it provides (with
whether a model judges them) and the probe truth it reads. ``validate`` holds an experiment
to these declarations, so it does not depend on the author asserting them.

A declaration starts nothing and measures nothing: the adapter registered under the kind's
type URL does. What carrying a kind out needs of the host (a program, a daemon, a variable)
is the adapter's to check (:class:`~ix.domain.ports.Preflight`), not the declaration's to
claim.

**The families are closed** (:data:`KIND_FAMILIES`). Each has its own place in a run: the
environment is brought up once, a subject is reached for each trial, a probe applied to it,
a sensor reads what it left behind. A new family would need a new place in that loop and in
the schedule, the record and the analysis, which a declaration cannot give it; a new kind of
an existing family needs only its file and its adapter.

**Facets** are how kinds fit together without knowing each other. A subject kind offers how
it can be reached (``url``, ``pid``, ``agent``); a probe kind needs some of those and offers
what applying it leaves behind (``load``, ``response``, ``usage``); a sensor needs what it
measures from.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from ix.domain import schema
from ix.domain.experiment import Direction, Level, Name, Shape, cells
from ix.domain.problem import Problem, from_validation_error, problem
from ix.domain.schema import DeclarationFile, Record

#: The families of kind, in the order a run uses them; closed (see the module docstring).
KIND_FAMILIES: tuple[str, ...] = ("subject", "probe", "sensor", "environment")

OptionType = Literal[
    "string",
    "integer",
    "number",
    "boolean",
    "duration",
    "rate",
    "list",
    "mapping",
    "path",
    "paths",
    "command",
    "any",
]

#: A placeholder, or a doubled brace, which is the brace itself: ``{{name}}`` is the text
#: ``{name}``, as in Python's ``str.format``.
_PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
#: A value that is one placeholder and nothing else: filled, it becomes the level itself.
_WHOLE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_BRACES = {"{{": "{", "}}": "}"}
_DURATION = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m|h)\s*$")
_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
_RATE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(?:/\s*(s|m))?\s*$")

#: The prefix of a key that is a note: it sits beside a kind's options and is not checked.
NOTE_PREFIX = "x-"


def placeholders(value: Any) -> list[str]:
    """Every ``{name}`` in the strings of ``value``, in order of appearance; a doubled brace
    (``{{name}}``) is text, not one."""
    if isinstance(value, str):
        return [name for name in _PLACEHOLDER.findall(value) if name]
    if isinstance(value, Mapping):
        return [name for item in value.values() for name in placeholders(item)]
    if isinstance(value, list | tuple):
        return [name for item in value for name in placeholders(item)]
    return []


def fill(value: Any, bound: Mapping[str, Any]) -> Any:
    """``value`` with each ``{name}`` that ``bound`` knows replaced by its level, in every
    string at any depth of mappings and lists. A string that is one placeholder and nothing
    else becomes the level itself, with its type (``"{t}"`` filled with 0.5 is 0.5); one
    inside more text is written into it. A doubled brace becomes one brace; an unknown
    name stays as written."""
    if isinstance(value, str):
        whole = _WHOLE.fullmatch(value)
        if whole is not None and whole.group(1) in bound:
            return bound[whole.group(1)]

        def replaced(match: re.Match[str]) -> str:
            name = match.group(1)
            if name is None:
                return _BRACES[match.group(0)]
            return str(bound[name]) if name in bound else match.group(0)

        return _PLACEHOLDER.sub(replaced, value)
    if isinstance(value, Mapping):
        return {key: fill(item, bound) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [fill(item, bound) for item in value]
    return value


def per_cell(options: Mapping[str, Any], factors: Mapping[str, tuple[Level, ...]]) -> list[Any]:
    """``options`` filled for each cell of ``factors``, as a trial in that cell has them;
    each distinct filling once, in the order of the cells."""
    found: list[Any] = []
    for cell in cells(dict(factors)):
        filled = fill(options, cell)
        if filled not in found:
            found.append(filled)
    return found


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


def arrivals(value: Any) -> tuple[int, Literal["s", "m"]] | None:
    """A rate option as (arrivals, per second or per minute): ``80/s``, ``600/m`` or a bare
    number of arrivals per second. None when it is not a whole number of at least one
    arrival: ``2.5/s`` is written ``150/m``, never rounded."""
    if isinstance(value, bool):
        return None
    match = _RATE.match(str(value))
    if match is None:
        return None
    count = float(match.group(1))
    if count < 1 or not count.is_integer():
        return None
    return int(count), "m" if match.group(2) == "m" else "s"


class Metric(Record):
    """A measurable a sensor provides: its unit, which way is better, and the shape of one
    reading. ``judged`` is true when a model produces the reading.

    ``bounds`` are the lowest and highest values a scalar reading can take, where it has
    any (a score from 0 to 1): a probe whose every reading sits at one of them cannot
    discriminate. ``flag`` makes a boolean a warning: the sentence a report prints when any
    trial reads true, and a metric with a flag is read whenever its sensor is in use.
    """

    name: Name
    unit: str = ""
    direction: Direction
    shape: Shape
    judged: bool = False
    bounds: tuple[float, float] | None = None
    flag: str = ""
    description: str = ""

    @model_validator(mode="after")
    def _coherent(self) -> Metric:
        if self.bounds is not None and not self.bounds[0] < self.bounds[1]:
            raise ValueError(f"bounds {list(self.bounds)} are not [lowest, highest]")
        if self.bounds is not None and self.shape != "scalar":
            raise ValueError("only a scalar metric declares bounds; a rate is bounded already")
        if self.flag and self.shape != "boolean":
            raise ValueError("only a boolean metric declares a flag")
        return self


class Confusion(Record):
    """For a sensor that names what was expected and what it observed in each trial (a skill
    that should fire, the one that did): the ``detail`` keys that hold the two, and the label
    that means none. A report tabulates expected against observed, with precision and recall
    for each label but ``negative``."""

    expected: str = Field(min_length=1)
    observed: str = Field(min_length=1)
    negative: str = "none"


class Option(Record):
    type: OptionType = "string"
    required: bool = False
    #: The legal values, where the option is a closed choice.
    allowed: tuple[str, ...] = ()
    #: Other options' values that make this one required: ``{load_model: open}``.
    required_when: dict[str, str] = {}
    #: The values to choose between, each with what choosing it means, where the set is
    #: open (unlike ``allowed``, another value is accepted). A missing option's fix lists
    #: them, so an author picks one knowingly rather than inheriting a default.
    choices: dict[str, str] = {}
    #: For a ``command``: the option that holds the directory its relative paths are
    #: relative to (``cwd``), itself relative to the experiment. Absent: the experiment's
    #: directory.
    within: str = ""
    #: The placeholders the adapter fills in this option's value itself, at run time (a
    #: loop's ``{prompt}`` and ``{response}``): validation reads them as bound.
    binds: tuple[str, ...] = ()
    description: str = ""


class Kind(Record):
    schema_: Literal["ix.v1.kind"] = Field(default=schema.KIND, alias="schema")
    #: Type URL: ``<namespace>.v<N>.<family>.<name>``.
    name: str
    summary: str = Field(min_length=1)
    options: dict[Name, Option] = {}
    offers: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    #: Sensors only: the metrics this sensor provides.
    metrics: tuple[Metric, ...] = ()
    #: Sensors only: the probe truth keys this sensor reads.
    truth: tuple[str, ...] = ()
    #: Probe kinds only: the duration options that add up to the length of one trial.
    duration: tuple[str, ...] = ()
    #: Sensors only: the expected and observed labels its readings' detail holds.
    confusion: Confusion | None = None
    #: Environment kinds only: what each service it brings up is bound with (``host``,
    #: ``port``, ``url``), which a subject's or a probe's option names as ``{<service>.<key>}``.
    binds: tuple[str, ...] = ()

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
        if self.confusion is not None and family != "sensor":
            raise ValueError("only a sensor kind declares confusion")
        if self.binds and family != "environment":
            raise ValueError("only an environment kind declares binds")
        for name in self.duration:
            if name not in self.options or self.options[name].type != "duration":
                raise ValueError(f"duration names {name!r}, which is not a duration option")
        for name, option in self.options.items():
            unknown = sorted(set(option.required_when) - set(self.options))
            if unknown:
                raise ValueError(f"{name} is required_when {unknown}, which are not options")
            if option.within and (option.type != "command" or option.within not in self.options):
                raise ValueError(f"{name} is within {option.within!r}: a command names an option")
        return self

    @property
    def family(self) -> str:
        return schema.family_of(self.name) or ""

    @property
    def label(self) -> str:
        """The name an experiment writes: short for a built-in, the type URL otherwise."""
        return schema.short(self.name)

    def required(self, options: Mapping[str, Any]) -> tuple[str, ...]:
        """The options that must be written beside this kind, given the ``options`` that are."""
        return tuple(
            name
            for name, option in self.options.items()
            if option.required
            or (
                option.required_when
                and all(options.get(key) == value for key, value in option.required_when.items())
            )
        )


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
        case "rate":
            ok = arrivals(value) is not None
        case "list":
            ok = isinstance(value, list | tuple)
        case "mapping":
            ok = isinstance(value, Mapping)
        case "path":
            ok = isinstance(value, str) and bool(value)
        case "paths":
            ok = isinstance(value, list | tuple) and all(map(_a_path, value))
        case "command":
            ok = isinstance(value, list | tuple) and bool(value) and all(map(_an_argument, value))
        case _:
            ok = True
    return ok and (not option.allowed or str(value) in option.allowed)


def _an_argument(item: Any) -> bool:
    """An item of a ``command``: a program or an argument, written as text or a number."""
    return isinstance(item, str | int | float) and not isinstance(item, bool)


def _a_path(item: Any) -> bool:
    """An item of a ``paths`` option: a path, or a mapping that holds one (``{type: local,
    path: plugins/lint}``)."""
    path = item.get("path") if isinstance(item, Mapping) else item
    return isinstance(path, str) and bool(path)


#: A type's name in a sentence, where it is not the name itself.
_TYPE_WORDS = {
    "paths": "list of paths",
    "command": "command: a program and its arguments, as a non-empty list of text",
}

_EXAMPLES = {
    "duration": ", such as 60s or 2m",
    "rate": ", a whole number of arrivals such as 80/s or 600/m (2.5/s is 150/m)",
    "path": ", relative to the experiment's directory",
    "paths": ", each relative to the experiment's directory",
}


def services(options: Mapping[str, Any]) -> tuple[str, ...] | None:
    """The services an environment's options name: the keys of a ``services`` mapping or the
    items of a list; None when they name none, so which services come up is known only once
    they are up (a compose file's every service)."""
    named = options.get("services")
    if isinstance(named, Mapping):
        return tuple(str(name) for name in named)
    if isinstance(named, list | tuple) and named:
        return tuple(str(name) for name in named)
    return None


def binding_names(kind: Kind | None, options: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Every ``<service>.<key>`` an environment of ``kind`` written with ``options`` binds;
    None when its services are not named, so any service may be."""
    if kind is None or not kind.binds:
        return ()
    named = services(options)
    if named is None:
        return None
    return tuple(f"{service}.{key}" for service in named for key in kind.binds)


def placed(kind: Kind, options: Mapping[str, Any], base: str) -> dict[str, Any]:
    """``options`` with each option ``kind`` declares a ``path`` resolved against the
    directory ``base``: a relative path is joined to it, an absolute one stands."""
    joined = dict(options)
    for name, option in kind.options.items():
        value = joined.get(name)
        if option.type == "path" and isinstance(value, str) and not value.startswith("/"):
            joined[name] = f"{base.rstrip('/')}/{value}"
    return joined


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
                    kind="option_unknown",
                    message=f"{kind.family} kind {label} takes no option {key!r}"
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
                f"one of {', '.join(option.allowed)}"
                if option.allowed
                else f"a {_TYPE_WORDS.get(option.type, option.type)}"
            )
            problems.append(
                problem(
                    f"{path}.{key}",
                    kind="option_invalid",
                    message=f"{label} option {key!r} is {expected}; got {value!r}",
                    fix=f"{key}: {option.allowed[0]}"
                    if option.allowed
                    else f"give {key!r} {expected}" + _EXAMPLES.get(option.type, ""),
                    allowed=option.allowed,
                    got=value if isinstance(value, str | int | float | bool) else None,
                )
            )
    for key in kind.required(options):
        if key not in options:
            problems.append(_missing(kind, key, path))
    return problems


def _missing(kind: Kind, key: str, path: str) -> Problem:
    option = kind.options[key]
    when = ", ".join(f"{other} is {value}" for other, value in option.required_when.items())
    return problem(
        f"{path}.{key}",
        kind="option_missing",
        message=f"{kind.family} kind {kind.label} requires option {key!r}"
        + (f" when {when}" if when else "")
        + (f": {option.description}" if option.description else ""),
        fix=f"add {key!r} beside kind: {kind.label}"
        + (
            "; choose one of "
            + ", ".join(f"{value} ({means})" for value, means in option.choices.items())
            if option.choices
            else ""
        ),
    )


# --- the set of kinds --------------------------------------------------------------------


class Kinds(Record):
    """The kinds an experiment may name, and the files that were refused, with reasons."""

    schema_: Literal["ix.v1.kinds"] = Field(default=schema.KINDS, alias="schema")
    kinds: dict[str, Kind] = {}
    #: Refused kind files, by kind name (or by origin when the file gave no name).
    rejected: dict[str, tuple[Problem, ...]] = {}
    #: The kinds (type URLs) whose adapter is registered and builds here: a run can use
    #: these. A kind that is declared and not listed here can be validated, planned and
    #: registered, not run.
    runnable: tuple[str, ...] = ()
    #: Kinds (type URLs) whose registered adapter cannot be built here, each with why: a
    #: need nothing supplies, or a factory that built something not of its port's shape.
    unbuildable: dict[str, str] = {}
    #: Extensions that failed to load, one sentence each: an adapter one of them registers
    #: is missing from ``runnable``.
    quarantined: tuple[str, ...] = ()

    def get(self, family: str, name: str) -> Kind | None:
        return self.kinds.get(schema.url(family, name))

    def of(self, family: str) -> tuple[Kind, ...]:
        return tuple(kind for kind in self.kinds.values() if kind.family == family)

    def names(self, family: str) -> tuple[str, ...]:
        return tuple(schema.short(kind.name) for kind in self.of(family))


def kinds(files: Iterable[DeclarationFile], *, grammar: schema.Grammar) -> Kinds:
    """Build the set of kinds from kind files. A file with any problem is refused whole and
    the others are unaffected; a name ``grammar`` refuses is a problem. When two files
    declare one name, a file that does not own it is refused alone if the other does, and
    both are refused otherwise."""
    rejected: dict[str, list[Problem]] = {}
    declared: dict[str, list[tuple[DeclarationFile, Kind]]] = {}

    for file in files:
        kind = _parsed(file, grammar, rejected)
        if kind is not None:
            declared.setdefault(kind.name, []).append((file, kind))

    accepted = settle(declared, rejected, noun="kind")
    return Kinds(
        kinds={name: kind for name, (_, kind) in sorted(accepted.items())},
        rejected={name: tuple(found) for name, found in sorted(rejected.items())},
    )


def _parsed(
    file: DeclarationFile, grammar: schema.Grammar, rejected: dict[str, list[Problem]]
) -> Kind | None:
    """The kind ``file`` declares, or None with its problems added to ``rejected``: a file
    that could not be read, does not hold an ``ix.v1.kind``, or has a name ``grammar``
    refuses."""
    if file.data is None:
        rejected.setdefault(file.origin, []).extend(file.problems or unreadable(file.origin))
        return None
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
        return None
    misnamed = grammar(kind.name)
    if misnamed is not None:
        rejected.setdefault(kind.name, []).append(
            problem(
                "name",
                kind="kind_invalid",
                message=misnamed,
                fix=f"rename the kind: {schema.NAMING}",
                got=kind.name,
                source=file.origin,
            )
        )
        return None
    return kind


def unreadable(origin: str) -> tuple[Problem, ...]:
    """What a file that could not be read and says nothing more about why is refused for."""
    return (
        problem(
            "",
            kind="wrong_shape",
            message="the file could not be read as a YAML mapping",
            fix="make the file a YAML mapping of keys to values",
            source=origin,
        ),
    )


def settle[T](
    declared: Mapping[str, list[tuple[DeclarationFile, T]]],
    rejected: dict[str, list[Problem]],
    *,
    noun: str,
) -> dict[str, tuple[DeclarationFile, T]]:
    """Of the files that declare each name, the one that keeps it; the others are added to
    ``rejected``. A file that owns the name's namespace keeps it, and every file that does
    not is refused; two or more files alike are all refused, so that none silently wins.
    Which file keeps a name does not depend on the order the files were read in."""
    accepted: dict[str, tuple[DeclarationFile, T]] = {}
    for name, held in declared.items():
        owners = [entry for entry in held if entry[0].owned]
        if owners:
            for file, _ in held:
                if not file.owned:
                    rejected.setdefault(name, []).append(
                        problem(
                            "name",
                            kind="name_taken",
                            message=f"{name} belongs to the package that owns its namespace; "
                            "this file cannot declare it again",
                            fix=f"give this {noun} a name in its own namespace",
                            source=file.origin,
                        )
                    )
        contenders = owners or held
        if len(contenders) == 1:
            accepted[name] = contenders[0]
            continue
        for file, _ in contenders:
            rejected.setdefault(name, []).append(
                problem(
                    "name",
                    kind="name_taken",
                    message=f"{name} is declared by more than one {noun} file",
                    fix="give one of them another name; ix refuses both so that neither "
                    "silently wins",
                    source=file.origin,
                )
            )
    return accepted
