"""Wiring is inferred from the kinds components require and provide, and checked all at once."""

from __future__ import annotations

from typing import Any

import pytest

from matrix import LEDGER, CompilationError, Component, Problem, Wiring, wire

A, B, C = "acme.v1.a", "acme.v1.b", "acme.v1.c"


class Part:
    """A component that declares kinds and does nothing."""

    def __init__(self, requires: Any = (), provides: Any = (), name: str | None = None) -> None:
        self.requires = requires
        self.provides = provides
        if name is not None:
            self.name = name

    async def run(self, inputs: Any, ctx: Any) -> None:
        return None


def problems_of(components: Any, **options: Any) -> list[str]:
    """Each problem ``wire`` reports, as ``path: message``."""
    return [f"{p.path}: {p.message}" for p in records_of(components, **options)]


def records_of(components: Any, **options: Any) -> list[Problem]:
    with pytest.raises(CompilationError) as e:
        wire(components, **options)
    assert e.value.kind == "config" and e.value.fix and e.value.complete
    assert all(p.fix for p in e.value.problems)
    return list(e.value.problems)


def test_a_component_depends_on_the_provider_of_each_kind_it_requires() -> None:
    wiring = wire(
        {
            "last": Part({B, C}, set()),
            "middle": Part({A}, {B, C}),
            "first": Part(set(), {A}),
        }
    )
    assert isinstance(wiring, Wiring)
    assert wiring.providers == {B: "middle", C: "middle", A: "first"}
    assert wiring.depends == {"last": ("middle",), "middle": ("first",), "first": ()}
    assert wiring.order() == ("first", "middle", "last")  # nobody declared an order


def test_a_kind_the_caller_supplies_is_a_run_input_and_needs_no_provider() -> None:
    wiring = wire({"only": Part({A}, {B})}, inputs=[A])
    assert wiring.inputs == {A} and wiring.depends == {"only": ()}
    assert wiring.kinds == (A, B)


def test_components_can_be_given_as_a_sequence_when_they_carry_their_names() -> None:
    wiring = wire([Part(set(), {A}, name="src"), Part({A}, set(), name="sink")])
    assert list(wiring.components) == ["src", "sink"]
    assert problems_of([Part(), Part(name="x"), Part(name="x")]) == [
        "components.0: Part has no name",
        "components.x: name used twice",
    ]


def test_one_class_can_be_used_twice_under_two_names() -> None:
    wiring = wire({"left": Part(set(), {A}), "right": Part(set(), {B})})
    assert wiring.providers == {A: "left", B: "right"}


def test_a_required_kind_nobody_provides_is_refused() -> None:
    (found,) = records_of({"lonely": Part({A}, set())})
    assert (found.path, found.kind, found.got) == ("components.lonely.requires", "unprovided", A)
    assert found.message == "nothing provides 'acme.v1.a' and it is not a run input"


def test_two_providers_of_one_kind_are_refused() -> None:
    (found,) = records_of({"one": Part(set(), {A}), "two": Part(set(), {A})})
    assert (found.path, found.kind) == ("components.two.provides", "name_taken")
    assert found.message.startswith("kind 'acme.v1.a' is provided by one, two; a kind has one")


def test_a_run_input_cannot_also_have_a_provider() -> None:
    (problem,) = problems_of({"one": Part(set(), {A})}, inputs=[A])
    assert "is a run input and is also provided by one" in problem


def test_a_cycle_is_refused_naming_its_components_and_kinds() -> None:
    (found,) = records_of(
        {"x": Part({C}, {A}), "y": Part({A}, {B}), "z": Part({B}, {C}), "free": Part(set(), set())}
    )
    assert found.kind == "cycle"
    assert found.message == "cycle: x -> z -> y -> x (through acme.v1.a, acme.v1.b, acme.v1.c)"


def test_a_component_cannot_require_what_it_provides() -> None:
    (problem,) = problems_of({"loop": Part({A}, {A})})
    assert problem == "components.loop: requires and provides 'acme.v1.a'; it cannot wait on itself"


def test_kinds_are_type_urls_and_the_ledger_is_matrixs_own() -> None:
    found = problems_of({"bad": Part({"Not A Kind"}, {LEDGER})}, inputs=["also/bad"])
    assert len(found) == 3
    assert any(p.startswith("inputs: 'also/bad' is not a kind") for p in found)
    assert any("components.bad.requires: 'Not A Kind' is not a kind" in p for p in found)
    assert any("components.bad.provides" in p and "provenance table" in p for p in found)


def test_a_thing_that_is_not_a_component_is_refused() -> None:
    class Bare:
        requires = "acme.v1.a"  # a string, not a collection of kinds

    found = problems_of({"bare": Bare(), "bad name!": Part()})
    assert (
        "components.bare.requires: a component declares `requires` as a collection of kinds"
        in found
    )
    assert (
        "components.bare.provides: a component declares `provides` as a collection of kinds"
        in found
    )
    assert "components.bare: a component has an async `run(inputs, ctx)`" in found
    assert any(p.startswith("components.bad name!: a name must match") for p in found)


def test_every_problem_is_reported_at_once() -> None:
    found = records_of(
        {
            "one": Part({"acme.v1.missing"}, {A}),
            "two": Part(set(), {A}),
            "x": Part({C}, {B}),
            "y": Part({B}, {C}),
        }
    )
    assert [(p.path, p.kind) for p in found] == [
        ("components.two.provides", "name_taken"),
        ("components.one.requires", "unprovided"),
        ("components.x", "cycle"),
    ]


def test_a_component_is_structural() -> None:
    assert isinstance(Part(), Component)  # no base class, no import of matrix needed
    assert not isinstance(object(), Component)


def test_a_wiring_reports_the_effects_its_components_declare() -> None:
    parts = {"a": Part(set(), {A}), "b": Part({A}, set())}
    assert wire(parts).declared_effects() is None  # nothing declared is unknown, never "none"
    known = wire(parts, effects={"a": frozenset({"network"}), "b": frozenset()})
    assert known.declared_effects() == {"network"}
    assert wire(parts, effects={"a": frozenset(), "b": None}).declared_effects() is None


def test_a_kind_without_a_version_segment_does_not_wire() -> None:
    """Regression: ``acme.shout`` wired, though the skill teaches a kind is
    ``<namespace>.v<N>.<name>``, so an unversioned one-way door went out unnoticed."""
    found = problems_of({"loud": Part({"acme.text"}, {"acme.shout"})}, inputs=["acme.text"])
    assert any("'acme.shout'" in p and "version segment" in p for p in found), found
    assert any("'acme.text'" in p and "version segment" in p for p in found), found
