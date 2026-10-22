"""The normalize language: paths into a record, the spec that maps them to columns, and the
built-in transforms.

A spec maps each output column to ``path`` or ``path|$transform``::

    title: title                          # a key
    pdf_url: "openAccessPdf.url"          # a nested key
    first_author: "authors.0.name"        # a list index
    authors: "authors.*.name"             # the path after ``*`` taken from every item
    abstract: "description|$html2text"    # resolve, then transform

A path that does not resolve gives ``None``, never an error: one record lacking a field must
not fail a table. A list-map gives ``None`` when any item lacks the rest of the path. One
``*`` per path; a second is a config error, reported by :func:`spec_problems` before a run.

Transforms are extensions registered under the entry-point group ``recon.transforms`` and
looked up by name without the ``$``; the pure built-ins live here. A built-in given a value
of a type it does not take gives ``None``, as a path that does not resolve does: one record
whose field has an unexpected type must not fail a table.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

from recon.domain.errors import ConfigError, NoRecordsError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from recon.domain.collector import Transform

_LIST_MAP = "*"


class _UnresolvedError(Exception):
    """The path does not resolve in this value."""


def _step(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        raise _UnresolvedError
    if isinstance(value, list) and key.isdigit() and int(key) < len(value):
        return value[int(key)]
    raise _UnresolvedError


def _walk(value: Any, keys: list[str]) -> Any:
    for index, key in enumerate(keys):
        if key != _LIST_MAP:
            value = _step(value, key)
            continue
        items = list(value.values()) if isinstance(value, dict) else value
        if not isinstance(items, list):
            raise _UnresolvedError
        rest = keys[index + 1 :]
        return [_walk(item, rest) for item in items] if rest else items
    return value


def path_problem(path: str) -> str | None:
    """Why ``path`` is not a path recon can resolve, or None."""
    if path.split(".").count(_LIST_MAP) > 1:
        return f"Nested list-maps not supported: {path!r}"
    return None


def resolve_path(data: Any, path: str) -> Any:
    """The value at ``path`` in ``data``, or None when it does not resolve."""
    if path_problem(path) is not None:
        raise ValueError(path_problem(path))
    try:
        return _walk(data, path.split("."))
    except _UnresolvedError:
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
        raise ValueError(f"after '|' expected a transform like $html2text, got {transform!r}")
    return path.strip(), transform[1:]


def spec_problems(spec: Mapping[str, Any], transforms: Mapping[str, Transform]) -> dict[str, str]:
    """Every problem in a normalize spec, by output column. Empty when the spec is sound.

    Checked before a run, so an unknown transform or an unsupported path is a config error at
    validation, never a column silently left empty mid-survey.
    """
    problems: dict[str, str] = {}
    for column, expr in spec.items():
        if not isinstance(expr, str):
            problems[column] = f"expected a path string, got {type(expr).__name__}"
            continue
        try:
            path, transform = parse_expression(expr)
        except ValueError as exc:
            problems[column] = str(exc)
            continue
        if (problem := path_problem(path)) is not None:
            problems[column] = problem
        elif transform is not None and transform not in transforms:
            installed = ", ".join(f"${name}" for name in sorted(transforms)) or "(none)"
            problems[column] = f"unknown transform ${transform}; installed: {installed}"
    return problems


def apply_normalize(
    raw: dict[str, Any],
    spec: Mapping[str, str],
    transforms: Mapping[str, Transform] | None = None,
) -> dict[str, Any]:
    """The record ``spec`` makes of ``raw``: one key per column, in the spec's order.
    ``transforms`` defaults to the pure built-ins.

    Raises ConfigError for a spec :func:`spec_problems` would have reported; reaching that
    means validation was skipped.
    """
    transforms = BUILTIN_TRANSFORMS if transforms is None else transforms
    result: dict[str, Any] = {}
    for column, expr in spec.items():
        try:
            path, transform_name = parse_expression(expr)
            value = resolve_path(raw, path)
        except ValueError as exc:
            raise ConfigError(f"normalize.{column}: {exc}") from exc
        if transform_name is not None:
            transform = transforms.get(transform_name)
            if transform is None:
                raise ConfigError(f"normalize.{column}: unknown transform ${transform_name}")
            value = transform(value)
        result[column] = value
    return result


def to_records(data: Any, extract: str | None) -> list[dict[str, Any]]:
    """The records in a parsed body: the value at ``extract`` (or the body), as a list of
    objects. A single object is one record; anything in a list that is not an object is
    dropped. An empty body, or an empty list at ``extract``, has no records.

    Raises NoRecordsError when a body that is not empty gives none: ``extract`` resolves to
    nothing (absent, or null) in it, or what it names holds no object (a list of ids, a
    string), so an empty table is never mistaken for a source that had nothing.
    """
    where = f"extract: {extract}" if extract else "the response"
    if extract and isinstance(data, dict | list):
        found = resolve_path(data, extract)
        if found is None and data:
            raise NoRecordsError(
                f"extract: {extract} resolved to nothing in a response that is not empty; "
                f"its top level holds {_shape(data)}",
                problem_kind="extract_unresolved",
                field="extract",
                fix="set extract: to the path of the records (survey once without it to see "
                "the response whole), or read the response for an error the source sent with "
                "a success status; a source that leaves the key out when it has no results "
                "gives this too",
            )
        data = found
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        records = [item for item in data if isinstance(item, dict)]
        if data and not records:
            raise _not_objects(f"the {len(data)} items at {where} are {_types(data)}, not objects")
        return records
    if data is None or data == "":
        return []
    raise _not_objects(f"{where} is a {type(data).__name__}, not an object or a list of them")


def _not_objects(message: str) -> NoRecordsError:
    return NoRecordsError(
        f"{message}: a record is an object",
        problem_kind="records_not_objects",
        field="extract",
        fix="point extract: at a list of objects (or at one object), not at its ids or names",
    )


def _shape(data: dict[str, Any] | list[Any]) -> str:
    if isinstance(data, list):
        return f"a list of {len(data)}"
    keys = sorted(map(str, data))
    return ", ".join(keys[:8]) + (f" and {len(keys) - 8} more" if len(keys) > 8 else "")


def _types(items: list[Any]) -> str:
    return " and ".join(sorted({type(item).__name__ for item in items}))


def nested_records(record: dict[str, Any], *, depth: int = 4) -> tuple[str, int] | None:
    """The path to the longest list of two or more objects inside ``record``, and how many
    objects it holds; None when there is none. Only objects are searched, ``depth`` keys
    deep: a list of records is the response's spine (arXiv's ``feed.entry``), not something
    inside a record.

    A source that returns one record holding such a list was most likely read without the
    ``extract:`` that names it.
    """
    best: tuple[str, int] | None = None
    level: list[tuple[str, dict[str, Any]]] = [("", record)]
    for _ in range(depth):
        below: list[tuple[str, dict[str, Any]]] = []
        for prefix, value in level:
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(child, dict):
                    below.append((path, child))
                elif isinstance(child, list):
                    objects = sum(1 for item in child if isinstance(item, dict))
                    if objects >= 2 and (best is None or objects > best[1]):
                        best = (path, objects)
        level = below
    return best


# --- built-in transforms -------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    _SKIPPED = ("script", "style", "noscript")
    _BREAKS = ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr")

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIPPED:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED:
            self._skip = False
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html2text(html: object) -> str | None:
    """The text of an HTML fragment: tags, scripts and styles removed, one line per block;
    None for a value that is not text."""
    if html is None or html == "":
        return ""
    if not isinstance(html, str):
        return None
    parser = _TextExtractor()
    parser.feed(html)
    lines = "".join(parser.parts).splitlines()
    return "\n".join(line.strip() for line in lines if line.strip())


def inverted_index(index: object) -> str | None:
    """The text an OpenAlex ``abstract_inverted_index`` encodes (word -> positions); None for
    a value that is not an object of lists of positions."""
    if index is None or index == {}:
        return ""
    if not isinstance(index, dict) or not all(_positions(p) for p in index.values()):
        return None
    placed = sorted((position, word) for word, positions in index.items() for position in positions)
    return " ".join(word for _, word in placed)


def _positions(value: object) -> bool:
    return isinstance(value, list) and all(type(item) is int for item in value)


def join(items: object, separator: str = ", ") -> str | None:
    """The items as text, separated; None for a value that is not a list."""
    if items is None or items == []:
        return ""
    if not isinstance(items, list):
        return None
    return separator.join(str(item) for item in items)


def first(items: object) -> object | None:
    """The first item of a list; None for an empty list or a value that is not one."""
    if not isinstance(items, list) or not items:
        return None
    item: object = items[0]
    return item


#: The pure built-ins. ``$markitdown`` is built in too, but it reads a file, so it is an
#: adapter and reaches a survey only through the entry-point group.
BUILTIN_TRANSFORMS: Mapping[str, Transform] = {
    "html2text": html2text,
    "inverted_index": inverted_index,
    "join": join,
    "first": first,
}
