"""The experiment: one file that declares a question and how it will be answered.

An experiment names its **genre** (the decision the answer must equip), its **subjects** (the
variants under test), its **probes** (the stimulus), the **metrics** it reads and which one
decides, and the decision parameters the genre asks for. Everything it leaves out is a
convention the genre supplies, and :mod:`ix.domain.design` shows which.

One field set serves every genre: a genre constrains values and cardinalities, never the
shape of this record.

A subject, a probe and the environment each bind a *kind* (``process``, ``http-load``,
``compose``); the keys beside ``kind`` are that kind's own options and are checked by the
adapter that implements the kind, when one is attached.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import Discriminator, Field, StringConstraints, Tag, field_validator, model_validator

from ix.domain import schema
from ix.domain.schema import Record

#: A name an author gives a subject, a metric or a factor. It appears in paths and in
#: ``{placeholders}``, so it is one token.
Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]

#: One level of a factor.
Level = bool | int | float | str

Shape = Literal["boolean", "scalar", "histogram", "series"]
Direction = Literal["higher", "lower", "neither"]

#: The statistics that mean something for each reading shape. A percentile of a boolean and a
#: rate of a histogram do not.
STATISTICS: dict[str, tuple[str, ...]] = {
    "boolean": ("rate",),
    "scalar": ("mean", "median", "min", "max"),
    "histogram": ("p50", "p90", "p95", "p99", "p999", "mean", "min", "max"),
    "series": (),
}

#: The statistic ix reads when an experiment names a metric and no statistic.
DEFAULT_STATISTIC: dict[str, str] = {"boolean": "rate", "scalar": "mean", "histogram": "p50"}


class Metric(Record):
    """A named measurable: its unit, which way is better, and the shape of one reading.

    ``judged`` is true when a model produces the reading (a rubric score, an LLM grader). A
    judged reading is reported like any other and never decides a gate.
    """

    name: Name
    unit: str = ""
    direction: Direction
    shape: Shape
    judged: bool = False


class MetricRef(Record):
    """The metric a decision reads, and the statistic of it. No statistic: the shape's default."""

    metric: Name
    statistic: str | None = None


_MARGIN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(%|pp)?\s*$")


class Margin(Record):
    """How much worse still counts as no worse: ``5%`` (relative), ``10pp`` (percentage
    points) or a bare number in the metric's own unit."""

    value: float = Field(ge=0)
    unit: Literal["percent", "pp", "absolute"] = "absolute"

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, data: Any) -> Any:
        if isinstance(data, bool):
            raise ValueError("a margin is a number, '5%' or '10pp'")
        if isinstance(data, int | float):
            return {"value": data}
        if isinstance(data, str):
            match = _MARGIN.match(data)
            if match is None:
                raise ValueError(f"{data!r} is not a margin; write a number, '5%' or '10pp'")
            unit = {"%": "percent", "pp": "pp", None: "absolute"}[match.group(2)]
            return {"value": float(match.group(1)), "unit": unit}
        return data

    def text(self) -> str:
        suffix = {"percent": "%", "pp": "pp", "absolute": ""}[self.unit]
        return f"{self.value:g}{suffix}"


class Guard(Record):
    """A second metric that must also hold, with its own margin, for the verdict to hold."""

    metric: Name
    statistic: str | None = None
    margin: Margin


_QUANTITY = re.compile(r"^\s*(-?[0-9]+(?:\.[0-9]+)?)\s*([A-Za-z%/][A-Za-z0-9%/]*)?\s*$")


class Bar(Record):
    """The bar the primary metric must meet: ``{op: "<", value: 300ms}``."""

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


def _fold(data: Any, reserved: frozenset[str]) -> Any:
    """Gather a binding's kind-specific keys into ``options``.

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


class SubjectBinding(Record):
    """One subject: a variant under test, by the kind of thing it is.

    ``effects`` states what starting it does (``starts process ./proxy``, ``spends money``);
    the plan lists them before anything runs.
    """

    kind: str = Field(min_length=1)
    description: str = ""
    effects: tuple[str, ...] = ()
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"kind", "description", "effects", "options"}))


class ProbeBinding(Record):
    """One probe: a unit of stimulus, by the kind that says what applying it means."""

    id: str = ""
    kind: str = Field(min_length=1)
    effects: tuple[str, ...] = ()
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"id", "kind", "effects", "options"}))


class ProbeSource(Record):
    """Where the probes are, when they are not written inline: ``{source: directory,
    path: probes}``."""

    source: str = Field(min_length=1)
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"source", "options"}))


class Environment(Record):
    """What must be up before the trials and down after them. ``none``: the subjects are
    already running, or start themselves."""

    provider: str = Field(default="none", min_length=1)
    effects: tuple[str, ...] = ()
    options: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _options(cls, data: Any) -> Any:
        return _fold(data, frozenset({"provider", "effects", "options"}))


class Spend(Record):
    """The most a run may cost, and the author's estimate of one trial's cost."""

    ceiling: float | None = Field(default=None, ge=0)
    per_trial: float | None = Field(default=None, ge=0)
    currency: str = "USD"


def _probes_tag(value: Any) -> str:
    return "source" if isinstance(value, Mapping | ProbeSource) else "inline"


Probes = Annotated[
    Annotated[tuple[ProbeBinding, ...], Tag("inline")] | Annotated[ProbeSource, Tag("source")],
    Discriminator(_probes_tag),
]


class Experiment(Record):
    """An experiment as its file declares it. Loading changes nothing in it."""

    schema_: Literal["ix.v2.experiment"] = Field(default=schema.EXPERIMENT, alias="schema")
    name: Name
    question: str = Field(min_length=1)
    hypothesis: str = ""
    genre: str = Field(min_length=1)

    #: The variants under test, in the order written. The genre gives each a role.
    subjects: dict[Name, SubjectBinding] = Field(min_length=1)
    #: Which subject is the control, where the genre has one. Default: the first.
    control: Name | None = None
    probes: Probes
    #: Sweep axes. Every combination of levels is a cell; every subject runs in every cell.
    factors: dict[Name, tuple[Level, ...]] = {}
    environment: Environment = Environment()

    metrics: tuple[Metric, ...] = ()
    primary: MetricRef | None = None
    bar: Bar | None = None
    margin: Margin | None = None
    rule: str | None = None
    guards: tuple[Guard, ...] = ()

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
    def _unique_metrics(cls, metrics: tuple[Metric, ...]) -> tuple[Metric, ...]:
        names = [metric.name for metric in metrics]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"metric declared more than once: {', '.join(repeated)}")
        return metrics

    def metric(self, name: str) -> Metric | None:
        return next((metric for metric in self.metrics if metric.name == name), None)

    def digest(self) -> str:
        """Digest of the declaration: the same for two files that declare the same thing."""
        return schema.digest(self.document())


class ProbeSet(Record):
    """The probes a run would apply, in order, and the digest that pins them."""

    probes: tuple[ProbeBinding, ...]
    digest: str

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(probe.id for probe in self.probes)


def probe_set(probes: tuple[ProbeBinding, ...]) -> ProbeSet:
    """Name every probe and pin the set. A probe with no ``id`` is ``probe-<n>``, from 1."""
    named = tuple(
        probe if probe.id else probe.model_copy(update={"id": f"probe-{index}"})
        for index, probe in enumerate(probes, start=1)
    )
    return ProbeSet(probes=named, digest=schema.digest([probe.document() for probe in named]))
