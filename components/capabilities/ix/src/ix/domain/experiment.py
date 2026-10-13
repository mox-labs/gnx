"""The experiment: one file that declares a question and how it will be answered.

An experiment names its **genre** (the decision the answer must inform), its **subjects** (the
configurations under test), its **probes** (what is applied to them), the metric that
decides, and the decision parameters the genre asks for. Everything it leaves out is
supplied by the genre, and :mod:`ix.domain.design` shows which.

One field set serves every genre: a genre constrains values and cardinalities, never the
shape of this record.

A subject, a probe, a sensor and the environment each name a *kind* (``process``,
``http-load``, ``timing``, ``compose``). The keys beside ``kind`` are that kind's options;
the kind's declaration says which exist, and ``validate`` holds the file to it. An option
key starting with ``x-`` is a note and is not checked.

**Two reductions.** A decision reads one number per subject per cell. ``statistic`` reduces
one trial's observation to a number (the p99 of a latency histogram); ``estimator`` combines
the trials' numbers (their mean, or the rate of a boolean). Both have defaults.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import Discriminator, Field, StringConstraints, Tag, field_validator, model_validator

from ix.domain import schema
from ix.domain.schema import Record, canonical

#: A name an author gives a subject, a factor or a metric. It appears in paths and in
#: ``{placeholders}``, so it is one token.
Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]

#: One level of a factor.
Level = bool | int | float | str

Shape = Literal["boolean", "scalar", "histogram", "series"]
Direction = Literal["higher", "lower", "neither"]

#: Per-trial reductions, by the shape of one reading. A boolean or a scalar reading is
#: already one number, so its only statistic is ``value``.
STATISTICS: dict[str, tuple[str, ...]] = {
    "boolean": ("value",),
    "scalar": ("value",),
    "histogram": ("p50", "p90", "p95", "p99", "p999", "mean", "min", "max"),
    "series": ("max", "min", "mean", "last"),
}

#: Across-trial estimators, by the shape of one reading. Booleans combine as a rate; every
#: other shape has been reduced to a number per trial, and numbers combine as below.
ESTIMATORS: dict[str, tuple[str, ...]] = {
    "boolean": ("rate",),
    "scalar": ("mean", "median", "min", "max"),
    "histogram": ("mean", "median", "min", "max"),
    "series": ("mean", "median", "min", "max"),
}

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


class Subject(Record):
    """One subject: a configuration under test, by the kind of thing it is.

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


class Probe(Record):
    """One probe: a declared stimulus, by the kind that says what applying it means.

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


class SourcedProbes(Record):
    """Where the probes are, when they are not written inline: ``{source: directory,
    path: probes}``. The keys beside ``source`` are that source's options."""

    source: str = Field(min_length=1)
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"source", "options"}))


class SensorSelection(Record):
    """A sensor the author names: to give it options, or to say which of two sensors
    provides a metric both could."""

    kind: str = Field(min_length=1)
    effects: tuple[str, ...] = ()
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"kind", "effects", "options"}))


class Environment(Record):
    """What must be up before the trials and down after them, and is not under test."""

    kind: str = Field(default="none", min_length=1)
    effects: tuple[str, ...] = ()
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"kind", "effects", "options"}))


class Spend(Record):
    """The most a run may cost, and the author's estimate of one trial's cost."""

    ceiling: float | None = Field(default=None, ge=0)
    per_trial: float | None = Field(default=None, ge=0)
    currency: str = "USD"


def _probes_tag(value: Any) -> str:
    return "<from-source>" if isinstance(value, Mapping | SourcedProbes) else "<inline>"


Probes = Annotated[
    Annotated[tuple[Probe, ...], Tag("<inline>")] | Annotated[SourcedProbes, Tag("<from-source>")],
    Discriminator(_probes_tag),
]


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
    subjects: dict[Name, Subject] = Field(min_length=1)
    #: Which subject is the control, where the genre has one. Default: the first.
    control: Name | None = None
    probes: Probes
    #: Conditions varied on purpose. Every combination of levels is a cell.
    factors: dict[Name, tuple[Level, ...]] = {}
    environment: Environment = Environment()
    #: Sensors named explicitly. Most experiments need none: ix selects the sensor that
    #: provides each metric the decision reads.
    sensors: tuple[SensorSelection, ...] = ()

    #: Further metrics to measure and report, each optionally with what is expected of it.
    metrics: tuple[MetricExpectation, ...] = ()
    primary: MetricRef | None = None
    threshold: Threshold | None = None
    comparison: str | None = None
    margin: Margin | None = None
    guardrails: tuple[Guardrail, ...] = ()

    repeats: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0)
    spend: Spend | None = None

    @field_validator("factors")
    @classmethod
    def _levels(cls, factors: dict[str, tuple[Level, ...]]) -> dict[str, tuple[Level, ...]]:
        for name, levels in factors.items():
            if not levels:
                raise ValueError(f"factor {name!r} has no levels; give at least one")
            if len(set(map(repr, levels))) != len(levels):
                raise ValueError(f"factor {name!r} repeats a level")
        return factors

    @field_validator("metrics")
    @classmethod
    def _unique_metrics(cls, metrics: tuple[MetricExpectation, ...]) -> Any:
        names = [metric.name for metric in metrics]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"metric named more than once: {', '.join(repeated)}")
        return metrics

    def digest(self, *, operative: bool = False) -> str:
        """Digest of what the experiment declares.

        It is over the fields that differ from their defaults, so a comment, key order, a
        default written out and a field a later ix adds leave it unchanged. ``operative``
        leaves the prose out (the question, the rationale, descriptions): that digest
        changes only when what a run would do changes.
        """
        data = canonical(self)
        if operative:
            for field in PROSE_FIELDS:
                data.pop(field, None)
            for subject in data.get("subjects", {}).values():
                subject.pop("description", None)
        return schema.digest(data)


class ProbeSet(Record):
    """The probes a run would apply, in order, and the digest that pins them."""

    probes: tuple[Probe, ...]
    digest: str

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(probe.id for probe in self.probes)


def probe_set(probes: tuple[Probe, ...]) -> ProbeSet:
    """Name every probe and pin the set. A probe with no ``id`` is ``probe-<n>``, from 1."""
    named = tuple(
        probe if probe.id else probe.model_copy(update={"id": f"probe-{index}"})
        for index, probe in enumerate(probes, start=1)
    )
    return ProbeSet(probes=named, digest=schema.digest([canonical(probe) for probe in named]))
