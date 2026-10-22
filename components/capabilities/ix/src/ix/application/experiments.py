"""The use cases of the experiment model: examine, validate, plan, register, deviations.

Each takes data: an experiment (or the data it should be), the probes read from its folder
when it writes none inline, the genres and kinds it may name, and what a stored registration
held. Reading and writing files is the composition root's work. Nothing starts a subject,
applies a probe or calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ix.domain import schema
from ix.domain.analysis import Spread, spread
from ix.domain.design import select_sensors
from ix.domain.errors import ConfigError, NotFoundError
from ix.domain.experiment import PROBES_FOLDER, Experiment, ProbeConfig, ProbeSet, probe_set
from ix.domain.plan import Plan, RegistrationStatus, plan, sensitivity
from ix.domain.ports import Review, Surroundings
from ix.domain.problem import InvalidExperimentError, Problem, Validation, problem
from ix.domain.registration import (
    Deviations,
    Registered,
    Registration,
    RegistrationConflictError,
    amend,
    deviations,
    freeze,
)
from ix.domain.report import design_of
from ix.domain.validation import check, parse, validation

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from ix.domain.design import Design
    from ix.domain.genre import Genre, Genres
    from ix.domain.kind import Kinds
    from ix.domain.run import RunRecord


@dataclass(frozen=True)
class Vocabulary:
    """What an experiment may name: the genres and the kinds."""

    genres: Genres
    kinds: Kinds
    #: The kinds (type URLs) a run could carry out here; None when nothing was said.
    adapters: frozenset[str] | None = None


@dataclass(frozen=True)
class FolderProbes:
    """What the probes folder beside an experiment held: its probes, or None when there is
    no such folder; and every problem with its files."""

    location: str
    probes: tuple[ProbeConfig, ...] | None = None
    problems: tuple[Problem, ...] = ()


@dataclass(frozen=True)
class Examined:
    """An experiment with everything judged that judging it needs."""

    #: Where the experiment came from, for messages: a file path, or ``<experiment>``.
    source: str
    experiment: Experiment | None
    genre: Genre | None
    probes: ProbeSet | None
    design: Design | None
    problems: tuple[Problem, ...]
    warnings: tuple[Problem, ...] = ()
    #: False when the file did not load, so the later checks never ran.
    complete: bool = True

    @property
    def name(self) -> str:
        return self.experiment.name if self.experiment else ""


def unreadable(source: str, problems: list[Problem]) -> Examined:
    """A file that is not an experiment: its problems, and nothing else was checked."""
    return Examined(
        source=source,
        experiment=None,
        genre=None,
        probes=None,
        design=None,
        problems=tuple(problems),
        complete=False,
    )


def _probes(
    experiment: Experiment, folder: FolderProbes | None
) -> tuple[ProbeSet | None, list[Problem]]:
    """The probes a run would apply: those written inline, or else the folder's."""
    if experiment.probes:
        return probe_set(experiment.probes), []
    if folder is None or (folder.probes is None and not folder.problems):
        where = folder.location if folder is not None else f"{PROBES_FOLDER}/"
        return None, [
            problem(
                "probes",
                kind="probes_empty",
                message=f"the experiment writes no probes and there is no folder {where} to "
                "read them from, so a run would apply no stimulus",
                fix=f"write probes: [...] in the file, or put one file per probe in "
                f"{PROBES_FOLDER}/ beside it",
            )
        ]
    if folder.probes is None:
        return None, list(folder.problems)
    return probe_set(folder.probes), list(folder.problems)


def examine(
    experiment: Experiment | object,
    vocabulary: Vocabulary,
    *,
    base: str,
    source: str = "",
    folder: FolderProbes | None = None,
) -> Examined:
    """Check an experiment, or the data an experiment should be.

    ``base`` is the directory relative paths in the experiment resolve against; ``folder``
    is what its probes folder held, read only when it writes no probes inline. Never raises
    on a wrong experiment: the problems are in the result.
    """
    source = source or "<experiment>"
    problems: list[Problem] = []
    if not isinstance(experiment, Experiment):
        parsed, problems = parse(experiment)
        if parsed is None:
            return unreadable(source, problems)
        experiment = parsed

    probes, problems = _probes(experiment, folder)
    checked = check(
        experiment,
        vocabulary.genres,
        vocabulary.kinds,
        probes,
        adapters=vocabulary.adapters,
    )
    return Examined(
        source=source,
        experiment=experiment,
        genre=checked.genre,
        probes=probes,
        design=checked.design if not problems else None,
        problems=(*problems, *checked.problems),
        warnings=checked.warnings,
    )


def reviewed(
    examined: Examined, vocabulary: Vocabulary, adapter: Callable[[str], object], *, base: str
) -> Examined:
    """``examined`` with what the adapters of its subjects and sensors find wrong added to
    its problems: each that is a :class:`~ix.domain.ports.Review`, ``adapter(kind)`` giving
    the adapter of a kind (its type URL) or None. What only an adapter can tell (an option
    its runtime refuses, a setting that leaves a sensor nothing to observe) is then a
    problem like any other. ``base`` is the directory the experiment's relative paths
    resolve against."""
    experiment, probes = examined.experiment, examined.probes
    if experiment is None:
        return examined
    instruments = select_sensors(experiment, vocabulary.kinds, probes)
    inline = bool(experiment.probes)
    placed = tuple(
        (f"probes.{index if inline else probe.id}", probe)
        for index, probe in enumerate(probes.probes if probes else ())
    )

    def around(path: str) -> Surroundings:
        return Surroundings(path=path, base=base, sensors=instruments.sensors, probes=placed)

    found: list[Problem] = []
    for name, subject in experiment.subjects.items():
        built = adapter(schema.url("subject", subject.kind))
        if isinstance(built, Review):
            found += built.review(subject.options, around(f"subjects.{name}"))
    listed = {
        schema.url("sensor", entry.kind): index for index, entry in enumerate(experiment.sensors)
    }
    for sensor in instruments.sensors:
        built = adapter(sensor.kind)
        if isinstance(built, Review):
            index = listed.get(sensor.kind)
            found += built.review(
                sensor.options, around("sensors" if index is None else f"sensors.{index}")
            )
    if not found:
        return examined
    return replace(examined, problems=(*examined.problems, *found), design=None)


def validate(examined: Examined) -> Validation:
    """Every problem with the experiment, in one document."""
    return validation(
        source=examined.source,
        problems=examined.problems,
        warnings=examined.warnings,
        experiment=examined.name,
        genre=examined.genre,
        complete=examined.complete,
    )


@dataclass(frozen=True)
class Valid:
    """The parts of an experiment that has no problem."""

    experiment: Experiment
    genre: Genre
    design: Design
    probes: ProbeSet


def valid(examined: Examined, verb: str) -> Valid:
    """The parts of a valid experiment, or :class:`InvalidExperimentError` with every
    problem. ``verb`` is what the caller meant to do with it (``planned``, ``run``)."""
    if (
        examined.problems
        or examined.experiment is None
        or examined.genre is None
        or examined.design is None
        or examined.probes is None
    ):
        raise InvalidExperimentError(
            f"{examined.source} has {len(examined.problems)} problem(s) and cannot be {verb}",
            examined.problems,
            fix=f"ix validate {examined.source}"
            if examined.source != "<experiment>"
            else "ix.validate(experiment) lists them",
            complete=examined.complete,
        )
    return Valid(
        experiment=examined.experiment,
        genre=examined.genre,
        design=examined.design,
        probes=examined.probes,
    )


def require_valid(examined: Examined, verb: str) -> None:
    """Raise :class:`InvalidExperimentError`, with every problem, unless the experiment is
    valid: an invalid experiment is the first thing to say, before anything is read from
    its registration or its runs."""
    valid(examined, verb)


def design(examined: Examined) -> Design:
    """The design of a valid experiment."""
    return valid(examined, "resolved").design


def standing(
    examined: Examined, vocabulary: Vocabulary, *, now: str, version: str
) -> tuple[Plan, Registration]:
    """The plan of a valid experiment and its registration as it would be frozen now."""
    checked = valid(examined, "planned")
    made = plan(
        checked.experiment,
        checked.genre,
        checked.design,
        checked.probes,
        vocabulary.kinds,
        adapters=vocabulary.adapters,
    )
    current = freeze(
        checked.experiment,
        checked.design,
        checked.probes,
        plan_digest=made.digest,
        registered_at=now,
        ix_version=version,
    )
    return made, current


def stored_registration(document: Mapping[str, Any] | None, location: str) -> Registration | None:
    """A registration document as read from ``location``, or None when there is none."""
    if document is None:
        return None
    try:
        return Registration.model_validate(dict(document))
    except ValidationError as error:
        reasons = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: "
            f"{item['msg'].removeprefix('Value error, ')}"
            for item in error.errors()
        )
        raise ConfigError(
            f"the registration at {location} cannot be read by this ix: {reasons}",
            fix="read it with the ix that wrote it; if the file was edited by hand, restore "
            "it from version control. A registration is written by `ix register` only",
        ) from error


def earlier_spread(design: Design, earlier: Iterable[RunRecord]) -> Spread | None:
    """How much the primary varied in the first of ``earlier`` (latest first) that finished
    and measured the same primary, read the same way; None when none did. A run a stand-in
    answered any trial of (a dry run) measured the harness, not the subjects, and is passed
    over."""
    primary = design.primary
    if primary is None:
        return None
    for record in earlier:
        if record.status != "completed":
            continue
        if any(interaction.simulated for interaction in record.interactions):
            continue
        try:
            ran = design_of(record)
        except ConfigError:
            continue  # written by another ix: its design cannot be read here
        # The spread is of the same numbers only if the primary, how it is compared (the
        # margin's basis) and whether there is a control are the same.
        basis = ran.margin.basis if ran.margin else None
        if (
            ran.primary != primary
            or basis != (design.margin.basis if design.margin else None)
            or (ran.control is None) != (design.control is None)
        ):
            continue
        found = spread(ran, record.trials, record.interactions, record.readings, run=record.run)
        if found is not None:
            return found
    return None


def make_plan(
    examined: Examined,
    vocabulary: Vocabulary,
    stored: Registration | None,
    *,
    location: str = "",
    unstartable: tuple[str, ...] = (),
    earlier: Iterable[RunRecord] = (),
) -> Plan:
    """What a run would do, where the experiment stands against its registration, the
    ``unstartable`` subjects the host found, and, from the ``earlier`` runs of the
    experiment (latest first), how small an effect it can detect."""
    made, current = standing(examined, vocabulary, now="", version="")
    checked = valid(examined, "planned")
    blocks = checked.design.repeats * len(checked.probes.probes)
    sensed = sensitivity(checked.design, blocks, earlier_spread(checked.design, earlier))
    if stored is None:
        status = RegistrationStatus(status="none", location=location)
    else:
        found = deviations(stored, current)
        status = RegistrationStatus(
            status="deviations" if found else "matches", deviations=len(found), location=location
        )
    return made.model_copy(
        update={
            "registration": status,
            "unstartable": unstartable,
            "sensitivity": sensed,
            "warnings": examined.warnings,
        }
    )


def register(
    examined: Examined,
    vocabulary: Vocabulary,
    stored: Registration | None,
    *,
    location: str,
    now: str,
    version: str,
    amend_reason: str | None = None,
) -> Registered:
    """Decide what registering does. The caller writes the record unless ``unchanged``.

    No record: ``created``. A record and nothing changed: ``unchanged``. A record and
    deviations: refused, unless ``amend_reason`` is given; then ``amended``, with the
    superseded record and the reason kept inside the new one.
    """
    _, current = standing(examined, vocabulary, now=now, version=version)
    if stored is None:
        if amend_reason is not None:
            raise NotFoundError(
                f"{examined.source} has no registration to amend",
                fix=f"ix register {examined.source}",
            )
        return Registered(outcome="created", location=location, registration=current)
    found = deviations(stored, current)
    if not found:
        return Registered(outcome="unchanged", location=location, registration=stored)
    if amend_reason is None:
        raise RegistrationConflictError(
            f"{examined.source} is registered and has changed since: "
            f"{', '.join(deviation.field for deviation in found)}",
            found,
            fix="leave the registration as it is and the change stands as a recorded "
            f"deviation (`ix deviations {examined.source}` lists them); to replace it on "
            f'purpose, `ix register {examined.source} --amend --reason "<why>"` keeps the '
            "old record",
        )
    amended = amend(stored, current, reason=amend_reason, accepted=found, amended_at=now)
    return Registered(outcome="amended", location=location, registration=amended)


def find_deviations(
    examined: Examined,
    vocabulary: Vocabulary,
    stored: Registration | None,
    *,
    location: str = "",
) -> Deviations:
    """How the experiment differs from its registration."""
    _, current = standing(examined, vocabulary, now="", version="")
    if stored is None:
        raise NotFoundError(
            f"{examined.source} is not registered",
            fix=f"ix register {examined.source}",
        )
    return Deviations(
        experiment=current.experiment,
        location=location,
        registered_at=stored.registered_at,
        deviations=deviations(stored, current),
    )
