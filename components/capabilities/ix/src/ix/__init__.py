"""ix: Intelligent Experimentation.

One file declares a question about a system and how it will be answered; ix checks it,
plans it and registers it before anything runs. ix does not yet run an experiment.

The names below are the library API: the functions, the documents they return, the three
records an author or an extension writes (``Experiment``, ``Genre``, ``Kind``), and the
errors. The command line calls these and nothing else. A record's parts (``Subject``,
``Probe``, ``Margin``, ...) are reachable as field types from ``ix.domain``.
"""

from importlib.metadata import PackageNotFoundError, version

from ix.api import (
    deviations,
    genres,
    kinds,
    load,
    plan,
    register,
    resolve,
    skill,
    validate,
)
from ix.domain.design import Design
from ix.domain.errors import EXIT_CODES, ConfigError, IxError, NotFoundError
from ix.domain.experiment import Experiment
from ix.domain.genre import Genre, Genres
from ix.domain.kind import Kind, Kinds
from ix.domain.plan import Plan
from ix.domain.problem import (
    PROBLEM_KINDS,
    InvalidExperimentError,
    Problem,
    Validation,
    error_document,
)
from ix.domain.registration import Deviations, Registered, RegistrationConflictError

try:
    __version__ = version("ix")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = [
    "EXIT_CODES",
    "PROBLEM_KINDS",
    "ConfigError",
    "Design",
    "Deviations",
    "Experiment",
    "Genre",
    "Genres",
    "InvalidExperimentError",
    "IxError",
    "Kind",
    "Kinds",
    "NotFoundError",
    "Plan",
    "Problem",
    "Registered",
    "RegistrationConflictError",
    "Validation",
    "__version__",
    "deviations",
    "error_document",
    "genres",
    "kinds",
    "load",
    "plan",
    "register",
    "resolve",
    "skill",
    "validate",
]
