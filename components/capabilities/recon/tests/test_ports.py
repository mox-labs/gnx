"""Ports carry no representation (owner ruling G5): no domain port names a filesystem path,
or a mission's directory, in any signature. Read from the source, every member of every
``Protocol`` the domain declares, so a port that grows a path fails here before it ships.

A path that is a person's own, not recon's storage, may cross a port: the project a mission
belongs to, a config file someone names, the directory a ``local`` source names, and where a
command a mission declares runs. Each is listed with its reason, and nothing else is."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

DOMAIN = Path(__file__).resolve().parents[1] / "src" / "recon" / "domain"

#: The members that may name a person's own location, each with why it is not storage.
PERSONS_OWN = {
    "MissionStore.project": "the project a mission belongs to: the person's directory",
    "MissionStore.read_config": "a config file a person names on the command line",
    "MissionStore.is_directory": "the directory a local source names in the config",
    "ShellRunner.run_lines": "where a cli command runs: a local source's directory",
    "Deliverer.deliver": "where a subscription's command runs: the project",
}

#: Names that say recon's own storage, whatever their type.
STORAGE_WORDS = re.compile(r"\b(mission_dir|archive_dir|scratch|capture_path|stream_path)\b")


def _protocols() -> dict[str, list[tuple[str, str]]]:
    """Each domain Protocol's members, by ``Class.member``: its signature as written."""
    found: dict[str, list[tuple[str, str]]] = {}
    for module in sorted(DOMAIN.glob("*.py")):
        tree = ast.parse(module.read_text())
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(ast.unparse(base).endswith("Protocol") for base in node.bases):
                continue
            members = []
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    returns = ast.unparse(item.returns) if item.returns is not None else ""
                    members.append((item.name, f"{ast.unparse(item.args)} -> {returns}"))
                elif isinstance(item, ast.AnnAssign):
                    members.append((ast.unparse(item.target), ast.unparse(item.annotation)))
            found[f"{module.stem}.{node.name}"] = members
    return found


def test_the_scan_sees_the_ports() -> None:
    """Check the checker: the ports this ruling is about are among those read."""
    names = {key.split(".", 1)[1] for key in _protocols()}
    assert {
        "Landing",
        "Hold",
        "Landed",
        "Archives",
        "ArchiveQuery",
        "MissionStore",
        "SubscriptionStore",
        "ScheduleStore",
        "CaptureLog",
        "KeyJoin",
    } <= names


@pytest.mark.parametrize("port", sorted(_protocols()))
def test_no_port_names_a_path_or_a_storage_location(port: str) -> None:
    for member, signature in _protocols()[port]:
        qualified = f"{port.split('.', 1)[1]}.{member}"
        if qualified in PERSONS_OWN:
            continue
        assert not re.search(r"\bPath\b", signature), (qualified, signature)
        assert not STORAGE_WORDS.search(signature), (qualified, signature)


def test_each_person_s_own_path_is_still_a_port_s() -> None:
    """The list stays true: each entry names a member that exists and takes a path."""
    members = {
        f"{port.split('.', 1)[1]}.{member}": signature
        for port, found in _protocols().items()
        for member, signature in found
    }
    for qualified in PERSONS_OWN:
        assert qualified in members, qualified
        assert re.search(r"\bPath\b", members[qualified]), qualified


def test_a_port_that_grows_a_path_is_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check the checker: a planted port whose member takes a ``Path`` fails the scan."""
    planted = tmp_path / "domain"
    planted.mkdir()
    (planted / "planted.py").write_text(
        "from typing import Protocol\n"
        "class Planted(Protocol):\n"
        "    def where(self, mission_dir: 'Path') -> None: ...\n"
    )
    monkeypatch.setattr(f"{__name__}.DOMAIN", planted)
    [(member, signature)] = _protocols()["planted.Planted"]
    assert member == "where" and re.search(r"\bPath\b", signature)
    with pytest.raises(AssertionError):
        test_no_port_names_a_path_or_a_storage_location("planted.Planted")
