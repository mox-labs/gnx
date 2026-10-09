"""CaptureCollector — normalize bodies some earlier fetch already captured.

Reads capture logs (format recon.v1.capture, docs/reference/capture-format.md) and turns
each matching capture's body into records with the usual ``extract`` + ``normalize``. The
bodies may come from an earlier survey (``preserve_raw: true``), from ``recon capture``
(a Claude Code hook on MCP tools), or from any other tool that writes the format. Nothing
is fetched: this is how recon normalizes data another process obtained.

Config::

    - name: exa-results
      type: capture
      path: captures/captures.jsonl     # default; relative to the mission dir; a glob works
      match: { collector: "mcp__exa__*" }  # fnmatch on source / collector / kind
      extract: results
      normalize:
        title: title
        url: url
        query: _capture.request.tool_input.query   # the capture line, as `_capture`

The body format follows the capture's ``content_type`` (JSON, XML, else text lines, each
parsed like a cli collector's stdout) unless ``response_format`` is set explicitly. When
the collector fans out over a catalog, each run keeps only captures whose ``source`` is
that catalog entry's name (unless ``match.source`` says otherwise).

Registered as the built-in collector type ``capture`` (entry-point group
``recon.collectors``).
"""

from __future__ import annotations

import glob
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon.adapters._out.parsing import parse_body, parse_line, to_records
from recon.application.captures import read_captures
from recon.application.transforms import BUILTIN_TRANSFORMS, apply_normalize
from recon.domain.capture import CAPTURE_LOG_NAME
from recon.domain.collector import CollectorType
from recon.domain.exceptions import CollectionError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Transform
    from recon.domain.models import CollectorEntry, SourceEntry

#: The mission's external capture log, fed by ``recon capture``.
DEFAULT_CAPTURE_PATH = f"captures/{CAPTURE_LOG_NAME}"

#: The name under which a normalize spec can reach the capture line itself.
CAPTURE_SCOPE = "_capture"


def body_format(content_type: str) -> str:
    """``json``, ``xml`` or ``text`` for a capture's content type."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == "application/json" or ct.endswith("+json"):
        return "json"
    if ct in ("application/xml", "text/xml") or ct.endswith("+xml"):
        return "xml"
    return "text"


class CaptureCollector:
    """Capture log(s) → matching bodies → records."""

    def __init__(
        self, mission_dir: Path, transforms: Mapping[str, Transform] = BUILTIN_TRANSFORMS
    ) -> None:
        self._mission_dir = mission_dir
        self._transforms = transforms

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        logs = self._logs(entry)
        explicit_format = "response_format" in entry.model_fields_set
        for log in logs:
            for capture, body in read_captures(log):
                if not _matches(entry, source, capture):
                    continue
                fmt = (
                    entry.response_format
                    if explicit_format
                    else body_format(str(capture.get("content_type", "")))
                )
                for record in _records(body, fmt, entry.extract, origin=f"{log} {capture['id']}"):
                    if not entry.normalize:
                        yield record
                        continue
                    # The capture line is addressable from the spec as `_capture.*` (the
                    # tool, its input, when it ran); it reaches the output only as the
                    # columns the spec names.
                    scope = {**record, CAPTURE_SCOPE: capture}
                    yield apply_normalize(scope, entry.normalize, self._transforms)

    def _logs(self, entry: CollectorEntry) -> list[Path]:
        pattern = entry.path or DEFAULT_CAPTURE_PATH
        full = Path(pattern) if Path(pattern).is_absolute() else self._mission_dir / pattern
        logs = sorted(p for p in map(Path, glob.glob(str(full), recursive=True)) if p.is_file())
        if not logs:
            msg = (
                f"capture collector '{entry.name}': no capture log matches {pattern!r} "
                f"(relative to {self._mission_dir}). Captures are written by `recon capture` "
                "and by surveys with preserve_raw: true"
            )
            raise CollectionError(msg, kind="config")
        return logs


def _matches(entry: CollectorEntry, source: SourceEntry | None, capture: dict[str, Any]) -> bool:
    match = entry.match
    wanted = {
        "source": match.source if match else None,
        "collector": match.collector if match else None,
        "kind": match.kind if match else None,
    }
    if wanted["source"] is None and source is not None:
        wanted["source"] = source.name
    for field, pattern in wanted.items():
        if pattern is None:
            continue
        value = capture.get(field)
        if value is None or not fnmatchcase(str(value), pattern):
            return False
    return True


def _records(
    body: bytes, fmt: str, extract_path: str | None, *, origin: str
) -> Iterator[dict[str, Any]]:
    text = body.decode("utf-8", errors="replace")
    if fmt in ("json", "xml"):
        yield from to_records(parse_body(text, fmt, origin=origin), extract_path)
        return
    for index, line in enumerate(text.splitlines(), start=1):
        record = parse_line(line, index)
        if record is not None:
            yield record


def _describe(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    match = entry.match.model_dump(exclude_none=True) if entry.match else {}
    if "source" not in match and source is not None:
        match["source"] = source.name
    return {"path": entry.path or DEFAULT_CAPTURE_PATH, "match": match}


collector_type = CollectorType(
    create=lambda ctx: CaptureCollector(ctx.mission_dir, ctx.transforms),
    effects=frozenset({"filesystem"}),
    summary="capture log (recon.v1.capture) → bodies → records; fetches nothing",
    describe=_describe,
)
