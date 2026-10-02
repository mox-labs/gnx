"""The registry is the boundary where config becomes typed. Errors must name file and key."""

from __future__ import annotations

import pytest
from hardline_helpers import DictSecrets

from hardline import ConfigError, ModelRegistry, UnknownModelError, build_runtime
from hardline.adapters._out.mock import MockBackend

ROW = {"backend": "mock", "model": "m", "family": "qwen"}


def test_valid_row_becomes_typed_spec() -> None:
    reg = ModelRegistry.from_config({"models": {"q": ROW}, "default": "q"})
    spec = reg.get()
    assert (spec.name, spec.family, spec.backend) == ("q", "qwen", "mock")
    assert reg.names() == ("q",)


def test_typo_names_source_and_key_path() -> None:
    with pytest.raises(ConfigError) as e:
        ModelRegistry.from_config(
            {"models": {"q": {**ROW, "famly": "qwen"}}}, source="./hardline.yaml"
        )
    msg = str(e.value)
    assert "./hardline.yaml" in msg and "models.q.famly" in msg


def test_error_attributed_to_the_tier_that_defined_the_row() -> None:
    with pytest.raises(ConfigError) as e:
        ModelRegistry.from_config(
            {"models": {"q": {**ROW, "max_tokens": 0}}},
            source="merged",
            origins={"q": "/home/u/.hardline/config.yaml"},
        )
    assert "/home/u/.hardline/config.yaml: models.q.max_tokens" in str(e.value)


def test_family_is_required_never_inferred() -> None:
    with pytest.raises(ConfigError, match=r"models\.q\.family"):
        ModelRegistry.from_config({"models": {"q": {"backend": "mock", "model": "m"}}})


def test_literal_api_key_is_rejected() -> None:
    with pytest.raises(ConfigError, match="reference, not a key"):
        ModelRegistry.from_config({"models": {"q": {**ROW, "api_key": "sk-live-abc"}}})


def test_unknown_top_level_key_lists_legal_set() -> None:
    with pytest.raises(ConfigError, match=r"Legal: \['default', 'models'\]"):
        ModelRegistry.from_config({"modles": {}})


def test_default_must_exist() -> None:
    with pytest.raises(ConfigError, match="default model 'nope'"):
        ModelRegistry.from_config({"models": {"q": ROW}, "default": "nope"})


def test_unknown_model_lists_known() -> None:
    reg = ModelRegistry.from_config({"models": {"q": ROW, "h": {**ROW, "family": "claude"}}})
    with pytest.raises(UnknownModelError, match="Known: h, q"):
        reg.get("gpt")


def test_no_name_and_no_default() -> None:
    with pytest.raises(UnknownModelError, match="no default"):
        ModelRegistry.from_config({"models": {"q": ROW}}).get()


def test_runtime_rejects_uninstalled_backend_naming_installed() -> None:
    with pytest.raises(ConfigError) as e:
        build_runtime(
            {"models": {"q": {**ROW, "backend": "vertex"}}},
            backends={"mock": MockBackend()},
            secrets=DictSecrets(),
        )
    assert "models.q.backend: 'vertex'" in str(e.value) and "Installed: mock" in str(e.value)


def test_runtime_rejects_structured_mode_the_backend_cannot_honour() -> None:
    from hardline.adapters._out.anthropic_native import AnthropicBackend

    with pytest.raises(ConfigError, match=r"models\.h\.structured: 'json_schema'"):
        build_runtime(
            {"models": {"h": {**ROW, "backend": "anthropic", "structured": "json_schema"}}},
            backends={"anthropic": AnthropicBackend()},
            secrets=DictSecrets(),
        )
