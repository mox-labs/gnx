"""The text ``recon --skill`` prints, and the reference texts ``--skill -r <name>`` prints.

They are the package's ``assets/skills/recon/SKILL.md`` and ``references/<name>.md``,
printed as written. The plugin copy under ``plugins/recon/skills/recon`` is generated from
this command, and the tests hold the text to the code: its commands, exit codes, error
kinds, version, and every mission it shows.
"""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

from recon.domain.errors import UsageError

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable


def _skill_dir() -> Traversable:
    return resources.files("recon.assets") / "skills" / "recon"


def skill_references() -> list[str]:
    """The names ``skill(reference)`` takes, sorted."""
    files = (_skill_dir() / "references").iterdir()
    return sorted(f.name.removesuffix(".md") for f in files if f.name.endswith(".md"))


def skill(reference: str | None = None) -> str:
    """The skill text, or the reference text named. Raises UsageError for an unknown name."""
    if reference is None:
        return (_skill_dir() / "SKILL.md").read_text(encoding="utf-8")
    available = skill_references()
    if reference not in available:
        raise UsageError(
            f"no skill reference {reference!r}",
            fix=f"recon --skill -r <name>, with one of: {', '.join(available)}",
            details={"value": reference, "allowed": available},
        )
    return (_skill_dir() / "references" / f"{reference}.md").read_text(encoding="utf-8")
