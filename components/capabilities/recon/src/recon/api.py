"""The library API: thin functions over the composition root.

Each returns a versioned document (``recon.v1.<shape>``) whose ``document()`` is what the
command line prints under ``--json``, or raises a
:class:`~recon.ReconError` whose ``kind`` says what to do next. Missions live under
``<root>/.recon/``: ``root`` is the project, by default the git root of the working
directory (or the working directory outside a repository), and a ``local`` source's
relative ``url`` is relative to it too, so a caller need not change directory. A config
path of ``-`` reads standard input. The root is built on each call, so importing ``recon``
wires nothing and a call sees the plugins and the environment of that moment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from recon.domain.archives import READ_LIMIT

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from recon.composition import Recon
    from recon.domain.documents import (
        Capture,
        MissionDocument,
        PlanDocument,
        QueryRows,
        RecordsSince,
        StatusDocument,
        SurveyDocument,
        TemplatesDocument,
    )
    from recon.domain.errors import Problem


def _root(project: Path | None) -> Recon:
    from recon.composition import root

    return root(project=project)


def init(
    name: str,
    *,
    template: str | None = None,
    config: Path | None = None,
    replace: bool = False,
    root: Path | None = None,
) -> MissionDocument:
    """Create the mission ``name`` from a built-in template (default ``code-forensics``) or
    from the config file ``config``. ``recon.v1.mission``.

    Idempotent: a mission that already has this config is left as it is (``outcome:
    unchanged``). Raises ConflictError when it has a different one, unless ``replace``
    (``outcome: replaced``); a new mission's is ``created``."""
    return _root(root).init(name, template=template, config=config, replace=replace)


def plan(
    name: str, *, config: Path | None = None, replace: bool = False, root: Path | None = None
) -> PlanDocument:
    """What ``survey`` would do, without doing it. ``recon.v1.plan``.

    Writes nothing, and fetches and runs nothing, but for the preflight of a collector type
    that declares one, which the document's ``preflight`` names: an ``mcp`` run's server is
    started (or connected to) and asked for its tools, and ``requirements`` says what was
    found. ``config`` is a file to plan instead of the mission's own; ``replace`` allows it to
    differ from the mission's config. Raises ConfigError listing every problem; its
    ``complete`` is false when more may follow their fixes."""
    return _root(root).plan(name, config=config, replace=replace)


def survey(
    name: str,
    *,
    config: Path | None = None,
    replace: bool = False,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    root: Path | None = None,
) -> SurveyDocument:
    """Plan, then collect every table into a new archive. ``recon.v1.survey``.

    Failed tables do not raise: the document says ``status: incomplete``, and
    :func:`survey_failure` turns it into the error to act on. ``on_event`` receives each
    table's outcome as it lands."""
    return _root(root).survey(name, config=config, replace=replace, on_event=on_event)


def status(mission: str | None = None, *, root: Path | None = None) -> StatusDocument:
    """Every mission with its latest archive, and the installed plugins. ``recon.v1.status``.

    Given ``mission``, that mission alone, the one entry of ``missions``, with every archive
    it has (id, path, times, state, tables) as its ``history``: the ids ``query`` takes as
    ``archive``. Raises NotFoundError for a mission that does not exist."""
    return _root(root).status(mission)


def query(
    name: str, sql: str, *, archive: str | None = None, root: Path | None = None
) -> QueryRows:
    """Rows from the latest archive of ``name``, or the archive named (an id ``status(name)``
    lists). ``recon.v1.query-rows``, naming the archive as ``archive_id``."""
    return _root(root).query(name, sql, archive=archive)


def read_since(
    name: str,
    cursor: str | None = None,
    *,
    archive: str | None = None,
    limit: int = READ_LIMIT,
    root: Path | None = None,
) -> RecordsSince:
    """The records of the latest archive of ``name``, or the archive named, written since
    ``cursor``: every record from the first when there is none. ``recon.v1.records-since``.

    Each read returns whole records only, at most ``limit``, from the tables sealed and the
    one being written alike, and a ``cursor`` to pass to the next read of the same archive
    (name it with ``archive``: its ``archive_id``). The cursor is opaque. ``state`` says
    whether a survey is still writing the archive; ``sealed`` and ``retracted`` name the
    tables that ended since, a retracted table's records void. Reads write nothing, so a
    reader changes no byte of the archive. Raises UsageError for a cursor recon did not give
    for that archive, or a ``limit`` below 1."""
    return _root(root).read_since(name, cursor, archive=archive, limit=limit)


def configure_telemetry() -> list[Problem]:
    """For an application, never a library: install OpenTelemetry's SDK so that recon's
    spans and metrics export as the ``OTEL_*`` variables say, for each signal they ask for
    whose provider is not installed yet. Returns warnings (``otel_unavailable``) for what
    they ask and cannot get, such as the SDK in an install without it. The command line
    calls this; the other operations never do, so a program that imports recon configures
    OpenTelemetry itself, or calls this."""
    from recon.composition import configure_telemetry as configure

    return configure()


def templates() -> TemplatesDocument:
    """The built-in templates. ``recon.v1.templates``."""
    return _root(None).templates()


def capture(name: str, payload: Any, *, root: Path | None = None) -> Capture:
    """Append a Claude Code ``PostToolUse`` hook payload to the mission's capture log.
    ``recon.v1.capture``: the line as written."""
    return _root(root).capture(name, payload)
