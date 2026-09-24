"""The skill text an agent reads via ``modelrt --skill`` (the recon pattern)."""

from __future__ import annotations

from importlib.resources import files


def get_skill() -> str:
    return files("modelrt").joinpath("assets/SKILL.md").read_text()
