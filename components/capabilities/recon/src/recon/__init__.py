"""recon: config-driven collection. One YAML file names the sources and what to ask them;
recon plans it, fetches or runs what it names, and writes the answers as JSONL tables that
SQL can query. A source that does not end is collected as a stream, into windows that are
archives too.

The names below are the library API: the operations (each returns a versioned document),
the skill text, the errors and the tables of error kinds and exit codes, and
:class:`StreamStop`, which stops a running stream. The command line calls these and nothing
else. Collector types and transforms are extensions: their contract is
``recon.domain.collector``. Telemetry goes through OpenTelemetry's API only; an
application installs the SDK (:func:`configure_telemetry`, or its own setup).
"""

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from recon.api import (
    ack,
    capture,
    changes_since,
    configure_telemetry,
    deliver,
    init,
    plan,
    query,
    read_since,
    schedule_install,
    schedule_list,
    schedule_remove,
    schedule_status,
    schedule_tick,
    status,
    stream,
    subscribe,
    survey,
    templates,
    unsubscribe,
    watch,
)
from recon.domain.documents import (
    Capture,
    ChangesSince,
    DeliverDocument,
    ErrorDocument,
    MissionDocument,
    PlanDocument,
    QueryRows,
    RecordsSince,
    ScheduleDocument,
    SchedulesDocument,
    StatusDocument,
    StreamDocument,
    SubscriptionDocument,
    SurveyDocument,
    TableOutcome,
    TemplatesDocument,
    TickDocument,
    WindowOutcome,
    error_document,
    stream_failure,
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

if TYPE_CHECKING:
    from recon.composition import StreamStop


def __getattr__(name: str) -> Any:
    """``StreamStop`` is the application's, which the facade reaches only through the
    composition root, as each operation does: on first use, so ``import recon`` wires
    nothing."""
    if name == "StreamStop":
        from recon.composition import StreamStop

        return StreamStop
    raise AttributeError(f"module 'recon' has no attribute {name!r}")


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
    "ChangesSince",
    "CollectionError",
    "ConfigError",
    "ConflictError",
    "DeliverDocument",
    "ErrorDocument",
    "MissionDocument",
    "NotFoundError",
    "PlanDocument",
    "Problem",
    "QueryError",
    "QueryRows",
    "ReconError",
    "RecordsSince",
    "ScheduleDocument",
    "SchedulesDocument",
    "StatusDocument",
    "StreamDocument",
    "StreamStop",
    "SubscriptionDocument",
    "SurveyDocument",
    "TableOutcome",
    "TemplatesDocument",
    "TickDocument",
    "UsageError",
    "WindowOutcome",
    "__version__",
    "ack",
    "capture",
    "changes_since",
    "configure_telemetry",
    "deliver",
    "error_document",
    "init",
    "plan",
    "query",
    "read_since",
    "schedule_install",
    "schedule_list",
    "schedule_remove",
    "schedule_status",
    "schedule_tick",
    "skill",
    "skill_references",
    "status",
    "stream",
    "stream_failure",
    "subscribe",
    "survey",
    "survey_failure",
    "templates",
    "unsubscribe",
    "watch",
]
