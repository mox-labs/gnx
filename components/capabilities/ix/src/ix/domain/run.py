"""A run's records, and the schedule: which trials a run carries out, in what order.

A **trial** is intent: one probe put to one subject in one cell in one repeat. What happened
when it was carried out is an **interaction**; what an instrument made of it is a
**reading**. They join on ``seq``, and are separate tables so that a failed measurement
never costs an interaction already paid for. Each names the rows of the run's session table
behind it (``sessions``): the model calls and agent sessions that answered. :func:`schedule`
is a pure function of the design, so the plan a person reviewed is the list a run carries
out.

**Failures have an owner.** ``subject``: the thing under test broke. ``harness``: ix or a
tool it drives did. ``sensor``: the instrument could not read an interaction that happened.
Only the first counts against the subject.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self

from pydantic import Field, model_validator

from ix.domain import schema
from ix.domain.experiment import Experiment, Level, ProbeSet, Shape, cells
from ix.domain.problem import Problem
from ix.domain.provenance import Content, Provenance
from ix.domain.registration import Deviation
from ix.domain.schema import Stored

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ix.domain.design import Design

#: The role of the control's second sample under A/A calibration.
CALIBRATION = "calibration"

Owner = Literal["subject", "harness", "sensor"]

#: How a run ended. ``interrupted`` covers a stop request (Ctrl-C, SIGTERM) and a process
#: that died while writing. ``running`` is a run some process is still writing.
RunStatus = Literal["running", "completed", "interrupted", "failed"]


class Failure(Stored):
    """Why a trial or a reading has no result, and whose failure it was."""

    owner: Owner
    #: A short stable word for the cause (``not_ready``, ``timeout``, ``cancelled``).
    reason: str
    message: str = ""


class TrialError(Exception):
    """Raised by an adapter for a trial it could not carry out: it becomes a failure owned by
    :attr:`owner`."""

    owner: ClassVar[Owner]

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


class SubjectError(TrialError):
    """The subject itself failed."""

    owner = "subject"


class HarnessError(TrialError):
    """The harness, or a tool it drives, failed; the subject did not."""

    owner = "harness"


class Trial(Stored):
    """One planned trial: a row of ``ix.v1.trial``."""

    #: Its place in the run's order, from 0.
    seq: int
    #: Which pass over the design, from 1.
    repeat: int
    cell: dict[str, Level] = {}
    probe: str
    subject: str
    #: The role the subject plays in this trial: the genre's role, or ``calibration``.
    role: str
    seed: int

    @property
    def block(self) -> tuple[int, str, str]:
        """The trials that ran side by side: one repeat of one probe in one cell."""
        return (self.repeat, cell_key(self.cell), self.probe)


class Interaction(Stored):
    """What happened when one trial was carried out: a row of ``ix.v1.interaction``."""

    seq: int
    subject: str
    role: str
    #: The probe that was applied.
    probe: str = ""
    #: When it started, in nanoseconds since the epoch, and how long it took.
    started_ns: int
    duration_ns: int
    #: What the probe left behind, by the facet it offers (``load``, ``response``).
    observations: dict[str, dict[str, Any]] = {}
    #: What each sensor that watched the subject during the probe saw, by sensor kind.
    watched: dict[str, dict[str, Any]] = {}
    #: Set when the trial could not be carried out. A failed trial is a row, never a gap.
    failure: Failure | None = None
    #: True when a stand-in answered instead of the subject (a mock runtime): nothing under
    #: test was measured in this trial.
    simulated: bool = False
    #: The rows of the run's session table (``matrix.v1.session``) behind this trial: every
    #: agent session and model call the subject made, failed ones included, in order. Each
    #: names the runtime and the model that answered.
    sessions: tuple[int, ...] = ()


class Reading(Stored):
    """One metric of one trial, as a sensor measured it: a row of ``ix.v1.reading``."""

    seq: int
    subject: str
    role: str
    #: The type URL of the sensor's kind.
    sensor: str
    metric: str
    shape: Shape
    unit: str = ""
    #: A boolean or scalar reading.
    value: bool | float | None = None
    #: A histogram or series reading, already reduced: each statistic of this trial's
    #: distribution, by name (``p50``, ``p99``, ``mean``, ...). A count reading: ``events``
    #: out of ``units``.
    statistics: dict[str, float] = {}
    failure: Failure | None = None
    #: What the sensor said about this trial beyond its numbers (the skill that fired, the
    #: tests that failed), the same for each metric it read from the trial.
    detail: dict[str, Any] = {}
    #: The rows of the run's session table the sensor made to read this trial (a judge's
    #: model calls or sessions), the same for each metric it read. The subject's sessions are
    #: the interaction's, under the same ``seq``.
    sessions: tuple[int, ...] = ()

    @model_validator(mode="after")
    def _inside_its_shape(self) -> Self:
        """A reading is refused outside its shape's domain: a boolean is 0 or 1, a scalar
        and every statistic finite, a count whole ``events`` from 0 to ``units``."""
        found = self._outside()
        if found:
            raise ValueError(
                f"a {self.shape} reading of {self.metric} is outside its domain: {found}"
            )
        return self

    def _outside(self) -> str:
        if self.shape == "boolean" and self.value is not None and self.value not in (0, 1):
            return f"{self.value!r} is neither true nor false"
        if self.value is not None and not math.isfinite(self.value):
            return f"{self.value!r} is not finite"
        not_finite = sorted(name for name, v in self.statistics.items() if not math.isfinite(v))
        if not_finite:
            return f"{', '.join(not_finite)} not finite"
        events, units = self.statistics.get("events"), self.statistics.get("units")
        if self.shape == "count" and events is not None and units is not None:
            if not (events.is_integer() and units.is_integer()):
                return f"{events:g} of {units:g} is not a count of whole units"
            if not 0 <= events <= units or units < 1:
                return f"{events:g} events of {units:g} units is not 0 to all of at least 1"
        return ""

    def number(self, statistic: str) -> float | None:
        """This trial's reading as one number under ``statistic``, or None when it has none.
        A count's one number is its ``rate``."""
        if self.failure is not None:
            return None
        if self.shape in ("boolean", "scalar"):
            return None if self.value is None else float(self.value)
        if self.shape == "count":
            counted = self.counted()
            return None if counted is None or statistic != "rate" else counted[0] / counted[1]
        return self.statistics.get(statistic)

    def counted(self) -> tuple[int, int] | None:
        """A count reading as (events, units); None for any other reading, or one without
        both counts."""
        if self.failure is not None or self.shape != "count":
            return None
        events, units = self.statistics.get("events"), self.statistics.get("units")
        if events is None or units is None or units < 1:
            return None
        return int(events), int(units)


class RunManifest(Stored):
    """What a run was asked to do and what it was held to: the one row of ``ix.v1.run``.

    The experiment and its design are stored as documents, as a registration stores a
    design, so a manifest written by a later ix still loads.
    """

    schema_: str = Field(default=schema.RUN, alias="schema")
    run: str
    experiment: str
    #: The ``ix.v1.experiment`` document that was run, and the directory its relative paths
    #: resolve against.
    declared: dict[str, Any] = {}
    base: str = ""
    experiment_digest: str = ""
    #: The digest of the plan this run carries out, and of its list of trials.
    plan_digest: str = ""
    schedule_digest: str = ""
    #: The ``ix.v1.design`` document: every value the run and its analysis use.
    design: dict[str, Any] = {}
    #: The probes as resolved when the run started.
    probes: list[dict[str, Any]] = []
    probes_digest: str = ""
    #: The digest of each file and folder the subjects and sensors name, as the run found
    #: them when it started: what was tested. Paths and digests, never what the files hold.
    contents: tuple[Content, ...] = ()
    #: Where the run ran from: the commit, and the versions of what ran it. None in a
    #: manifest an earlier ix wrote.
    provenance: Provenance | None = None
    #: The registration the run is held to, as it stood when the run started; None for an
    #: exploratory run of an unregistered experiment.
    registration: dict[str, Any] | None = None
    #: How the experiment differed from that registration when the run started.
    deviations: tuple[Deviation, ...] = ()
    #: False for an exploratory run, and for one whose experiment had deviations from its
    #: registration: its analysis is not a gate result.
    gated: bool = False
    #: True when the run was asked to be exploratory (``--exploratory``).
    exploratory: bool = False
    #: True for a dry run (``--dry-run``): the experiment as written, every subject on a
    #: stand-in. ``stood_in`` says what was set aside to put it there.
    dry_run: bool = False
    stood_in: tuple[str, ...] = ()
    #: What the run was told to change from the experiment as written, each in words: the
    #: repeats of a pilot (``ix run --repeats``). Never in a gated run.
    overridden: tuple[str, ...] = ()
    #: What the run warned of before it started: what ``validate`` warns of in the
    #: experiment run (a typo in prompt text), and a runtime that lets the agent use tools
    #: without asking. Said once per run, however many sessions were built.
    warnings: tuple[Problem, ...] = ()
    started_at: str = ""
    ix_version: str = ""


class Target(Stored):
    """What reaching a subject yields: how to address it. Descriptors, never live handles.

    ``facets`` holds what the subject's kind offers (``url``, ``pid``, ``headers``, or the
    handle of an agent session). ``simulated`` is true when a stand-in answers for the
    subject (a mock runtime): the trial then measures the harness, never the subject.
    """

    subject: str
    facets: dict[str, Any] = {}
    simulated: bool = False


#: What an environment provides to the trials: each service by name, with how to reach it.
Bindings = dict[str, dict[str, Any]]


def bound(bindings: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The bindings as the placeholders a subject or a probe names them by: ``{db.url}`` is
    the ``url`` the environment bound for its service ``db``."""
    return {
        f"{service}.{key}": value
        for service, found in bindings.items()
        for key, value in found.items()
    }


def cell_key(cell: dict[str, Level]) -> str:
    """A cell as one stable string: ``size=1MiB,rate=80/s``; ``-`` for the cell of no factors."""
    return ",".join(f"{name}={value}" for name, value in cell.items()) or "-"


def turns(design: Design) -> list[tuple[str, str]]:
    """Each (subject, role) that takes a turn in every block: the subjects in the order
    written, then the control's second sample when the design calibrates."""
    found = [(assigned.subject, assigned.role) for assigned in design.roles]
    if design.calibration == "aa" and design.control is not None:
        found.append((design.control, CALIBRATION))
    return found


def schedule(experiment: Experiment, design: Design, probes: ProbeSet) -> tuple[Trial, ...]:
    """Every trial of one run, in the order it is carried out.

    A **block** is one repeat of one probe in one cell; every subject takes one turn in it
    (and the control a second, under A/A). Blocks follow each other by repeat, then cell,
    then probe. Under ``interleaved`` the turns within a block are put in an order drawn
    from the seed, so that drift over a run falls on every subject alike; under
    ``sequential`` they stay in the order written.

    The order drawn is a sort by a digest of (seed, block, subject, role). It depends on
    nothing but its arguments, so the same design gives the same list on every machine and
    under every Python. Each trial's own seed is derived the same way, role included, so the
    control and its calibration sample never share one.
    """
    takes = turns(design)
    trials: list[Trial] = []
    for repeat in range(1, design.repeats + 1):
        for cell in cells(experiment.factors):
            for probe in probes.probes:
                block = [design.seed, repeat, cell_key(cell), probe.id]
                ordered = takes
                if design.order == "interleaved":
                    ordered = sorted(takes, key=lambda turn: schema.digest([*block, *turn]))
                for subject, role in ordered:
                    trials.append(
                        Trial(
                            seq=len(trials),
                            repeat=repeat,
                            cell=dict(cell),
                            probe=probe.id,
                            subject=subject,
                            role=role,
                            seed=int(schema.digest([*block, subject, role])[:8], 16),
                        )
                    )
    return tuple(trials)


def schedule_digest(trials: tuple[Trial, ...]) -> str:
    """The digest of a list of trials: equal exactly when two runs would do the same things
    in the same order."""
    return schema.digest([trial.document() for trial in trials])


@dataclass(frozen=True)
class RunRecord:
    """One run as read back from its record: the manifest, the three tables, and how the
    run ended. Readable for a run in any state, a killed one included."""

    run: str
    location: str
    status: RunStatus
    manifest: RunManifest
    trials: tuple[Trial, ...] = ()
    interactions: tuple[Interaction, ...] = ()
    readings: tuple[Reading, ...] = ()
    ended_at: str = ""
    #: What whatever ran it says went wrong, one sentence each. Empty for a clean run.
    errors: tuple[str, ...] = ()
    #: Each table's kind and the file that holds it, for a reader with ``jq`` or SQL.
    tables: Mapping[str, str] = field(default_factory=dict)

    @property
    def stood_in(self) -> bool:
        """A stand-in answered: the run was a dry run, or a stand-in (a mock runtime) answered
        any of its trials. Such a run measured the harness, not the subject."""
        return self.manifest.dry_run or any(i.simulated for i in self.interactions)

    @property
    def gated(self) -> bool:
        """Held to its registration, and measuring the subject: a run a gate may read. The
        manifest's ``gated`` is written before any trial, so a stand-in found answering
        afterwards ungates it here."""
        return self.manifest.gated and not self.stood_in


class BindingsRow(Stored):
    """What the environment provided: the one row of ``ix.v1.bindings``."""

    services: dict[str, dict[str, Any]] = {}
