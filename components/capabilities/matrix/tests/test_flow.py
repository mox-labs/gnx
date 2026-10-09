"""Flow compilation follows slick's wiring kernel and reports every problem at once."""

from __future__ import annotations

from typing import Any

import pytest

from matrix import CompilationError, Flow, Member, Port, compile_flow

TEXT = "acme.v1.text"
NUMBER = "acme.v1.number"


class Fake:
    """A component with declared ports that returns a fixed value per provide port."""

    def __init__(
        self,
        requires: dict[str, Any] | None = None,
        provides: dict[str, Any] | None = None,
        value: Any = "v",
    ) -> None:
        self.requires = requires or {}
        self.provides = provides or {}
        self.value = value

    async def run(self, inputs: Any) -> dict[str, Any]:
        return {name: self.value for name in self.provides}


def problems_of(flow: Flow) -> list[str]:
    with pytest.raises(CompilationError) as e:
        compile_flow(flow)
    assert e.value.kind == "config" and e.value.fix
    return list(e.value.details["problems"])


def test_a_chain_compiles_into_levels() -> None:
    flow = Flow(
        "chain",
        (
            Member("b", Fake({"x": TEXT}, {"y": TEXT}), {"x": "t1", "y": "t2"}),
            Member("a", Fake({}, {"out": TEXT}), {"out": "t1"}),
        ),
    )
    compiled = compile_flow(flow)
    assert compiled.levels == (("a",), ("b",))
    assert compiled.topics == {"t1": TEXT, "t2": TEXT}
    assert compiled.producers["t1"] == (("a", "out"),)


def test_independent_members_share_a_level_in_declaration_order() -> None:
    flow = Flow(
        "wide",
        (
            Member("z", Fake({}, {"o": TEXT}), {"o": "a"}),
            Member("y", Fake({}, {"o": TEXT}), {"o": "b"}),
        ),
    )
    assert compile_flow(flow).levels == (("z", "y"),)


def test_flow_inputs_are_topics_a_caller_supplies() -> None:
    flow = Flow("in", (Member("a", Fake({"x": TEXT}, {}), {"x": "q"}),), inputs={"q": TEXT})
    assert compile_flow(flow).inputs == {"q": TEXT}


def test_phantom_binding_names_the_declared_ports() -> None:  # FW-4
    flow = Flow("f", (Member("a", Fake({}, {"o": TEXT}), {"o": "t", "nope": "t2"}),))
    (problem,) = problems_of(flow)
    assert "members.a.bindings.nope: no such port" in problem and "o" in problem


def test_unbound_required_port() -> None:  # FW-5
    (problem,) = problems_of(Flow("f", (Member("a", Fake({"x": TEXT}, {}), {}),)))
    assert problem == "members.a.requires.x: required port is not bound"


def test_optional_required_port_may_stay_unbound() -> None:
    flow = Flow("f", (Member("a", Fake({"x": Port(TEXT, optional=True)}, {}), {}),))
    assert compile_flow(flow).levels == (("a",),)


def test_one_topic_one_type() -> None:  # FW-6
    flow = Flow(
        "f",
        (
            Member("a", Fake({}, {"o": TEXT}), {"o": "t"}),
            Member("b", Fake({"x": NUMBER}, {}), {"x": "t"}),
        ),
    )
    (problem,) = problems_of(flow)
    assert "topic 't' carries 'acme.v1.text'" in problem and "acme.v1.number" in problem


def test_a_consumed_topic_needs_a_producer_or_an_input() -> None:  # FW-7
    (problem,) = problems_of(Flow("f", (Member("a", Fake({"x": TEXT}, {}), {"x": "t"}),)))
    assert "nothing produces it" in problem and "a.x" in problem


def test_fan_in_needs_a_many_port() -> None:  # SD-09: legal, but the consumer must say so
    makers = (
        Member("p1", Fake({}, {"o": TEXT}), {"o": "t"}),
        Member("p2", Fake({}, {"o": TEXT}), {"o": "t"}),
    )
    (problem,) = problems_of(Flow("f", (*makers, Member("c", Fake({"x": TEXT}, {}), {"x": "t"}))))
    assert "2 producers (p1.o, p2.o)" in problem and "many=True" in problem
    ok = Flow("f", (*makers, Member("c", Fake({"x": Port(TEXT, many=True)}, {}), {"x": "t"})))
    assert compile_flow(ok).levels == (("p1", "p2"), ("c",))


def test_a_flow_input_has_no_producer() -> None:
    flow = Flow("f", (Member("a", Fake({}, {"o": TEXT}), {"o": "q"}),), inputs={"q": TEXT})
    assert any("is a flow input" in p for p in problems_of(flow))


def test_cycles_name_the_members_and_the_placeholder_status() -> None:  # FW-10
    flow = Flow(
        "loop",
        (
            Member("a", Fake({"x": TEXT}, {"o": TEXT}), {"x": "t2", "o": "t1"}),
            Member("b", Fake({"x": TEXT}, {"o": TEXT}), {"x": "t1", "o": "t2"}),
        ),
    )
    with pytest.raises(CompilationError) as e:
        compile_flow(flow)
    message = str(e.value)
    assert "a -> b -> a" in message or "b -> a -> b" in message
    assert "placeholder" in message and "t1" in message


def test_after_orders_without_data_and_joins_the_cycle_check() -> None:
    flow = Flow(
        "ordered",
        (Member("a", Fake(), after=("b",)), Member("b", Fake())),
    )
    assert compile_flow(flow).levels == (("b",), ("a",))
    with pytest.raises(CompilationError, match="cycle"):
        compile_flow(Flow("self", (Member("a", Fake(), after=("a",)),)))
    assert any(
        "no member named 'x'" in p
        for p in problems_of(Flow("f", (Member("a", Fake(), after=("x",)),)))
    )


def test_every_problem_is_reported_at_once() -> None:
    flow = Flow(
        "many-problems",
        (
            Member("a", Fake({"x": TEXT}, {}), {"bad": "t"}),
            Member("a", Fake()),
            Member("b c", Fake()),
        ),
    )
    found = problems_of(flow)
    assert len(found) >= 4  # phantom, unbound, duplicate alias, bad alias


def test_requires_and_provides_names_are_disjoint() -> None:  # SD-05
    assert any(
        "both required and provided" in p
        for p in problems_of(Flow("f", (Member("a", Fake({"x": TEXT}, {"x": TEXT}), {}),)))
    )


def test_port_types_follow_the_grammar() -> None:
    assert any(
        "acme.v1/text" in p
        for p in problems_of(Flow("f", (Member("a", Fake({}, {"o": "acme.v1/text"}), {}),)))
    )


def test_extension_ids_resolve_through_the_resolver() -> None:
    built: list[tuple[str, dict[str, Any], str]] = []

    def resolve(extension: str, config: dict[str, Any], where: str) -> Fake:
        built.append((extension, config, where))
        return Fake({}, {"o": TEXT})

    flow = Flow("f", (Member("a", "acme.v1.maker", {"o": "t"}, config={"n": 1}),))
    compile_flow(flow, resolve=resolve)
    assert built == [("acme.v1.maker", {"n": 1}, "members.a")]


def test_an_extension_id_without_a_resolver_is_a_problem() -> None:
    assert any("no registry" in p for p in problems_of(Flow("f", (Member("a", "acme.v1.x"),))))


def test_a_required_port_fed_only_by_optional_outputs_is_refused() -> None:
    flow = Flow(
        "f",
        (
            Member("p", Fake({}, {"o": Port(TEXT, optional=True)}), {"o": "t"}),
            Member("c", Fake({"x": TEXT}, {}), {"x": "t"}),
        ),
    )
    (problem,) = problems_of(flow)
    assert "fed only by optional outputs" in problem and "members.c.x" in problem
