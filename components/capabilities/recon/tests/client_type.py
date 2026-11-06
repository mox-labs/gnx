"""A collector type written as a package outside recon writes one: it imports nothing of recon
but ``recon.extension``, the published extension contract (``test_conformance.py`` holds that).

``acme.v1.collector.pages`` reads a list its entry holds (``items:``), a page of ``size`` at a
time when its entry says ``paginate: {type: offset, param: offset}``; and ``acme.v1.collector.
ticks`` counts from a number for as long as it is read, each tick a record positioned by its
count, so a stream of it resumes after the last tick kept. Neither reaches anything.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from recon.extension import (
    POINTS,
    Body,
    Effect,
    Intent,
    Item,
    Page,
    Position,
    Problem,
    Record,
    RecordsKind,
    Run,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class PagesSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[dict[str, Any]]
    size: int = 2


class PagesCollector:
    """The whole list as one JSON body, or one page of it as recon asks."""

    effects: frozenset[Effect] | None = frozenset()
    records: RecordsKind = "finite"
    accepts: frozenset[str] | None = None

    def describe(self, run: Run) -> Intent:
        settings = run.settings_as(PagesSettings)
        return {"items": len(settings.items), "size": settings.size}

    def check(self, run: Run) -> list[Problem]:
        settings = run.settings_as(PagesSettings)
        if settings.size < 1:
            return [Problem("size", "a page holds one item at least", fix="size: 1 or more")]
        return []

    def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
        settings = run.settings_as(PagesSettings)
        yield Body("application/json", json.dumps(settings.items).encode())

    def page(self, run: Run, token: Any) -> Page:
        settings = run.settings_as(PagesSettings)
        offset = token if isinstance(token, int) else 0
        chunk = settings.items[offset : offset + settings.size]
        return Page(Body("application/json", json.dumps(chunk).encode(),
                         request={"offset": offset}))  # fmt: skip

    def close(self) -> None:
        return None


class TicksSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int = 0


class TicksCollector:
    """A count that does not end: each tick a record, positioned by its count."""

    effects: frozenset[Effect] | None = frozenset()
    records: RecordsKind = "unbounded_recon_paced"
    accepts: frozenset[str] | None = None

    def __init__(self) -> None:
        self._closed = threading.Event()

    def describe(self, run: Run) -> Intent:
        return {"start": run.settings_as(TicksSettings).start}

    def check(self, run: Run) -> list[Problem]:
        return []

    def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
        count = run.settings_as(TicksSettings).start if resume is None else resume.value + 1
        self._closed.clear()
        while not self._closed.is_set():
            yield Record({"tick": count}, position=count)
            count += 1

    def close(self) -> None:
        self._closed.set()


def register(registry: Any) -> None:
    """What the package's ``matrix.extensions`` entry point would be."""
    registry.register(POINTS["collector"], "acme.v1.collector.pages", PagesCollector,
                      config=PagesSettings, summary="a list held in the entry")  # fmt: skip
    registry.register(POINTS["collector"], "acme.v1.collector.ticks", TicksCollector,
                      config=TicksSettings, summary="a count that does not end")  # fmt: skip
