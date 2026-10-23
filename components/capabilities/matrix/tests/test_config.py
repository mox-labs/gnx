"""Tests for Matrix config composition — types, sources, loading."""

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from matrix.composition.config import load_config
from matrix.domain.config import Config, MatrixConfig
from matrix.domain.errors import ConfigError

# --- Test client schema ---


class SampleClientConfig(BaseModel):
    """Minimal client config for testing."""

    model_config = ConfigDict(frozen=True)

    trials: int = 5
    sensor: str = "activation"


# --- MatrixConfig defaults ---


class TestMatrixConfig:
    def test_defaults_compose_nothing(self) -> None:
        config = MatrixConfig()
        assert (
            config.agent_runtimes,
            config.model_runtimes,
            config.agents,
            config.definitions,
            config.models,
        ) == ({}, {}, {}, (), None)

    def test_runtime_keeps_its_options_for_the_runtime_to_validate(self) -> None:
        config = MatrixConfig.model_validate(
            {"agent_runtimes": {"sdk": {"type": "claude-sdk", "permission_mode": "plan"}}}
        )
        assert config.agent_runtimes["sdk"].type == "claude-sdk"
        assert config.agent_runtimes["sdk"].options() == {"permission_mode": "plan"}

    def test_unknown_top_level_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="runtime"):
            MatrixConfig.model_validate({"runtime": {"model": "x"}})
        with pytest.raises(ValidationError, match="runtimes"):  # agent_ or model_runtimes
            MatrixConfig.model_validate({"runtimes": {"m": {"type": "mock"}}})

    def test_agent_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="promt"):
            MatrixConfig.model_validate({"agents": {"a": {"runtime": "r", "promt": "x"}}})

    def test_frozen(self) -> None:
        config = MatrixConfig()
        with pytest.raises(ValidationError):
            config.agent_runtimes = {}


# --- Config[C] composition ---


class TestConfig:
    def test_compose_with_client(self) -> None:
        config = Config(client=SampleClientConfig(trials=10))
        assert config.matrix == MatrixConfig()
        assert config.client.trials == 10

    def test_matrix_defaults_when_absent(self) -> None:
        """Matrix section uses defaults when not explicitly provided."""
        config = Config(client=SampleClientConfig())
        assert config.matrix == MatrixConfig()

    def test_custom_matrix(self) -> None:
        config = Config(
            matrix=MatrixConfig(definitions=("agents",)),
            client=SampleClientConfig(),
        )
        assert config.matrix.definitions == ("agents",)
        assert config.client.sensor == "activation"

    def test_frozen(self) -> None:
        config = Config(client=SampleClientConfig())
        with pytest.raises(ValidationError):
            config.client = SampleClientConfig(trials=99)


# --- load_config ---


class TestLoadConfig:
    def test_no_files_uses_defaults(self, tmp_path: Path) -> None:
        """When no config files exist, Pydantic defaults apply."""
        config = load_config(
            SampleClientConfig,
            client_key="ix",
            sources=[tmp_path / "nonexistent.yaml"],
        )
        assert config.matrix == MatrixConfig()
        assert config.client.trials == 5

    def test_single_source(self, tmp_path: Path) -> None:
        f = tmp_path / "matrix.yaml"
        f.write_text("matrix:\n  definitions: [agents]\nix:\n  trials: 20\n")
        config = load_config(SampleClientConfig, client_key="ix", sources=[f])
        # Relative definition directories resolve against the file that declared them.
        assert config.matrix.definitions == (str((tmp_path / "agents").resolve()),)
        assert config.client.trials == 20

    def test_project_overrides_user(self, tmp_path: Path) -> None:
        """Later sources override earlier ones."""
        user = tmp_path / "user.yaml"
        user.write_text("matrix:\n  definitions: [user-agents]\nix:\n  trials: 10\n")

        project = tmp_path / "project.yaml"
        project.write_text("matrix:\n  definitions: [project-agents]\n")

        config = load_config(SampleClientConfig, client_key="ix", sources=[user, project])
        # Definition directories accumulate across tiers: your agents plus the project's.
        assert config.matrix.definitions == (
            str((tmp_path / "user-agents").resolve()),
            str((tmp_path / "project-agents").resolve()),
        )
        assert config.client.trials == 10  # not overridden by project

    def test_definitions_from_a_stream_accumulate_and_stay_as_written(self, tmp_path: Path) -> None:
        import io

        piped = io.StringIO("matrix:\n  definitions: [team-agents]\n")
        project = tmp_path / "project.yaml"
        project.write_text("matrix:\n  definitions: [project-agents]\n")

        config = load_config(sources=[piped, project])
        assert config.matrix.definitions == (
            "team-agents",  # a stream has no folder to resolve against
            str((tmp_path / "project-agents").resolve()),
        )

    def test_custom_client_key(self, tmp_path: Path) -> None:
        f = tmp_path / "matrix.yaml"
        f.write_text("radix:\n  trials: 42\n")
        config = load_config(SampleClientConfig, client_key="radix", sources=[f])
        assert config.client.trials == 42

    def test_partial_override_preserves_sibling(self, tmp_path: Path) -> None:
        """A later tier overriding one runtime option keeps that runtime's other options."""
        user = tmp_path / "user.yaml"
        user.write_text(
            "matrix:\n  agent_runtimes:\n    sdk:\n      type: claude-sdk\n"
            "      permission_mode: plan\n"
        )
        project = tmp_path / "project.yaml"
        project.write_text("matrix:\n  agent_runtimes:\n    sdk:\n      cwd: /work\n")
        config = load_config(SampleClientConfig, client_key="ix", sources=[user, project])
        assert config.matrix.agent_runtimes["sdk"].options() == {
            "permission_mode": "plan",
            "cwd": "/work",
        }

    def test_invalid_section_names_key_path_and_sources(self, tmp_path: Path) -> None:
        f = tmp_path / "matrix.yaml"
        f.write_text("matrix:\n  agents:\n    a:\n      runtim: sdk\n")
        with pytest.raises(ConfigError) as e:
            load_config(SampleClientConfig, client_key="ix", sources=[f])
        found = {(p.path, p.kind, p.source) for p in e.value.problems}
        assert ("matrix.agents.a.runtim", "unknown_key", str(f)) in found
        assert ("matrix.agents.a.runtime", "missing_key", str(f)) in found
        assert e.value.details["sources"] == [str(f)]

    def test_both_sections_are_checked_at_once(self, tmp_path: Path) -> None:
        """Every problem at once: the matrix section's and the tool's, in one error."""
        f = tmp_path / "ix.yaml"
        f.write_text("matrix:\n  bogus: 1\nix:\n  trials: many\n")
        with pytest.raises(ConfigError) as e:
            load_config(SampleClientConfig, client_key="ix", sources=[f])
        assert {p.path for p in e.value.problems} == {"matrix.bogus", "ix.trials"}

    def test_store_and_limits_are_config(self, tmp_path: Path) -> None:
        f = tmp_path / "matrix.yaml"
        f.write_text(
            "matrix:\n  store: {type: memory}\n"
            "  limits:\n    concurrency: 2\n    retry: {attempts: 3, errors: [rate_limited]}\n"
            "    components: {slow: {deadline_s: 9}}\n"
        )
        config = load_config(sources=[f]).matrix
        assert config.store.type == "memory"
        assert config.limits.concurrency == 2
        assert config.limits.retry_of("other").attempts == 3
        assert config.limits.of("slow")[0] == 9

    def test_a_bad_limit_is_a_problem_at_its_path(self, tmp_path: Path) -> None:
        f = tmp_path / "matrix.yaml"
        f.write_text("matrix:\n  limits:\n    retry: {attempts: 0, errors: [sometimes]}\n")
        with pytest.raises(ConfigError) as e:
            load_config(sources=[f])
        by_path = {p.path: p for p in e.value.problems}
        assert by_path["matrix.limits.retry.attempts"].kind == "invalid_value"
        assert "rate_limited" in by_path["matrix.limits.retry.errors.0"].allowed


def test_security_names_every_tier_read_from_where_a_command_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The working directory's config files are an ambient route to a capability grant, and
    SECURITY.md says which: every file read from the directory a command runs
    in, and every variable that adds one, is named there."""
    from pathlib import Path

    from matrix.composition.config import discover_sources

    monkeypatch.setenv("MATRIX_CONFIG", str(tmp_path / "shared.yaml"))
    (tmp_path / "shared.yaml").write_text("{}")
    security = (Path(__file__).parents[1] / "SECURITY.md").read_text()
    for path in discover_sources("acme", project_root=tmp_path):
        if path.parent == tmp_path and path.name != "shared.yaml":
            name = path.name.replace("acme", "<tool>")
            assert f"`./{name}`" in security, name
    assert "`$MATRIX_CONFIG`" in security and "`$<TOOL>_CONFIG`" in security
    assert "matrix config --sources" in security
