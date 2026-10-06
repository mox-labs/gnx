"""Tier discovery and merging, secret resolution, entry-point discovery, the CLI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from hardline import BackendError, ConfigError, SecretError, build_runtime
from hardline.adapters._in.cli import error_payload, main
from hardline.adapters._out.secrets import EnvFileSecretResolver
from hardline.adapters._out.yaml_source import MappingConfigSource, YamlConfigSource
from hardline.composition import discover_backends, discover_sources, load_registry, merge_tiers
from hardline.domain.structured import extract_json

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
    monkeypatch.setenv("HARDLINE_CONFIG", str(tmp_path / "missing.yaml"))
    with pytest.raises(ConfigError, match="HARDLINE_CONFIG"):
        discover_sources(tmp_path)


def test_explicit_env_tier_is_highest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    explicit = _write(tmp_path / "x.yaml", {"models": {}})
    monkeypatch.setenv("HARDLINE_CONFIG", str(explicit))
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
    from hardline.adapters._out.mock import MockBackend

    monkeypatch.setattr(
        "hardline.composition.entry_points", lambda group: [_EP("thirdparty", MockBackend)]
    )
    assert "thirdparty" in discover_backends()


def test_entry_point_may_not_shadow_a_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    from hardline.adapters._out.mock import MockBackend

    monkeypatch.setattr(
        "hardline.composition.entry_points", lambda group: [_EP("mock", MockBackend)]
    )
    with pytest.raises(ConfigError, match="collides"):
        discover_backends()


def test_entry_point_must_produce_a_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hardline.composition.entry_points", lambda group: [_EP("bad", object)])
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
        tmp_path / "hardline.yaml",
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
    # An unknown model name is input the caller must change: config, exit 3.
    assert main(["complete", "missing", "x"]) == 3
    assert "unknown model 'missing'" in capsys.readouterr().err


# --- CLI: exit codes, JSON errors, check (agent-drivability F2/F3/F5) ----------------

_ERROR_KEYS = {"kind", "message", "retryable", "retry_after", "fix"}


def _scripted_config(tmp_path: Path, reason: str) -> None:
    _write(
        tmp_path / "hardline.yaml",
        {
            "models": {
                "s": {
                    "backend": "mock",
                    "model": "m",
                    "family": "mocka",
                    "retries": 0,  # no backoff sleep: the first failure is final
                    "options": {"script": [{"error": reason}]},
                }
            }
        },
    )


def _error_line(err: str) -> dict[str, Any]:
    lines = err.strip().splitlines()
    assert len(lines) == 1, f"expected one JSON line on stderr, got: {err!r}"
    payload: dict[str, Any] = json.loads(lines[0])
    assert set(payload) == {"error"} and set(payload["error"]) == _ERROR_KEYS
    return payload["error"]


@pytest.mark.parametrize(
    ("reason", "code", "retryable"),
    [
        ("rate_limit", 5, True),
        ("timeout", 5, True),
        ("unavailable", 5, True),
        ("auth", 6, False),
        ("bad_request", 3, False),
        ("unknown", 1, False),
    ],
)
def test_cli_backend_reason_sets_exit_code_and_json_error(
    reason: str,
    code: int,
    retryable: bool,
    cli_config: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _scripted_config(tmp_path, reason)
    assert main(["complete", "s", "x", "--json"]) == code
    out, err = capsys.readouterr()
    assert out == ""
    error = _error_line(err)
    assert error["kind"] == reason
    assert error["retryable"] is retryable
    assert error["retry_after"] is None
    assert (error["fix"] is None) == (reason == "unknown")


def test_cli_backend_error_without_json_is_prose_with_same_code(
    cli_config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _scripted_config(tmp_path, "auth")
    assert main(["complete", "s", "x"]) == 6
    assert capsys.readouterr().err.startswith("hardline: model 's' (mock): scripted auth")


def test_error_payload_carries_retry_after() -> None:
    payload, code = error_payload(BackendError("slow down", reason="rate_limit", retry_after=7.0))
    assert code == 5
    assert payload["error"]["retry_after"] == 7.0 and payload["error"]["retryable"] is True


def test_cli_unknown_model_json_error(cli_config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["complete", "missing", "x", "--json"]) == 3
    error = _error_line(capsys.readouterr().err)
    assert error["kind"] == "config" and "unknown model 'missing'" in error["message"]
    assert error["fix"] is not None and "hardline models" in error["fix"]


def test_cli_invalid_config_is_exit_3(
    cli_config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path / "hardline.yaml", {"models": {"q": {"backend": "mock", "model": "m"}}})
    assert main(["models", "--json"]) == 3
    error = _error_line(capsys.readouterr().err)
    assert error["kind"] == "config" and "family" in error["message"]


def _secret_config(tmp_path: Path) -> None:
    _write(
        tmp_path / "hardline.yaml",
        {
            "models": {
                "q": {"backend": "mock", "model": "m", "family": "qwen"},
                "g": {
                    "backend": "mock",
                    "model": "m",
                    "family": "gemini",
                    "api_key": "env:HL_TEST_MISSING_KEY",
                },
            }
        },
    )


def test_cli_check_fails_on_unset_env_secret(
    cli_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("HL_TEST_MISSING_KEY", raising=False)
    _secret_config(tmp_path)
    assert main(["check"]) == 3
    out, err = capsys.readouterr()
    assert "ok:" not in out
    assert "models.g.api_key" in err and "HL_TEST_MISSING_KEY is not set" in err


def test_cli_check_json_reports_per_row_and_never_the_value(
    cli_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("HL_TEST_MISSING_KEY", raising=False)
    _secret_config(tmp_path)
    assert main(["check", "--json"]) == 3
    out, err = capsys.readouterr()
    report = json.loads(out)
    assert set(report) == {"ok", "tiers", "models"} and report["ok"] is False
    rows = {row["name"]: row for row in report["models"]}
    assert rows["q"]["ok"] is True and rows["q"]["problem"] is None
    assert rows["g"]["ok"] is False and "HL_TEST_MISSING_KEY" in rows["g"]["problem"]
    assert _error_line(err)["kind"] == "config"

    monkeypatch.setenv("HL_TEST_MISSING_KEY", "sk-very-secret-value")
    assert main(["check", "--json"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out)["ok"] is True and err == ""
    assert "sk-very-secret-value" not in out


def test_cli_check_json_ok(cli_config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert set(report["tiers"][0]) == {"source", "present"}
    assert set(report["models"][0]) == {"name", "backend", "family", "ok", "problem"}


@pytest.mark.parametrize(
    "argv",
    [
        ["complete", "", "x", "--temperature", "-1"],
        ["complete", "", "x", "--temperature", "nan"],
        ["complete", "", "x", "--max-tokens", "0"],
    ],
)
def test_cli_numeric_bounds_are_usage_errors(
    argv: list[str], cli_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


def test_cli_temperature_zero_is_accepted(cli_config: Path) -> None:
    assert main(["complete", "", "x", "--temperature", "0"]) == 0


def test_cli_help_lists_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "exit codes:" in out and "5 transient" in out and "6 auth" in out


def test_cli_skill(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--skill"]) == 0
    assert "name: hardline" in capsys.readouterr().out


def test_schema_instruction_leads_with_a_fill_in_template() -> None:
    from pydantic import BaseModel

    from hardline.domain.structured import schema_instruction

    class Verdict(BaseModel):
        supported: bool
        reason: str

    text = schema_instruction(Verdict.model_json_schema())
    assert '{"supported": <boolean>, "reason": <string>}' in text
    assert "do not repeat it" in text
