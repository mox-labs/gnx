"""The use cases of the experiment model: validate, plan, register, and check a registration.

Each reads an experiment file, the genre catalogue visible from it and its probes through
ports, then hands them to the domain. None starts a subject, applies a probe or spends
anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ix.domain import schema
from ix.domain.errors import ConfigError, NotFoundError, ProbeSourceError
from ix.domain.experiment import ProbeSet, probe_set
from ix.domain.genre import Catalogue, catalogue
from ix.domain.plan import Plan, plan
from ix.domain.problem import InvalidExperimentError, Problem, Validation
from ix.domain.registration import (
    Deviation,
    Registration,
    RegistrationConflictError,
    deviations,
    freeze,
)
from ix.domain.validation import Checked, check, parse, validation

if TYPE_CHECKING:
    from collections.abc import Callable

    from ix.domain.design import ResolvedDesign
    from ix.domain.experiment import Experiment
    from ix.domain.ports import ExperimentFiles, GenreLibrary, ProbeReader, Registrations


@dataclass(frozen=True)
class Study:
    """An experiment file with everything read that judging it needs."""

    source: str
    file_digest: str
    experiment: Experiment | None
    catalogue: Catalogue
    probes: ProbeSet | None
    checked: Checked | None
    problems: tuple[Problem, ...]
    warnings: tuple[Problem, ...]


@dataclass(frozen=True)
class Registered:
    """The outcome of ``register``: the record, where it is, and whether this call wrote it."""

    registration: Registration
    location: str
    created: bool

    def document(self) -> dict[str, Any]:
        """An ``ix.v1.registered`` document."""
        return {
            "schema": schema.REGISTERED,
            "created": self.created,
            "location": self.location,
            "registration": self.registration.document(),
        }


@dataclass(frozen=True)
class RegistrationCheck:
    """An experiment held against its registration."""

    registration: Registration
    location: str
    deviations: tuple[Deviation, ...]

    def document(self) -> dict[str, Any]:
        """An ``ix.v1.deviations`` document."""
        return {
            "schema": schema.DEVIATIONS,
            "experiment": self.registration.experiment,
            "location": self.location,
            "registered_at": self.registration.registered_at,
            "deviations": [deviation.document() for deviation in self.deviations],
        }


class Experiments:
    """The experiment model's use cases over its four ports."""

    def __init__(
        self,
        files: ExperimentFiles,
        genres: GenreLibrary,
        probes: ProbeReader,
        registrations: Registrations,
        *,
        version: str,
        clock: Callable[[], str],
    ) -> None:
        self._files = files
        self._genres = genres
        self._probes = probes
        self._registrations = registrations
        self._version = version
        self._clock = clock

    def catalogue(self, beside: str | None = None) -> Catalogue:
        """The genres visible from the experiment file ``beside`` (None: installed only)."""
        return catalogue(self._genres.records(beside))

    def study(self, path: str) -> Study:
        """Read and check the experiment at ``path``. Raises only when there is no file."""
        document = self._files.read(path)
        genres = self.catalogue(document.source)
        file_digest = schema.digest_bytes(document.raw)

        def unread(problems: list[Problem]) -> Study:
            return Study(
                source=document.source,
                file_digest=file_digest,
                experiment=None,
                catalogue=genres,
                probes=None,
                checked=None,
                problems=tuple(problems),
                warnings=(),
            )

        if document.error:
            return unread(
                [
                    Problem(
                        path="",
                        kind="yaml_syntax",
                        message=f"the file is not well-formed YAML: {document.error}",
                        fix="correct the YAML at the line and column named",
                    )
                ]
            )
        experiment, problems = parse(document.data, source=document.source)
        if experiment is None:
            return unread(problems)

        probes: ProbeSet | None = None
        if isinstance(experiment.probes, tuple):
            probes = probe_set(experiment.probes)
        else:
            try:
                probes = probe_set(self._probes.read(experiment.probes, document.source))
            except ProbeSourceError as error:
                problems.append(
                    Problem(
                        path=error.path,
                        kind="probe_source",
                        message=str(error),
                        fix=error.fix or "probes: {source: directory, path: probes}",
                    )
                )
        checked = check(experiment, genres, probes)
        return Study(
            source=document.source,
            file_digest=file_digest,
            experiment=experiment,
            catalogue=genres,
            probes=probes,
            checked=checked,
            problems=(*problems, *checked.problems),
            warnings=checked.warnings,
        )

    def validate(self, path: str) -> Validation:
        """Every problem with the experiment at ``path``, in one document."""
        study = self.study(path)
        genre = study.checked.genre if study.checked else None
        return validation(
            source=study.source,
            problems=study.problems,
            warnings=study.warnings,
            experiment=study.experiment.name if study.experiment else "",
            genre=genre.name if genre else "",
        )

    def _valid(self, path: str) -> tuple[Study, Experiment, ResolvedDesign, ProbeSet]:
        study = self.study(path)
        design = study.checked.design if study.checked else None
        if study.problems or study.experiment is None or design is None or study.probes is None:
            count = len(study.problems)
            raise InvalidExperimentError(
                f"{study.source} has {count} problem(s) and cannot be planned",
                study.problems,
                fix=f"ix validate {study.source}",
            )
        return study, study.experiment, design, study.probes

    def load(self, path: str) -> Experiment:
        """The experiment at ``path`` as declared. Raises when the file is not one."""
        study = self.study(path)
        if study.experiment is None:
            raise InvalidExperimentError(
                f"{study.source} is not an experiment file ({len(study.problems)} problem(s))",
                study.problems,
                fix=f"ix validate {study.source}",
            )
        return study.experiment

    def design(self, path: str) -> ResolvedDesign:
        """The resolved design of the valid experiment at ``path``."""
        return self._valid(path)[2]

    def plan(self, path: str) -> Plan:
        """What a run of the valid experiment at ``path`` would do."""
        _, experiment, design, probes = self._valid(path)
        return plan(experiment, design, probes)

    def _current(self, path: str) -> tuple[Study, Registration]:
        study, experiment, design, probes = self._valid(path)
        current = freeze(
            experiment,
            design,
            probes,
            file_digest=study.file_digest,
            registered_at=self._clock(),
            ix_version=self._version,
        )
        return study, current

    def _stored(self, source: str) -> Registration | None:
        document = self._registrations.read(source)
        if document is None:
            return None
        try:
            return Registration.model_validate(dict(document))
        except ValidationError as error:
            location = self._registrations.location(source)
            raise ConfigError(
                f"the registration at {location} is not an ix.v1.registration: "
                f"{error.error_count()} field(s) wrong",
                fix=f"restore {location} from version control; a registration is written "
                "once by `ix register` and not edited",
            ) from error

    def register(self, path: str) -> Registered:
        """Freeze the valid experiment at ``path``.

        Registering again with nothing changed returns the existing record. Registering an
        experiment that has changed since is refused: the change is a deviation, and
        overwriting the record would erase it.
        """
        study, current = self._current(path)
        location = self._registrations.location(study.source)
        stored = self._stored(study.source)
        if stored is None:
            self._registrations.write(study.source, current.document())
            return Registered(registration=current, location=location, created=True)
        found = deviations(stored, current)
        if found:
            fields = ", ".join(deviation.field for deviation in found)
            raise RegistrationConflictError(
                f"{study.source} is already registered and has changed since: {fields}",
                found,
                fix="keep the registration and let the change stand as a recorded deviation "
                f"(`ix register {study.source} --check` lists them); to register anew, "
                f"remove {location} yourself, which discards the earlier registration",
            )
        return Registered(registration=stored, location=location, created=False)

    def deviations(self, path: str, registration: Registration | None = None) -> RegistrationCheck:
        """How the experiment at ``path`` differs from ``registration`` (default: the stored
        one). Raises :class:`NotFoundError` when it was never registered."""
        study, current = self._current(path)
        location = self._registrations.location(study.source)
        registered = registration or self._stored(study.source)
        if registered is None:
            raise NotFoundError(
                f"{study.source} is not registered",
                fix=f"ix register {study.source}",
            )
        return RegistrationCheck(
            registration=registered,
            location=location,
            deviations=deviations(registered, current),
        )
