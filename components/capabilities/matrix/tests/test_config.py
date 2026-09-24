"""Tests for Matrix config composition — types, sources, loading."""

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from matrix.adapters._out.config.yaml_source import YamlConfigSource
from matrix.composition.config import deep_merge, load_config
from matrix.domain.config import Config, MatrixConfig
from matrix.domain.errors import ConfigError

# --- Test client schema ---


class SampleClientConfig(BaseModel):
    """Minimal client config for testing."""

    model_config = ConfigDict(frozen=True)

    trials: int = 5
    sensor: str = "activation"


# --- deep_merge ---


class TestDeepMerge:
    def test_flat_override(self):
        assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_override(self):
        base = {"matrix": {"runtime": {"model": "old"}}}
        override = {"matrix": {"runtime": {"model": "new"}}}
        result = deep_merge(base, override)
        assert result["matrix"]["runtime"]["model"] == "new"

    def test_nested_partial_override(self):
        """Override one nested key without clobbering siblings."""
        base = {"matrix": {"runtime": {"model": "old", "max_tokens": 2048}}}
        override = {"matrix": {"runtime": {"model": "new"}}}
        result = deep_merge(base, override)
        assert result["matrix"]["runtime"]["model"] == "new"
        assert result["matrix"]["runtime"]["max_tokens"] == 2048

    def test_list_replaces(self):
        """Lists replace entirely — no merging."""
        assert deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}

    def test_new_keys_added(self):
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_empty_override(self):
        assert deep_merge({"a": 1}, {}) == {"a": 1}

    def test_empty_base(self):
        assert deep_merge({}, {"a": 1}) == {"a": 1}

    def test_both_empty(self):
        assert deep_merge({}, {}) == {}


# --- MatrixConfig defaults ---


class TestMatrixConfig:
    def test_defaults_compose_nothing(self):
        config = MatrixConfig()
        assert (config.runtimes, config.agents, config.definitions, config.models) == (
            {},
            {},
            (),
            None,
        )

    def test_runtime_keeps_its_options_for_the_runtime_to_validate(self):
        config = MatrixConfig.model_validate(
            {"runtimes": {"sdk": {"type": "claude-sdk", "permission_mode": "plan"}}}
        )
        assert config.runtimes["sdk"].type == "claude-sdk"
        assert config.runtimes["sdk"].options() == {"permission_mode": "plan"}

    def test_unknown_top_level_key_rejected(self):
        with pytest.raises(ValidationError, match="runtime"):
            MatrixConfig.model_validate({"runtime": {"model": "x"}})

    def test_agent_rejects_unknown_field(self):
        with pytest.raises(ValidationError, match="promt"):
            MatrixConfig.model_validate({"agents": {"a": {"runtime": "r", "promt": "x"}}})

    def test_frozen(self):
        config = MatrixConfig()
        with pytest.raises(ValidationError):
            config.runtimes = {}


# --- Config[C] composition ---


class TestConfig:
    def test_compose_with_client(self):
        config = Config(client=SampleClientConfig(trials=10))
        assert config.matrix == MatrixConfig()
        assert config.client.trials == 10

    def test_matrix_defaults_when_absent(self):
        """Matrix section uses defaults when not explicitly provided."""
        config = Config(client=SampleClientConfig())
        assert config.matrix == MatrixConfig()

    def test_custom_matrix(self):
        config = Config(
            matrix=MatrixConfig(definitions=("agents",)),
            client=SampleClientConfig(),
        )
        assert config.matrix.definitions == ("agents",)
        assert config.client.sensor == "activation"

    def test_frozen(self):
        config = Config(client=SampleClientConfig())
        with pytest.raises(ValidationError):
            config.client = SampleClientConfig(trials=99)


# --- YamlConfigSource ---


class TestYamlConfigSource:
    def test_reads_yaml(self, tmp_path):
        f = tmp_path / "config.yaml"
        f.write_text("matrix:\n  definitions: [agents]\n")
        source = YamlConfigSource(f)
        data = source.read()
        assert data["matrix"]["definitions"] == ["agents"]

    def test_missing_file_returns_empty(self, tmp_path):
        source = YamlConfigSource(tmp_path / "nonexistent.yaml")
        assert source.read() == {}

    def test_empty_file_returns_empty(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("")
        source = YamlConfigSource(f)
        assert source.read() == {}

    def test_non_mapping_is_an_error_naming_the_file(self, tmp_path):
        """A file that exists but is not a mapping is a mistake, not an empty tier.

        Previously it read as {} — a config file that did nothing, silently.
        """
        f = tmp_path / "scalar.yaml"
        f.write_text("just a string")
        with pytest.raises(ConfigError, match="scalar.yaml: top level must be a mapping"):
            YamlConfigSource(f).read()

    def test_invalid_yaml_names_the_file(self, tmp_path):
        f = tmp_path / "broken.yaml"
        f.write_text("matrix: [unclosed")
        with pytest.raises(ConfigError, match="broken.yaml: not valid YAML"):
            YamlConfigSource(f).read()


# --- load_config ---


class TestLoadConfig:
    def test_no_files_uses_defaults(self, tmp_path):
        """When no config files exist, Pydantic defaults apply."""
        config = load_config(
            SampleClientConfig,
            client_key="ix",
            sources=[tmp_path / "nonexistent.yaml"],
        )
        assert config.matrix == MatrixConfig()
        assert config.client.trials == 5

    def test_single_source(self, tmp_path):
        f = tmp_path / "matrix.yaml"
        f.write_text("matrix:\n  definitions: [agents]\nix:\n  trials: 20\n")
        config = load_config(SampleClientConfig, client_key="ix", sources=[f])
        assert config.matrix.definitions == ("agents",)
        assert config.client.trials == 20

    def test_project_overrides_user(self, tmp_path):
        """Later sources override earlier ones."""
        user = tmp_path / "user.yaml"
        user.write_text("matrix:\n  definitions: [user-agents]\nix:\n  trials: 10\n")

        project = tmp_path / "project.yaml"
        project.write_text("matrix:\n  definitions: [project-agents]\n")

        config = load_config(SampleClientConfig, client_key="ix", sources=[user, project])
        assert config.matrix.definitions == ("project-agents",)
        assert config.client.trials == 10  # not overridden by project

    def test_custom_client_key(self, tmp_path):
        f = tmp_path / "matrix.yaml"
        f.write_text("radix:\n  trials: 42\n")
        config = load_config(SampleClientConfig, client_key="radix", sources=[f])
        assert config.client.trials == 42

    def test_partial_override_preserves_sibling(self, tmp_path):
        """A later tier overriding one runtime option keeps that runtime's other options."""
        user = tmp_path / "user.yaml"
        user.write_text(
            "matrix:\n  runtimes:\n    sdk:\n      type: claude-sdk\n      permission_mode: plan\n"
        )
        project = tmp_path / "project.yaml"
        project.write_text("matrix:\n  runtimes:\n    sdk:\n      cwd: /work\n")
        config = load_config(SampleClientConfig, client_key="ix", sources=[user, project])
        assert config.matrix.runtimes["sdk"].options() == {
            "permission_mode": "plan",
            "cwd": "/work",
        }

    def test_invalid_section_names_key_path_and_sources(self, tmp_path):
        f = tmp_path / "matrix.yaml"
        f.write_text("matrix:\n  agents:\n    a:\n      runtim: sdk\n")
        with pytest.raises(ConfigError) as e:
            load_config(SampleClientConfig, client_key="ix", sources=[f])
        msg = str(e.value)
        assert "matrix.agents.a.runtim" in msg and str(f) in msg
