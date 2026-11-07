"""The registry: typed config, declared needs, one discovery path, quarantine, no silent wins."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from matrix import (
    MATRIX_POINTS,
    ConfigError,
    NotFoundError,
    Registry,
    default_registry,
    load_extensions,
)


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
    registry = Registry().register("component", "acme.v1.thing", lambda: "built")
    assert registry.create("component", "acme.v1.thing") == "built"
    with pytest.raises(ConfigError, match="takes no config"):
        registry.create("component", "acme.v1.thing", {"x": 1})


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


def _entry_point(points: Any, register: Any, dist: str = "pkg") -> list[Any]:
    """The entry points of ``dist``'s extension: one per point it declares, each naming
    ``<dist>:register``, as its pyproject.toml would."""

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

    found = []
    for point in [points] if isinstance(points, str) else points:
        ep = _EP()
        ep.name = point  # type: ignore[attr-defined]
        ep.dist = d  # type: ignore[attr-defined]
        found.append(ep)
    return found


def test_a_broken_extension_is_quarantined_and_the_rest_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _entry_point(
        "component", lambda r: r.register("component", "good.v1.x", build), "good-pkg"
    )
    bad = _entry_point("component", ImportError("no module named nope"), "bad-pkg")
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: bad + good)
    registry = load_extensions(Registry())
    assert ("component", "good.v1.x") in registry
    assert registry.entry("component", "good.v1.x").origin == "good-pkg"
    (failure,) = registry.failures
    assert (failure.extension, failure.origin) == ("bad-pkg:register", "bad-pkg")
    assert "no module named nope" in failure.error


def test_a_collision_names_both_origins_whatever_the_install_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def claims(registry: Registry) -> None:
        registry.register("component", "shared.v1.x", build)

    first = _entry_point("component", claims, "pkg-a")
    second = _entry_point("component", claims, "pkg-b")
    for order in (first + second, second + first):
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
    assert [(n["name"], n["type"]) for n in described["needs"]] == [("cwd", "str | None")]
    assert "permission_mode" in described["config"]["properties"]


def test_an_entry_declares_no_effects() -> None:
    """Changed requirement (owner ruling 2026-10-22): effects were labels nothing checked, and are
    removed. What an entry is handed is its ``needs``; what it needs of the host its owner
    checks."""
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

    eps = _entry_point("sensor", sensor_plugin, "acme") + _entry_point("sensor", owner, "ix")
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: eps)
    registry = load_extensions(Registry())
    assert ("sensor", "acme.v1.sensor.x") in registry
    assert registry.failures == []


def test_a_point_nobody_declares_is_still_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def orphan(registry: Registry) -> None:
        registry.register("sensor", "acme.v1.sensor.x", build)

    monkeypatch.setattr(
        "matrix.composition.container.entry_points", lambda group: _entry_point("sensor", orphan)
    )
    (failure,) = load_extensions(Registry()).failures
    assert "no extension point 'sensor'" in failure.error


def test_registration_is_all_or_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def half(registry: Registry) -> None:
        registry.register("component", "acme.v1.first", build)
        raise RuntimeError("broke half way")

    monkeypatch.setattr(
        "matrix.composition.container.entry_points", lambda group: _entry_point("component", half)
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

    eps = _entry_point("agent-runtime", impostor, "aaa") + _entry_point(
        MATRIX_POINTS, matrix_register, "matrix"
    )
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: eps)
    registry = load_extensions(Registry())
    assert registry.entry("agent-runtime", "matrix.v1.agent-runtime.mock").origin == "matrix"
    assert (
        "agent-runtime",
        "aaa.v1.agent-runtime.fine",
    ) not in registry  # the impostor is dropped whole
    assert any(f.extension == "aaa:register" and "namespace" in f.error for f in registry.failures)


def test_a_quarantined_impostor_is_named_by_its_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from matrix.composition.builtins import register as matrix_register

    def impostor(registry: Registry) -> None:
        registry.register("agent-runtime", "matrix.v1.agent-runtime.mock", lambda: "hijacked")

    eps = _entry_point("agent-runtime", impostor, "aaa-dist") + _entry_point(
        MATRIX_POINTS, matrix_register, "matrix"
    )
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: eps)
    (failure,) = load_extensions(Registry()).failures
    assert (failure.extension, failure.origin) == ("aaa-dist:register", "aaa-dist")


def test_an_extension_is_loaded_only_for_a_session_that_needs_one_of_its_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M-DISC: a session loads the extensions that declare one of its points, read from their
    entry points' names, and imports no other: a recon session never imports ix's
    registration. A registry for every point (a catalog) loads them all."""
    imported: list[str] = []

    def at(name: str, point: str, type_url: str) -> Any:
        def register(registry: Registry) -> None:
            imported.append(name)
            registry.register(point, type_url, build)

        return register

    eps = _entry_point("component", at("acme", "component", "acme.v1.x"), "acme") + _entry_point(
        "store", at("other", "store", "other.v1.store"), "other"
    )
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: eps)
    scoped = default_registry(points={"component"})
    assert imported == ["acme"] and ("component", "acme.v1.x") in scoped
    assert ("store", "other.v1.store") not in scoped
    imported.clear()
    every = default_registry()
    assert sorted(imported) == ["acme", "other"] and ("store", "other.v1.store") in every


def test_an_extension_that_registers_where_it_did_not_declare_is_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The declaration is held to the code: registering at, or declaring, a point the entry
    points do not name quarantines the extension whole, with the line to add. An entry point
    written for 0.9, named after its distribution, declares a point of that name, and so is
    quarantined at its first registration."""

    def wide(registry: Registry) -> None:
        registry.register("component", "acme.v1.x", build)
        registry.register("store", "acme.v1.store", build)

    monkeypatch.setattr(
        "matrix.composition.container.entry_points",
        lambda group: (
            _entry_point("component", wide, "acme")
            + _entry_point("old", lambda r: r.register("component", "old.v1.x", build), "old")
        ),
    )
    registry = load_extensions(Registry())
    assert ("component", "acme.v1.x") not in registry  # all or nothing
    acme, old = sorted(registry.failures, key=lambda f: f.origin)
    assert "registers at 'store', which its entry points do not declare" in acme.error
    assert '"store" = "acme:register"' in acme.error
    assert "'component'" in old.error and ("component", "old.v1.x") not in registry


def test_an_extension_serving_another_owner_s_point_is_not_quarantined_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An extension that registers at two owners' points (an ix sensor and a recon collector)
    loads in a session of either: a registry scoped to a session's points keeps the entries at
    them and ignores the rest, where the point's owner, not loaded, would leave it unknown."""

    def both(registry: Registry) -> None:
        registry.register("recon.collector", "acme.v1.collector.x", build)
        registry.register("ix.sensor", "acme.v1.sensor.x", build)

    eps = _entry_point(["recon.collector", "ix.sensor"], both, "acme") + _entry_point(
        "recon.collector", lambda r: r.add_point("recon.collector"), "recon"
    )
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: eps)
    registry = default_registry(points={"recon.collector"})
    assert registry.failures == []
    assert ("recon.collector", "acme.v1.collector.x") in registry
    assert registry.entries("ix.sensor") == []


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

    def check(self, model: str | None, *, uses: frozenset[str] = frozenset()) -> None: ...

    def call(self, request: object) -> object: ...


@pytest.mark.parametrize(
    ("point", "cls", "said"),
    [("agent-runtime", _NoRun, "run"), ("model-runtime", _SyncCall, "call")],
)
def test_a_runtime_class_that_breaks_its_port_is_refused_when_registered(
    point: str, cls: type, said: str
) -> None:
    """Regression: one with ``check`` but no ``run`` composed cleanly and failed
    only when an agent ran on it."""
    with pytest.raises(ConfigError, match=said) as e:
        Registry().register(point, f"acme.v1.{point}.x", cls)
    assert e.value.fix


@pytest.mark.parametrize("point", ["agent-runtime", "model-runtime"])
def test_a_runtime_built_as_something_else_is_a_config_error_not_an_attribute_error(
    point: str,
) -> None:
    """Regression: a factory that built ``object()`` made compose raise a bare
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
    pytest.importorskip("claude_agent_sdk")
    from matrix import AgentRuntime, ModelRuntime
    from matrix.adapters._out.runtime.mock import MockModelRuntime

    registry = default_registry(discover=False)
    ports = {"agent-runtime": AgentRuntime, "model-runtime": ModelRuntime}
    needs = {"cwd": None, "model_runtimes": {"m": MockModelRuntime()}}
    configs = {"matrix.v1.agent-runtime.pydantic-ai": {"model_runtime": "m"}}
    for point, port in ports.items():
        entries = registry.entries(point)
        assert entries
        for entry in entries:
            built = registry.create(point, entry.type_url, configs.get(entry.type_url), needs=needs)
            assert isinstance(built, port)


# --- stores and observers are held to their ports; points have owners ----------------------


class _NoLoad:
    """A third party's store that forgot ``load``."""

    def open(self, run: object) -> object:
        raise NotImplementedError

    def describe(self, run_id: str) -> dict[str, object]:
        raise NotImplementedError

    def runs(self) -> list[str]:
        raise NotImplementedError


class _Silent:
    """A third party's observer that receives nothing."""

    def on_events(self, event: object) -> None: ...


@pytest.mark.parametrize(
    ("point", "cls", "said"), [("store", _NoLoad, "load"), ("observer", _Silent, "on_event")]
)
def test_a_store_or_observer_class_that_breaks_its_port_is_refused_when_registered(
    point: str, cls: type, said: str
) -> None:
    """As runtimes are: a store without ``load`` composed and failed at ``--from``; an
    observer without ``on_event`` was logged as failing on every event of every run."""
    with pytest.raises(ConfigError, match=said) as e:
        Registry().register(point, f"acme.v1.{point}.x", cls)
    assert e.value.fix


@pytest.mark.parametrize("point", ["store", "observer"])
def test_a_store_or_observer_built_as_something_else_is_a_config_error(point: str) -> None:
    registry = Registry().register(point, f"acme.v1.{point}.x", lambda: object())
    with pytest.raises(ConfigError, match="not a") as e:
        registry.create(point, f"acme.v1.{point}.x", where="matrix.x")
    assert e.value.exit_code == 3


def test_every_built_in_store_and_observer_keeps_its_port(tmp_path: Any) -> None:
    from matrix import ConstructStore, Observer

    registry = default_registry(discover=False)
    for entry in registry.entries("store"):
        config = {"root": str(tmp_path)} if entry.config is not None else None
        assert isinstance(registry.create("store", entry.type_url, config), ConstructStore)
    for entry in registry.entries("observer"):
        assert isinstance(registry.create("observer", entry.type_url), Observer)


def test_each_point_records_its_owner_and_another_cannot_redeclare_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``add_point`` recorded no owner, so any extension could declare another's point, with
    a port of its own (ix.md, D30). The owner is the distribution that declared it first;
    matrix owns its own points."""

    def ix(registry: Registry) -> None:
        registry.add_point("ix.subject")

    def squatter(registry: Registry) -> None:
        registry.register("component", "squat.v1.x", build)
        registry.add_point("ix.subject", port=Settings)

    eps = _entry_point("ix.subject", ix, "ix") + _entry_point(
        ["component", "ix.subject"], squatter, "squat"
    )
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: eps)
    registry = load_extensions(Registry())
    assert registry.owners["ix.subject"] == "ix"
    assert registry.owners["agent-runtime"] == "matrix"
    assert "ix.subject" not in registry.ports  # the squatter's port did not land
    assert ("component", "squat.v1.x") not in registry  # quarantined whole
    (failure,) = registry.failures
    assert failure.extension == "squat:register" and "ix.subject" in failure.error
    assert "ix" in failure.error
    registry.add_point("ix.subject")  # code that composes directly is trusted, as for type URLs
    assert registry.owners["ix.subject"] == "ix"


def test_a_point_under_a_distribution_s_name_is_its_own_whoever_declared_it_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'aaa' sorts first and declares ``ix.subject`` with a port of its own; ix keeps its
    point anyway, as matrix keeps its type URLs, and the impostor is dropped whole."""

    def impostor(registry: Registry) -> None:
        registry.add_point("ix.subject", port=Settings)
        registry.register("component", "aaa.v1.x", build)

    def ix(registry: Registry) -> None:
        registry.add_point("ix.subject")

    eps = _entry_point(["ix.subject", "component"], impostor, "aaa") + _entry_point(
        "ix.subject", ix, "ix"
    )
    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: eps)
    registry = load_extensions(Registry())
    assert registry.owners["ix.subject"] == "ix" and "ix.subject" not in registry.ports
    assert ("component", "aaa.v1.x") not in registry
    (failure,) = registry.failures
    assert failure.extension == "aaa:register" and "ix.subject" in failure.error
