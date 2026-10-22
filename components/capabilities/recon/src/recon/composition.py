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
directories of JSONL, DuckDB for queries, the system clock, httpx2 for HTTP, markitdown for
documents, and telemetry to two sinks: the survey's event log in its archive, always, and
OpenTelemetry's API, which exports only when the application has installed an SDK.

recon is a library here, so the root never installs OpenTelemetry's SDK: OpenTelemetry's
guidance is that libraries use the API and applications configure the SDK.
:func:`configure_telemetry` is what an application calls to do that from ``OTEL_*``; the
command line is such an application.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon import __version__
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.duckdb_query import DuckdbQuery
from recon.adapters._out.markitdown_converter import MarkitdownConverter, markitdown_path
from recon.adapters._out.mission_files import MissionFiles, project_root
from recon.adapters._out.otel_telemetry import OtelEvents, export_from_env
from recon.adapters._out.system_clock import SystemClock
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.application.plan import fan_out
from recon.application.use_cases import Recon as Recon
from recon.domain.collector import BrokenPlugin, CollectorContext, CollectorType, Plugins
from recon.domain.errors import CollectionError, ReconError
from recon.domain.normalize import apply_normalize
from recon.domain.ratelimit import RateLimiter
from recon.domain.telemetry import Telemetry

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator

    from recon.domain.capture import CaptureLog
    from recon.domain.clock import Clock
    from recon.domain.collector import Collector, Transform
    from recon.domain.errors import Problem
    from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
    from recon.domain.telemetry import EventSink

COLLECTORS_GROUP = "recon.collectors"
TRANSFORMS_GROUP = "recon.transforms"

type Discover = Callable[[str], Iterable[EntryPoint]]


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


class ScopedEnv(Mapping[str, str]):
    """The environment one collector type may read: the variables its sources declare.

    A mission declares a variable by naming it as ``auth.env`` on a catalog source. A
    declared variable that is unset is absent, as in any mapping. Reading a name the mission
    did not declare raises instead: answering "not set" would be indistinguishable from an
    unset credential, and the request would go out without it.
    """

    def __init__(self, env: Mapping[str, str], declared: Collection[str], owner: str) -> None:
        self._env = env
        self._declared = frozenset(declared)
        self._owner = owner

    def __getitem__(self, name: str) -> str:
        if name not in self._declared:
            raise CollectionError(
                f"collector type {self._owner!r} read the environment variable {name!r}, "
                "which this mission does not declare for it",
                kind="config",
                fix=f"name {name} as auth.env on a catalog source that a "
                f"{self._owner} collector uses",
            )
        return self._env[name]

    def __iter__(self) -> Iterator[str]:
        return (name for name in sorted(self._declared) if name in self._env)

    def __len__(self) -> int:
        return sum(1 for _ in self)


def declared_env(config: ReconConfig) -> dict[str, set[str]]:
    """For each collector type the config uses, the ``auth.env`` names of the sources its
    collectors run against."""
    catalog = {s.name: s for s in config.catalog}
    declared: dict[str, set[str]] = {}
    for entry in config.collectors:
        names = declared.setdefault(entry.type, set())
        for source, _output in fan_out(entry, catalog):
            if source is not None and source.auth.env:
                names.add(source.auth.env)
    return declared


def _bound_transforms(
    transforms: Mapping[str, Transform], config: ReconConfig, mission_dir: Path
) -> dict[str, Transform]:
    """The transforms a survey runs with. ``$markitdown`` reads files a record names, so
    it is bound to the directories this mission declares: its own, and each ``local``
    source's."""
    roots = [mission_dir, *(Path(s.url) for s in config.catalog if s.type == "local")]
    return {
        name: partial(fn, roots=roots) if fn is markitdown_path else fn
        for name, fn in transforms.items()
    }


class _Normalizing:
    """Applies ``normalize:`` to the records of a collector whose type does not.

    The plan validates the spec for every type; this is what makes the survey honour it
    for every type, whether or not a plugin's author remembered to.
    """

    def __init__(self, collector: Collector, transforms: Mapping[str, Transform]) -> None:
        self._collector = collector
        self._transforms = transforms

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        records = self._collector.collect(entry, source, captures=captures)
        if not entry.normalize:
            yield from records
            return
        # A consumer that stops early closes this generator; pass that on, so the
        # collector's own cleanup runs now rather than whenever it is collected.
        try:
            for record in records:
                yield apply_normalize(record, entry.normalize, self._transforms)
        finally:
            close = getattr(records, "close", None)
            if close is not None:
                close()


def build_collectors(
    plugins: Plugins,
    mission_dir: Path,
    config: ReconConfig,
    telemetry: Telemetry,
    *,
    clock: Clock,
    env: Mapping[str, str],
) -> tuple[dict[str, Collector], list[BrokenPlugin]]:
    """One collector per installed type, sharing one Requester (one rate limit per source,
    one span per request in ``telemetry``), and the types whose factory raised.

    Each type's factory is handed a ``ScopedEnv`` over ``env``, holding only the variables
    ``config`` declares for that type's sources. httpx2 is imported here, not at module load,
    so planning and status never import it.
    """
    from recon.adapters._out.http_requester import HttpRequester

    transforms = _bound_transforms(plugins.transforms, config, mission_dir)
    ctx = CollectorContext(
        requester=HttpRequester(RateLimiter(clock), clock, telemetry),
        converter=MarkitdownConverter(),
        transforms=transforms,
        mission_dir=mission_dir,
        env={},
        user_agent=DEFAULT_USER_AGENT,
    )
    declared = declared_env(config)
    collectors: dict[str, Collector] = {}
    failed: list[BrokenPlugin] = []
    for name, ct in plugins.collector_types.items():
        scoped = ScopedEnv(env, declared.get(name, ()), name)
        try:
            collector = ct.create(replace(ctx, env=scoped))
        except Exception as exc:  # one plugin's factory must not stop the others
            error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, ReconError) and exc.fix:
                error += f" (fix: {exc.fix})"
            failed.append(BrokenPlugin(COLLECTORS_GROUP, name, "create()", error))
            continue
        collectors[name] = collector if ct.normalizes else _Normalizing(collector, transforms)
    return collectors, failed


def root(env: Mapping[str, str] | None = None, *, project: Path | None = None) -> Recon:
    """recon wired to this process: missions under ``project`` (default: the project root of
    the working directory), and the environment ``env`` (default: the process environment).
    Installs nothing of OpenTelemetry's: see :func:`configure_telemetry`."""
    clock = SystemClock()
    process_env = os.environ if env is None else env
    plugins = load_plugins()
    archives = ArchiveFiles(clock)

    def telemetry(archive: Path | None) -> Telemetry:
        sinks: list[EventSink] = [OtelEvents(__version__)]
        if archive is not None:
            sinks.insert(0, archives.event_log(archive))
        return Telemetry(sinks, clock)

    return Recon(
        missions=MissionFiles(project.resolve() if project else project_root(Path.cwd()), clock),
        archives=archives,
        query=DuckdbQuery(),
        clock=clock,
        plugins=plugins,
        build_collectors=partial(build_collectors, plugins, clock=clock, env=process_env),
        telemetry=telemetry,
        env=process_env,
        version=__version__,
    )


def configure_telemetry(env: Mapping[str, str] | None = None) -> list[Problem]:
    """For an application: install OpenTelemetry's SDK from the ``OTEL_*`` variables in
    ``env`` (default: the process environment) when they ask to export and nothing is
    installed yet; return warnings for what they ask and cannot get."""
    return export_from_env(os.environ if env is None else env, __version__)
