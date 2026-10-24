"""Collector — the port in Recon's hexagon, and the extension contract for collector types.

A collector takes a collector entry + optional source and yields records. It is
transport-agnostic: HTTP, a subprocess, a capture log, anything else.

The dispatch loop consumes the iterator and writes to JSONL line-by-line; collectors that
return a list should wrap it in `iter()`. Streaming collectors yield as they produce,
enabling bounded memory for large outputs.

Collector *types* are extensions. Each is a ``CollectorType`` object registered under the
entry-point group ``recon.collectors``; the entry-point name is the config's ``type:``. The
built-ins (``api``, ``cli``, ``web``, ``capture``, ``mcp``) register through the same group, so
a third-party type is wired exactly as they are::

    [project.entry-points."recon.collectors"]
    sql = "my_package.recon_sql:collector_type"
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from recon.domain.paginate import NoPages
from recon.domain.stream import NoGaps

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel

    from recon.domain.capture import CaptureLog
    from recon.domain.clock import Clock
    from recon.domain.converters import DocumentConverter
    from recon.domain.deadline import Deadline
    from recon.domain.errors import Problem
    from recon.domain.http import Requester
    from recon.domain.models import CollectorEntry, SourceEntry
    from recon.domain.paginate import PageLog
    from recon.domain.ratelimit import RateLimiter
    from recon.domain.shell import ShellRunner
    from recon.domain.stream import GapLog, PacedBy
    from recon.domain.telemetry import Telemetry

#: What running a collector can touch. Reported by ``survey --dry-run`` for every planned
#: run; a type that declares ``effects=None`` is shown as ``unknown``, never as none.
Effect = Literal["network", "subprocess", "filesystem"]

#: A normalize transform: the resolved value in, the normalized value out. Registered
#: under the entry-point group ``recon.transforms``; the entry-point name is used in a
#: normalize spec as ``path|$name``.
Transform = Callable[[Any], Any]


class Collector(Protocol):
    """Collect data, yield records.

    ``captures`` is the mission's capture log when ``preserve_raw`` is on, else None. A
    collector that fetches bytes records them there before parsing, so the archive can
    prove what the source returned and be re-normalized without fetching again.

    One collector serves every table of its type in a survey, so it may hold a session
    across them (a connection, a stdio server it started once). It may then have a
    ``close()``, an optional member read only when present: recon calls it once when the
    survey ends, however it ends (finished, failed, interrupted).

    A collector whose type declares a ``preflight`` may have a second optional member,
    ``requirements(entry, source) -> Sequence[Requirement]``: what the run needs of this host
    or of what it reaches (a server that starts, a tool it lists, arguments its schema
    accepts), each checked now. A dry run builds the collector to ask it, then closes it.

    **A stream** (an entry with ``window:``, or of a type that declares ``unbounded``) calls
    ``collect`` once and reads its records for as long as the stream runs, on a thread of the
    stream's own, so its iterator may never end. A record whose place in its source can be
    named carries it as ``_position`` (``recon.domain.stream.POSITION_FIELD``): any JSON
    value from which the source can go on (a file's inode and offset, a server-sent event's
    id). A type whose records carry one takes a keyword ``after`` too: a stream resumed after
    a stop calls ``collect(entry, source, captures=None, after=<the last position kept>)``,
    and the collector yields the records after it, so nothing is collected twice. Its
    ``close()`` ends a read in progress, from another thread, so a stream stops promptly. It
    reports a stretch when its source was not read through ``CollectorContext.gaps``, and
    opens no telemetry span (the stream's telemetry is its writer's thread's).
    """

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]: ...


@dataclass(frozen=True)
class Requirement:
    """Something a planned run needs of this host, or of what it reaches, and whether it holds
    there now. ``need`` says what, in words that stay the same whether it holds or not (``the
    server of source 'papers' lists the tool 'search'``); ``why`` says what was found when it
    does not, and ``fix`` how to make it hold."""

    need: str
    holds: bool
    why: str = ""
    fix: str = ""


@dataclass(frozen=True)
class CollectorContext:
    """What a collector type's factory is handed for one survey.

    Shared on purpose: one Requester and one ``rate_limiter`` (one bucket per source) serve
    every collector in a run; a type that reaches a source other than through the requester
    draws a token for each call (``ctx.rate_limiter.acquire(source, ctx.deadline)``), so the
    source's ``rate_limit`` holds whichever collector calls it. ``shell`` runs a command.
    ``telemetry`` is the survey's: a span opened there (``with ctx.telemetry.span(
    "acme.call", {...}):``) nests under the table's ``recon.collect`` span, in the event log
    and in OpenTelemetry alike.
    ``deadline`` is the survey's: what is left of the survey's and the table's bounds in
    time (``remaining()``, ``limit(seconds)``, ``check()``), which a type that waits or runs
    a command keeps to, as the requester does. ``mission_dir`` anchors relative paths in a
    config. ``project_dir`` is the project root, against which a ``local`` source's relative
    url resolves, and where a process the mission starts without naming a directory runs;
    ``None`` in a context recon did not build, for the working directory.
    ``user_agent`` is what an HTTP collector sends when its source sets none. ``clock`` is the
    survey's clock port, which a type that waits between reads (a file followed as it grows)
    or before reconnecting waits on, so a test's clock decides it. ``gaps`` is where a
    stream's collector says when its source was not read (:class:`~recon.domain.stream.Gap`);
    a snapshot survey's keeps none. ``pages`` is where a collector that reads its source page
    by page (an entry's ``paginate:``) says each page it read and why its crawl stopped short
    (:class:`~recon.domain.paginate.PageLog`), for the table being collected; a stream's keeps
    none.

    ``env`` is not the process environment. It holds only the variables the mission
    declares for this type: the ``auth.env`` names of the catalog sources its collectors
    run against, and the variables their kinds read (``CollectorType.source_env``). Reading
    any other name raises a config error that says where to declare it.
    """

    requester: Requester
    converter: DocumentConverter
    shell: ShellRunner
    telemetry: Telemetry
    deadline: Deadline
    rate_limiter: RateLimiter
    transforms: Mapping[str, Transform]
    mission_dir: Path
    env: Mapping[str, str]
    user_agent: str
    project_dir: Path | None = None
    clock: Clock | None = None
    gaps: GapLog = field(default_factory=NoGaps)
    pages: PageLog = field(default_factory=NoPages)


def _no_problems(entry: CollectorEntry) -> list[Problem]:
    return []


def _no_run_problems(entry: CollectorEntry, source: SourceEntry | None) -> list[Problem]:
    return []


def _no_description(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    return {}


def _no_source_problems(source: SourceEntry) -> list[Problem]:
    return []


def _no_source_env(source: SourceEntry) -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class CollectorType:
    """A registered collector type: how to build it, and what a plan needs to know about it.

    - ``create`` builds the collector from the shared context.
    - ``settings`` is the schema (a pydantic model that forbids unknown keys) of the type's
      own keys in an entry, beyond the ones recon reads for every type: an entry's other
      keys (``entry.settings``) are validated against it at plan time, each problem with its
      path. ``None`` means the type takes no setting of its own, so any other key is
      unknown. The type parses ``entry.settings`` with it.
    - ``source_kinds`` are the catalog source kinds (a source's ``type:``) the type adds to
      the ones installed; a source of any other kind is a plan problem.
    - ``accepts_kinds`` are the source kinds a collector of the type runs against: an
      unpinned one fans out over the catalog's sources of these kinds alone, and the plan
      names the rest as skipped; a collector pinned to a source of another kind is a plan
      problem. ``None`` takes a source of any kind (``capture``, which only filters by the
      source's name, and a type that does not say).
    - ``source_settings`` is the schema (a pydantic model that forbids unknown keys) of the
      keys a source of one of ``source_kinds`` takes beyond the ones every source has: a
      source's other keys (``source.settings``) are validated against it at plan time.
      ``None`` means its kinds take no key of their own, and such a source needs a ``url``.
      With a schema, ``check_source`` says what the kind needs of the shared keys.
    - ``check_source`` returns problems in a source of one of ``source_kinds`` whose
      settings ``source_settings`` accepts, with paths relative to it (``command``,
      ``url``); the planner prefixes ``catalog.<i>.``. Pure: no I/O.
    - ``source_env`` names the environment variables such a source reads beyond
      ``auth.env``, each with where the source names it (``{"GITHUB_TOKEN": "token"}``): the
      mission declares them for every type whose collectors run against the source, and the
      plan warns when one is unset. Pure.
    - ``effects`` declares what a run can touch; ``None`` means unknown.
    - ``requires_source`` makes a missing ``source`` (with an empty catalog) a plan error.
    - ``normalizes`` says the collector applies ``entry.normalize`` itself. Left ``False``,
      recon applies the spec to every record the collector yields, so a type cannot forget
      it. Set it only when the spec needs something the records alone do not carry (the
      capture collector's ``_capture`` scope).
    - ``extracts`` says the collector finds its records at ``entry.extract`` in a body, and
      only such a type takes ``extract:``. A survey whose table of this type gets one record
      holding a list of objects warns that ``extract:`` may be missing, naming the list's
      path.
    - ``check`` returns problems in an entry, with paths relative to it (``run``,
      ``endpoint``); the planner prefixes ``collectors.<i>.``. It is called only for an
      entry whose settings ``settings`` accepts. Pure: no I/O.
    - ``check_run`` returns problems in one run of an entry against the source it runs
      against (``None`` for a run with no source), for what ``check`` cannot see without
      the catalog (a ``cli`` run's ``{url}`` with no source to fill it); paths are relative
      to the entry, as ``check``'s are. Pure: no I/O.
    - ``describe`` returns what a planned run would do (the URL, the command) for a
      reviewer. Pure: no I/O, no network.
    - ``preflight`` says, in words, what a dry run does to check a run of the type (``starts
      each stdio server a run names``). Non-empty, a dry run builds the type's collector, asks
      its ``requirements(entry, source)`` for each run, says this in the plan, and closes it.
      Empty, a dry run never builds the type.
    - ``unbounded`` says the type's records do not end (a file followed as it grows, a server
      that sends events): an entry of it is a stream, collected into windows, and one with no
      ``window:`` gets the default (``window: {seconds: 300}``), which the plan says it
      filled in. An entry of any type with ``window:`` is a stream too.
    - ``paced_by`` says who sets the pace the type's records arrive at: ``recon``, which reads
      the next when it is ready (a file, a command, a polled API), or ``source``, which sends
      whether recon is ready or not (server-sent events). It decides a stream's default
      overflow (``recon.domain.stream.default_overflow``): ``block`` or ``drop_newest``.
    - ``paginates`` says the collector reads ``entry.paginate`` (:mod:`recon.domain.paginate`),
      and only such a type takes ``paginate:``: it follows the stopping rules of
      :class:`~recon.domain.paginate.Crawl`, gives each record ``_position`` ``{page, i}``, and
      says each page and a crawl cut short through ``CollectorContext.pages``. The plan reports
      the cap and the least time the crawl takes at the source's rate limit.
    """

    create: Callable[[CollectorContext], Collector]
    effects: frozenset[Effect] | None
    summary: str = ""
    settings: type[BaseModel] | None = None
    source_kinds: frozenset[str] = frozenset()
    accepts_kinds: frozenset[str] | None = None
    source_settings: type[BaseModel] | None = None
    requires_source: bool = False
    normalizes: bool = False
    extracts: bool = False
    check: Callable[[CollectorEntry], list[Problem]] = field(default=_no_problems)
    check_run: Callable[[CollectorEntry, SourceEntry | None], list[Problem]] = field(
        default=_no_run_problems
    )
    describe: Callable[[CollectorEntry, SourceEntry | None], dict[str, Any]] = field(
        default=_no_description
    )
    check_source: Callable[[SourceEntry], list[Problem]] = field(default=_no_source_problems)
    source_env: Callable[[SourceEntry], dict[str, str]] = field(default=_no_source_env)
    preflight: str = ""
    unbounded: bool = False
    paced_by: PacedBy = "recon"
    paginates: bool = False


@dataclass(frozen=True)
class BrokenPlugin:
    """An entry point that could not be loaded. Reported, never fatal to other plugins."""

    group: str
    name: str
    value: str
    error: str

    def describe(self) -> str:
        return f"{self.group}:{self.name} ({self.value}) failed to load: {self.error}"


@dataclass(frozen=True)
class Plugins:
    """Everything installed: collector types and transforms by name, and what failed to
    load. ``collector_packages`` names the package that registered each collector type, as
    far as it is known (``recon`` for its own), so what is said of a type can say whose it
    is."""

    collector_types: dict[str, CollectorType]
    transforms: dict[str, Transform]
    broken: list[BrokenPlugin]
    collector_packages: dict[str, str] = field(default_factory=dict)
