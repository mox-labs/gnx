"""Transforms — the built-in field transforms + the normalize spec engine.

Normalize specs map output column names to source paths with optional transforms:
  title: title                          # direct field
  abstract: summary                     # rename
  pdf_url: "openAccessPdf.url"          # nested access
  authors: "authors.*.name"             # list-map
  abstract: "description|$html2text"    # access + transform

Path resolution uses glom. Our '*' list-map syntax translates to glom tuple specs.
The '|$transform' pipe syntax is ours — applied after path resolution.

Transforms are extensions: each is registered under the entry-point group
``recon.transforms`` (the built-ins below included) and looked up by name without the
``$``. This module holds no registry of its own; callers pass the mapping they loaded, and
``BUILTIN_TRANSFORMS`` is the default for code that runs without the composition root.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

from glom import GlomError, glom

from recon.domain.exceptions import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from recon.domain.collector import Transform

# --- Transforms ---


class _TextExtractor(HTMLParser):
    """HTML text extractor via stdlib."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)


def html2text(html: str) -> str:
    """Strip HTML tags, scripts, styles. Preserve paragraph breaks."""
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    lines = "".join(parser._parts).splitlines()
    return "\n".join(line.strip() for line in lines if line.strip())


def inverted_index(index: dict[str, list[int]] | None) -> str:
    """Reconstruct text from OpenAlex inverted index format."""
    if not index:
        return ""
    word_positions: list[tuple[int, str]] = []
    for word, positions in index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(word for _, word in word_positions)


def join(items: list[object] | None, separator: str = ", ") -> str:
    """Join list elements into a string."""
    if not items:
        return ""
    return separator.join(str(item) for item in items)


def first(items: list[object] | None) -> object | None:
    """Return first element or None."""
    if not items:
        return None
    return items[0]


#: The pure built-ins. `markitdown` is also built in, but it is an adapter (it reads a
#: file), so it lives in adapters/_out and reaches the engine only through the
#: entry-point group.
BUILTIN_TRANSFORMS: Mapping[str, Transform] = {
    "html2text": html2text,
    "inverted_index": inverted_index,
    "join": join,
    "first": first,
}


# --- Normalize Spec Engine ---


def _build_glom_spec(path: str) -> Any:
    """Translate our path syntax to a glom spec.

    Simple:  "title"                         → "title"
    Dotted:  "openAccessPdf.url"             → "openAccessPdf.url"
    ListMap: "authors.*.name"                → ("authors", ["name"])
    Deep:    "authorships.*.author.name"     → ("authorships", ["author.name"])
    Bare:    "items.*"                       → "items"
    """
    if "*" not in path:
        return path

    if path == "*":
        return path  # glom iterates dict values — likely a config mistake, but valid

    if path.endswith(".*"):
        return path[:-2]

    parts = path.split(".*.", maxsplit=1)
    if len(parts) == 2:
        if "*" in parts[1]:
            msg = f"Nested list-maps not supported: {path!r}"
            raise ValueError(msg)
        return (parts[0], [parts[1]])

    return path


def resolve_path(data: Any, path: str) -> Any:
    """Navigate a dotted path with list-map support via '*'.

    Uses glom for path resolution with our '*' list-map syntax on top.
    """
    spec = _build_glom_spec(path)
    try:
        return glom(data, spec)
    except (GlomError, KeyError, TypeError):
        return None


def parse_expression(expr: str) -> tuple[str, str | None]:
    """Split ``path|$transform`` into (path, transform name without ``$``, or None).

    Raises ValueError when the part after the pipe is not ``$name``.
    """
    if "|" not in expr:
        return expr.strip(), None
    path, _, transform = expr.rpartition("|")
    transform = transform.strip()
    if not transform.startswith("$") or len(transform) < 2:
        msg = f"after '|' expected a transform like $html2text, got {transform!r}"
        raise ValueError(msg)
    return path.strip(), transform[1:]


def spec_problems(spec: Mapping[str, Any], transforms: Mapping[str, Transform]) -> dict[str, str]:
    """Every problem in a normalize spec, by output column. Empty when the spec is sound.

    Checked before a run so that an unknown transform or an unsupported path is a config
    error at validation, never a silently skipped transform mid-survey.
    """
    problems: dict[str, str] = {}
    for column, expr in spec.items():
        if not isinstance(expr, str):
            problems[column] = f"expected a path string, got {type(expr).__name__}"
            continue
        try:
            path, transform = parse_expression(expr)
            _build_glom_spec(path)
        except ValueError as exc:
            problems[column] = str(exc)
            continue
        if transform is not None and transform not in transforms:
            installed = ", ".join(f"${name}" for name in sorted(transforms)) or "(none)"
            problems[column] = f"unknown transform ${transform}; installed: {installed}"
    return problems


def apply_normalize(
    raw: dict[str, Any],
    spec: Mapping[str, str],
    transforms: Mapping[str, Transform] = BUILTIN_TRANSFORMS,
) -> dict[str, Any]:
    """Apply a normalize spec to a raw dict.

    Each spec entry is: output_column: "source.path|$transform"
    The pipe and transform are optional. An unknown transform raises ConfigError; the
    planner reports it before any run, so reaching it here means validation was skipped.
    """
    result: dict[str, Any] = {}
    for output_col, expr in spec.items():
        try:
            path, transform_name = parse_expression(expr)
        except ValueError as exc:
            raise ConfigError(f"normalize.{output_col}: {exc}") from exc
        value = resolve_path(raw, path)
        if transform_name is not None:
            transform = transforms.get(transform_name)
            if transform is None:
                msg = f"normalize.{output_col}: unknown transform ${transform_name}"
                raise ConfigError(msg)
            value = transform(value)
        result[output_col] = value
    return result
