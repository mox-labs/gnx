"""Tier discovery and merging, secret resolution, entry-point discovery, the CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml

from modelrt import ConfigError, SecretError, build_runtime
from modelrt.adapters._in.cli import main
from modelrt.adapters._out.secrets import EnvFileSecretResolver
from modelrt.adapters._out.yaml_source import MappingConfigSource, YamlConfigSource
from modelrt.composition import discover_backends, discover_sources, load_registry, merge_tiers
from modelrt.domain.structured import extract_json

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


def test_later_tier_overrides_field_by_field(tmp_path: Path) -> None:
    user = _write(
        tmp_path / "user.yaml",
        {
            "default": "q",
            "models": {"q": {"backend": "mock", "model": "a", "family": "qwen", "max_tokens": 100}},
        },
    )
    project = _write(tmp_path / "proj.yaml", {"models": {"q": {"model": "b"}}})
    reg = load_registry([YamlConfigSource(user), YamlConfigSource(project)])
    spec = reg.get()
    assert (spec.model, spec.max_tokens) == ("b", 100)


def test_error_names_the_last_tier_that_touched_the_row(tmp_path: Path) -> None:
    user = _write(
        tmp_path / "user.yaml",
        {"models": {"q": {"backend": "mock", "model": "a", "family": "qwen"}}},
    )
    project = _write(tmp_path / "proj.yaml", {"models": {"q": {"max_tokens": -1}}})
    with pytest.raises(ConfigError, match=r"proj\.yaml: models\.q\.max_tokens"):
        load_registry([YamlConfigSource(user), YamlConfigSource(project)])


def test_missing_tiers_read_as_empty(tmp_path: Path) -> None:
    merged, _, consulted = merge_tiers([YamlConfigSource(tmp_path / "nope.yaml")])
    assert merged == {"models": {}} and "absent" in consulted


def test_bad_yaml_names_the_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("models: [unclosed")
    with pytest.raises(ConfigError, match="bad.yaml: not valid YAML"):
        YamlConfigSource(bad).read()


def test_explicit_env_tier_must_exist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MODELRT_CONFIG", str(tmp_path / "missing.yaml"))
    with pytest.raises(ConfigError, match="MODELRT_CONFIG"):
        discover_sources(tmp_path)


def test_explicit_env_tier_is_highest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    explicit = _write(tmp_path / "x.yaml", {"models": {}})
    monkeypatch.setenv("MODELRT_CONFIG", str(explicit))
    assert discover_sources(tmp_path)[-1].describe() == str(explicit)


def test_mapping_source_is_how_another_root_hands_its_section() -> None:
    rt = build_runtime(
        {"models": {"q": {"backend": "mock", "model": "m", "family": "qwen"}}},
        label="ix.yaml#models",
    )
    assert rt.registry.names() == ("q",)
    assert MappingConfigSource({}, "ix.yaml#models").describe() == "ix.yaml#models"


# --- secrets -------------------------------------------------------------------------


def test_env_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "v")
    assert EnvFileSecretResolver().resolve("env:K") == "v"


def test_missing_env_secret_names_variable_not_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(SecretError, match="NOPE is not set"):
        EnvFileSecretResolver().resolve("env:NOPE")


def test_file_secret_is_stripped(tmp_path: Path) -> None:
    f = tmp_path / "key"
    f.write_text("abc\n")
    assert EnvFileSecretResolver().resolve(f"file:{f}") == "abc"


def test_missing_file_secret(tmp_path: Path) -> None:
    with pytest.raises(SecretError, match="cannot read"):
        EnvFileSecretResolver().resolve(f"file:{tmp_path / 'none'}")


def test_unknown_secret_scheme() -> None:
    with pytest.raises(SecretError, match="unknown scheme"):
        EnvFileSecretResolver().resolve("vault:x")


# --- entry points --------------------------------------------------------------------


class _EP:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self.value, self._obj = name, f"pkg:{name}", obj

    def load(self) -> Any:
        return self._obj


def test_entry_point_backend_is_discovered(monkeypatch: pytest.MonkeyPatch) -> None:
    from modelrt.adapters._out.mock import MockBackend

    monkeypatch.setattr(
        "modelrt.composition.entry_points", lambda group: [_EP("thirdparty", MockBackend)]
    )
    assert "thirdparty" in discover_backends()


def test_entry_point_may_not_shadow_a_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    from modelrt.adapters._out.mock import MockBackend

    monkeypatch.setattr(
        "modelrt.composition.entry_points", lambda group: [_EP("mock", MockBackend)]
    )
    with pytest.raises(ConfigError, match="collides"):
        discover_backends()


def test_entry_point_must_produce_a_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("modelrt.composition.entry_points", lambda group: [_EP("bad", object)])
    with pytest.raises(ConfigError, match="did not produce a ModelBackend"):
        discover_backends()


# --- structured extraction -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('Here you go:\n```json\n{"a": "}"}\n```\nDone.', '{"a": "}"}'),
        ('prefix [{"a": 1}] suffix', '[{"a": 1}]'),
        ('{"a": {"b": [1, 2]}} trailing {', '{"a": {"b": [1, 2]}}'),
    ],
)
def test_extract_json(text: str, expected: str) -> None:
    assert extract_json(text) == expected


def test_extract_json_none() -> None:
    with pytest.raises(ValueError, match="no JSON"):
        extract_json("no structure here")


# --- CLI -----------------------------------------------------------------------------


@pytest.fixture
def cli_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    cfg = _write(
        tmp_path / "modelrt.yaml",
        {
            "default": "q",
            "models": {"q": {"backend": "mock", "model": "m", "family": "qwen", "local": True}},
        },
    )
    return cfg


def test_cli_models(cli_config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["models"]) == 0
    out = capsys.readouterr().out
    assert "q *" in out and "qwen" in out and "yes" in out


def test_cli_check(cli_config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check"]) == 0
    assert "1 model(s) valid" in capsys.readouterr().out


def test_cli_complete_json(cli_config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["complete", "", "hello", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"family": "qwen"' in out and '"raw"' not in out


def test_cli_error_exit_code(cli_config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["complete", "missing", "x"]) == 1
    assert "unknown model 'missing'" in capsys.readouterr().err


def test_cli_skill(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--skill"]) == 0
    assert "name: modelrt" in capsys.readouterr().out


def test_schema_instruction_leads_with_a_fill_in_template() -> None:
    from pydantic import BaseModel

    from modelrt.domain.structured import schema_instruction

    class Verdict(BaseModel):
        supported: bool
        reason: str

    text = schema_instruction(Verdict.model_json_schema())
    assert '{"supported": <boolean>, "reason": <string>}' in text
    assert "do not repeat it" in text
