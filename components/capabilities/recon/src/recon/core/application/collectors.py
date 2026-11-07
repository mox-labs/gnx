"""An act's collectors: what each collector type is built with, and what recon holds every type
to, whoever wrote it.

* **Only the types the mission uses are built**, one collector each per act, as
  ``build(**needs)`` with the needs its registration names (``recon.core.domain.collector.
  NEEDS``), from the driven ports the composition root binds (:class:`CollectorPorts`) and the
  mission's own (:class:`MissionPorts`); a build that raises is reported as a broken plugin and
  its tables fail as ``config``, while the others build.
* **What an act opens, it closes.** One collector serves every table of its type, so it may
  hold a session across them (a connection, a stdio server); recon calls its ``close()`` once
  when the act ends, however it ends, and closes the act's Requester
  (:meth:`ActCollectors.close`).
* **A type sees only the environment its mission declares for it**: the ``auth.token:
  env:NAME`` variables of the catalog sources its runs use, and the variables those sources
  name through their kind's own keys (``SourceKind.env``) (:class:`ScopedEnv`).
* **A source's rate limit holds whoever calls it**: one limiter, one bucket per source, serves
  the act's Requester and every type that reaches a source another way.
* **A checked dry run asks only the types that are** ``Preflight``: their ``requirements``
  are collected as :attr:`ActCollectors.requirements`.
* **What every type gives is captured, converted, found and shaped by recon**
  (:mod:`recon.core.application.items`): :meth:`ActCollectors.shaping` is a run's ``normalize:``.
* **A transform that reads files reads only where the mission says.** A transform that takes
  a keyword-only ``roots`` is handed the directories it may read under: the mission's own and
  each ``local`` source's; one that takes ``base`` is handed a ``local`` source's directory as
  where a relative path in a record of a run against it is.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon.core.application.items import Shaping
from recon.core.application.plan import fan_out
from recon.core.domain.collector import BrokenPlugin, Collector, Preflight
from recon.core.domain.converters import Converters, claimed
from recon.core.domain.credentials import env_name
from recon.core.domain.errors import CollectionError, ReconError
from recon.core.domain.normalize import bound
from recon.core.domain.optional import SupportsClose
from recon.core.domain.ratelimit import RateLimiter
from recon.core.domain.telemetry import Telemetry

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator, Sequence

    from recon.core.domain.capture import CaptureLogs
    from recon.core.domain.clock import Clock
    from recon.core.domain.collector import Plugins, Requirement, Run, Transform
    from recon.core.domain.credentials import Credentials
    from recon.core.domain.deadline import Deadline
    from recon.core.domain.http import Requester
    from recon.core.domain.models import ReconConfig
    from recon.core.domain.shell import ShellRunner

#: What a collector type's failure to build or close is reported under: its point.
COLLECTOR_POINT = "recon.collector"


@dataclass(frozen=True)
class CollectorPorts:
    """The driven ports every act's collectors are built over, bound once by the root.

    ``requester`` builds the act's one Requester over the act's rate limiter (one span per
    request in the act's telemetry, every send and wait inside its deadline); ``clock`` is
    what the rate limiter waits on; ``user_agent`` is what an HTTP request sends when its
    source sets none; ``project`` the project root."""

    requester: Callable[[Telemetry, Deadline, RateLimiter], Requester]
    shell: ShellRunner
    user_agent: str
    clock: Clock
    project: Path | None = None


@dataclass(frozen=True)
class MissionPorts:
    """What is the mission's own: its credentials, resolved over the environment a type may
    read and within the act's deadline (a relative ``file:`` being the mission's); its
    capture logs; and the directories its transforms may read under (its own)."""

    credentials: Callable[[Mapping[str, str], Deadline], Credentials]
    capture_logs: CaptureLogs
    roots: Sequence[Path] = ()


@dataclass
class ActCollectors:
    """One act's collectors by type name, the types whose build raised, what to end when the
    act ends, each Preflight collector's ``requirements``, the converters by claim, and the
    transforms the act's runs are shaped with (each that reads files confined to the
    mission's directories)."""

    collectors: dict[str, Collector]
    failed: list[BrokenPlugin]
    converters: Converters = field(default_factory=lambda: Converters({}, {}))
    transforms: Mapping[str, Transform] = field(default_factory=dict)
    _ends: list[tuple[str, Callable[[], object]]] = field(default_factory=list)
    requirements: dict[str, Callable[[Run], Collection[Requirement]]] = field(default_factory=dict)

    def shaping(self, run: Run) -> Shaping:
        """``normalize:`` for ``run``: its entry's spec, the act's transforms, and a ``local``
        source's directory as where a relative path in its records is."""
        source = run.source
        transforms = self.transforms
        if source is not None and source.type == "local":
            transforms = bound(transforms, base=Path(source.url))
        return Shaping(run.entry.normalize, transforms)

    def close(self) -> list[BrokenPlugin]:
        """Close each collector, then the act's Requester, once each, all of them whatever
        one raises; return the ones that raised."""
        ends, self._ends = self._ends, []
        failed = []
        for name, end in ends:
            try:
                end()
            except Exception as exc:  # one plugin's close must not keep the rest open
                error = f"close() raised {type(exc).__name__}: {exc}"
                failed.append(BrokenPlugin(COLLECTOR_POINT, name, "close()", error))
        return failed


class ScopedEnv(Mapping[str, str]):
    """The environment one collector type may read: the variables its sources declare.

    A mission declares a variable by naming it as ``auth.token: env:NAME`` on a catalog
    source, or through the source kind's own keys (an ``mcp`` source's ``env:NAME``). A
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
                fix=f"name it as auth.token: env:{name} on a catalog source that a "
                f"{self._owner} collector uses",
            )
        return self._env[name]

    def __iter__(self) -> Iterator[str]:
        return (name for name in sorted(self._declared) if name in self._env)

    def __len__(self) -> int:
        return sum(1 for _ in self)


def declared_env(config: ReconConfig, plugins: Plugins | None = None) -> dict[str, set[str]]:
    """For each collector type the config uses, the ``auth.token: env:NAME`` variables of the
    sources its runs use, and the names each such source reads through its kind's own keys,
    as the installed kind says (``SourceKind.env``)."""
    catalog = {s.name: s for s in config.catalog}
    kinds = plugins.source_kinds if plugins is not None else {}
    declared: dict[str, set[str]] = {}
    for entry in config.collectors:
        names = declared.setdefault(entry.type, set())
        accepts = plugins.accepts(entry.type) if plugins is not None else None
        for source, _output in fan_out(entry, catalog, accepts):
            if source is None:
                continue
            if (named := env_name(source.auth.token)) is not None:
                names.add(named)
            kind = kinds.get(source.type)
            if kind is not None:
                names.update(kind.kind.env(source))
    return declared


def converters_of(plugins: Plugins) -> Converters:
    """The installed converters, by name and by the media types they claim (recon's own keep
    theirs)."""
    ordered = sorted(plugins.converters.values(), key=lambda c: (c.package != "recon", c.name))
    by_claim, _ = claimed((c.name, c.converter) for c in ordered)
    return Converters({c.name: c.converter for c in ordered}, by_claim)


def build_collectors(
    plugins: Plugins,
    ports: CollectorPorts,
    env: Mapping[str, str],
    mission: MissionPorts,
    config: ReconConfig,
    telemetry: Telemetry,
    deadline: Deadline,
) -> ActCollectors:
    """One collector for each installed type ``config`` uses, sharing one Requester and the
    act's ``telemetry``, and the types whose build raised. Each is handed only the needs its
    registration names: a ``ScopedEnv`` over ``env`` holding only the variables ``config``
    declares for that type's sources, credentials resolved over it, and the act's ports."""
    roots = [*mission.roots, *(Path(s.url) for s in config.catalog if s.type == "local")]
    transforms = bound(plugins.transforms, roots=roots)
    rate_limiter = RateLimiter(ports.clock)
    requester = ports.requester(telemetry, deadline, rate_limiter)
    declared = declared_env(config, plugins)
    built = ActCollectors({}, [], converters_of(plugins), transforms)
    for name in dict.fromkeys(entry.type for entry in config.collectors):
        installed = plugins.collector_types[name]
        scoped = ScopedEnv(env, declared.get(name, ()), name)
        supplied: dict[str, Any] = {
            "requester": requester,
            "shell": ports.shell,
            "clock": ports.clock,
            "telemetry": telemetry,
            "deadline": deadline,
            "rate_limiter": rate_limiter,
            "credentials": mission.credentials(scoped, deadline),
            "env": scoped,
            "user_agent": ports.user_agent,
            "project": ports.project,
            "capture_logs": mission.capture_logs,
        }
        try:
            collector = installed.build(**{need: supplied[need] for need in installed.needs})
            if not isinstance(collector, Collector):
                raise TypeError(f"its build gave a {type(collector).__name__}, not a Collector")
        except Exception as exc:  # one plugin's build must not stop the others
            error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, ReconError) and exc.fix:
                error += f" (fix: {exc.fix})"
            built.failed.append(BrokenPlugin(COLLECTOR_POINT, name, "build()", error))
            continue
        built._ends.append((name, collector.close))
        if isinstance(collector, Preflight):
            built.requirements[name] = collector.requirements
        built.collectors[name] = collector
    if isinstance(requester, SupportsClose):
        built._ends.append(("requester", requester.close))
    return built


class StandIn:
    """A need a type is built with when recon builds it only to read what it declares (a plan,
    ``recon types``): any use of it fails, since a type's build keeps its needs and uses them
    only once it collects."""

    def __init__(self, need: str) -> None:
        self._need = need

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"the {self._need} need was used while recon built the type to read what it "
            f"declares ({name}): a collector type's build keeps its needs, and uses them only "
            "in collect"
        )

    def __getitem__(self, name: str) -> Any:
        raise AttributeError(f"the {self._need} need was read while recon built the type ({name})")

    def __iter__(self) -> Iterator[Any]:
        raise AttributeError(f"the {self._need} need was read while recon built the type")

    def __repr__(self) -> str:
        return f"StandIn({self._need!r})"


def stand_ins(clock: Clock, user_agent: str) -> dict[str, Any]:
    """The needs recon builds every installed type with to read what it declares: the clock,
    telemetry with no sink, an unbounded deadline and a rate limiter of their own, the user
    agent, and a :class:`StandIn` for each need that would reach anything."""
    from recon.core.domain.deadline import Deadline

    return {
        "requester": StandIn("requester"),
        "shell": StandIn("shell"),
        "clock": clock,
        "telemetry": Telemetry([], clock),
        "deadline": Deadline(clock),
        "rate_limiter": RateLimiter(clock),
        "credentials": StandIn("credentials"),
        "env": StandIn("env"),
        "user_agent": user_agent,
        "project": StandIn("project"),
        "capture_logs": StandIn("capture_logs"),
    }
