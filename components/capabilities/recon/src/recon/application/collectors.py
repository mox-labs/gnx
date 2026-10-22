"""A survey's collectors: what each collector type is handed, and what recon holds every type
to, whoever wrote it.

* **Only the types the mission uses are built**, one collector each, from the driven ports the
  composition root binds (:class:`CollectorPorts`); a factory that raises is reported as a
  broken plugin and its tables fail as ``config``, while the others build.
* **What a survey opens, it closes.** One collector serves every table of its type, so it
  may hold a session across them (a connection, a stdio server); recon calls its ``close()``
  once when the survey ends, however it ends, and closes the survey's Requester
  (:meth:`SurveyCollectors.close`).
* **A type sees only the environment its mission declares for it**: the ``auth.env`` names of
  the catalog sources its collectors run against, and the variables those sources name
  through their kind's own settings (``CollectorType.source_env``) (:class:`ScopedEnv`).
* **A source's rate limit holds whoever calls it**: one limiter, one bucket per source, serves
  the survey's Requester and every type that reaches a source another way.
* **A dry run asks only the types that declare a preflight**: their collectors' optional
  ``requirements`` are collected as :attr:`SurveyCollectors.requirements`.
* **``normalize:`` is applied to every type's records**: a type that does not apply it itself
  (``CollectorType.normalizes``) has it applied to what it yields (:class:`Normalizing`).
* **A transform that reads files reads only where the mission says.** A transform that takes
  a keyword-only ``roots`` is handed the directories it may read under: the mission's own and
  each ``local`` source's. Nothing names the transform: any that asks is bound.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon.application.plan import fan_out
from recon.domain.collector import BrokenPlugin, CollectorContext
from recon.domain.errors import CollectionError, ReconError
from recon.domain.normalize import apply_normalize, bound
from recon.domain.ratelimit import RateLimiter

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator

    from recon.domain.capture import CaptureLog
    from recon.domain.clock import Clock
    from recon.domain.collector import Collector, CollectorType, Plugins, Requirement, Transform
    from recon.domain.converters import DocumentConverter
    from recon.domain.deadline import Deadline
    from recon.domain.http import Requester
    from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
    from recon.domain.shell import ShellRunner
    from recon.domain.telemetry import Telemetry

COLLECTORS_GROUP = "recon.collectors"


@dataclass(frozen=True)
class CollectorPorts:
    """The driven ports every survey's collectors are built over, bound once by the root.

    ``requester`` builds the survey's one Requester over the survey's rate limiter (one span
    per request in the survey's telemetry, every send and wait inside its deadline);
    ``clock`` is what the rate limiter waits on; ``user_agent`` is what an HTTP collector
    sends when its source sets none."""

    requester: Callable[[Telemetry, Deadline, RateLimiter], Requester]
    converter: DocumentConverter
    shell: ShellRunner
    user_agent: str
    clock: Clock


@dataclass
class SurveyCollectors:
    """One survey's collectors by type name, the types whose factory raised, what to end
    when the survey ends, and, by type name, each collector's ``requirements`` when it has
    one."""

    collectors: dict[str, Collector]
    failed: list[BrokenPlugin]
    _ends: list[tuple[str, Callable[[], object]]] = field(default_factory=list)
    requirements: dict[
        str, Callable[[CollectorEntry, SourceEntry | None], Collection[Requirement]]
    ] = field(default_factory=dict)

    def close(self) -> list[BrokenPlugin]:
        """Close each collector that has a ``close()``, then the survey's Requester, once
        each, all of them whatever one raises; return the ones that raised."""
        ends, self._ends = self._ends, []
        failed = []
        for name, end in ends:
            try:
                end()
            except Exception as exc:  # one plugin's close must not keep the rest open
                error = f"close() raised {type(exc).__name__}: {exc}"
                failed.append(BrokenPlugin(COLLECTORS_GROUP, name, "close()", error))
        return failed


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


def declared_env(
    config: ReconConfig, types: Mapping[str, CollectorType] | None = None
) -> dict[str, set[str]]:
    """For each collector type the config uses, the ``auth.env`` names of the sources its
    collectors run against, and the names each such source reads through its kind's own
    settings, as the installed type that adds the kind says (``CollectorType.source_env``)."""
    catalog = {s.name: s for s in config.catalog}
    adders: dict[str, CollectorType] = {}
    for ct in (types or {}).values():
        for kind in sorted(ct.source_kinds):
            adders.setdefault(kind, ct)
    declared: dict[str, set[str]] = {}
    for entry in config.collectors:
        names = declared.setdefault(entry.type, set())
        for source, _output in fan_out(entry, catalog):
            if source is None:
                continue
            if source.auth.env:
                names.add(source.auth.env)
            adder = adders.get(source.type)
            if adder is not None and adder.source_settings is not None:
                names.update(adder.source_env(source))
    return declared


def confined_transforms(
    transforms: Mapping[str, Transform], config: ReconConfig, mission_dir: Path
) -> dict[str, Transform]:
    """The transforms a survey runs with: each that takes ``roots`` bound to the directories
    this mission declares, its own and each ``local`` source's."""
    roots = [mission_dir, *(Path(s.url) for s in config.catalog if s.type == "local")]
    return bound(transforms, roots=roots)


class Normalizing:
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
    ports: CollectorPorts,
    env: Mapping[str, str],
    mission_dir: Path,
    config: ReconConfig,
    telemetry: Telemetry,
    deadline: Deadline,
) -> SurveyCollectors:
    """One collector for each installed type ``config`` uses, sharing one Requester and the
    survey's ``telemetry``, and the types whose factory raised. Each factory is handed a
    ``ScopedEnv`` over ``env`` holding only the variables ``config`` declares for that type's
    sources."""
    transforms = confined_transforms(plugins.transforms, config, mission_dir)
    rate_limiter = RateLimiter(ports.clock)
    requester = ports.requester(telemetry, deadline, rate_limiter)
    ctx = CollectorContext(
        requester=requester,
        converter=ports.converter,
        shell=ports.shell,
        telemetry=telemetry,
        deadline=deadline,
        rate_limiter=rate_limiter,
        transforms=transforms,
        mission_dir=mission_dir,
        env={},
        user_agent=ports.user_agent,
    )
    declared = declared_env(config, plugins.collector_types)
    built = SurveyCollectors({}, [])
    for name in dict.fromkeys(entry.type for entry in config.collectors):
        ct = plugins.collector_types[name]
        scoped = ScopedEnv(env, declared.get(name, ()), name)
        try:
            collector = ct.create(replace(ctx, env=scoped))
        except Exception as exc:  # one plugin's factory must not stop the others
            error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, ReconError) and exc.fix:
                error += f" (fix: {exc.fix})"
            built.failed.append(BrokenPlugin(COLLECTORS_GROUP, name, "create()", error))
            continue
        close = getattr(collector, "close", None)
        if callable(close):
            built._ends.append((name, close))
        requirements = getattr(collector, "requirements", None)
        if ct.preflight and callable(requirements):
            built.requirements[name] = requirements
        built.collectors[name] = collector if ct.normalizes else Normalizing(collector, transforms)
    close = getattr(requester, "close", None)
    if callable(close):
        built._ends.append(("requester", close))
    return built
