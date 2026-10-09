"""The registry: typed config, declared needs, one discovery path, quarantine, no silent wins."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from matrix import ConfigError, NotFoundError, Registry, default_registry


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
        registry.create("component", "acme.v1.thing", {"sise": 3}, where="flow.members.t")
    problems = e.value.details["problems"]
    assert any("flow.members.t.size" in p for p in problems)
    assert any("sise" in p for p in problems)
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
    monkeypatch.setattr("matrix.domain.registry.entry_points", lambda group: [bad, good])
    registry = Registry().discover()
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
        monkeypatch.setattr("matrix.domain.registry.entry_points", lambda group, o=order: o)
        registry = Registry().discover()
        assert registry.entry("component", "shared.v1.x").origin == "pkg-a"  # name order
        (failure,) = registry.failures
        assert "pkg-a" in failure.error and "pkg-b" in failure.error


def test_the_built_ins_arrive_through_the_entry_point() -> None:
    registry = default_registry()
    assert registry.entry("runtime", "matrix.v1.runtime.mock").origin == "matrix"
    assert not registry.failures


def test_entries_describe_themselves_for_agents() -> None:
    entry = default_registry(discover=False).entry("runtime", "matrix.v1.runtime.claude-sdk")
    described = entry.describe()
    assert described["effects"] == ["filesystem", "model", "network", "subprocess"]
    assert described["needs"] == ["cwd"]
    assert "permission_mode" in described["config"]["properties"]


def test_unknown_effects_stay_unknown() -> None:
    entry = Registry().register("component", "acme.v1.x", build).entry("component", "acme.v1.x")
    assert entry.effects is None and entry.describe()["effects"] is None


def test_an_extension_at_a_point_declared_later_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sensor plugin named 'acme' loads before 'ix' declares the sensor point."""

    def sensor_plugin(registry: Registry) -> None:
        registry.register("sensor", "acme.v1.sensor.x", build)

    def owner(registry: Registry) -> None:
        registry.add_point("sensor")

    eps = [_entry_point("acme", sensor_plugin, "acme"), _entry_point("ix", owner, "ix")]
    monkeypatch.setattr("matrix.domain.registry.entry_points", lambda group: eps)
    registry = Registry().discover()
    assert ("sensor", "acme.v1.sensor.x") in registry
    assert registry.failures == []


def test_a_point_nobody_declares_is_still_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def orphan(registry: Registry) -> None:
        registry.register("sensor", "acme.v1.sensor.x", build)

    monkeypatch.setattr(
        "matrix.domain.registry.entry_points", lambda group: [_entry_point("acme", orphan)]
    )
    (failure,) = Registry().discover().failures
    assert "no extension point 'sensor'" in failure.error


def test_registration_is_all_or_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def half(registry: Registry) -> None:
        registry.register("component", "acme.v1.first", build)
        raise RuntimeError("broke half way")

    monkeypatch.setattr(
        "matrix.domain.registry.entry_points", lambda group: [_entry_point("acme", half)]
    )
    registry = Registry().discover()
    assert ("component", "acme.v1.first") not in registry
    assert "broke half way" in registry.failures[0].error
