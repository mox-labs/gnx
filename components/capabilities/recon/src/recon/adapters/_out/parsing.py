"""recon's built-in converters of structured bodies, at ``recon.converter``: ``json``, ``xml``
and ``lines`` (``recon.core.domain.collector.Converter``).

A body becomes records in three steps: its converter parses it into values, ``extract:`` finds
the records in each value, and the objects among them are kept
(``recon.core.domain.normalize.to_records``). The same steps apply whether recon fetched the
body just now (``api``) or something else fetched it earlier (``capture``). Document formats
(HTML, PDF, DOCX, ...) are the ``document`` converter's (``markitdown_converter``).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from recon.core.domain.errors import CollectionError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

#: Namespaces stripped (None) or shortened when parsing XML, so normalize paths stay short:
#: xmltodict joins a short name to the element with ``:``, giving ``arxiv:primary_category``.
XML_NAMESPACES: dict[str, str | None] = {
    "http://www.w3.org/2005/Atom": None,
    "http://arxiv.org/schemas/atom": "arxiv",
    "http://a9.com/-/spec/opensearch/1.1/": "opensearch",
}


_FORMAT_FIX = "set response_format: to what the source returns (json | xml), or omit it for text"


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
            raise CollectionError(msg, fix=_FORMAT_FIX) from exc
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
            msg = f"Response is not valid XML from {origin}: {exc}"
            raise CollectionError(msg, fix=_FORMAT_FIX) from exc
    return text


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


def charset(media_type: str) -> str:
    """The ``charset`` a media type names, else UTF-8."""
    for parameter in media_type.split(";")[1:]:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            return value.strip().strip('"')
    return "utf-8"


def text_of(chunks: Iterable[bytes], media_type: str) -> str:
    """The whole body, decoded by its charset (an unknown one as UTF-8), a byte it cannot
    decode replaced."""
    data = b"".join(chunks)
    try:
        return data.decode(charset(media_type), errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


class JsonConverter:
    """``json``: a JSON body, its one value (None for an empty body)."""

    media_types = frozenset({"application/json", "+json"})

    def convert(self, chunks: Iterable[bytes], media_type: str, origin: str) -> Iterator[Any]:
        yield parse_body(text_of(chunks, media_type), "json", origin=origin)


class XmlConverter:
    """``xml``: an XML body, as one value (namespaces shortened, as :data:`XML_NAMESPACES`)."""

    media_types = frozenset({"application/xml", "text/xml", "+xml"})

    def convert(self, chunks: Iterable[bytes], media_type: str, origin: str) -> Iterator[Any]:
        yield parse_body(text_of(chunks, media_type), "xml", origin=origin)


class LinesConverter:
    """``lines``: a text body line by line, as it arrives: a line that is a JSON object is that
    value, any other ``{line_number, line}`` (numbered from 1), a blank line nothing. A line
    ends at ``\\n``, and a ``\\r`` before it is no part of it."""

    media_types = frozenset({"text/plain", "text/*", "application/x-ndjson", "application/jsonl"})

    def convert(self, chunks: Iterable[bytes], media_type: str, origin: str) -> Iterator[Any]:
        encoding = charset(media_type)
        pending = b""
        index = 0
        for chunk in chunks:
            *whole, pending = (pending + chunk).split(b"\n")
            for raw in whole:
                index += 1
                found = _line(raw, encoding, index)
                if found is not None:
                    yield found
        if pending:
            found = _line(pending, encoding, index + 1)
            if found is not None:
                yield found


def _line(raw: bytes, encoding: str, index: int) -> dict[str, Any] | None:
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    return parse_line(text.removesuffix("\r"), index)


def register(registry: Any) -> None:
    """The built-in converters at ``recon.converter``."""
    for name, converter, summary in (
        ("json", JsonConverter, "a JSON body, its one value"),
        ("xml", XmlConverter, "an XML body, its one value"),
        ("lines", LinesConverter, "a text body line by line: a JSON object, or the line"),
    ):
        registry.register("recon.converter", f"recon.v1.converter.{name}", converter,
                          summary=summary)  # fmt: skip
