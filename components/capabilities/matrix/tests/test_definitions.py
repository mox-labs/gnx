"""Claude Code agent files are matrix definitions without translation."""

from __future__ import annotations

from pathlib import Path

import pytest

from matrix import ConfigError
from matrix.adapters._out.definitions.markdown import MarkdownDefinitionSource, parse_definition


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def test_frontmatter_and_body(tmp_path: Path) -> None:
    d = parse_definition(
        _write(
            tmp_path,
            "x.md",
            "---\nname: rev\ndescription: d\ntools: Read, Grep\nmodel: opus\n"
            "maxTurns: 4\ncolor: red\n---\nBody here.\n",
        )
    )
    assert (d.name, d.description, d.system_prompt) == ("rev", "d", "Body here.")
    assert (d.tools, d.model, d.max_turns, d.metadata) == (
        ("Read", "Grep"),
        "opus",
        4,
        {"color": "red"},
    )


def test_tools_as_list_and_absent(tmp_path: Path) -> None:
    listed = parse_definition(_write(tmp_path, "a.md", "---\ntools: [Read]\n---\nx"))
    absent = parse_definition(_write(tmp_path, "b.md", "---\ndescription: y\n---\nx"))
    assert listed.tools == ("Read",) and absent.tools is None
    assert absent.name == "b"  # name defaults to the file stem


def test_no_frontmatter_is_all_prompt(tmp_path: Path) -> None:
    d = parse_definition(_write(tmp_path, "plain.md", "Just a prompt."))
    assert (d.name, d.system_prompt) == ("plain", "Just a prompt.")


def test_unclosed_frontmatter_names_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="bad.md: frontmatter opened"):
        parse_definition(_write(tmp_path, "bad.md", "---\nname: x\nno close"))


def test_bad_tools_names_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="t.md: tools must be"):
        parse_definition(_write(tmp_path, "t.md", "---\ntools: 3\n---\nx"))


def test_invalid_field_names_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="m.md: invalid agent definition: max_turns"):
        parse_definition(_write(tmp_path, "m.md", "---\nmax_turns: 0\n---\nx"))


def test_duplicate_names_in_one_directory(tmp_path: Path) -> None:
    _write(tmp_path, "one.md", "---\nname: same\n---\nx")
    _write(tmp_path, "two.md", "---\nname: same\n---\ny")
    with pytest.raises(ConfigError, match="'same' defined twice"):
        MarkdownDefinitionSource(tmp_path).load()


def test_reads_every_real_agent_file_in_the_repo() -> None:
    """Every agent file gnx holds — incubator and projected plugins — loads as a definition.

    41 files written by hand for Claude Code, in the wild: block-scalar descriptions, comma
    and list tool forms, extra keys like color. If one fails, the loader is wrong for the
    format it claims to read.
    """
    root = Path(__file__).resolve().parents[4]
    agents = sorted(root.glob("incubator/agents/*/agent.md")) + sorted(
        root.glob("plugins/*/agents/*.md")
    )
    if not agents:
        pytest.skip("no agent files in this checkout")
    for path in agents:
        d = parse_definition(path)
        assert d.system_prompt, path
        assert d.name, path
