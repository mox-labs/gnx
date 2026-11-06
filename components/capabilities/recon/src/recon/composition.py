"""The composition root: the one place adapters meet ports.

It names recon's built-in collector types and transforms (:func:`builtins`), which recon
registers on matrix's registry from its ``matrix.extensions`` entry point exactly as a third
party registers its own (``recon._matrix``, the one module that names matrix); and it reads the
installed ones back from there (:func:`load_plugins`). A plugin that fails to load is reported
as a ``BrokenPlugin`` and left out; every other plugin still loads.

:func:`root` binds the rest: missions as files under the project root, archives as
directories of JSONL, DuckDB for queries, the system clock, httpx2 for HTTP, a shell for
commands, markitdown for documents, and telemetry to two sinks: the survey's event log in its
archive, always, and OpenTelemetry's API, which exports only when the application has
installed an SDK. It holds no policy of its own: what each collector type is handed, and what
recon holds every type to, is the application's (``recon.core.application.collectors``).

recon is a library here, so the root never installs OpenTelemetry's SDK: OpenTelemetry's
guidance is that libraries use the API and applications configure the SDK.
:func:`configure_telemetry` is what an application calls to do that from ``OTEL_*``; the
command line is such an application.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from recon import __version__
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.cli_collector import PopenRunner
from recon.adapters._out.command_deliverer import CommandDeliverer
from recon.adapters._out.duckdb_query import DuckdbJoin, DuckdbQuery, TableFiles
from recon.adapters._out.event_channel import ThreadChannel
from recon.adapters._out.markitdown_converter import MarkitdownConverter
from recon.adapters._out.mission_files import MissionFiles, find_project
from recon.adapters._out.mission_layout import JOINS, mission_dir
from recon.adapters._out.otel_telemetry import OtelEvents, export_from_env
from recon.adapters._out.schedule_files import ScheduleFiles
from recon.adapters._out.schedulers import (
    host_platform,
    make_scheduler,
    run_command,
    safe_path,
    scheduler_commands,
    this_recon,
)
from recon.adapters._out.subscription_files import SubscriptionFiles
from recon.adapters._out.system_clock import SystemClock
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.core.application.collection import Collection
from recon.core.application.collectors import (
    CollectorPorts,
    SurveyCollectors,
    build_collectors,
)
from recon.core.application.stream import StreamStop as StreamStop
from recon.core.domain.errors import UsageError
from recon.core.domain.normalize import BUILTIN_TRANSFORMS
from recon.core.domain.telemetry import Telemetry
from recon.observe.application.changes import MEMORY_KEYS
from recon.observe.application.observation import Observation
from recon.observe.domain.scheduling import ScheduleHost

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from datetime import datetime

    from recon._matrix import Register
    from recon.core.application.collection import ChannelFactory
    from recon.core.domain.archives import Store
    from recon.core.domain.clock import Clock
    from recon.core.domain.collector import CollectorType, Plugins, Transform
    from recon.core.domain.converters import DocumentConverter
    from recon.core.domain.deadline import Deadline
    from recon.core.domain.errors import Problem
    from recon.core.domain.http import Requester
    from recon.core.domain.models import ReconConfig
    from recon.core.domain.paginate import PageLog
    from recon.core.domain.ratelimit import RateLimiter
    from recon.core.domain.schedule import Platform
    from recon.core.domain.shell import ShellRunner
    from recon.core.domain.stream import GapLog
    from recon.core.domain.telemetry import Channel, EventSink
    from recon.observe.domain.delivery import Deliverer
    from recon.observe.domain.joins import KeyJoin
    from recon.observe.domain.scheduling import Scheduler

#: Builds a survey's Requester, reporting through the survey's telemetry, keeping to its
#: deadline and drawing from its rate limiter, which every collector of the survey shares.
type RequesterFactory = Callable[[Telemetry, Deadline, RateLimiter], Requester]


@dataclass(frozen=True)
class Recon:
    """recon, opened over one project: the collect core's driving port (``collection``: an
    act planned and run, a stream read into windows, SQL over what was landed, missions made)
    and the observe layer's (``observation``: a mission planned and surveyed over time, its
    streams, what changed, subscriptions and their delivery, schedules, status)."""

    collection: Collection
    observation: Observation


def builtins() -> tuple[dict[str, CollectorType], dict[str, Transform]]:
    """recon's own collector types and transforms, by their short names: what recon registers
    at its points (``recon._matrix.register``)."""
    from recon.adapters._out import (
        api_collector,
        capture_collector,
        cli_collector,
        markitdown_converter,
        mcp_collector,
        sse_collector,
        tail_collector,
        web_collector,
    )

    collectors = {
        "api": api_collector.collector_type,
        "cli": cli_collector.collector_type,
        "web": web_collector.collector_type,
        "capture": capture_collector.collector_type,
        "mcp": mcp_collector.collector_type,
        "tail": tail_collector.collector_type,
        "sse": sse_collector.collector_type,
    }
    to_markdown: Transform = markitdown_converter.markitdown_path
    transforms: dict[str, Transform] = {**BUILTIN_TRANSFORMS, "markitdown": to_markdown}
    return collectors, transforms


def load_plugins(extensions: Iterable[Register] = ()) -> Plugins:
    """Every collector type and transform registered at recon's points on matrix's registry,
    recon's own and every installed extension's, and each of ``extensions`` registers
    in-process; with what failed to load. Raises UsageError for an extension given that takes
    a type URL registered already."""
    from recon import _matrix

    return _matrix.plugins(extensions)


def root(
    env: Mapping[str, str] | None = None,
    *,
    project: Path | None = None,
    clock: Clock | None = None,
    plugins: Plugins | None = None,
    archives: Callable[[Path], Store] | None = None,
    requester: RequesterFactory | None = None,
    shell: ShellRunner | None = None,
    converter: DocumentConverter | None = None,
    channel: ChannelFactory | None = None,
    schedulers: Callable[[Platform | None], Scheduler] | None = None,
    schedule_host: ScheduleHost | None = None,
    local: Callable[[datetime], datetime] | None = None,
    key_join: Callable[[str], KeyJoin] | None = None,
    memory_keys: int = MEMORY_KEYS,
    deliverer: Deliverer | None = None,
) -> Recon:
    """recon wired to this process: missions under ``project`` (default: the project root of
    the working directory), and the environment ``env`` (default: the process environment).
    Installs nothing of OpenTelemetry's: see :func:`configure_telemetry`.

    Each port the rest names is bound to its real adapter unless one is given: the clock,
    the installed plugins, the archives (``archives`` builds them over the project's
    ``.recon`` directory; they are the landing, what is read back, and the files DuckDB
    queries), the survey's Requester (built per survey by
    ``requester`` from the survey's telemetry), the shell, the document converter, and each
    survey's event channel (``channel``, default a :class:`ThreadChannel` with its defaults),
    the operating system's scheduler of each platform (``schedulers``: the user's own
    launchd, systemd or crontab, run by their commands), what a scheduled tick runs
    (``schedule_host``: this installation's ``recon`` command, and the ``PATH`` of ``env``
    kept to absolute directories no other user can write), ``local``, the clock's time as
    the host's local time, which every scheduler keeps its calendar in, and the join of a
    mission's change detection's large comparisons (``key_join``, by the mission's name;
    default DuckDB's, spilling under the mission), each of more than ``memory_keys`` keys
    (200,000 by default; fewer is joined in memory). What each of a
    mission's subscriptions acked is kept beside the mission, in its files, and each delivery
    is given to its ``command:`` (``deliverer``, default a :class:`CommandDeliverer` handed
    the few variables of ``env`` a desktop notifier needs, and no other, its ``PATH`` the one a
    scheduled tick runs with).
    A test's root is this one with fakes given for the ports it stands in for."""
    clock = clock or SystemClock()
    process_env = os.environ if env is None else env
    plugins = plugins if plugins is not None else load_plugins()
    found, found_by = (project.resolve(), "argument") if project else find_project(Path.cwd())
    missions = MissionFiles(found, clock)
    home = missions.root
    store = archives(home) if archives is not None else ArchiveFiles(home, clock)
    if not isinstance(store, TableFiles):
        raise UsageError(
            f"the landing given ({type(store).__name__}) keeps its tables where recon's query "
            "engine cannot read them",
            fix="land into recon's archive directory (no landing), or one built over another "
            "directory: recon.open(landing=...) with recon's own archive store",
        )
    ports = CollectorPorts(
        requester=requester or partial(_http_requester, clock),
        converter=converter or MarkitdownConverter(),
        shell=shell or PopenRunner(),
        user_agent=DEFAULT_USER_AGENT,
        clock=clock,
    )

    def telemetry(
        events: EventSink | None, channel: Channel | None, reads: Clock | None = None, /
    ) -> Telemetry:
        otel = OtelEvents(__version__)
        sinks: list[EventSink] = [otel if channel is None else channel.sink(otel)]
        if events is not None:
            sinks.insert(0, events)
        return Telemetry(sinks, reads or clock)

    def collectors(
        mission: str,
        config: ReconConfig,
        telemetry: Telemetry,
        deadline: Deadline,
        /,
        *,
        gaps: GapLog | None = None,
        pages: PageLog | None = None,
    ) -> SurveyCollectors:
        return build_collectors(
            plugins, ports, process_env, mission_dir(home, mission), config, telemetry,
            deadline, project=missions.project, gaps=gaps, pages=pages,
        )  # fmt: skip

    host = schedule_host or ScheduleHost(
        this_recon(), safe_path(process_env.get("PATH", ""), project=found)
    )
    collection = Collection(
        missions=missions,
        landing=store,
        archives=store,
        query=DuckdbQuery(store),
        clock=clock,
        plugins=plugins,
        build_collectors=collectors,
        telemetry=telemetry,
        env=process_env,
        version=__version__,
        root_found_by=found_by,
        channel=channel or ThreadChannel,
    )
    observation = Observation(
        collection=collection,
        missions=missions,
        archives=store,
        clock=clock,
        schedulers=schedulers or partial(_scheduler, process_env),
        schedule_store=ScheduleFiles(home),
        schedule_host=host,
        local=local or _local,
        key_join=key_join if key_join is not None else partial(_mission_join, home),
        memory_keys=memory_keys,
        subscriptions=SubscriptionFiles(home),
        deliverer=deliverer or CommandDeliverer(process_env, path=host.path),
    )
    return Recon(collection, observation)


def open_session(
    project: Path | None = None,
    *,
    extensions: Iterable[Register] = (),
    landing: Store | None = None,
) -> Recon:
    """recon opened over ``project`` (default: the git root of the working directory, or the
    working directory outside a repository), with the installed collector types and transforms
    and the ones each of ``extensions`` registers in-process, its acts landing in ``landing``
    (default: the project's archive directory). Each call opens a session of its own."""
    plugins = load_plugins(extensions)
    given = None if landing is None else (lambda home: landing)
    return root(project=project, plugins=plugins, archives=given)


#: The session each project's module functions run over, made on first use.
_DEFAULTS: dict[tuple[Path, bool], Recon] = {}
_DEFAULTS_HELD = threading.Lock()


def default(project: Path | None = None) -> Recon:
    """The session the module functions run over for ``project`` (None: the project the working
    directory is in, found again each call), opened once and kept: the plugins and adapters of
    its first use, the environment as it is at each call."""
    key = (project.resolve(), True) if project is not None else (find_project(Path.cwd())[0], False)
    with _DEFAULTS_HELD:
        if key not in _DEFAULTS:
            _DEFAULTS[key] = root(project=project)
        return _DEFAULTS[key]


def _scheduler(env: Mapping[str, str], platform: Platform | None) -> Scheduler:
    """The user's own scheduler of ``platform`` (None: this host's), run by its commands."""
    home = Path(env.get("HOME") or Path.home())
    configured = env.get("XDG_CONFIG_HOME", "")
    config_home = Path(configured) if configured.startswith("/") else home / ".config"
    return make_scheduler(
        platform or host_platform(env), home=home, config_home=config_home, uid=os.getuid(),
        run=run_command, commands=scheduler_commands(),
    )  # fmt: skip


def _mission_join(home: Path, mission: str) -> KeyJoin:
    """DuckDB's join of a large told index, spilling under the mission's own directory."""
    return DuckdbJoin(scratch=mission_dir(home, mission) / JOINS)


def _local(at: datetime) -> datetime:
    """``at`` in the host's local time, which every scheduler keeps its calendar in."""
    return at.astimezone()


def _http_requester(
    clock: Clock, telemetry: Telemetry, deadline: Deadline, rate_limiter: RateLimiter
) -> Requester:
    """The survey's Requester over httpx2, imported only when a survey runs, so planning and
    status never import it."""
    from recon.adapters._out.http_requester import HttpRequester

    return HttpRequester(rate_limiter, clock, telemetry, deadline=deadline)


def configure_telemetry(env: Mapping[str, str] | None = None) -> list[Problem]:
    """For an application: install OpenTelemetry's SDK from the ``OTEL_*`` variables in
    ``env`` (default: the process environment), for each signal they ask for whose provider
    is not installed yet; return warnings for what they ask and cannot get."""
    return export_from_env(os.environ if env is None else env, __version__)
