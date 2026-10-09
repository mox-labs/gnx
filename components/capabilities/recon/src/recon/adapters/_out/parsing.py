"""Response-body parsing shared by the api and capture collectors.

A body becomes records in three steps: parse it by format, navigate to the record array
with ``extract``, and keep the dicts. The same steps apply whether recon fetched the body
just now (api) or something else fetched it earlier (capture).
"""

from __future__ import annotations

import json
from typing import Any

from glom import GlomError, glom

from recon.domain.exceptions import CollectionError

#: Namespaces stripped (None) or prefixed when parsing XML, so normalize paths stay short.
XML_NAMESPACES: dict[str, str | None] = {
    "http://www.w3.org/2005/Atom": None,
    "http://arxiv.org/schemas/atom": "arxiv:",
    "http://a9.com/-/spec/opensearch/1.1/": "opensearch:",
}


def parse_body(text: str, fmt: str, *, origin: str) -> Any:
    """Parse ``text`` as ``json`` or ``xml``; any other format returns the text itself.

    ``origin`` (a URL or capture location) is named in the error when parsing fails.
    """
    if fmt == "json":
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"Response is not valid JSON from {origin}: {exc}"
            raise CollectionError(msg) from exc
    if fmt == "xml":
        import xmltodict

        try:
            return xmltodict.parse(
                text,
                process_namespaces=True,
                namespaces=XML_NAMESPACES,
                force_list=("entry", "author", "link", "category"),
            )
        except Exception as exc:  # expat raises its own ExpatError
            raise CollectionError(f"Response is not valid XML from {origin}: {exc}") from exc
    return text


def extract(data: Any, dotted_path: str) -> Any:
    """Navigate nested dict via dotted path using glom."""
    try:
        return glom(data, dotted_path)
    except (GlomError, KeyError, TypeError):
        return None


def ensure_list(data: Any) -> list[dict[str, Any]]:
    """Coerce response to list of dicts."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def to_records(data: Any, extract_path: str | None) -> list[dict[str, Any]]:
    if extract_path and isinstance(data, (dict, list)):
        data = extract(data, extract_path)
    return ensure_list(data)


def parse_line(line: str, index: int) -> dict[str, Any] | None:
    """Parse one line of text output. A JSON object wins; otherwise {line_number, line}."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return {"line_number": index, "line": line}
