"""The library API of the experiment model, wired to its filesystem adapters.

This is where the model's ports meet their adapters: YAML experiment files, genre files found
through the ``ix.genre`` entry point and beside the experiment, probe directories, and a
registration record beside the file. Everything here takes the path of an experiment file (or
of a directory holding ``experiment.yaml``) and returns a versioned document model.

The earlier agent-eval harness has its own composition root, :mod:`ix.composition`, which
this module does not import.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from ix.adapters._out.experiment_files import FileRegistrations, YamlExperimentFiles
from ix.adapters._out.genre_files import GenreFiles
from ix.adapters._out.probe_files import ProbeFiles
from ix.application import skill as _skill
from ix.application.experiments import Experiments

if TYPE_CHECKING:
    from ix.application.experiments import Registered, RegistrationCheck
    from ix.domain.design import ResolvedDesign
    from ix.domain.experiment import Experiment
    from ix.domain.genre import Catalogue
    from ix.domain.plan import Plan
    from ix.domain.problem import Validation
    from ix.domain.registration import Registration

type PathLike = str | os.PathLike[str]

try:
    __version__ = version("ix")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@cache
def _experiments() -> Experiments:
    return Experiments(
        YamlExperimentFiles(),
        GenreFiles(),
        ProbeFiles(),
        FileRegistrations(),
        version=__version__,
        clock=_now,
    )


def load(path: PathLike) -> Experiment:
    """The experiment at ``path`` as its file declares it.

    Raises :class:`InvalidExperimentError` (with every problem) when the file is not an
    experiment, and :class:`NotFoundError` when there is no file.
    """
    return _experiments().load(os.fspath(path))


def catalogue(beside: PathLike | None = None) -> Catalogue:
    """The genre catalogue: installed genres, plus those in ``genres/`` beside the
    experiment file ``beside``. Refused genre files are in ``Catalogue.rejected``.

    Not named ``genres``: ``ix.genres`` is the package that holds the built-in genre files.
    """
    return _experiments().catalogue(None if beside is None else os.fspath(beside))


def validate(path: PathLike) -> Validation:
    """Every problem with the experiment at ``path``. Runs nothing; never raises on a wrong
    experiment, only on a missing file."""
    return _experiments().validate(os.fspath(path))


def resolve(path: PathLike) -> ResolvedDesign:
    """The resolved design: the experiment plus everything its genre supplies, each value
    with its source. Raises :class:`InvalidExperimentError` when the experiment is invalid."""
    return _experiments().design(os.fspath(path))


def plan(path: PathLike) -> Plan:
    """What a run would do. Pure: the same file gives the same plan and the same digest.
    Raises :class:`InvalidExperimentError` when the experiment is invalid."""
    return _experiments().plan(os.fspath(path))


def register(path: PathLike) -> Registered:
    """Freeze the experiment's decision parameters in a record beside it.

    Idempotent while nothing changes. Raises :class:`RegistrationConflictError` when the
    experiment has changed since it was registered.
    """
    return _experiments().register(os.fspath(path))


def deviations(path: PathLike, registration: Registration | None = None) -> RegistrationCheck:
    """How the experiment differs from ``registration`` (default: the record beside it).
    Raises :class:`NotFoundError` when there is no registration."""
    return _experiments().deviations(os.fspath(path), registration)


def skill() -> str:
    """The document that teaches an agent to write an experiment for each installed genre."""
    return _skill.render(catalogue())
