"""ix: Intelligent Experimentation.

One file declares a question about a system and how it will be answered. ix checks it,
plans it and registers it before anything runs; then runs it, one trial at a time, and
reports what the readings say about the decision the file declared.

The names below are the library API: the functions, the documents they return, the three
records an author or an extension writes (``Experiment``, ``Genre``, ``Kind``), and the
errors. The command line calls these and nothing else. Every part of those records
(``SubjectConfig``, ``Margin``, ``Decision``, ``Interval``, ...) and every port an extension
implements (``Subject``, ``Probe``, ``Sensor``, ``Environment``) is exported from
``ix.domain``; the modules inside it are not public.
"""

from importlib.metadata import PackageNotFoundError, version

from ix.api import (
    configure_telemetry,
    deviations,
    genres,
    kinds,
    load,
    plan,
    register,
    report,
    resolve,
    run,
    runs,
    skill,
    validate,
)
from ix.domain.design import Design
from ix.domain.errors import (
    ERROR_KINDS,
    EXIT_CODES,
    EXIT_MEANINGS,
    EXIT_NEXT,
    ConfigError,
    IxError,
    NotFoundError,
    UnregisteredError,
    UsageError,
)
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
from ix.domain.report import Report, Runs

try:
    __version__ = version("ix")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = [
    "ERROR_KINDS",
    "EXIT_CODES",
    "EXIT_MEANINGS",
    "EXIT_NEXT",
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
    "Report",
    "Runs",
    "UnregisteredError",
    "UsageError",
    "Validation",
    "__version__",
    "configure_telemetry",
    "deviations",
    "error_document",
    "genres",
    "kinds",
    "load",
    "plan",
    "register",
    "report",
    "resolve",
    "run",
    "runs",
    "skill",
    "validate",
]
