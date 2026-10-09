"""Typed registration, config errors in config terms, and discovery."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from matrix import ComponentRegistry, ConfigError


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size: int


def test_typed_config_validated_before_build() -> None:
    reg = ComponentRegistry().register_typed("t.v1/x", _Cfg, lambda c: c.size * 2)
    assert reg.create("t.v1/x", {"size": 3}) == 6


def test_typed_config_error_names_source_type_url_and_key() -> None:
    reg = ComponentRegistry().register_typed("t.v1/x", _Cfg, lambda c: c)
    with pytest.raises(ConfigError) as e:
        reg.create("t.v1/x", {"siz": 3}, source="lab/exp/experiment.yaml")
    msg = str(e.value)
    assert "lab/exp/experiment.yaml: t.v1/x: siz" in msg


def test_kwargs_factory_unknown_key_is_a_config_error_not_a_type_error() -> None:
    def factory(*, name: str = "n") -> str:
        return name

    reg = ComponentRegistry().register("t.v1/y", factory)
    with pytest.raises(
        ConfigError, match=r"unknown config key\(s\) \['nmae'\]. Accepted: \['name'\]"
    ):
        reg.create("t.v1/y", {"nmae": "x"})


def test_type_error_inside_factory_body_is_not_misreported() -> None:
    def factory(**kw: object) -> object:
        raise TypeError("real bug inside the factory")

    reg = ComponentRegistry().register("t.v1/z", factory)
    with pytest.raises(TypeError, match="real bug"):
        reg.create("t.v1/z", {"a": 1})


def test_unknown_type_url_lists_registered() -> None:
    reg = ComponentRegistry().register("t.v1/a", lambda **kw: None)
    with pytest.raises(KeyError, match=r"Registered: t\.v1/a"):
        reg.create("t.v1/b")


def test_discover_rejects_non_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EP:
        name, value = "bad", "pkg:thing"

        @staticmethod
        def load() -> object:
            return 42

    monkeypatch.setattr("matrix.domain.registry.entry_points", lambda group: [_EP()])
    with pytest.raises(ConfigError, match="is not callable"):
        ComponentRegistry().discover()
