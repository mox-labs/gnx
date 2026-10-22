"""The CLI's contracts with its siblings: the agreed exit codes and the agreed error envelope.

``components/capabilities/exit-codes.json`` and ``error-envelope.json`` are the workspace's,
shared by matrix, ix, recon and hardline; these are the two tests each of them has.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest

from hardline import ConfigError, Problem
from hardline.adapters._in import cli
from hardline.adapters._in.cli import (
    EXIT_CODES,
    EXIT_MEANINGS,
    EXIT_NEXT,
    PROBLEM_KINDS,
    ErrorKind,
    error_payload,
    main,
)

CONTRACTS = Path(__file__).resolve().parents[2]


def _contract(name: str) -> dict[str, Any]:
    """One of the workspace's shared contracts, beside the packages in the repository."""
    path = CONTRACTS / name
    if not path.is_file():
        pytest.skip(f"not run from the repository: no {path}")
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def test_the_agreed_kinds_keep_their_agreed_codes() -> None:
    """A kind the tools share exits with the agreed code; a kind of hardline's own takes a code
    from 10 up; one kind to a code (W10.4). Before: rate_limit, timeout and unavailable all on
    5, and bad_request on 3, beside config."""
    agreed = _contract("exit-codes.json")["kinds"]
    shared = set(agreed) & set(EXIT_CODES)
    assert {kind: EXIT_CODES[kind] for kind in shared} == {kind: agreed[kind] for kind in shared}
    own = {kind: code for kind, code in EXIT_CODES.items() if kind not in agreed}
    assert all(10 <= code < 128 for code in own.values()), own
    assert len(set(EXIT_CODES.values())) == len(EXIT_CODES)  # one kind to a code
    assert set(EXIT_CODES) - {"ok"} == set(get_args(ErrorKind))
    assert set(EXIT_CODES) == set(EXIT_MEANINGS) == set(EXIT_NEXT)


def test_the_envelope_is_the_agreed_shape() -> None:
    contract = _contract("error-envelope.json")
    assert "hardline" in contract["description"], "the contract names the tools it binds"
    problem = Problem(path="models.q.famly", kind="unknown_key", message="m", source="cwd x")
    payload, code = error_payload(ConfigError("bad", problems=[problem]))
    assert code == 3 and list(payload) == list(contract["envelope"]) == ["schema", "error"]
    assert payload["schema"] == "hardline.v1.error"
    body = payload["error"]
    assert list(body) == list(contract["envelope"]["error"])
    assert set(body["problems"][0]) == set(contract["problem"])
    assert body["complete"] is True and body["details"] == {} and body["fix"]
    # hardline names a shared problem kind by its agreed name, and adds its own beside them
    own = PROBLEM_KINDS - set(contract["problem_kinds"])
    assert own == {"shared_fate", "secret_unresolved"}


def test_a_defect_is_still_one_envelope_of_kind_unknown(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # W10.1: an exception hardline did not mean to raise escaped as a traceback, exit 1, with no
    # JSON line under --json, though the help promised one for every failure.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(cli, "load_registry", broken)
    assert main(["models", "--json"]) == 1
    payload = json.loads(capsys.readouterr().err)
    error = payload["error"]
    assert error["kind"] == "unknown" and error["message"] == "RuntimeError: a bug"
    assert "bug in hardline" in error["fix"]
    assert main(["models"]) == 1
    text = capsys.readouterr().err
    assert "Traceback" in text and "hardline: error (unknown): RuntimeError: a bug" in text


def test_the_cli_says_what_each_code_means_in_its_skill() -> None:
    from hardline import get_skill

    skill = get_skill()
    for kind, code in EXIT_CODES.items():
        assert f"| {code} | `{kind}` |" in skill, kind


def test_under_json_stderr_keeps_to_the_envelope(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A broken backend plugin no row names is logged as a warning when backends are found.
    # Under --json that line would sit on stderr beside, or instead of, the one JSON line a
    # caller parses; without --json it is said.
    from importlib.metadata import entry_points

    installed = list(entry_points(group="hardline.backends"))

    class Broken:
        name, value = "broken", "acme:Broken"

        def load(self) -> Any:
            raise ImportError("No module named 'acme'")

    monkeypatch.setattr("hardline.composition.entry_points", lambda group: [*installed, Broken()])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "hardline.yaml").write_text("models:\n  q: {backend: mock, model: m, family: x}\n")
    assert main(["models", "--json"]) == 0
    assert capsys.readouterr().err == ""
    assert main(["models"]) == 0
    assert "broken" in capsys.readouterr().err
