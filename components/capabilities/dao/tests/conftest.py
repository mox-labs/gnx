"""The project every dao test starts from.

It is the worked organization in the skill's `references/documents.md`, read from that file,
so the documented formats, the audit and the book cannot drift apart.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from dao.domain.spec import SPEC
from dao.skill_files import skill

if TYPE_CHECKING:
    from pathlib import Path

_EXAMPLE = re.compile(r"^## (\S+\.md)\n.*?^```markdown\n(.*?)^```", re.S | re.M)
POINTER = "This project is run as a dao. Read `dao/README.md` at the start of a session.\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """The documented example, written out as a project."""
    examples = dict(_EXAMPLE.findall(skill("documents")))
    assert set(examples) == {e.file for e in SPEC}, "documents.md must show all six"
    (tmp_path / "dao").mkdir()
    for name, body in examples.items():
        (tmp_path / "dao" / name).write_text(body, encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text(POINTER, encoding="utf-8")
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "cold-reader.md").write_text("---\nname: cold-reader\n---\nRead the diff.\n")
    return tmp_path
