"""CaptureCollector — normalize bodies some earlier fetch already captured.

Reads capture logs (format recon.v1.capture, `recon --skill -r capture-format`, through the
``capture_logs`` need) and gives each matching capture's body to recon, which converts it,
finds its records with the usual ``extract`` and shapes them with ``normalize``, as it does a
body fetched now. The
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

A capture of one page of a paginated table (its request names the ``page``) is that page's
body, so its records get the ``_position`` the table gave them, ``{page, i}``, and a crawl kept
with ``preserve_raw`` replays to the same table.

A run that gives no record says why when there is a why (``NoRecordsError``): it read
captures and its filters kept none, or the ``extract`` fits none of the bodies it kept. A
capture whose body the ``extract`` does not fit is skipped when others give records: a log
holds many tools' results (recon's rule for every type's bodies).

Registered as the built-in collector type ``capture``.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from recon.core.domain.capture import CAPTURE_LOG_NAME
from recon.core.domain.collector import Body
from recon.core.domain.errors import NoRecordsError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from recon.core.domain.capture import CaptureLogs
    from recon.core.domain.collector import Effect, Intent, Item, Position, RecordsKind, Run
    from recon.core.domain.errors import Problem
    from recon.core.domain.models import SourceEntry

#: The mission's external capture log, fed by ``recon capture``.
DEFAULT_CAPTURE_PATH = f"captures/{CAPTURE_LOG_NAME}"

#: The name under which a normalize spec can reach the capture line itself.
CAPTURE_SCOPE = "_capture"


class CaptureMatch(BaseModel):
    """Filters a `capture` collector applies to capture-log lines.

    Each field is a shell-style glob (`fnmatch`) matched against the capture's field of the
    same name; an unset field matches everything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str | None = None
    collector: str | None = None
    kind: str | None = None


class CaptureSettings(BaseModel):
    """A ``capture`` collector's own keys: ``path`` (a capture log, or a glob, relative to
    the mission), ``match`` and ``response_format`` (the bodies' format, when not their
    content type's)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str | None = None
    match: CaptureMatch | None = None
    response_format: Literal["json", "xml", "html", "text"] = "json"


def body_format(content_type: str) -> str:
    """``json``, ``xml`` or ``text`` for a capture's content type."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == "application/json" or ct.endswith("+json"):
        return "json"
    if ct in ("application/xml", "text/xml") or ct.endswith("+xml"):
        return "xml"
    return "text"


#: The media type and the converter each body format is read with.
_READ = {
    "json": ("application/json", "json"),
    "xml": ("application/xml", "xml"),
    "text": ("text/plain", "lines"),
    "html": ("text/plain", "lines"),
}


class CaptureCollector:
    """Capture log(s) → matching bodies, each converted by recon as a fetched one is."""

    effects: frozenset[Effect] | None = frozenset({"filesystem"})
    records: RecordsKind = "finite"
    accepts: frozenset[str] | None = None  # it only filters by a source's name

    def __init__(self, *, capture_logs: CaptureLogs) -> None:
        self._logs = capture_logs

    def describe(self, run: Run) -> Intent:
        settings = run.settings_as(CaptureSettings)
        return {
            "path": settings.path or DEFAULT_CAPTURE_PATH,
            "match": _wanted(settings, run.source),
        }

    def check(self, run: Run) -> list[Problem]:
        return []

    def close(self) -> None:
        return None

    def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
        settings = run.settings_as(CaptureSettings)
        source = run.source
        logs = self._logs.logs(settings.path or DEFAULT_CAPTURE_PATH)
        explicit_format = "response_format" in settings.model_fields_set
        wanted = _wanted(settings, source)
        read = kept = 0
        for log in logs:
            for capture, body in self._logs.read(log):
                read += 1
                if not _matches(wanted, capture):
                    continue
                kept += 1
                fmt = (
                    settings.response_format
                    if explicit_format
                    else body_format(str(capture.get("content_type", "")))
                )
                media_type, converter = _READ[fmt]
                request = capture.get("request")
                status = capture.get("status")
                headers = capture.get("headers")
                yield Body(
                    media_type=media_type,
                    data=body,
                    request=request if isinstance(request, dict) else {},
                    converter=converter,
                    kind=str(capture.get("kind") or "external"),
                    status=status if type(status) is int else None,
                    headers=headers if isinstance(headers, dict) else {},
                    origin=f"{log} {capture['id']}",
                    page=_page(capture),
                    unfit="skip",  # a log holds many tools' results
                    # The capture line is addressable from the spec as `_capture.*` (the
                    # tool, its input, when it ran); it reaches the output only as the
                    # columns the spec names.
                    scope={CAPTURE_SCOPE: capture},
                )
        if read and not kept:
            match = settings.match
            implied = source is not None and (match is None or match.source is None)
            raise _unmatched(read, wanted, implied=implied)


def _page(capture: dict[str, Any]) -> int | None:
    """The page of a paginated table a capture holds, as its request names it; None for a
    capture of no page."""
    request = capture.get("request")
    page = request.get("page") if isinstance(request, dict) else None
    return page if type(page) is int and page >= 0 else None


def _wanted(settings: CaptureSettings, source: SourceEntry | None) -> dict[str, str]:
    """The globs a capture must match, by field: ``match:``, and the source's name as the
    ``source`` glob when the run has a source and ``match.source`` is not set."""
    wanted = settings.match.model_dump(exclude_none=True) if settings.match else {}
    if "source" not in wanted and source is not None:
        wanted["source"] = source.name
    return wanted


def _matches(wanted: dict[str, str], capture: dict[str, Any]) -> bool:
    for field, pattern in wanted.items():
        value = capture.get(field)
        if value is None or not fnmatchcase(str(value), pattern):
            return False
    return True


def _unmatched(read: int, wanted: dict[str, str], *, implied: bool) -> NoRecordsError:
    globs = ", ".join(f"{field} {pattern!r}" for field, pattern in wanted.items())
    why = (
        f"; source {wanted['source']!r} is the catalog source this run is for, which a "
        "capture collector with a source keeps unless match.source says otherwise"
        if implied
        else ""
    )
    return NoRecordsError(
        f"none of the {read} capture(s) read matched {globs}{why}",
        problem_kind="captures_unmatched",
        field="match",
        fix="set match: to what the captures hold (recon capture records an MCP tool's "
        'server as its source; match.source: "*" keeps every source), then survey again',
    )


def register(registry: Any) -> None:
    """The ``capture`` collector type."""
    registry.register(
        "recon.collector",
        "recon.v1.collector.capture",
        CaptureCollector,
        config=CaptureSettings,
        needs={"capture_logs"},
        summary="capture log (recon.v1.capture) → bodies → records; fetches nothing",
    )
