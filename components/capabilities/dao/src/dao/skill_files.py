"""The text `dao --skill` prints, and the reference texts `--skill -r <name>` prints.

They are the package's `assets/skills/dao/SKILL.md` and `references/<name>.md`, printed as
written. The plugin under `plugins/dao` is projected from the same files, and the tests
audit the organization `references/documents.md` shows, so the text and the audit agree.
"""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable


def _skill_dir() -> Traversable:
    return resources.files("dao.assets") / "skills" / "dao"


def skill_references() -> list[str]:
    """The names `skill(reference)` takes, sorted."""
    files = (_skill_dir() / "references").iterdir()
    return sorted(f.name.removesuffix(".md") for f in files if f.name.endswith(".md"))


def skill(reference: str | None = None) -> str:
    """The skill text, or the reference text named. Raises KeyError for an unknown name."""
    if reference is None:
        return (_skill_dir() / "SKILL.md").read_text(encoding="utf-8")
    if reference not in skill_references():
        raise KeyError(reference)
    return (_skill_dir() / "references" / f"{reference}.md").read_text(encoding="utf-8")
