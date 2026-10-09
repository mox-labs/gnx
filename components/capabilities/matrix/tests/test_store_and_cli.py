"""Stores keep runs as typed JSONL tables; the CLI inspects without running; errors are data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from matrix import (
    EXIT_CODES,
    AgentRuntimeError,
    ConfigError,
    Executor,
    Flow,
    Inputs,
    JsonlConstructStore,
    MatrixError,
    Member,
    MemoryConstructStore,
    NotFoundError,
    compile_flow,
    discover_sources,
    load_config,
)
from matrix.adapters._in.cli import main

TEXT = "acme.v1.text"


class Emit:
    requires: dict[str, Any] = {}
    provides = {"out": TEXT}

    def __init__(self, value: Any) -> None:
        self.value = value

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        return {"out": self.value}


async def _run() -> Any:
    flow = Flow(
        "s",
        (Member("a", Emit({"n": 1}), {"out": "t"}), Member("b", Emit({"n": 2}), {"out": "t"})),
    )
    return await Executor().run(compile_flow(flow))


async def test_jsonl_round_trip_keeps_order_types_and_provenance(tmp_path: Path) -> None:
    run = await _run()
    store = JsonlConstructStore(tmp_path)
    where = Path(store.save(run))
    record = json.loads((where / "run.json").read_text())
    assert record["schema"] == "matrix.v1.run" and record["status"] == "completed"
    assert record["tables"]["t"] == {"type_url": TEXT, "rows": 2, "file": "t.jsonl"}
    loaded = store.load(run.run_id)
    assert loaded.values("t") == ({"n": 1}, {"n": 2})
    assert [a.producer for a in loaded.rows("t")] == ["a", "b"]
    assert store.runs() == [run.run_id]


async def test_runs_are_never_rewritten(tmp_path: Path) -> None:
    run = await _run()
    store = JsonlConstructStore(tmp_path)
    store.save(run)
    with pytest.raises(ConfigError, match="never rewritten"):
        store.save(run)
    memory = MemoryConstructStore()
    memory.save(run)
    with pytest.raises(ConfigError):
        memory.save(run)


def test_missing_runs_and_unsafe_ids(tmp_path: Path) -> None:
    store = JsonlConstructStore(tmp_path)
    with pytest.raises(NotFoundError, match="Runs: \\(none\\)"):
        store.load("nope")
    with pytest.raises(ConfigError, match="safe"):
        store.load("../etc")


def test_every_error_kind_has_an_exit_code_and_a_payload() -> None:
    assert set(EXIT_CODES) == {
        "config",
        "not_found",
        "contract",
        "component",
        "transient",
        "auth",
        "unknown",
    }
    error = ConfigError("bad", fix="do this", path="a.b")
    assert error.payload() == {
        "error": {"kind": "config", "message": "bad", "fix": "do this", "path": "a.b"}
    }
    assert (error.exit_code, AgentRuntimeError("x", reason="auth").exit_code) == (3, 6)
    assert isinstance(error, MatrixError) and isinstance(error, ValueError)


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
    shared.write_text("matrix:\n  runtimes:\n    m: {type: mock}\n")
    tool = tmp_path / "ix.yaml"
    tool.write_text("matrix:\n  agents:\n    a: {runtime: m, system_prompt: hi}\n")
    config = load_config(None, "ix", sources=[shared, tool])
    assert set(config.matrix.runtimes) == {"m"} and set(config.matrix.agents) == {"a"}
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
    assert {"matrix.v1.runtime.mock", "matrix.v1.agent-step"} <= urls
    assert data["failures"] == []


def test_describe_shows_the_config_schema(capsys: pytest.CaptureFixture[str]) -> None:
    code, data, _ = _cli(capsys, "describe", "matrix.v1.runtime.mock", "--json")
    assert code == 0 and data["entries"][0]["config"]["properties"]["default"]


def test_describe_unknown_is_not_found_with_a_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = _cli(capsys, "describe", "acme.v1.nothing", "--json")
    assert code == 4
    assert json.loads(err)["error"]["kind"] == "not_found"


def test_check_composes_without_calling_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "matrix.yaml").write_text(
        "matrix:\n  runtimes:\n    m: {type: mock}\n"
        "  agents:\n    a: {runtime: m, system_prompt: hi}\n"
    )
    code, data, _ = _cli(capsys, "check", "--json")
    assert code == 0 and data["agents"] == ["a"] and data["runtimes"] == ["m"]
    code, data, _ = _cli(capsys, "agents", "--json")
    assert data["agents"][0]["runtime_type"] == "mock"
    code, data, _ = _cli(capsys, "config", "--sources", "--json")
    assert any(s.endswith("matrix.yaml") for s in data["sources"])


def test_a_bad_config_exits_3_with_the_key_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "matrix.yaml").write_text("matrix:\n  runtimes:\n    m: {type: nope}\n")
    code, _, err = _cli(capsys, "check", "--json")
    assert code == 3
    error = json.loads(err)["error"]
    assert error["kind"] == "config" and "runtimes.m.type" in error["message"] and error["fix"]


def test_usage_errors_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert main(["bogus"]) == 2
