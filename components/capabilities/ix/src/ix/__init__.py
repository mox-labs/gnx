"""ix: Intelligent Experimentation.

Evals, benchmarks, and QoS experiments for AI agents, skills and services.

The names below are the library API of the experiment model: the records an experiment file
and a genre file load into, and the functions that validate, plan and register an experiment.
The command line calls these and nothing else.
"""

from ix.api import (
    __version__,
    catalogue,
    deviations,
    load,
    plan,
    register,
    resolve,
    skill,
    validate,
)
from ix.application.experiments import Registered, RegistrationCheck
from ix.domain.design import ResolvedDesign, Setting, SubjectRole
from ix.domain.errors import ConfigError, IxError, NotFoundError
from ix.domain.experiment import (
    Bar,
    Environment,
    Experiment,
    Guard,
    Margin,
    Metric,
    MetricRef,
    ProbeBinding,
    ProbeSource,
    Spend,
    SubjectBinding,
)
from ix.domain.genre import AnalysisRef, Catalogue, Default, DesignDefaults, Genre, Role
from ix.domain.plan import Plan
from ix.domain.problem import PROBLEM_KINDS, InvalidExperimentError, Problem, Validation
from ix.domain.registration import Deviation, Registration, RegistrationConflictError

__all__ = [
    "PROBLEM_KINDS",
    "AnalysisRef",
    "Bar",
    "Catalogue",
    "ConfigError",
    "Default",
    "DesignDefaults",
    "Deviation",
    "Environment",
    "Experiment",
    "Genre",
    "Guard",
    "InvalidExperimentError",
    "IxError",
    "Margin",
    "Metric",
    "MetricRef",
    "NotFoundError",
    "Plan",
    "ProbeBinding",
    "ProbeSource",
    "Problem",
    "Registered",
    "Registration",
    "RegistrationCheck",
    "RegistrationConflictError",
    "ResolvedDesign",
    "Role",
    "Setting",
    "Spend",
    "SubjectBinding",
    "SubjectRole",
    "Validation",
    "__version__",
    "catalogue",
    "deviations",
    "load",
    "plan",
    "register",
    "resolve",
    "skill",
    "validate",
]
