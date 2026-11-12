"""The command surface CONTRACT.md promises: exit codes, and the skill text as written."""

from __future__ import annotations

from importlib import metadata
from typing import TYPE_CHECKING

from click.testing import CliRunner

from dao.adapters._in.cli import main
from dao.skill_files import skill, skill_references

if TYPE_CHECKING:
    from pathlib import Path


def test_check_exits_1_on_an_empty_project(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["check", str(tmp_path)])
    assert result.exit_code == 1
    assert "INCOMPLETE" in result.output
    assert "PRESENT is not a review" in result.output


def test_check_exits_2_when_the_project_is_not_a_directory(tmp_path: Path) -> None:
    assert CliRunner().invoke(main, ["check", str(tmp_path / "nope")]).exit_code == 2


def test_skill_prints_the_text_as_written() -> None:
    result = CliRunner().invoke(main, ["--skill"])
    assert result.exit_code == 0
    assert result.output == skill()


def test_skill_reference_and_its_misuse() -> None:
    assert skill_references() == ["documents", "findings"]
    result = CliRunner().invoke(main, ["--skill", "-r", "documents"])
    assert result.output == skill("documents")
    assert CliRunner().invoke(main, ["-r", "documents"]).exit_code == 2
    assert CliRunner().invoke(main, ["--skill", "-r", "nope"]).exit_code == 2


def test_the_skill_names_the_version_it_ships_with() -> None:
    version = metadata.version("dao")
    text = skill()
    assert f'version: "{version}"' in text
    assert f"# dao {version}:" in text
    assert f"gnx@dao-v{version}#" in text


def test_spec_and_policy_exit_0() -> None:
    assert CliRunner().invoke(main, ["spec", "--findings"]).exit_code == 0
    result = CliRunner().invoke(main, ["policy"])
    assert result.exit_code == 0
    assert "position_remit_min_chars" in result.output
