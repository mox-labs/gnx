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
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING, Any

from recon.application.plan import BrokenPlugin
from recon.domain.collector import CollectorContext, CollectorType

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from recon.domain.collector import Collector, Transform

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


def build_collectors(
    registry: Registry,
    mission_dir: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Collector]:
    """One collector per installed type, sharing one Requester (one rate limit per source).

    The HTTP and conversion adapters are imported here, not at module load, so planning
    and status never import httpx or markitdown.
    """
    from recon.adapters._out.http_requester import HttpxRequester
    from recon.adapters._out.markitdown_converter import MarkitdownConverter
    from recon.application.utilization import RateLimiter

    ctx = CollectorContext(
        requester=HttpxRequester(RateLimiter()),
        converter=MarkitdownConverter(),
        transforms=registry.transforms,
        mission_dir=mission_dir,
        env=env if env is not None else os.environ,
    )
    collectors: dict[str, Collector] = {}
    for name, ct in registry.collector_types.items():
        try:
            collectors[name] = ct.create(ctx)
        except Exception as exc:  # one plugin's factory must not stop the others
            registry.broken.append(
                BrokenPlugin(COLLECTORS_GROUP, name, "create()", f"{type(exc).__name__}: {exc}")
            )
    return collectors
