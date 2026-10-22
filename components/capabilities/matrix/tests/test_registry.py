"""The registry: typed config, declared needs, one discovery path, quarantine, no silent wins."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from matrix import ConfigError, NotFoundError, Registry, default_registry, load_extensions


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size: int


def build(config: Settings, **needs: Any) -> tuple[Settings, dict[str, Any]]:
    return config, needs


def test_config_is_validated_and_needs_are_handed_over() -> None:
    registry = Registry().register(
        "component", "acme.v1.thing", build, config=Settings, needs={"agents"}
    )
    config, needs = registry.create(
        "component", "acme.v1.thing", {"size": 3}, needs={"agents": {}, "cwd": "/x"}
    )
    assert config.size == 3
    assert needs == {"agents": {}}  # only what it declared


def test_bad_config_names_every_key_path() -> None:
    registry = Registry().register("component", "acme.v1.thing", build, config=Settings)
    with pytest.raises(ConfigError) as e:
        registry.create("component", "acme.v1.thing", {"sise": 3}, where="components.t")
    found = {(p.path, p.kind) for p in e.value.problems}
    assert found == {("components.t.size", "missing_key"), ("components.t.sise", "unknown_key")}
    assert "matrix describe acme.v1.thing" in (e.value.fix or "")


def test_a_missing_need_is_a_config_error() -> None:
    registry = Registry().register(
        "component", "acme.v1.thing", build, config=Settings, needs={"agents"}
    )
    with pytest.raises(ConfigError, match=r"needs \['agents'\]"):
        registry.create("component", "acme.v1.thing", {"size": 1})


def test_untyped_entries_take_no_config() -> None:
    registry = Registry().register("observer", "acme.v1.obs", lambda: "built")
    assert registry.create("observer", "acme.v1.obs") == "built"
    with pytest.raises(ConfigError, match="takes no config"):
        registry.create("observer", "acme.v1.obs", {"x": 1})


def test_unknown_point_and_malformed_ids_are_refused() -> None:
    with pytest.raises(ConfigError, match="no extension point 'sensor'"):
        Registry().register("sensor", "acme.v1.s", build)
    assert "sensor" in Registry().add_point("sensor").points
    with pytest.raises(ConfigError, match="not a type URL"):
        Registry().register("component", "acme.v1/thing", build)


def test_a_lookup_miss_lists_what_is_registered() -> None:
    registry = Registry().register("component", "acme.v1.a", build)
    with pytest.raises(NotFoundError, match="Registered: acme.v1.a") as e:
        registry.entry("component", "acme.v1.b")
    assert isinstance(e.value, KeyError) and e.value.kind == "not_found"


def _entry_point(name: str, register: Any, dist: str = "pkg") -> Any:
    class _Dist:
        pass

    d = _Dist()
    d.name = dist  # type: ignore[attr-defined]

    class _EP:
        value = f"{dist}:register"

        def load(self) -> Any:
            if isinstance(register, Exception):
                raise register
            return register

    ep = _EP()
    ep.name = name  # type: ignore[attr-defined]
    ep.dist = d  # type: ignore[attr-defined]
    return ep


def test_a_broken_extension_is_quarantined_and_the_rest_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _entry_point("good", lambda r: r.register("component", "good.v1.x", build), "good-pkg")
    bad = _entry_point("bad", ImportError("no module named nope"), "bad-pkg")
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: [bad, good])
    registry = load_extensions(Registry())
    assert ("component", "good.v1.x") in registry
    assert registry.entry("component", "good.v1.x").origin == "good-pkg"
    (failure,) = registry.failures
    assert (failure.extension, failure.origin) == ("bad", "bad-pkg")
    assert "no module named nope" in failure.error


def test_a_collision_names_both_origins_whatever_the_install_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def claims(registry: Registry) -> None:
        registry.register("component", "shared.v1.x", build)

    first = _entry_point("a-first", claims, "pkg-a")
    second = _entry_point("b-second", claims, "pkg-b")
    for order in ([first, second], [second, first]):
        monkeypatch.setattr("matrix.composition.container.entry_points", lambda group, o=order: o)
        registry = load_extensions(Registry())
        assert registry.entry("component", "shared.v1.x").origin == "pkg-a"  # name order
        (failure,) = registry.failures
        assert "pkg-a" in failure.error and "pkg-b" in failure.error


def test_the_built_ins_arrive_through_the_entry_point() -> None:
    registry = default_registry()
    assert registry.entry("agent-runtime", "matrix.v1.agent-runtime.mock").origin == "matrix"
    assert not registry.failures


def test_entries_describe_themselves_for_agents() -> None:
    entry = default_registry(discover=False).entry(
        "agent-runtime", "matrix.v1.agent-runtime.claude-sdk"
    )
    described = entry.describe()
    assert described["needs"] == ["cwd"]
    assert "permission_mode" in described["config"]["properties"]


def test_an_entry_declares_no_effects() -> None:
    """Changed requirement (Q6): effects were labels nothing checked, and are removed. What
    an entry is handed is its ``needs``; what it needs of the host its owner checks."""
    entry = Registry().register("component", "acme.v1.x", build).entry("component", "acme.v1.x")
    assert "effects" not in entry.describe()
    with pytest.raises(TypeError):
        Registry().register("component", "acme.v1.y", build, effects=set())  # type: ignore[call-arg]


def test_an_extension_at_a_point_declared_later_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sensor plugin named 'acme' loads before 'ix' declares the sensor point."""

    def sensor_plugin(registry: Registry) -> None:
        registry.register("sensor", "acme.v1.sensor.x", build)

    def owner(registry: Registry) -> None:
        registry.add_point("sensor")

    eps = [_entry_point("acme", sensor_plugin, "acme"), _entry_point("ix", owner, "ix")]
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: eps)
    registry = load_extensions(Registry())
    assert ("sensor", "acme.v1.sensor.x") in registry
    assert registry.failures == []


def test_a_point_nobody_declares_is_still_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def orphan(registry: Registry) -> None:
        registry.register("sensor", "acme.v1.sensor.x", build)

    monkeypatch.setattr(
        "matrix.composition.container.entry_points", lambda group: [_entry_point("acme", orphan)]
    )
    (failure,) = load_extensions(Registry()).failures
    assert "no extension point 'sensor'" in failure.error


def test_registration_is_all_or_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def half(registry: Registry) -> None:
        registry.register("component", "acme.v1.first", build)
        raise RuntimeError("broke half way")

    monkeypatch.setattr(
        "matrix.composition.container.entry_points", lambda group: [_entry_point("acme", half)]
    )
    registry = load_extensions(Registry())
    assert ("component", "acme.v1.first") not in registry
    assert "broke half way" in registry.failures[0].error


def test_an_extension_cannot_shadow_a_namespace_it_does_not_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'aaa' sorts first and claims matrix's mock runtime; matrix keeps its name anyway."""
    from matrix.composition.builtins import register as matrix_register

    def impostor(registry: Registry) -> None:
        registry.register("agent-runtime", "aaa.v1.agent-runtime.fine", lambda: "fine")
        registry.register("agent-runtime", "matrix.v1.agent-runtime.mock", lambda: "hijacked")

    eps = [_entry_point("aaa", impostor, "aaa"), _entry_point("matrix", matrix_register, "matrix")]
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: eps)
    registry = load_extensions(Registry())
    assert registry.entry("agent-runtime", "matrix.v1.agent-runtime.mock").origin == "matrix"
    assert (
        "agent-runtime",
        "aaa.v1.agent-runtime.fine",
    ) not in registry  # the impostor is dropped whole
    assert any(f.extension == "aaa" and "namespace" in f.error for f in registry.failures)


def test_a_quarantined_impostor_is_named_by_its_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from matrix.composition.builtins import register as matrix_register

    def impostor(registry: Registry) -> None:
        registry.register("agent-runtime", "matrix.v1.agent-runtime.mock", lambda: "hijacked")

    eps = [
        _entry_point("a-mock-plugin", impostor, "aaa-dist"),
        _entry_point("matrix", matrix_register, "matrix"),
    ]
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: eps)
    (failure,) = load_extensions(Registry()).failures
    assert (failure.extension, failure.origin) == ("a-mock-plugin", "aaa-dist")


def test_the_built_ins_alone_still_belong_to_matrix() -> None:
    registry = default_registry(discover=False)
    assert {entry.origin for entry in registry.entries()} == {"matrix"}


def test_a_payload_schemas_summary_is_the_first_line_of_its_docstring() -> None:
    class Review(BaseModel):
        """A code review.

        Every finding has a file and a line, and the verdict is one of three words.
        """

    registry = Registry().register_payload("acme.v1.review", Review)
    assert registry.entry("payload-type", "acme.v1.review").summary == "A code review."


def test_a_type_url_without_a_version_segment_is_refused_at_registration() -> None:
    """Regression: ``Registry().register('component', 'acme.scorer', ...)`` succeeded, though
    what is registered is a name others depend on and must be versioned."""
    with pytest.raises(ConfigError) as raised:
        Registry().register("component", "acme.scorer", lambda: None)
    assert "version segment" in raised.value.message
    assert raised.value.details["value"] == "acme.scorer"


# --- a runtime is held to its port ---------------------------------------------------------


class _NoRun:
    """A third party's agent runtime that forgot ``run``."""

    def check(self, definition: object) -> None: ...


class _SyncCall:
    """A third party's model runtime whose ``call`` is not awaitable."""

    def check(self, model: str | None) -> None: ...

    def call(self, request: object) -> object: ...


@pytest.mark.parametrize(
    ("point", "cls", "said"),
    [("agent-runtime", _NoRun, "run"), ("model-runtime", _SyncCall, "call")],
)
def test_a_runtime_class_that_breaks_its_port_is_refused_when_registered(
    point: str, cls: type, said: str
) -> None:
    """Regression (mx-tests 3): one with ``check`` but no ``run`` composed cleanly and failed
    only when an agent ran on it."""
    with pytest.raises(ConfigError, match=said) as e:
        Registry().register(point, f"acme.v1.{point}.x", cls)
    assert e.value.fix


@pytest.mark.parametrize("point", ["agent-runtime", "model-runtime"])
def test_a_runtime_built_as_something_else_is_a_config_error_not_an_attribute_error(
    point: str,
) -> None:
    """Regression (mx-tests 3): a factory that built ``object()`` made compose raise a bare
    ``AttributeError`` with no kind, which the command reported as a defect (exit 1)."""
    registry = Registry().register(point, f"acme.v1.{point}.x", lambda: object())
    with pytest.raises(ConfigError, match="not a") as e:
        registry.create(point, f"acme.v1.{point}.x", where="matrix.x")
    assert e.value.exit_code == 3 and "matrix.x" in str(e.value)


def test_a_broken_runtime_fails_composition_at_its_key() -> None:
    from matrix import MatrixConfig, compose

    registry = default_registry(discover=False)
    registry.register("agent-runtime", "acme.v1.agent-runtime.broken", lambda: object())
    config = {
        "agent_runtimes": {"b": {"type": "acme.v1.agent-runtime.broken"}},
        "agents": {"a": {"runtime": "b", "system_prompt": "x"}},
    }
    with pytest.raises(ConfigError) as e:
        compose(MatrixConfig.model_validate(config), registry=registry)
    assert [p.path for p in e.value.problems] == ["matrix.agent_runtimes.b"]
    assert e.value.complete is False  # the agent on it was not checked


def test_every_built_in_runtime_keeps_its_port() -> None:
    """Built through the registry, which holds each to its port as it builds it."""
    pytest.importorskip("hardline")
    pytest.importorskip("claude_agent_sdk")
    from matrix import AgentRuntime, ModelRuntime

    registry = default_registry(discover=False)
    ports = {"agent-runtime": AgentRuntime, "model-runtime": ModelRuntime}
    needs = {
        "cwd": None,
        "models": {"models": {"q": {"backend": "mock", "model": "q", "family": "q"}}},
    }
    for point, port in ports.items():
        entries = registry.entries(point)
        assert entries
        for entry in entries:
            assert isinstance(registry.create(point, entry.type_url, needs=needs), port)
