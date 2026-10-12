"""Composition root — discover the installed collector types and transforms, and wire them.

Both are entry-point groups, and recon's own built-ins are registered through them exactly
as a third-party package's would be (see ``[project.entry-points]`` in pyproject.toml):

    recon.collectors   name → a ``CollectorType``    (config ``type: <name>``)
    recon.transforms   name → a callable             (normalize ``path|$<name>``)

A plugin that fails to import, or that registers the wrong kind of object, is reported as
a ``BrokenPlugin`` and left out; every other plugin still loads. When two packages
register the same name, recon's own registration wins and the other is reported.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon.application.plan import BrokenPlugin, fan_out
from recon.application.transforms import apply_normalize
from recon.domain.collector import CollectorContext, CollectorType
from recon.domain.exceptions import CollectionError, ReconError

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Collector, Transform
    from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry

COLLECTORS_GROUP = "recon.collectors"
TRANSFORMS_GROUP = "recon.transforms"


def _discover(group: str) -> list[EntryPoint]:
    return list(entry_points(group=group))


def _load(
    group: str,
    accept: Callable[[Any], bool],
    expected: str,
    discover: Callable[[str], Iterable[EntryPoint]] | None = None,
) -> tuple[dict[str, Any], list[BrokenPlugin]]:
    found = list((discover or _discover)(group))
    # recon's own registrations first, so a third party cannot shadow a built-in.
    found.sort(key=lambda ep: (getattr(ep.dist, "name", None) != "recon", ep.name))
    loaded: dict[str, Any] = {}
    broken: list[BrokenPlugin] = []
    for ep in found:
        if ep.name in loaded:
            error = f"name already registered by {_origin(found, ep.name)}"
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


def _origin(found: list[EntryPoint], name: str) -> str:
    for ep in found:
        if ep.name == name:
            return ep.value
    return "?"


def load_collector_types(
    discover: Callable[[str], Iterable[EntryPoint]] | None = None,
) -> tuple[dict[str, CollectorType], list[BrokenPlugin]]:
    """Installed collector types by config name, and the plugins that failed to load."""
    return _load(
        COLLECTORS_GROUP,
        lambda obj: isinstance(obj, CollectorType),
        "a recon.domain.collector.CollectorType",
        discover,
    )


def load_transforms(
    discover: Callable[[str], Iterable[EntryPoint]] | None = None,
) -> tuple[dict[str, Transform], list[BrokenPlugin]]:
    """Installed normalize transforms by name (no ``$``), and the ones that failed to load."""
    return _load(TRANSFORMS_GROUP, callable, "a callable", discover)


class Registry:
    """Everything installed: collector types, transforms, and what failed to load."""

    def __init__(
        self,
        collector_types: dict[str, CollectorType],
        transforms: dict[str, Transform],
        broken: list[BrokenPlugin],
    ) -> None:
        self.collector_types = collector_types
        self.transforms = transforms
        self.broken = broken

    def as_dict(self) -> dict[str, Any]:
        return {
            "collectors": [
                {
                    "type": name,
                    "summary": ct.summary,
                    "effects": sorted(ct.effects) if ct.effects is not None else "unknown",
                }
                for name, ct in sorted(self.collector_types.items())
            ],
            "transforms": sorted(f"${name}" for name in self.transforms),
            "broken": [
                {"group": b.group, "name": b.name, "value": b.value, "error": b.error}
                for b in self.broken
            ],
        }


def load_registry(
    discover: Callable[[str], Iterable[EntryPoint]] | None = None,
) -> Registry:
    types, broken_types = load_collector_types(discover)
    transforms, broken_transforms = load_transforms(discover)
    return Registry(types, transforms, broken_types + broken_transforms)


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
    from recon.adapters._out.markitdown_converter import markitdown_path

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
    registry: Registry,
    mission_dir: Path,
    config: ReconConfig,
    env: Mapping[str, str] | None = None,
) -> dict[str, Collector]:
    """One collector per installed type, sharing one Requester (one rate limit per source).

    Each type's factory is handed a ``ScopedEnv`` over ``env`` (default: the process
    environment), holding only the variables ``config`` declares for that type's sources.

    The HTTP and conversion adapters are imported here, not at module load, so planning
    and status never import httpx or markitdown.
    """
    from recon.adapters._out.http_requester import HttpxRequester
    from recon.adapters._out.markitdown_converter import MarkitdownConverter
    from recon.application.utilization import RateLimiter

    transforms = _bound_transforms(registry.transforms, config, mission_dir)
    ctx = CollectorContext(
        requester=HttpxRequester(RateLimiter()),
        converter=MarkitdownConverter(),
        transforms=transforms,
        mission_dir=mission_dir,
        env={},
    )
    process_env = env if env is not None else os.environ
    declared = declared_env(config)
    collectors: dict[str, Collector] = {}
    for name, ct in registry.collector_types.items():
        scoped = ScopedEnv(process_env, declared.get(name, ()), name)
        try:
            collector = ct.create(replace(ctx, env=scoped))
        except Exception as exc:  # one plugin's factory must not stop the others
            error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, ReconError) and exc.fix:
                error += f" (fix: {exc.fix})"
            registry.broken.append(BrokenPlugin(COLLECTORS_GROUP, name, "create()", error))
            continue
        collectors[name] = collector if ct.normalizes else _Normalizing(collector, transforms)
    return collectors
