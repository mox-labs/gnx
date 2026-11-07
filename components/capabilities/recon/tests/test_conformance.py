"""The collector contract held by behaviour (review C7, migration M5: U3): a client type written
outside recon, importing only the published extension surface, passes the conformance kit
unchanged, and runs through recon given in-process; and the kit itself fails a type that breaks
the contract, so its passes mean something.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

import recon
from recon.testing import CollectorConformance
from tests import client_type

ITEMS = [{"id": i, "name": f"n{i}"} for i in range(5)]


class TestThePagesClientType(CollectorConformance):
    type_name = "acme.v1.collector.pages"

    def register(self, registry: Any) -> None:
        client_type.register(registry)

    def entry(self) -> dict[str, Any]:
        return {"name": "listed", "type": self.type_name, "items": ITEMS}

    def paged_entry(self) -> dict[str, Any] | None:
        return {**self.entry(), "size": 2, "paginate": {"type": "offset", "param": "offset"}}


class TestTheTicksClientType(CollectorConformance):
    type_name = "acme.v1.collector.ticks"

    def register(self, registry: Any) -> None:
        client_type.register(registry)

    def entry(self) -> dict[str, Any]:
        return {"name": "ticks", "type": self.type_name, "start": 3}


def test_the_client_type_imports_nothing_of_recon_but_the_extension_surface() -> None:
    source = Path(client_type.__file__).read_text()
    named = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            named.add(node.module)
        elif isinstance(node, ast.Import):
            named.update(alias.name for alias in node.names)
    assert {name for name in named if name.startswith("recon")} == {"recon.extension"}


def test_recon_crawls_the_client_type_and_lands_every_page(tmp_path: Path) -> None:
    """A third party's Paged type, given in-process: recon drives the crawl (offsets it
    counts itself), positions each record and keeps the page log."""
    session = recon.open(tmp_path, extensions=[client_type.register])
    config = {
        "collectors": [
            {"name": "listed", "type": "acme.v1.collector.pages", "items": ITEMS, "size": 2,
             "paginate": {"type": "offset", "param": "offset"}},
        ]
    }  # fmt: skip
    document = session.observation.survey("m", config=config)
    [table] = document.tables
    assert table.status == "ok" and table.records == 5
    rows = session.collection.query("m", "SELECT id, _position FROM listed ORDER BY id").rows
    assert [row["id"] for row in rows] == [0, 1, 2, 3, 4]
    assert [json.loads(json.dumps(row["_position"])) for row in rows][:3] == [
        {"page": 0, "i": 0}, {"page": 0, "i": 1}, {"page": 1, "i": 0},
    ]  # fmt: skip


def test_recon_streams_the_client_type_and_resumes_after_its_last_tick(tmp_path: Path) -> None:
    session = recon.open(tmp_path, extensions=[client_type.register])
    config = {"collectors": [{"name": "ticks", "type": "acme.v1.collector.ticks",
                              "window": {"records": 3}}]}  # fmt: skip
    first = session.observation.stream("m", "ticks", config=config, windows=1)
    again = session.observation.stream("m", "ticks", config=config, windows=1)
    assert first.windows and again.windows
    rows = session.collection.query("m", "SELECT tick FROM ticks ORDER BY tick", stream="ticks")
    assert [row["tick"] for row in rows.rows] == [0, 1, 2, 3, 4, 5]


# --- the kit fails a type that breaks the contract ------------------------------------------


class _Leaky:
    """A type whose describe reaches its requester, which a plan must never do."""

    effects = frozenset({"network"})
    records = "finite"
    accepts = None

    def __init__(self, *, requester: Any) -> None:
        self._requester = requester

    def describe(self, run: Any) -> dict[str, Any]:
        return {"answer": self._requester.request(run.source, "GET", "https://x.test")}

    def check(self, run: Any) -> list[Any]:
        return []

    def collect(self, run: Any, resume: Any) -> Any:
        yield {"not": "an item"}

    def close(self) -> None:
        return None


def _leaky(registry: Any) -> None:
    registry.register("recon.collector", "acme.v1.collector.leaky", _Leaky, needs={"requester"})


class _BrokenSuite(CollectorConformance):
    type_name = "acme.v1.collector.leaky"

    def register(self, registry: Any) -> None:
        _leaky(registry)

    def entry(self) -> dict[str, Any]:
        return {"name": "leaky", "type": self.type_name}

    def needs(self) -> dict[str, Any]:
        return {"requester": object()}


@pytest.mark.parametrize(
    "check",
    [
        "test_describe_and_check_are_pure_and_say_what_they_promise",
        "test_a_run_gives_items_recon_turns_into_records",
    ],
)
def test_the_kit_fails_a_type_that_breaks_the_contract(check: str) -> None:
    """The check of the check (CRITIC §3.2): a describe that reaches the network, and items
    that are not items, each fail the kit."""
    with pytest.raises((AssertionError, AttributeError)):
        getattr(_BrokenSuite(), check)()
