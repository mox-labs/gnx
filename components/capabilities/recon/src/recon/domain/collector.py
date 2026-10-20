"""Collector — the port in Recon's hexagon, and the extension contract for collector types.

A collector takes a collector entry + optional source and yields records. It is
transport-agnostic: HTTP, a subprocess, a capture log, anything else.

The dispatch loop consumes the iterator and writes to JSONL line-by-line; collectors that
return a list should wrap it in `iter()`. Streaming collectors yield as they produce,
enabling bounded memory for large outputs.

Collector *types* are extensions. Each is a ``CollectorType`` object registered under the
entry-point group ``recon.collectors``; the entry-point name is the config's ``type:``. The
built-ins (``api``, ``cli``, ``web``, ``capture``) register through the same group, so a
third-party type is wired exactly as they are::

    [project.entry-points."recon.collectors"]
    sql = "my_package.recon_sql:collector_type"
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from recon.domain.capture import CaptureLog
    from recon.domain.converters import DocumentConverter
    from recon.domain.errors import Problem
    from recon.domain.http import Requester
    from recon.domain.models import CollectorEntry, SourceEntry

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
    """

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]: ...


@dataclass(frozen=True)
class CollectorContext:
    """What the composition root hands a collector type's factory.

    Shared on purpose: one Requester (and so one rate limiter per source) serves every
    HTTP collector in a run. ``mission_dir`` anchors relative paths in a config.
    ``user_agent`` is what an HTTP collector sends when its source sets none.

    ``env`` is not the process environment. It holds only the variables the mission
    declares for this type: the ``auth.env`` names of the catalog sources its collectors
    run against. Reading any other name raises a config error that says where to declare it.
    """

    requester: Requester
    converter: DocumentConverter
    transforms: Mapping[str, Transform]
    mission_dir: Path
    env: Mapping[str, str]
    user_agent: str


def _no_problems(entry: CollectorEntry) -> list[Problem]:
    return []


def _no_description(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class CollectorType:
    """A registered collector type: how to build it, and what a plan needs to know about it.

    - ``create`` builds the collector from the shared context.
    - ``effects`` declares what a run can touch; ``None`` means unknown.
    - ``requires_source`` makes a missing ``source`` (with an empty catalog) a plan error.
    - ``normalizes`` says the collector applies ``entry.normalize`` itself. Left ``False``,
      recon applies the spec to every record the collector yields, so a type cannot forget
      it. Set it only when the spec needs something the records alone do not carry (the
      capture collector's ``_capture`` scope).
    - ``extracts`` says the collector finds its records at ``entry.extract`` in a body. A
      survey whose table of this type gets one record holding a list of objects warns that
      ``extract:`` may be missing, naming the list's path.
    - ``check`` returns problems in an entry, with paths relative to it (``run``,
      ``endpoint``); the planner prefixes ``collectors.<i>.``. Pure: no I/O.
    - ``describe`` returns what a planned run would do (the URL, the command) for a
      reviewer. Pure: no I/O, no network.
    """

    create: Callable[[CollectorContext], Collector]
    effects: frozenset[Effect] | None
    summary: str = ""
    requires_source: bool = False
    normalizes: bool = False
    extracts: bool = False
    check: Callable[[CollectorEntry], list[Problem]] = field(default=_no_problems)
    describe: Callable[[CollectorEntry, SourceEntry | None], dict[str, Any]] = field(
        default=_no_description
    )


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
    load."""

    collector_types: dict[str, CollectorType]
    transforms: dict[str, Transform]
    broken: list[BrokenPlugin]
