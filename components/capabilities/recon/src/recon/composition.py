"""The composition root: the one place adapters meet ports.

It discovers the installed collector types and transforms, which are entry-point groups that
recon's own built-ins register through exactly as a third-party package would (see
``[project.entry-points]`` in pyproject.toml)::

    recon.collectors   name → a ``CollectorType``    (config ``type: <name>``)
    recon.transforms   name → a callable             (normalize ``path|$<name>``)

A plugin that fails to import, or that registers the wrong kind of object, is reported as a
``BrokenPlugin`` and left out; every other plugin still loads. When two packages register
the same name, recon's own registration wins and the other is reported.

:func:`root` binds the rest: missions as files under the project root, archives as
directories of JSONL, DuckDB for queries, the system clock, httpx2 for HTTP, a shell for
commands, markitdown for documents, and telemetry to two sinks: the survey's event log in its
archive, always, and OpenTelemetry's API, which exports only when the application has
installed an SDK. It holds no policy of its own: what each collector type is handed, and what
recon holds every type to, is the application's (``recon.application.collectors``).

recon is a library here, so the root never installs OpenTelemetry's SDK: OpenTelemetry's
guidance is that libraries use the API and applications configure the SDK.
:func:`configure_telemetry` is what an application calls to do that from ``OTEL_*``; the
command line is such an application.
"""

from __future__ import annotations

import os
from functools import partial
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon import __version__
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.cli_collector import PopenRunner
from recon.adapters._out.duckdb_query import DuckdbQuery
from recon.adapters._out.event_channel import ThreadChannel
from recon.adapters._out.markitdown_converter import MarkitdownConverter
from recon.adapters._out.mission_files import MissionFiles, find_project
from recon.adapters._out.otel_telemetry import OtelEvents, export_from_env
from recon.adapters._out.system_clock import SystemClock
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.application.collectors import COLLECTORS_GROUP, CollectorPorts, build_collectors
from recon.application.use_cases import Recon as Recon
from recon.domain.collector import BrokenPlugin, CollectorType, Plugins
from recon.domain.telemetry import Telemetry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from recon.application.use_cases import ChannelFactory
    from recon.domain.archives import ArchiveStore
    from recon.domain.clock import Clock
    from recon.domain.collector import Transform
    from recon.domain.converters import DocumentConverter
    from recon.domain.deadline import Deadline
    from recon.domain.errors import Problem
    from recon.domain.http import Requester
    from recon.domain.ratelimit import RateLimiter
    from recon.domain.shell import ShellRunner
    from recon.domain.telemetry import Channel, EventSink

TRANSFORMS_GROUP = "recon.transforms"

type Discover = Callable[[str], Iterable[EntryPoint]]

#: Builds a survey's Requester, reporting through the survey's telemetry, keeping to its
#: deadline and drawing from its rate limiter, which every collector of the survey shares.
type RequesterFactory = Callable[[Telemetry, Deadline, RateLimiter], Requester]


def _discover(group: str) -> list[EntryPoint]:
    return list(entry_points(group=group))


def _load(
    group: str, accept: Callable[[Any], bool], expected: str, discover: Discover
) -> tuple[dict[str, Any], list[BrokenPlugin]]:
    found = list(discover(group))
    # recon's own registrations first, so a third party cannot shadow a built-in.
    found.sort(key=lambda ep: (getattr(ep.dist, "name", None) != "recon", ep.name))
    loaded: dict[str, Any] = {}
    broken: list[BrokenPlugin] = []
    for ep in found:
        if ep.name in loaded:
            origin = next(e.value for e in found if e.name == ep.name)
            error = f"name already registered by {origin}"
            broken.append(BrokenPlugin(group, ep.name, ep.value, error))
            continue
        try:
            obj = ep.load()
        except Exception as exc:  # a plugin's import error must not break the others
            broken.append(BrokenPlugin(group, ep.name, ep.value, f"{type(exc).__name__}: {exc}"))
            continue
        if not accept(obj):
            error = f"expected {expected}, got {type(obj).__name__}"
            broken.append(BrokenPlugin(group, ep.name, ep.value, error))
            continue
        loaded[ep.name] = obj
    return loaded, broken


def load_collector_types(
    discover: Discover = _discover,
) -> tuple[dict[str, CollectorType], list[BrokenPlugin]]:
    """Installed collector types by config name, and the plugins that failed to load."""
    return _load(
        COLLECTORS_GROUP,
        lambda obj: isinstance(obj, CollectorType),
        "a recon.domain.collector.CollectorType",
        discover,
    )


def load_transforms(
    discover: Discover = _discover,
) -> tuple[dict[str, Transform], list[BrokenPlugin]]:
    """Installed normalize transforms by name (no ``$``), and the ones that failed to load."""
    return _load(TRANSFORMS_GROUP, callable, "a callable", discover)


def load_plugins(discover: Discover = _discover) -> Plugins:
    """Every installed collector type and transform, and the plugins that failed to load."""
    types, broken_types = load_collector_types(discover)
    transforms, broken_transforms = load_transforms(discover)
    return Plugins(types, transforms, broken_types + broken_transforms)


def root(
    env: Mapping[str, str] | None = None,
    *,
    project: Path | None = None,
    clock: Clock | None = None,
    plugins: Plugins | None = None,
    archives: ArchiveStore | None = None,
    requester: RequesterFactory | None = None,
    shell: ShellRunner | None = None,
    converter: DocumentConverter | None = None,
    channel: ChannelFactory | None = None,
) -> Recon:
    """recon wired to this process: missions under ``project`` (default: the project root of
    the working directory), and the environment ``env`` (default: the process environment).
    Installs nothing of OpenTelemetry's: see :func:`configure_telemetry`.

    Each port the rest names is bound to its real adapter unless one is given: the clock,
    the installed plugins, the archive store, the survey's Requester (built per survey by
    ``requester`` from the survey's telemetry), the shell, the document converter, and each
    survey's event channel (``channel``, default a :class:`ThreadChannel` with its defaults).
    A test's root is this one with fakes given for the ports it stands in for."""
    clock = clock or SystemClock()
    process_env = os.environ if env is None else env
    plugins = plugins if plugins is not None else load_plugins()
    archives = archives if archives is not None else ArchiveFiles(clock)
    ports = CollectorPorts(
        requester=requester or partial(_http_requester, clock),
        converter=converter or MarkitdownConverter(),
        shell=shell or PopenRunner(),
        user_agent=DEFAULT_USER_AGENT,
        clock=clock,
    )

    def telemetry(
        archive: Path | None, channel: Channel | None, reads: Clock | None = None, /
    ) -> Telemetry:
        otel = OtelEvents(__version__)
        sinks: list[EventSink] = [otel if channel is None else channel.sink(otel)]
        if archive is not None:
            sinks.insert(0, archives.event_log(archive))
        return Telemetry(sinks, reads or clock)

    found, found_by = (project.resolve(), "argument") if project else find_project(Path.cwd())
    missions = MissionFiles(found, clock)
    return Recon(
        missions=missions,
        archives=archives,
        query=DuckdbQuery(archives),
        clock=clock,
        plugins=plugins,
        build_collectors=partial(
            build_collectors, plugins, ports, process_env, project=missions.project
        ),
        telemetry=telemetry,
        env=process_env,
        version=__version__,
        root_found_by=found_by,
        channel=channel or ThreadChannel,
    )


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
