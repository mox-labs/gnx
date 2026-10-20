"""recon: config-driven collection. One YAML file names the sources and what to ask them;
recon plans it, fetches or runs what it names, and writes the answers as JSONL tables that
SQL can query.

The names below are the library API: the operations (each returns a versioned document),
the skill text, the errors and the tables of error kinds and exit codes. The command line
calls these and nothing else. Collector types and transforms are extensions: their contract
is ``recon.domain.collector``. Telemetry goes through OpenTelemetry's API only; an
application installs the SDK (:func:`configure_telemetry`, or its own setup).
"""

from importlib.metadata import PackageNotFoundError, version

from recon.api import (
    capture,
    configure_telemetry,
    init,
    plan,
    query,
    status,
    survey,
    templates,
)
from recon.domain.documents import (
    Capture,
    ErrorDocument,
    MissionDocument,
    PlanDocument,
    QueryRows,
    StatusDocument,
    SurveyDocument,
    TableOutcome,
    TemplatesDocument,
    error_document,
    survey_failure,
)
from recon.domain.errors import (
    ERROR_KINDS,
    EXIT_CODES,
    EXIT_MEANINGS,
    EXIT_NEXT,
    PROBLEM_KINDS,
    CollectionError,
    ConfigError,
    ConflictError,
    NotFoundError,
    Problem,
    QueryError,
    ReconError,
    UsageError,
)
from recon.skill_files import skill, skill_references

try:
    __version__ = version("recon")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = [
    "ERROR_KINDS",
    "EXIT_CODES",
    "EXIT_MEANINGS",
    "EXIT_NEXT",
    "PROBLEM_KINDS",
    "Capture",
    "CollectionError",
    "ConfigError",
    "ConflictError",
    "ErrorDocument",
    "MissionDocument",
    "NotFoundError",
    "PlanDocument",
    "Problem",
    "QueryError",
    "QueryRows",
    "ReconError",
    "StatusDocument",
    "SurveyDocument",
    "TableOutcome",
    "TemplatesDocument",
    "UsageError",
    "__version__",
    "capture",
    "configure_telemetry",
    "error_document",
    "init",
    "plan",
    "query",
    "skill",
    "skill_references",
    "status",
    "survey",
    "survey_failure",
    "templates",
]
