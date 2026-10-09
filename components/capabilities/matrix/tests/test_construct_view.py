"""Declared access, enforced: a component sees only the kinds in its ``requires``."""

from __future__ import annotations

import pytest

from matrix import Artifact, Construct, ConstructView, ContractError, Orchestrator, TypedStruct


class _Reader:
    def __init__(self, name: str, requires: frozenset[str], reads: str, provides: str) -> None:
        self.name, self.requires, self._reads, self.provides = name, requires, reads, provides

    async def run(self, construct):  # type: ignore[no-untyped-def]
        return TypedStruct(self.provides, construct[self._reads])


class _Source:
    requires: frozenset[str] = frozenset()

    def __init__(self, name: str, provides: str, value: object) -> None:
        self.name, self.provides, self._value = name, provides, value

    async def run(self, construct):  # type: ignore[no-untyped-def]
        return TypedStruct(self.provides, self._value)


async def test_declared_read_is_allowed() -> None:
    construct = await Orchestrator(
        [_Source("a", "k.a", 1), _Reader("r", frozenset({"k.a"}), "k.a", "k.r")]
    ).run()
    assert construct["k.r"] == 1


async def test_undeclared_read_is_a_contract_error() -> None:
    orchestrator = Orchestrator(
        [
            _Source("a", "k.a", 1),
            _Source("b", "k.b", 2),
            _Reader("r", frozenset({"k.a"}), "k.b", "k.r"),  # declares k.a, reads k.b
        ]
    )
    with pytest.raises(ContractError, match=r"'r' read 'k\.b'.*declared: k\.a"):
        await orchestrator.run()


def test_view_restricts_every_accessor() -> None:
    construct = Construct()
    for kind in ("k.a", "k.b"):
        construct.append(Artifact.create(type_url=kind, producer="p", data=kind))
    view = ConstructView(construct, reader="r", allowed={"k.a"})
    assert view["k.a"] == "k.a" and view.last("k.a").data == "k.a" and "k.a" in view
    assert view.kinds() == frozenset({"k.a"})
    for access in (lambda: view["k.b"], lambda: view.query("k.b"), lambda: "k.b" in view):
        with pytest.raises(ContractError):
            access()
