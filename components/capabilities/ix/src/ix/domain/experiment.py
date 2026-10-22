"""The experiment: one file that declares a question and how it will be answered.

It names its **genre** (the decision the answer informs), its **subjects**, its **probes**,
the metric that decides and the decision parameters the genre asks for; the genre supplies
the rest. A genre constrains values, never the shape of this record.

A subject, a probe, a sensor and the environment each name a *kind*; the keys beside
``kind`` are that kind's options, held to the kind's declaration. A key starting with ``x-``
is a note and is not checked.

**Two reductions.** ``statistic`` reduces one trial's observation to a number (the p99 of a
latency histogram); ``estimator`` combines the trials' numbers (their mean, or the rate of a
boolean).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from itertools import product
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from ix.domain import schema
from ix.domain.schema import Record, canonical

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _one_token(value: str) -> str:
    if _TOKEN.match(value) is None:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.") or "name"
        raise ValueError(
            f"{value!r} is not a name: a name is one token of letters, digits, '_', '.' and "
            f"'-', starting with a letter or digit; for example {token!r}"
        )
    return value


#: A name an author gives a subject, a factor or a metric. It appears in paths and in
#: ``{placeholders}``, so it is one token.
Name = Annotated[str, AfterValidator(_one_token)]

Level = bool | int | float | str

#: The shape of one trial's reading: one boolean; one number; a distribution (histogram) or a
#: series, each reduced to named statistics; or a ``count``, events out of units (failed
#: requests out of requests sent), which ix keeps as the two counts.
Shape = Literal["boolean", "scalar", "histogram", "series", "count"]
Direction = Literal["higher", "lower", "neither"]

#: Per-trial reductions, by the shape of one reading. A boolean or a scalar reading is
#: already one number, so its only statistic is ``value``; a count's is its ``rate``.
STATISTICS: dict[str, tuple[str, ...]] = {
    "boolean": ("value",),
    "scalar": ("value",),
    "count": ("rate",),
    "histogram": ("p50", "p90", "p95", "p99", "p999", "mean", "min", "max"),
    "series": ("max", "min", "mean", "last"),
}

#: Across-trial estimators, by the shape of one reading. Booleans combine as a rate over
#: trials, and counts as a rate pooled over every unit; every other shape has been reduced to
#: a number per trial, and numbers combine as below.
ESTIMATORS: dict[str, tuple[str, ...]] = {
    "boolean": ("rate",),
    "count": ("rate",),
    "scalar": ("mean", "median", "min", "max"),
    "histogram": ("mean", "median", "min", "max"),
    "series": ("mean", "median", "min", "max"),
}


def default_estimator(shape: str, genre_estimator: str = "mean") -> str:
    """The estimator a metric of ``shape`` combines its trials by when none is written: a
    boolean or a count as a rate, whatever the genre says; anything else as the genre
    says. The one default the design, validation and the analysis share."""
    return ESTIMATORS[shape][0] if shape in ("boolean", "count") else genre_estimator


#: Experiment fields that are prose for a reader. A change to one changes the experiment's
#: digest and not the plan's.
PROSE_FIELDS = frozenset({"question", "rationale"})


class MetricRef(Record):
    """The metric a decision reads, how one trial is reduced, and how trials combine."""

    metric: Name
    statistic: str | None = None
    estimator: str | None = None


class MetricExpectation(Record):
    """A metric the author wants measured, and what they expect of it. Whatever is stated is
    checked against the sensor that provides the metric."""

    name: Name
    unit: str | None = None
    direction: Direction | None = None
    shape: Shape | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_name(cls, data: Any) -> Any:
        return {"name": data} if isinstance(data, str) else data


_MARGIN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(%|pp)?\s*$")
_BASIS = {"%": "relative", "pp": "points", None: "absolute"}
_SUFFIX = {"relative": "%", "points": "pp", "absolute": ""}


class Margin(Record):
    """The largest loss still counted as no worse: ``5%`` (relative to the control), ``10pp``
    (percentage points) or a bare number in the metric's own unit."""

    value: float = Field(ge=0)
    basis: Literal["relative", "points", "absolute"] = "absolute"

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, data: Any) -> Any:
        if isinstance(data, bool):
            raise ValueError("a margin is a number, '5%' or '10pp'")
        if isinstance(data, int | float):
            return {"value": data, "basis": "absolute"}
        if isinstance(data, str):
            match = _MARGIN.match(data)
            if match is None:
                raise ValueError(f"{data!r} is not a margin; write a number, '5%' or '10pp'")
            return {"value": float(match.group(1)), "basis": _BASIS[match.group(2)]}
        return data

    def text(self) -> str:
        return f"{self.value:g}{_SUFFIX[self.basis]}"


_QUANTITY = re.compile(r"^\s*(-?[0-9]+(?:\.[0-9]+)?)\s*([A-Za-z%/][A-Za-z0-9%/]*)?\s*$")


class Threshold(Record):
    """An absolute bound a metric must satisfy: ``{op: "<", value: 300ms}``."""

    op: Literal["<", "<=", ">", ">="]
    value: float
    unit: str = ""

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and isinstance(data.get("value"), str):
            match = _QUANTITY.match(data["value"])
            if match is None:
                raise ValueError(
                    f"{data['value']!r} is not a quantity; write a number, or a number and a "
                    "unit such as 300ms"
                )
            if match.group(2) and data.get("unit"):
                raise ValueError("give the unit once: in 'value' or in 'unit'")
            parsed = dict(data)
            parsed["value"] = float(match.group(1))
            if match.group(2):
                parsed["unit"] = match.group(2)
            return parsed
        return data

    def text(self) -> str:
        return f"{self.op} {self.value:g}{(' ' + self.unit) if self.unit else ''}"


class Guardrail(Record):
    """A further metric that must also hold for the verdict to hold: against an absolute
    ``threshold``, or within a ``margin`` of the control. Exactly one of the two."""

    metric: Name
    statistic: str | None = None
    estimator: str | None = None
    threshold: Threshold | None = None
    margin: Margin | None = None

    @model_validator(mode="after")
    def _one_form(self) -> Guardrail:
        if (self.threshold is None) == (self.margin is None):
            raise ValueError("a guardrail has exactly one of 'threshold' or 'margin'")
        return self


def _fold(data: Any, reserved: frozenset[str]) -> Any:
    """Gather the keys beside ``kind`` into ``options``.

    An author writes ``{kind: process, command: [...]}``; the record holds
    ``kind`` and ``options: {command: [...]}``.
    """
    if not isinstance(data, Mapping):
        return data
    folded = {key: value for key, value in data.items() if key in reserved}
    rest = {key: value for key, value in data.items() if key not in reserved}
    if rest:
        explicit = folded.get("options", {})
        if not isinstance(explicit, Mapping):
            return data  # let the field's own check report the wrong type
        folded["options"] = {**explicit, **rest}
    return folded


class SubjectConfig(Record):
    """One subject as the experiment configures it: the subject kind it is (a registered
    type) and that kind's options. The configuration under test.

    ``effects`` adds to what the kind declares it does; it can remove nothing.
    """

    kind: str = Field(min_length=1)
    description: str = ""
    effects: tuple[str, ...] = ()
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"kind", "description", "effects", "options"}))


class ProbeConfig(Record):
    """One probe as the experiment configures it: the probe kind that says what applying
    it means (a registered type) and that kind's options.

    ``truth`` is reference data a sensor measures against (an expected skill, test cases).
    It never reaches the subject.
    """

    id: str = ""
    kind: str = Field(min_length=1)
    effects: tuple[str, ...] = ()
    truth: dict[str, Any] = {}
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"id", "kind", "effects", "truth", "options"}))


#: Where probes are read from when the experiment writes none inline: this folder beside the
#: experiment file, one file per probe.
PROBES_FOLDER = "probes"

#: The suffixes of the files in :data:`PROBES_FOLDER` that are read as probes.
PROBE_SUFFIXES = (".md", ".yaml", ".yml")

#: The keys a probe file uses for the probe itself; every other key is the probe's truth.
_PROBE_KEYS = frozenset({"id", "kind", "effects", "truth", "options", "body", "prompt"})


class ProbeFile(Record):
    """One probe as a file in :data:`PROBES_FOLDER` declares it: a markdown file (YAML
    frontmatter, then the prompt) or a YAML file.

    The text after a markdown file's frontmatter, its ``body``, is the ``prompt``. ``id``
    (default: the file's name without its suffix), ``kind`` (default ``prompt``), ``effects``
    and ``options`` are the probe's own; every other key is its truth, beside any written
    under ``truth:``. A ``prompt`` probe's text is its ``prompt`` option even when empty, so
    validation can say it is missing.
    """

    id: str = ""
    kind: str = Field(default="prompt", min_length=1)
    effects: tuple[str, ...] = ()
    truth: dict[str, Any] = {}
    options: dict[str, Any] = {}
    prompt: str | None = Field(default=None, alias="body")

    @model_validator(mode="before")
    @classmethod
    def _truth(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        rest = {key: value for key, value in data.items() if key not in _PROBE_KEYS}
        written = data.get("truth", {})
        if not rest or not isinstance(written, Mapping):
            return data  # nothing to gather, or a wrong type the field's own check reports
        kept = {key: value for key, value in data.items() if key in _PROBE_KEYS}
        return {**kept, "truth": {**rest, **written}}

    def config(self) -> ProbeConfig:
        """The probe as the same probe written inline would be."""
        options = dict(self.options)
        if self.prompt is not None and (self.prompt.strip() or self.kind == "prompt"):
            options["prompt"] = self.prompt
        return ProbeConfig(
            id=self.id, kind=self.kind, effects=self.effects, truth=self.truth, options=options
        )


class SensorConfig(Record):
    """A sensor the author names, as a sensor kind (a registered type) and its options: to
    give it options, or to say which of two sensors provides a metric both could."""

    kind: str = Field(min_length=1)
    effects: tuple[str, ...] = ()
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"kind", "effects", "options"}))


class EnvironmentConfig(Record):
    """What must be up before the trials and down after them, and is not under test: the
    environment kind (a registered type) and its options."""

    kind: str = Field(default="none", min_length=1)
    effects: tuple[str, ...] = ()
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"kind", "effects", "options"}))


def _shape_slip(error_type: str, message: str) -> PydanticCustomError:
    """A value of the wrong shape, with the sentence that fixes it. The error type is one a
    loader reads as ``wrong_shape``."""
    return PydanticCustomError(error_type, message)


class Experiment(Record):
    """An experiment as its file declares it. Loading changes nothing in it.

    An absent ``schema`` means ``ix.v1.experiment``, and always will.
    """

    schema_: Literal["ix.v1.experiment"] = Field(default=schema.EXPERIMENT, alias="schema")
    name: Name
    question: str = Field(min_length=1)
    #: Why the author expects what they expect. The hypothesis itself is rendered from the
    #: genre and the decision parameters, so it cannot drift from them.
    rationale: str = ""
    genre: str = Field(min_length=1)

    #: The configurations under test, in the order written. The genre gives each a role.
    subjects: dict[Name, SubjectConfig] = Field(min_length=1)
    #: Which subject is the control, where the genre has one. Default: the first.
    control: Name | None = None
    #: The probes, written inline. Left out, they are the files in the ``probes`` folder
    #: beside the experiment file.
    probes: tuple[ProbeConfig, ...] = ()
    #: Conditions varied on purpose. Every combination of levels is a cell.
    factors: dict[Name, tuple[Level, ...]] = {}
    environment: EnvironmentConfig = EnvironmentConfig()
    #: Sensors named explicitly. Most experiments need none: ix selects the sensor that
    #: provides each metric the decision reads.
    sensors: tuple[SensorConfig, ...] = ()

    #: Further metrics to measure and report, each optionally with what is expected of it.
    metrics: tuple[MetricExpectation, ...] = ()
    primary: MetricRef | None = None
    threshold: Threshold | None = None
    comparison: str | None = None
    margin: Margin | None = None
    guardrails: tuple[Guardrail, ...] = ()

    repeats: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0)

    @field_validator("subjects", mode="before")
    @classmethod
    def _subjects_shape(cls, value: Any) -> Any:
        if isinstance(value, list):
            raise _shape_slip(
                "dict_type",
                "subjects is a mapping of each subject's name to its kind and options, not a "
                "list: subjects: {<name>: {kind: <subject kind>, ...}}",
            )
        return value

    @field_validator("probes", mode="before")
    @classmethod
    def _probes_shape(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            raise _shape_slip(
                "list_type",
                "probes is a list of probes, each item starting with '-'; leave it out to read "
                f"one file per probe from the {PROBES_FOLDER}/ folder beside the experiment",
            )
        return value

    @field_validator("factors")
    @classmethod
    def _levels(cls, factors: dict[str, tuple[Level, ...]]) -> dict[str, tuple[Level, ...]]:
        for name, levels in factors.items():
            if not levels:
                raise ValueError(f"factor {name!r} has no levels; give at least one")
            # Told apart as a cell names them (cell_key), so 1 and "1" are one level.
            if len(set(map(str, levels))) != len(levels):
                raise ValueError(
                    f"factor {name!r} repeats a level: levels that read the same in a cell "
                    "(1 and '1') are one"
                )
        return factors

    @field_validator("metrics")
    @classmethod
    def _unique_metrics(
        cls, metrics: tuple[MetricExpectation, ...]
    ) -> tuple[MetricExpectation, ...]:
        names = [metric.name for metric in metrics]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"metric named more than once: {', '.join(repeated)}")
        return metrics

    def digest(self, *, operative: bool = False) -> str:
        """Digest of what the experiment declares, over :func:`~ix.domain.schema.canonical`.
        ``operative`` leaves the prose out (the question, the rationale, descriptions): that
        digest changes only when what a run would do changes."""
        data = canonical(self)
        if operative:
            for field in PROSE_FIELDS:
                data.pop(field, None)
            for subject in data.get("subjects", {}).values():
                subject.pop("description", None)
        return schema.digest(data)


def cells(factors: dict[str, tuple[Level, ...]]) -> tuple[dict[str, Level], ...]:
    """Every combination of factor levels, the first factor varying slowest. No factors:
    one cell with no levels."""
    names = list(factors)
    return tuple(
        dict(zip(names, levels, strict=True))
        for levels in product(*(factors[name] for name in names))
    )


class ProbeSet(Record):
    """The probes a run would apply, in order, and the digest that pins them."""

    probes: tuple[ProbeConfig, ...]
    digest: str

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(probe.id for probe in self.probes)


def probe_set(probes: tuple[ProbeConfig, ...]) -> ProbeSet:
    """Name every probe and pin the set. A probe with no ``id`` is ``probe-<n>``, from 1."""
    named = tuple(
        probe if probe.id else probe.model_copy(update={"id": f"probe-{index}"})
        for index, probe in enumerate(probes, start=1)
    )
    return ProbeSet(probes=named, digest=schema.digest([canonical(probe) for probe in named]))
