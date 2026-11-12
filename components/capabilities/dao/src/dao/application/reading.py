"""Read an organization's documents into domain types.

The documents are Markdown a person reads on a forge, so the grammar is the smallest one
that still parses the same way every time:

- a document's parts are its `## ` sections;
- a position is one section of `positions.md`, with `Does:`, `Must not:` and `Checked by:`
  fields (a field may be bold, or a list item, and may run over several lines);
- participants are the rows of the first table in `participants.md` that has a
  `Participant` and a `Position` column.

Everything here returns what it found and nothing it guessed. A row or a field that does
not parse is left out, and the audit reports what is missing.
"""

from __future__ import annotations

import re
from pathlib import Path

from dao.domain.models import Kind, Participant, Position

_H2 = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_BOLD = r"(?:\*\*|__)?"
_FIELD = re.compile(
    rf"^\s*(?:[-*+]\s+)?{_BOLD}\s*(does|must not|checked by)\s*{_BOLD}\s*:\s*{_BOLD}\s*(.*)$",
    re.I,
)
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_NOBODY = {"", "-", "—", "–", "none", "nobody", "no one", "n/a"}

_KINDS = {
    "human": Kind.HUMAN,
    "person": Kind.HUMAN,
    "agent": Kind.AGENT,
}


def read_text(path: Path) -> str:
    """A file's text, or the empty string when it is absent or not UTF-8."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def norm(name: str) -> str:
    """How two spellings of one name compare equal: case, code ticks and spacing ignored."""
    return " ".join(name.replace("`", "").replace("*", "").split()).lower()


def sections(text: str) -> dict[str, str]:
    """The `## ` sections of a document: heading (as written) to body.

    A heading inside a fenced code block is not a heading.
    """
    out: dict[str, str] = {}
    current: str | None = None
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        m = None if fenced else _H2.match(line)
        if m:
            current = m.group(1).strip()
            out[current] = ""
        elif current is not None:
            out[current] += line + "\n"
    return {k: v.strip() for k, v in out.items()}


def find_section(parts: dict[str, str], pattern: str) -> tuple[str, str] | None:
    """The first section whose heading matches `pattern`, as (heading, body)."""
    rx = re.compile(pattern, re.I)
    for heading, body in parts.items():
        if rx.search(heading):
            return heading, body
    return None


def list_items(text: str) -> int:
    """How many list items a document has, outside fenced code."""
    count = 0
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and _LIST_ITEM.match(line):
            count += 1
    return count


def _names(value: str) -> tuple[str, ...]:
    """A cell or field holding one or more names, split on commas and 'and'."""
    parts = re.split(r",|\band\b|;", value)
    cleaned = (norm(p).strip(" .") for p in parts)
    return tuple(p for p in cleaned if p not in _NOBODY)


def read_positions(text: str) -> list[Position]:
    """The positions in `positions.md`, one per `## ` section."""
    positions: list[Position] = []
    for heading, body in sections(text).items():
        fields: dict[str, list[str]] = {"does": [], "must not": [], "checked by": []}
        current: str | None = None
        for line in body.splitlines():
            m = _FIELD.match(line)
            if m:
                current = m.group(1).lower()
                fields[current].append(m.group(2).strip())
            elif current is not None:
                fields[current].append(line.strip())
        positions.append(
            Position(
                name=norm(heading),
                does=" ".join(x for x in fields["does"] if x),
                must_not=" ".join(x for x in fields["must not"] if x),
                checked_by=_names(" ".join(fields["checked by"])),
            )
        )
    return positions


def _cells(line: str) -> list[str] | None:
    m = _TABLE_ROW.match(line)
    return [c.strip() for c in m.group(1).split("|")] if m else None


def _cell(cells: list[str], columns: dict[str, int], name: str) -> str:
    i = columns.get(name)
    return cells[i] if i is not None and i < len(cells) else ""


def read_participants(text: str) -> list[Participant] | None:
    """The participants in `participants.md`.

    None when the document has no table with a Participant and a Position column, which is
    a different finding from a table with no rows.
    """
    columns: dict[str, int] | None = None
    participants: list[Participant] = []
    for line in text.splitlines():
        cells = _cells(line)
        if cells is None:
            if columns is not None:
                break  # the table ended
            continue
        if columns is None:
            header = [norm(c) for c in cells]
            if "participant" in header and "position" in header:
                columns = {name: i for i, name in enumerate(header)}
            continue
        if _TABLE_RULE.match(line):
            continue

        name = _cell(cells, columns, "participant").replace("`", "").replace("*", "").strip()
        if not name:
            continue
        defined_in = _cell(cells, columns, "defined in").replace("`", "").strip()
        participants.append(
            Participant(
                name=name,
                kind=_KINDS.get(norm(_cell(cells, columns, "kind"))),
                positions=_names(_cell(cells, columns, "position")),
                defined_in="" if norm(defined_in) in _NOBODY else defined_in,
            )
        )
    return None if columns is None else participants
