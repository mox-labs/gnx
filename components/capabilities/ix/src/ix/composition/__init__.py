"""Composition root: the only module that joins the use cases to concrete adapters.

It reads the experiment file, finds the genre and kind files visible from it, names the
probe sources, and keeps the registration file. Everything it hands to the use cases is
data, except the probe sources, which are the model's one port.

A source is the path of an experiment file (or of a directory holding ``experiment.yaml``),
or an :class:`~ix.domain.experiment.Experiment` built in memory. For a path, relative paths
resolve against the file's directory, the lab's ``genres/`` and ``kinds/`` beside it are
visible, and the registration is the file beside it. For an experiment object, ``base``
says where relative paths resolve (default: the current directory) and ``registration``
names the registration file, when there is to be one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version
from typing import TYPE_CHECKING

from ix.adapters._out.declaration_files import GenreFiles, KindFiles
from ix.adapters._out.experiment_file import RegistrationFile, read_experiment
from ix.adapters._out.probe_directory import DirectoryProbes
from ix.application import experiments as use_cases
from ix.application import skill as skill_document
from ix.application.experiments import Examined, Vocabulary
from ix.domain.errors import ConfigError
from ix.domain.experiment import Experiment
from ix.domain.genre import Genres, genres
from ix.domain.kind import Kinds, kinds
from ix.domain.problem import InvalidExperimentError, problem

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ix.domain.design import Design
    from ix.domain.plan import Plan
    from ix.domain.ports import ProbeSource
    from ix.domain.problem import Validation
    from ix.domain.registration import Deviations, Registered, Registration

type Source = str | os.PathLike[str] | Experiment
type PathLike = str | os.PathLike[str]

try:
    VERSION = _installed_version("ix")
except PackageNotFoundError:  # running from a source tree that was never installed
    VERSION = "0+unknown"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class _Opened:
    """A source, examined, with the vocabulary it was judged against and its registration
    file (None for an experiment object given no ``registration``)."""

    examined: Examined
    vocabulary: Vocabulary
    registration: RegistrationFile | None


class Root:
    """The wired experiment model. Build one to bind other adapters; :func:`root` is the
    default."""

    def __init__(
        self,
        genre_files: GenreFiles | None = None,
        kind_files: KindFiles | None = None,
        probe_sources: Mapping[str, ProbeSource] | None = None,
        *,
        version: str = VERSION,
        clock: Callable[[], str] = _now,
    ) -> None:
        self._genre_files = genre_files or GenreFiles()
        self._kind_files = kind_files or KindFiles()
        self._probe_sources: Mapping[str, ProbeSource] = (
            {"directory": DirectoryProbes()} if probe_sources is None else probe_sources
        )
        self._version = version
        self._clock = clock

    # --- what an experiment may name ---

    def genres(self, lab: PathLike | None = None) -> Genres:
        """The installed genres, plus those in ``<lab>/genres``."""
        return genres(self._genre_files.files(None if lab is None else os.fspath(lab)))

    def kinds(self, lab: PathLike | None = None) -> Kinds:
        """The installed kinds, plus those in ``<lab>/kinds``."""
        return kinds(self._kind_files.files(None if lab is None else os.fspath(lab)))

    def vocabulary(self, lab: PathLike | None = None) -> Vocabulary:
        return Vocabulary(
            genres=self.genres(lab), kinds=self.kinds(lab), probe_sources=self._probe_sources
        )

    def skill(self) -> str:
        return skill_document.render(self.genres(), self.kinds(), sorted(self._probe_sources))

    # --- opening a source ---

    def _open(
        self, source: Source, base: PathLike | None, registration: PathLike | None
    ) -> _Opened:
        record = None if registration is None else RegistrationFile(os.fspath(registration))
        if isinstance(source, Experiment):
            vocabulary = self.vocabulary(base)
            examined = use_cases.examine(
                source, vocabulary, base=os.fspath(base) if base is not None else os.getcwd()
            )
            return _Opened(examined, vocabulary, record)
        file = read_experiment(os.fspath(source))
        vocabulary = self.vocabulary(file.base)
        if file.error:
            examined = use_cases.unreadable(
                file.source,
                [
                    problem(
                        "",
                        "yaml_syntax",
                        f"the file is not well-formed YAML: {file.error}",
                        fix="correct the YAML at the line and column named",
                    )
                ],
            )
        else:
            examined = use_cases.examine(file.data, vocabulary, base=file.base, source=file.source)
        return _Opened(examined, vocabulary, record or RegistrationFile.beside(file.source))

    def _stored(self, opened: _Opened) -> Registration | None:
        if opened.registration is None:
            return None
        return use_cases.stored_registration(
            opened.registration.read(), opened.registration.location
        )

    # --- the use cases ---

    def load(self, path: PathLike) -> Experiment:
        examined = self._open(path, None, None).examined
        if examined.experiment is None:
            raise InvalidExperimentError(
                f"{examined.source} is not an experiment file "
                f"({len(examined.problems)} problem(s))",
                examined.problems,
                fix=f"ix validate {examined.source}",
            )
        return examined.experiment

    def validate(self, source: Source, *, base: PathLike | None = None) -> Validation:
        return use_cases.validate(self._open(source, base, None).examined)

    def design(self, source: Source, *, base: PathLike | None = None) -> Design:
        return use_cases.design(self._open(source, base, None).examined)

    def plan(
        self,
        source: Source,
        *,
        base: PathLike | None = None,
        registration: PathLike | None = None,
    ) -> Plan:
        opened = self._open(source, base, registration)
        # Check the experiment before the record: an invalid experiment is the first thing
        # to say, whatever state its registration is in.
        use_cases.design(opened.examined)
        return use_cases.make_plan(
            opened.examined,
            opened.vocabulary,
            self._stored(opened),
            location=opened.registration.location if opened.registration else "",
            version=self._version,
        )

    def register(
        self,
        source: Source,
        *,
        base: PathLike | None = None,
        registration: PathLike | None = None,
        amend: str | None = None,
    ) -> Registered:
        opened = self._open(source, base, registration)
        if opened.registration is None:
            raise ConfigError(
                "an experiment built in memory has no file to register beside",
                fix="pass registration=<path of the registration file>",
            )
        if amend is not None and not amend.strip():
            raise ConfigError(
                "amending a registration needs a reason",
                fix='ix register <path> --amend --reason "<why the design changed>"',
            )
        use_cases.design(opened.examined)
        outcome = use_cases.register(
            opened.examined,
            opened.vocabulary,
            self._stored(opened),
            location=opened.registration.location,
            now=self._clock(),
            version=self._version,
            amend_reason=amend,
        )
        if outcome.outcome != "unchanged":
            opened.registration.write(outcome.registration.document())
        return outcome

    def deviations(
        self,
        source: Source,
        *,
        base: PathLike | None = None,
        registration: PathLike | None = None,
    ) -> Deviations:
        opened = self._open(source, base, registration)
        use_cases.design(opened.examined)
        return use_cases.find_deviations(
            opened.examined,
            opened.vocabulary,
            self._stored(opened),
            location=opened.registration.location if opened.registration else "",
            version=self._version,
        )


_DEFAULT: Root | None = None


def root() -> Root:
    """The default wiring: installed genres and kinds, the ``directory`` probe source, the
    registration file beside the experiment."""
    global _DEFAULT  # noqa: PLW0603
    if _DEFAULT is None:
        _DEFAULT = Root()
    return _DEFAULT
