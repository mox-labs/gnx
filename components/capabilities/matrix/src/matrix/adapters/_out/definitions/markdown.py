"""MarkdownDefinitionSource — a directory of agent files in Claude Code's format.

    ---
    name: reviewer
    description: Reviews a diff for correctness.
    tools: Read, Grep, Glob          # comma string or YAML list; omit for the runtime default
    model: sonnet                    # optional
    max_turns: 3                     # optional (maxTurns also accepted)
    color: blue                      # anything else is kept in metadata, never interpreted
    ---
    You are a careful reviewer...     # the body is the system prompt

So a plugin's ``agents/`` directory is a matrix definition source without translation.
Errors name the file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from matrix.domain.agent import AgentDefinition
from matrix.domain.errors import ConfigError

if TYPE_CHECKING:
    from pathlib import Path

_INTERPRETED = {"name", "description", "tools", "model", "max_turns", "maxTurns"}


def _split(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise ConfigError(f"{path}: frontmatter opened with '---' but never closed")
    head = parts[0][3:]
    body = parts[1].split("\n", 1)[1] if "\n" in parts[1] else ""
    try:
        meta = yaml.safe_load(head) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: frontmatter is not valid YAML: {e}") from e
    if not isinstance(meta, dict):
        raise ConfigError(f"{path}: frontmatter must be a mapping")
    return meta, body.strip()


def _tools(value: Any, path: Path) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return tuple(t.strip() for t in value.split(",") if t.strip())
    if isinstance(value, list) and all(isinstance(t, str) for t in value):
        return tuple(value)
    raise ConfigError(f"{path}: tools must be a comma-separated string or a list of names")


def parse_definition(path: Path) -> AgentDefinition:
    meta, body = _split(path.read_text(), path)
    fields: dict[str, Any] = {
        "name": str(meta.get("name", path.stem)),
        "description": str(meta.get("description", "")),
        "system_prompt": body,
        "model": meta.get("model"),
        "tools": _tools(meta.get("tools"), path),
        "metadata": {k: v for k, v in meta.items() if k not in _INTERPRETED},
    }
    turns = meta.get("max_turns", meta.get("maxTurns"))
    if turns is not None:
        fields["max_turns"] = turns
    try:
        return AgentDefinition.model_validate(fields)
    except ValidationError as e:
        issues = "; ".join(f"{'.'.join(map(str, i['loc']))}: {i['msg']}" for i in e.errors())
        raise ConfigError(f"{path}: invalid agent definition: {issues}") from None


class MarkdownDefinitionSource:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def load(self) -> list[AgentDefinition]:
        if not self._dir.is_dir():
            raise ConfigError(f"definitions directory {self._dir} does not exist")
        definitions = [parse_definition(p) for p in sorted(self._dir.glob("*.md"))]
        seen: dict[str, Path] = {}
        for d, p in zip(definitions, sorted(self._dir.glob("*.md")), strict=True):
            if d.name in seen:
                raise ConfigError(f"agent name {d.name!r} defined twice: {seen[d.name]} and {p}")
            seen[d.name] = p
        return definitions

    def describe(self) -> str:
        return str(self._dir)
