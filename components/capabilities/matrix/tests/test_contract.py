"""The component-author contract, pinned: what a component and its registration are written
against, and the one surface of matrix that changes only by addition (README, "What is
promised"; CONTRACT.md).

A failure here is not a test to update to the code. It says a promised name changed: make the
change an addition, or make it a break the changelog says, with every consumer in this
repository migrated in the same change, and then update the snapshot.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from matrix import (
    NEEDS,
    Attribution,
    Component,
    Context,
    ContractError,
    Inputs,
    MatrixError,
    Registry,
    check_type_url,
    wire,
)
from matrix.composition.container import ENTRY_POINT_GROUP


def _members(protocol: type) -> dict[str, str]:
    """Each member of ``protocol``, as its signature reads, a property as its type."""
    found: dict[str, str] = {}
    for name in sorted(protocol.__protocol_attrs__):  # type: ignore[attr-defined]
        member = inspect.getattr_static(protocol, name)
        if isinstance(member, property):
            found[name] = f"property -> {inspect.signature(member.fget).return_annotation}"  # type: ignore[arg-type]
        else:
            found[name] = str(inspect.signature(member))
    return found


def test_the_component_protocol_is_as_promised() -> None:
    assert _members(Component) == {
        "provides": "property -> Collection[str]",
        "requires": "property -> Collection[str]",
        "run": "(self, inputs: 'Inputs', ctx: 'Context') -> 'Mapping[str, Any] | None'",
    }


def test_the_context_protocol_is_as_promised() -> None:
    assert _members(Context) == {
        "attributed": "(self) -> 'AbstractAsyncContextManager[Attribution]'",
        "component": "property -> str",
        "deadline": "property -> float | None",
        "defer": "(self, release: 'Release') -> 'None'",
        "emit": "(self, name: 'str', /, **fields: 'Any') -> 'None'",
        "measure": "(self, name: 'str', value: 'float', /, *, unit: 'str' = '', "
        "kind: 'str' = 'gauge', **attrs: 'Any') -> 'None'",
        "provide": "(self, kind: 'str', row: 'Any', /, *, "
        "sessions: 'Iterable[int] | None' = None) -> 'None'",
        "run_id": "property -> str",
        "span": "(self, name: 'str', /, **fields: 'Any') -> 'AbstractAsyncContextManager[None]'",
        "stopping": "property -> asyncio.Event",
    }


def test_inputs_and_attribution_are_as_promised() -> None:
    assert str(inspect.signature(Inputs.one)) == "(self, kind: 'str') -> 'Any'"
    assert str(inspect.signature(Inputs.__init__)) == (
        "(self, rows: 'Mapping[str, Iterable[Any]]') -> 'None'"
    )
    inputs = Inputs({"acme.v1.a": ["x"]})
    assert dict(inputs) == {"acme.v1.a": ("x",)}  # a read-only mapping of kind to rows
    with pytest.raises(LookupError):
        Inputs({"acme.v1.a": []}).one("acme.v1.a")
    assert str(inspect.signature(Attribution.sessions)) == "(self) -> 'tuple[int, ...]'"


def test_registration_is_as_promised() -> None:
    assert str(inspect.signature(Registry.register)) == (
        "(self, point: 'str', type_url: 'str', build: 'Callable[..., Any]', *, "
        "config: 'type[BaseModel] | None' = None, "
        "needs: 'frozenset[str] | set[str] | tuple[str, ...]' = (), summary: 'str' = '', "
        "stability: 'Stability' = 'beta', stand_in: 'bool' = False, "
        "drives: 'tuple[str, ...] | list[str]' = (), "
        "capabilities: 'Callable[[Any], AgentCapabilities] | None' = None) -> 'Registry'"
    )
    assert str(inspect.signature(Registry.register_payload)) == (
        "(self, type_url: 'str', model: 'type[BaseModel]', summary: 'str' = '') -> 'Registry'"
    )
    assert ENTRY_POINT_GROUP == "matrix.extensions"


def test_the_stable_needs_are_as_promised() -> None:
    stable = {name: need.type for name, need in NEEDS.items() if need.stability == "stable"}
    assert stable == {
        "agents": "Mapping[str, Agent]",
        "model_runtimes": "Mapping[str, ModelRuntime]",
        "schemas": "Mapping[str, type[BaseModel]]",
        "cwd": "str | None",
        "name": "str",
    }


@pytest.mark.parametrize("kind", ["acme.v1.review", "acme.tools.v2beta1.score", "a.v1.b-c"])
def test_the_kind_grammar_admits(kind: str) -> None:
    assert check_type_url(kind) == kind


@pytest.mark.parametrize(
    "kind", ["acme.review", "Acme.v1.review", "acme.v1/review", "acme.v1.skill"]
)
def test_the_kind_grammar_refuses(kind: str) -> None:
    with pytest.raises(MatrixError):
        wire({"c": _Provides(kind)})


class _Provides:
    requires: frozenset[str] = frozenset()

    def __init__(self, kind: str) -> None:
        self.provides = frozenset({kind})

    async def run(self, inputs: Inputs, ctx: Context) -> Any:
        return None


def test_two_providers_of_one_kind_are_refused() -> None:
    with pytest.raises(MatrixError, match="one provider"):
        wire({"a": _Provides("acme.v1.x"), "b": _Provides("acme.v1.x")})


def test_breaking_the_contract_is_a_contract_error() -> None:
    assert issubclass(ContractError, MatrixError)
    assert ContractError.kind == "contract"
