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

from recon.domain.archives import READ_MAX_BYTES, READ_MAX_RECORDS, WATCH_INTERVAL_S

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from datetime import datetime
    from pathlib import Path

    from recon.composition import Recon
    from recon.domain.documents import (
        Capture,
        MissionDocument,
        PlanDocument,
        QueryRows,
        RecordsSince,
        StatusDocument,
        StreamDocument,
        SurveyDocument,
        TemplatesDocument,
    )
    from recon.domain.errors import Problem
    from recon.domain.stream import Stop


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
    name: str,
    *,
    config: Path | None = None,
    replace: bool = False,
    stream: str | None = None,
    windows: int | None = None,
    until: datetime | None = None,
    forever: bool = False,
    root: Path | None = None,
) -> PlanDocument:
    """What ``survey`` would do, without doing it. ``recon.v1.plan``.

    Writes nothing, and fetches and runs nothing, but for the preflight of a collector type
    that declares one, which the document's ``preflight`` names: an ``mcp`` run's server is
    started (or connected to) and asked for its tools, and ``requirements`` says what was
    found. ``config`` is a file to plan instead of the mission's own; ``replace`` allows it to
    differ from the mission's config. Raises ConfigError listing every problem; its
    ``complete`` is false when more may follow their fixes. ``provenance`` says where each
    stream setting came from. With ``stream``, what :func:`stream` would do with
    ``windows``, ``until`` and ``forever``: refused as it would be, and ``provenance`` says
    how long the run is (``stream.windows``, one by default)."""
    return _root(root).plan(
        name, config=config, replace=replace, stream=stream, windows=windows, until=until,
        forever=forever,
    )  # fmt: skip


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


def stream(
    name: str,
    stream: str,
    *,
    windows: int | None = None,
    until: datetime | None = None,
    forever: bool = False,
    config: Path | None = None,
    replace: bool = False,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    stop: Stop | None = None,
    root: Path | None = None,
) -> StreamDocument:
    """Plan, then collect the mission's stream ``stream`` (a collector with ``window:``, or
    of a type whose records do not end, by its name, as ``read_since``, ``watch`` and
    ``query`` take ``stream=``) window after window, each sealed as an archive.
    ``recon.v1.stream``.

    It stops after ``windows`` windows, at ``until`` (a time with its zone), when its source
    ends, or when it is stopped: ``stop.ask()`` (a :class:`StreamStop`, safe from any thread
    or a signal handler) or this thread interrupted (``KeyboardInterrupt``). The window being
    written is then sealed ``stopped`` once what waits is written, a second stop seals it at
    once, and this returns; with none of ``windows``, ``until`` and
    ``forever``, after one window, so it returns unless told otherwise. A later call goes on
    from the last window, its source resumed after the last record kept. ``on_event``
    receives each window as it is sealed. Raises UsageError for a bound it cannot keep,
    NotFoundError for a name that is no stream of the mission, ReconError (``busy``) while
    another writer holds the stream. A window whose source failed is in the document:
    :func:`stream_failure` turns it into the error to act on."""
    return _root(root).stream(
        name, stream, windows=windows, until=until, forever=forever, config=config,
        replace=replace, on_event=on_event, stop=stop,
    )  # fmt: skip


def status(mission: str | None = None, *, root: Path | None = None) -> StatusDocument:
    """Every mission with its latest archive, and the installed plugins. ``recon.v1.status``.

    Given ``mission``, that mission alone, the one entry of ``missions``, with every archive
    it has (id, path, times, state, tables) as its ``history``: the ids ``query`` takes as
    ``archive``. Raises NotFoundError for a mission that does not exist."""
    return _root(root).status(mission)


def query(
    name: str,
    sql: str,
    *,
    archive: str | None = None,
    stream: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    root: Path | None = None,
) -> QueryRows:
    """Rows from the latest archive of ``name``, or the archive named (an id ``status(name)``
    lists). ``recon.v1.query-rows``, naming the archive by its id, ``archive``, and its
    directory, ``path``.

    With ``stream``, rows from that stream's windows, each table the union of its windows':
    every window, the one ``archive`` names, or those meeting ``[since, until)`` (times with
    their zones), which ``window_range`` says. Windows collected under different configs are
    refused (QueryError), with the ranges to narrow to."""
    return _root(root).query(name, sql, archive=archive, stream=stream, since=since,
                             until=until)  # fmt: skip


def read_since(
    name: str,
    cursor: str | None = None,
    *,
    archive: str | None = None,
    stream: str | None = None,
    max_records: int = READ_MAX_RECORDS,
    max_bytes: int = READ_MAX_BYTES,
    root: Path | None = None,
) -> RecordsSince:
    """The records of the latest archive of ``name``, or the archive named, written since
    ``cursor``: every record from the first when there is none. ``recon.v1.records-since``.

    Each read returns whole records only, at most ``max_records`` of them and ``max_bytes`` of
    their lines (1 MiB), at least one, from the tables sealed and the one being written
    alike, ``more`` when a bound cut it, and a ``cursor`` to pass to the next read of the
    same archive (name it with ``archive``: the read's ``archive``). The cursor is opaque.
    ``state`` says whether a survey is still writing the archive; ``exhausted`` names the
    tables read to their end since (sealed, every record read: what was read of them
    stands) and ``retracted`` those that failed, their records void. Reads
    write nothing, so a reader changes no byte of the archive. Raises UsageError for a
    cursor recon did not give for that archive, or a ``max_records`` or ``max_bytes`` below 1.

    With ``stream``, a read of that stream's latest window (or the window ``archive`` names,
    or where ``cursor`` left off): ``archive`` is the window read, and a read that ends a
    sealed window with a later one there returns that one's cursor and ``more``."""
    return _root(root).read_since(name, cursor, archive=archive, stream=stream,
                                  max_records=max_records, max_bytes=max_bytes)  # fmt: skip


def watch(
    name: str,
    cursor: str | None = None,
    *,
    archive: str | None = None,
    stream: str | None = None,
    interval_s: float = WATCH_INTERVAL_S,
    max_records: int = READ_MAX_RECORDS,
    max_bytes: int = READ_MAX_BYTES,
    root: Path | None = None,
) -> Iterator[RecordsSince]:
    """Follow the latest archive of ``name``, or the archive named, from ``cursor``: each
    :func:`read_since` in turn, waiting ``interval_s`` seconds whenever a read has caught up
    with a survey still writing it. Ends after the read that finds the archive no longer
    ``writing`` with nothing left to read, so it returns: at once, after reading it whole,
    for a finished archive. Raises UsageError for an ``interval_s`` not above 0, and as
    :func:`read_since` does.

    With ``stream``, follow that stream from its latest window (or the one ``archive``
    names) into every window after it; ends once nothing extends the stream and its last
    window is read to its end."""
    return _root(root).watch(
        name, cursor, archive=archive, stream=stream, interval_s=interval_s,
        max_records=max_records, max_bytes=max_bytes,
    )  # fmt: skip


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
