"""The use cases of the experiment model: examine, validate, plan, register, deviations.

Each takes data: an experiment (or the parsed file it should be), the genres and kinds it
may name, and what a stored registration held. Reading and writing files is the composition
root's work; the one port used here is the probe source. Nothing starts a subject, applies a
probe or spends anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ix.domain.errors import ConfigError, NotFoundError, ProbeSourceError
from ix.domain.experiment import Experiment, ProbeSet, probe_set
from ix.domain.plan import Plan, RegistrationStatus, plan
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
from ix.domain.validation import check, parse, validation

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ix.domain.design import Design
    from ix.domain.genre import Genre, Genres
    from ix.domain.kind import Kinds
    from ix.domain.ports import ProbeSource


@dataclass(frozen=True)
class Vocabulary:
    """What an experiment may name: the genres, the kinds, and the probe sources by name."""

    genres: Genres
    kinds: Kinds
    probe_sources: Mapping[str, ProbeSource]


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


def examine(
    experiment: Experiment | Any, vocabulary: Vocabulary, *, base: str, source: str = ""
) -> Examined:
    """Check an experiment, or the parsed data of an experiment file.

    ``base`` is the directory relative paths in the experiment resolve against. Never raises
    on a wrong experiment: the problems are in the result.
    """
    source = source or "<experiment>"
    problems: list[Problem] = []
    if not isinstance(experiment, Experiment):
        parsed, problems = parse(experiment)
        if parsed is None:
            return unreadable(source, problems)
        experiment = parsed

    probes: ProbeSet | None = None
    if isinstance(experiment.probes, tuple):
        probes = probe_set(experiment.probes)
    else:
        names = sorted(vocabulary.probe_sources)
        reader = vocabulary.probe_sources.get(experiment.probes.source)
        if reader is None:
            problems.append(
                problem(
                    "probes.source",
                    "probe_source",
                    f"no probe source named {experiment.probes.source!r}",
                    allowed=names,
                    got=experiment.probes.source,
                )
            )
        else:
            try:
                probes = probe_set(reader.probes(experiment.probes.options, base))
            except ProbeSourceError as error:
                problems.append(problem(error.path, "probe_source", str(error), fix=error.fix))

    checked = check(experiment, vocabulary.genres, vocabulary.kinds, probes)
    return Examined(
        source=source,
        experiment=experiment,
        genre=checked.genre,
        probes=probes,
        design=checked.design if not problems else None,
        problems=(*problems, *checked.problems),
        warnings=checked.warnings,
    )


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


def _valid(examined: Examined, verb: str) -> tuple[Experiment, Genre, Design, ProbeSet]:
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
        )
    return examined.experiment, examined.genre, examined.design, examined.probes


def design(examined: Examined) -> Design:
    """The design of a valid experiment."""
    return _valid(examined, "resolved")[2]


def _current(
    examined: Examined, vocabulary: Vocabulary, *, now: str, version: str
) -> tuple[Plan, Registration]:
    experiment, genre, resolved, probes = _valid(examined, "planned")
    made = plan(experiment, genre, resolved, probes, vocabulary.kinds)
    current = freeze(
        experiment,
        resolved,
        probes,
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


def make_plan(
    examined: Examined,
    vocabulary: Vocabulary,
    stored: Registration | None,
    *,
    location: str = "",
    version: str = "",
) -> Plan:
    """What a run would do, and where the experiment stands against its registration."""
    made, current = _current(examined, vocabulary, now="", version=version)
    if stored is None:
        status = RegistrationStatus(status="none", location=location)
    else:
        found = deviations(stored, current)
        status = RegistrationStatus(
            status="deviations" if found else "matches", deviations=len(found), location=location
        )
    return made.model_copy(update={"registration": status})


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
    _, current = _current(examined, vocabulary, now=now, version=version)
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
    version: str = "",
) -> Deviations:
    """How the experiment differs from its registration."""
    _, current = _current(examined, vocabulary, now="", version=version)
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
