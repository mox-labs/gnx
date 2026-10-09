"""Collector types and transforms load from entry points; a broken plugin breaks nothing else."""

import sys
from importlib.metadata import EntryPoint

import pytest

from recon.composition import (
    COLLECTORS_GROUP,
    TRANSFORMS_GROUP,
    build_collectors,
    load_collector_types,
    load_registry,
    load_transforms,
)
from recon.domain.collector import CollectorType


def test_builtins_register_through_the_entry_point_groups():
    """The installed package declares its own types and transforms like any plugin would."""
    registry = load_registry()
    assert {"api", "cli", "web", "capture"} <= set(registry.collector_types)
    assert {"html2text", "inverted_index", "join", "first", "markitdown"} <= set(
        registry.transforms
    )
    assert registry.broken == []


@pytest.fixture
def plugin_module(tmp_path, monkeypatch):
    """A throwaway module on sys.path holding a good type, a bad object, and a raiser."""
    (tmp_path / "recon_test_plugins.py").write_text(
        "from recon.domain.collector import CollectorType\n"
        "good = CollectorType(create=lambda ctx: object(), effects=frozenset({'network'}))\n"
        "not_a_type = 42\n"
        "def shout(v):\n    return str(v).upper()\n"
    )
    (tmp_path / "recon_test_explodes.py").write_text("raise RuntimeError('boom on import')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield
    for name in ("recon_test_plugins", "recon_test_explodes"):
        sys.modules.pop(name, None)


def _discover(entries):
    def discover(group):
        return [EntryPoint(name=n, value=v, group=g) for n, v, g in entries if g == group]

    return discover


def test_a_plugin_that_raises_on_import_is_reported_and_others_load(plugin_module):
    discover = _discover(
        [
            ("good", "recon_test_plugins:good", COLLECTORS_GROUP),
            ("boom", "recon_test_explodes:anything", COLLECTORS_GROUP),
            ("wrong", "recon_test_plugins:not_a_type", COLLECTORS_GROUP),
            ("missing", "no_such_module_anywhere:x", COLLECTORS_GROUP),
        ]
    )
    types, broken = load_collector_types(discover)

    assert set(types) == {"good"}
    assert isinstance(types["good"], CollectorType)
    by_name = {b.name: b for b in broken}
    assert set(by_name) == {"boom", "wrong", "missing"}
    assert "RuntimeError: boom on import" in by_name["boom"].error
    assert "expected a recon.domain.collector.CollectorType" in by_name["wrong"].error
    assert "ModuleNotFoundError" in by_name["missing"].error


def test_transforms_load_by_name_and_reject_non_callables(plugin_module):
    discover = _discover(
        [
            ("shout", "recon_test_plugins:shout", TRANSFORMS_GROUP),
            ("nope", "recon_test_plugins:not_a_type", TRANSFORMS_GROUP),
        ]
    )
    transforms, broken = load_transforms(discover)
    assert transforms["shout"]("a") == "A"
    assert [b.name for b in broken] == ["nope"]


def test_duplicate_name_is_reported_not_silently_shadowed(plugin_module):
    discover = _discover(
        [
            ("dup", "recon_test_plugins:good", COLLECTORS_GROUP),
            ("dup", "recon_test_plugins:good", COLLECTORS_GROUP),
        ]
    )
    types, broken = load_collector_types(discover)
    assert set(types) == {"dup"}
    assert len(broken) == 1 and "already registered" in broken[0].error


def test_a_factory_that_raises_is_reported_and_others_build(tmp_path):
    from dataclasses import replace

    registry = load_registry()

    def explode(ctx):
        raise ValueError("no credentials file")

    registry.collector_types["fragile"] = replace(registry.collector_types["cli"], create=explode)
    collectors = build_collectors(registry, tmp_path, env={})
    assert "fragile" not in collectors
    assert {"api", "cli", "web", "capture"} <= set(collectors)
    assert any(b.name == "fragile" and "no credentials file" in b.error for b in registry.broken)
