"""The CLI inspects without running; config tiers are shared; errors are data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest

from matrix import (
    EXIT_CODES,
    EXITS_AS,
    AgentRuntimeError,
    ComponentError,
    ConfigError,
    ContractError,
    ErrorKind,
    MatrixError,
    Problem,
    StoreError,
    discover_sources,
    load_config,
)
from matrix.adapters._in.cli import main
from matrix.domain.problems import PROBLEM_KINDS


def test_every_error_kind_has_an_exit_code_and_a_payload() -> None:
    """Changed requirement: the table also holds success (``ok``) and Ctrl-C
    (``interrupted``), so the CLI reads every code it exits with from it. Changed
    requirement: structured details sit under ``details``, as in ix's and recon's envelopes,
    so a detail can never overwrite ``kind``, ``message`` or ``fix``. Changed requirement
    (S6): one kind per code, as ``exit-codes.json`` agrees: ``contract`` and ``component``
    exit as ``run_failed``, ``store`` has its own code, and ``unknown`` is only a bug.
    Changed requirement: spend was removed, and with it the ``spend`` kind."""
    assert set(EXIT_CODES) | set(EXITS_AS) == set(get_args(ErrorKind)) | {"ok"}
    assert len(set(EXIT_CODES.values())) == len(EXIT_CODES)  # one kind per code
    assert EXITS_AS == {"contract": "run_failed", "component": "run_failed"}
    assert "spend" not in get_args(ErrorKind)
    error = ConfigError("bad", fix="do this", path="a.b")
    assert error.payload() == {
        "schema": "matrix.v1.error",
        "error": {
            "kind": "config",
            "message": "bad",
            "fix": "do this",
            "complete": True,
            "problems": [],
            "details": {"path": "a.b"},
        },
    }
    shadowing = ConfigError("bad", kind="not the kind").payload()["error"]
    assert (shadowing["kind"], shadowing["details"]) == ("config", {"kind": "not the kind"})
    assert (error.exit_code, AgentRuntimeError("x", reason="auth").exit_code) == (3, 6)
    assert (StoreError("x").kind, StoreError("x").exit_code) == ("store", 9)
    assert (ContractError("x").exit_code, ComponentError("x").exit_code) == (8, 8)
    assert isinstance(error, MatrixError) and isinstance(error, ValueError)


def test_the_envelope_is_the_agreed_shape() -> None:
    """S8: the envelope and its problems are those ``error-envelope.json`` agrees."""
    contract = json.loads((Path(__file__).resolve().parents[2] / "error-envelope.json").read_text())
    error = ConfigError(
        "bad", problems=[Problem(path="a", kind="unknown_key", message="m")], complete=False
    )
    body = error.payload()["error"]
    assert set(body) == set(contract["envelope"]["error"])
    assert set(body["problems"][0]) == set(contract["problem"])
    assert body["complete"] is False
    assert set(contract["problem_kinds"]) == set(PROBLEM_KINDS)
    assert MatrixError("x").payload()["error"]["fix"]  # never null: what to do next on its kind


def test_shared_and_tool_tiers_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("MATRIX_CONFIG", raising=False)
    monkeypatch.delenv("IX_CONFIG", raising=False)
    sources = discover_sources("ix", project_root=tmp_path)
    assert sources == [
        home / ".matrix" / "config.yaml",
        home / ".ix" / "config.yaml",
        tmp_path / "matrix.yaml",
        tmp_path / "ix.yaml",
    ]
    explicit = tmp_path / "x.yaml"
    explicit.write_text("matrix: {}\n")
    monkeypatch.setenv("MATRIX_CONFIG", str(explicit))
    assert discover_sources("ix", project_root=tmp_path)[-1] == explicit
    monkeypatch.setenv("IX_CONFIG", str(tmp_path / "missing.yaml"))
    with pytest.raises(ConfigError, match="IX_CONFIG"):
        discover_sources("ix", project_root=tmp_path)


def test_the_shared_file_feeds_every_tool(tmp_path: Path) -> None:
    shared = tmp_path / "matrix.yaml"
    shared.write_text("matrix:\n  agent_runtimes:\n    m: {type: mock}\n")
    tool = tmp_path / "ix.yaml"
    tool.write_text("matrix:\n  agents:\n    a: {runtime: m, system_prompt: hi}\n")
    config = load_config(None, "ix", sources=[shared, tool])
    assert set(config.matrix.agent_runtimes) == {"m"} and set(config.matrix.agents) == {"a"}
    assert config.sources == (str(shared), str(tool))


def _cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    try:
        data = json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        data = out
    return code, data, err


def test_catalog_lists_built_ins_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, data, _ = _cli(capsys, "catalog", "--json")
    assert code == 0 and data["schema"] == "matrix.v1.catalog"
    urls = {e["type_url"] for e in data["entries"]}
    assert {"matrix.v1.agent-runtime.mock", "matrix.v1.agent-step"} <= urls
    assert data["failures"] == []


def test_describe_shows_the_config_schema(capsys: pytest.CaptureFixture[str]) -> None:
    code, data, _ = _cli(capsys, "describe", "matrix.v1.agent-runtime.mock", "--json")
    assert code == 0 and data["entries"][0]["config"]["properties"]["default"]


def test_describe_unknown_is_not_found_with_a_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = _cli(capsys, "describe", "acme.v1.nothing", "--json")
    assert code == 4
    error = json.loads(err)["error"]
    assert error["kind"] == "not_found" and error["details"]["value"] == "acme.v1.nothing"
    assert "matrix.v1.agent-runtime.mock" in error["details"]["allowed"]


def test_catalog_of_an_unknown_point_is_a_usage_error_listing_the_points(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: a mistyped ``--point`` printed an empty catalog and exited 0."""
    code, data, err = _cli(capsys, "catalog", "--point", "bogus", "--json")
    assert (code, data) == (2, None)
    error = json.loads(err)["error"]
    assert error["kind"] == "usage" and error["details"]["value"] == "bogus"
    assert {"agent-runtime", "model-runtime", "component", "payload-type", "observer"} <= set(
        error["details"]["allowed"]
    )


def test_check_composes_without_calling_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "matrix.yaml").write_text(
        "matrix:\n  agent_runtimes:\n    m: {type: mock}\n"
        "  agents:\n    a: {runtime: m, system_prompt: hi}\n"
        "  inputs: [matrix.v1.task]\n"
        "  components:\n"
        "    - {name: ask, type: matrix.v1.agent-step, config: {agent: a}}\n"
    )
    code, data, _ = _cli(capsys, "check", "--json")
    assert code == 0 and data["agents"] == ["a"] and data["agent_runtimes"] == ["m"]
    assert data["model_runtimes"] == []
    assert data["components"] == ["ask"]
    code, data, _ = _cli(capsys, "agents", "--json")
    assert data["agents"][0]["runtime_type"] == "mock"
    code, data, _ = _cli(capsys, "config", "--sources", "--json")
    assert any(s.endswith("matrix.yaml") for s in data["sources"])


def test_a_bad_config_exits_3_with_the_key_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "matrix.yaml").write_text("matrix:\n  agent_runtimes:\n    m: {type: nope}\n")
    code, _, err = _cli(capsys, "check", "--json")
    assert code == 3
    error = json.loads(err)["error"]
    assert error["kind"] == "config" and "runtimes.m.type" in error["message"] and error["fix"]
    (found,) = error["problems"]
    assert (found["path"], found["kind"], found["got"]) == (
        "matrix.agent_runtimes.m.type",
        "name_unknown",
        "nope",
    )
    assert "mock" in found["allowed"] and found["source"].endswith("matrix.yaml")


def test_no_command_prints_help_and_a_usage_error_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 0 and "catalog" in capsys.readouterr().out
    assert main(["bogus"]) == 2
    assert main(["--json", "bogus"]) == 2
    document = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert document["schema"] == "matrix.v1.error" and document["error"]["kind"] == "usage"


def test_check_reports_components_that_do_not_wire(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "matrix.yaml").write_text(
        "matrix:\n  agent_runtimes:\n    m: {type: mock}\n"
        "  agents:\n    a: {runtime: m, system_prompt: hi}\n"
        "  components:\n"
        "    - {name: ask, type: matrix.v1.agent-step, config: {agent: a}}\n"
        "    - {name: lost, type: acme.v1.nothing}\n"
    )
    code, _, err = _cli(capsys, "check", "--json")
    error = json.loads(err)["error"]
    assert code == 3 and error["kind"] == "config"
    # the unknown type, and the kind nobody supplies
    assert [(p["path"], p["kind"]) for p in error["problems"]] == [
        ("matrix.components.lost.type", "name_unknown"),
        ("matrix.components.ask.requires", "unprovided"),
    ]


def test_the_agreed_kinds_keep_their_agreed_codes() -> None:
    """``components/capabilities/exit-codes.json`` holds the codes every tool agrees on."""
    fixture = Path(__file__).resolve().parents[2] / "exit-codes.json"
    agreed = json.loads(fixture.read_text())["kinds"]
    shared = set(agreed) & set(EXIT_CODES)
    assert {kind: EXIT_CODES[kind] for kind in shared} == {kind: agreed[kind] for kind in shared}
    # matrix's other kinds are its own, from 10 up; it has none, so every kind is agreed
    assert all(kind in agreed or code >= 10 for kind, code in EXIT_CODES.items())


def test_help_answers_to_h_and_lists_every_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-h"]) == 0
    text = capsys.readouterr().out
    for kind, code in EXIT_CODES.items():
        assert f"{code}  {kind}:" in text, kind


def test_a_defect_is_still_one_envelope_of_kind_unknown(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import matrix

    def broken(*_: object) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(matrix, "catalog", broken)
    code, _, err = _cli(capsys, "catalog", "--json")
    error = json.loads(err)["error"]
    assert code == 1 and error["kind"] == "unknown" and error["message"] == "RuntimeError: a bug"
    assert "bug in matrix" in error["fix"]
    assert main(["catalog"]) == 1
    text = capsys.readouterr().err
    assert "Traceback" in text and "error (unknown): RuntimeError: a bug" in text


def test_ctrl_c_is_one_envelope_of_kind_interrupted_and_exits_130(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import matrix

    def interrupted(*_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(matrix, "catalog", interrupted)
    code, _, err = _cli(capsys, "catalog", "--json")
    assert code == 130 == EXIT_CODES["interrupted"]
    assert json.loads(err) == {
        "schema": "matrix.v1.error",
        "error": {
            "kind": "interrupted",
            "message": "interrupted before the command finished",
            "fix": "run the command again",
            "complete": True,
            "problems": [],
            "details": {},
        },
    }
    assert main(["catalog"]) == 130
    assert "error (interrupted):" in capsys.readouterr().err


def test_telemetry_without_its_extra_is_a_config_error_with_a_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a missing ``matrix[otel]`` raised a bare ImportError, with no kind or fix."""
    import sys

    from matrix import configure_telemetry

    monkeypatch.setitem(sys.modules, "opentelemetry.sdk", None)  # the SDK is the extra's
    with pytest.raises(ConfigError) as raised:
        configure_telemetry(console=True)
    assert raised.value.kind == "config" and "matrix[otel]" in (raised.value.fix or "")
