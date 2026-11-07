"""What recon does with a run's items, once, for every collector type (ruling R-NS).

A collector gives :data:`~recon.core.domain.collector.Item` s; :class:`Unpacking` turns them into
:class:`Raw` records and the events beside them:

* a :class:`~recon.core.domain.collector.Body` is **captured** (with ``preserve_raw``: its
  bytes as the type fetched them, its secrets kept out), **converted** by the converter its
  media type names, and its records **found** (``extract:``) in each value the converter
  gives, each carrying the body's ``fields`` and, for a page, its position ``{page, i}``;
* a :class:`~recon.core.domain.collector.Record` has its records found the same way;
* :class:`~recon.core.domain.collector.PageMark`, :class:`~recon.core.domain.collector.Truncated`,
  :class:`~recon.core.domain.stream.Gap` and :class:`~recon.core.domain.collector.Dropped` pass on
  to the act (a snapshot's page log, a stream's buffer).

A body or record ``extract:`` does not fit ends the table with the reason (``NoRecordsError``:
before its first record the table is kept, empty, and warned about; after, it fails), as the
source's error it most likely is: a page past the first that holds an error object. A body that
says ``unfit="skip"`` (one of many bodies of unrelated shape: a capture log's) gives nothing
instead, and the table ends with the first such reason only when none of its items gave a
record.

:class:`Shaping` applies ``normalize:`` to a raw record (with what its item's ``scope`` lets the
spec read) and keeps its position as ``_position``: a snapshot shapes as it writes, a stream
after it has captured the record.

:func:`crawl` drives a :class:`~recon.core.domain.collector.Paged` type's crawl: the stopping
rules (:class:`~recon.core.domain.paginate.Crawl`), the page log, each record's position, and
for a stream's crawl resumed after a stop, finding where it left off among the shaped records
of the page asked for again (:class:`~recon.core.domain.paginate.Seek`), said as :class:`Moved`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from recon.core.domain.capture import redact_credentials
from recon.core.domain.collector import (
    Body,
    BodyEnd,
    Dropped,
    Paged,
    PageMark,
    Record,
    Truncated,
)
from recon.core.domain.converters import essence
from recon.core.domain.errors import CollectionError, NoRecordsError
from recon.core.domain.http import reported_url
from recon.core.domain.normalize import RESERVED_PREFIX, apply_normalize, to_records
from recon.core.domain.optional import SupportsClose
from recon.core.domain.paginate import COUNTED, FIRST_PAGE, Crawl, Resume, Seek, Stop, content
from recon.core.domain.paginate import position as page_position
from recon.core.domain.stream import POSITION_FIELD, Gap

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping

    from recon.core.domain.capture import CaptureLog
    from recon.core.domain.collector import Item, Position, Run, Transform
    from recon.core.domain.converters import Converters


@dataclass(frozen=True)
class Raw:
    """A record as recon found it in an item, before ``normalize:``: its data (with the body's
    ``fields``), its position in its source, what a normalize spec may also read, and whether
    it came from a body."""

    data: dict[str, Any]
    position: Any = None
    scope: Mapping[str, Any] = field(default_factory=dict)
    bodied: bool = False

    def line(self) -> dict[str, Any]:
        """The record as a stream's buffer holds it and its capture keeps it: its data, and
        its position as ``_position``."""
        if self.position is None:
            return dict(self.data)
        return {**self.data, POSITION_FIELD: self.position}


@dataclass(frozen=True)
class Moved:
    """A resumed crawl's page moved under it: ``restarted`` (the source refused its token, and
    the crawl began again) or ``shifted`` by ``by`` records (None: its boundary not found)."""

    what: Literal["restarted", "shifted"]
    index: int
    by: int | None = None


#: What an act is handed for a run's items.
Event = Raw | PageMark | Truncated | Gap | Dropped | Moved


class Shaping:
    """``normalize:`` for one run: the spec, the transforms it may name (bound to the
    directories the mission may read, and a ``local`` source's as where a relative path in a
    record is), applied to each raw record with its scope; its position kept as
    ``_position``."""

    def __init__(self, spec: Mapping[str, str] | None, transforms: Mapping[str, Transform]):
        self._spec = spec
        self._transforms = transforms

    def __call__(
        self, data: Mapping[str, Any], scope: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self._spec:
            return dict(data)
        return apply_normalize({**data, **(scope or {})}, self._spec, self._transforms)

    def raw(self, raw: Raw) -> dict[str, Any]:
        """The record ``raw`` is, shaped, with its position."""
        record = self(raw.data, raw.scope)
        if raw.position is not None:
            record[POSITION_FIELD] = raw.position
        return record


class Unpacking:
    """One table's items into :data:`Event` s, run by run (a ranged table's steps are runs of
    one table). ``converters`` are the installed ones by claim; ``captures`` the act's capture
    log, None when the mission does not keep captures."""

    def __init__(self, converters: Converters, captures: CaptureLog | None = None) -> None:
        self._converters = converters
        self._captures = captures
        #: Records found so far in the table, and the first item extract: did not fit.
        self.found = 0
        self._unfit: NoRecordsError | None = None

    def events(self, run: Run, items: Iterable[Item]) -> Generator[Event, None, None]:
        """``items`` as events; at their end, the reason none gave a record, if one did not.
        Closing this closes ``items``."""
        iterator = iter(items)
        try:
            for item in iterator:
                if isinstance(item, Body):
                    yield from self.body(run, item)
                elif isinstance(item, Record):
                    yield from self.record(run, item)
                elif isinstance(item, PageMark | Truncated | Gap | Dropped):
                    yield item
                else:
                    raise CollectionError(
                        f"collector {run.entry.name!r} gave a {type(item).__name__}, "
                        "which is not an item: give Body, Record, PageMark, Gap, Dropped or "
                        "Truncated",
                        fix="report it to the package that registered the collector type",
                    )
        finally:
            if isinstance(iterator, SupportsClose):  # a generator, stopped early
                iterator.close()

    def end(self) -> None:
        """Raise the reason none of the table's items gave a record, when one did not fit."""
        if self.found == 0 and self._unfit is not None:
            raise self._unfit

    def body(self, run: Run, body: Body) -> Generator[Raw, None, None]:
        """A body captured, converted, its records found."""
        _scoped(body.fields, "fields", run)
        _scoped(body.scope, "scope", run)
        converter = self._converters.pick(body.media_type, body.converter)
        if converter is None:
            what = (
                f"converter {body.converter!r}"
                if body.converter is not None
                else f"a converter for {essence(body.media_type) or 'a body with no media type'}"
            )
            raise CollectionError(
                f"collector {run.entry.name!r} gave a body that needs {what}, and none is "
                f"installed; installed: {', '.join(sorted(self._converters.named)) or '(none)'}",
                kind="config",
                fix="install a package that registers it at recon.converter, or read the source "
                "in a format recon converts (recon types --json lists the converters)",
            )
        chunks = self._captured(run, body)
        values = converter.convert(chunks, body.media_type, body.origin)
        index = 0
        try:
            for value in values:
                for record in self._found(value, run.entry.extract, skip=body.unfit == "skip"):
                    data = {**record, **body.fields}
                    at = None if body.page is None else page_position(body.page, index)
                    index += 1
                    self.found += 1
                    yield Raw(data, at, body.scope, bodied=True)
        finally:
            if isinstance(values, SupportsClose):
                values.close()

    def record(self, run: Run, record: Record) -> Generator[Raw, None, None]:
        _scoped(record.scope, "scope", run)
        for found in self._found(dict(record.data), run.entry.extract):
            self.found += 1
            yield Raw(dict(found), record.position, record.scope)

    def _found(
        self, value: Any, extract: str | None, *, skip: bool = False
    ) -> list[dict[str, Any]]:
        try:
            return to_records(value, extract)
        except NoRecordsError as unfit:
            if not skip:
                raise
            self._unfit = self._unfit or unfit
            return []

    def _captured(self, run: Run, body: Body) -> Iterable[bytes]:
        """The body's bytes, as given to its converter; captured too when the mission keeps
        captures."""
        if isinstance(body.data, bytes | bytearray):
            data = bytes(body.data)
            if self._captures is not None:
                self._captures.record(
                    collector=run.entry.name,
                    source=run.source.name if run.source else None,
                    kind=body.kind or run.entry.type,
                    request=_request(body, body.request),
                    status=body.status,
                    content_type=essence(body.media_type),
                    headers=dict(body.headers),
                    body=kept(data, body.secrets),
                )
            return (data,)
        return self._flowing(run, body)

    def _flowing(self, run: Run, body: Body) -> Generator[bytes, None, None]:
        """A body read as it arrives, each chunk captured as it passes; the capture logged
        when it ends (with the :class:`BodyEnd` its generator returned), or when it is let go
        early."""
        assert not isinstance(body.data, bytes | bytearray)
        captures = self._captures
        if captures is None:
            yield from body.data
            return
        name = run.entry.name

        def tee(chunk: bytes) -> None:
            captures.tee(name, kept_text(chunk, body.secrets))

        ended: list[Any] = []
        try:
            ended.append((yield from _teed(body.data, tee)))
        finally:
            closing = ended[0] if ended and isinstance(ended[0], BodyEnd) else BodyEnd()
            captures.finalize_stream(
                name,
                source=run.source.name if run.source else None,
                kind=body.kind or run.entry.type,
                request=_request(body, closing.request or body.request),
                status=closing.status if closing.status is not None else body.status,
                content_type=essence(body.media_type),
            )


def _teed(data: Iterable[bytes], tee: Callable[[bytes], object]) -> Generator[bytes, None, Any]:
    """``data``'s chunks, each told to ``tee`` as it passes; returns what ``data`` returned
    (a generator's return value). Closing this closes ``data``."""
    iterator = iter(data)
    try:
        while True:
            try:
                chunk = next(iterator)
            except StopIteration as stop:
                return stop.value
            tee(chunk)
            yield chunk
    finally:
        if isinstance(iterator, SupportsClose):
            iterator.close()


def _request(body: Body, request: Mapping[str, Any]) -> dict[str, Any]:
    """What a capture keeps of the request: its secrets redacted, and a page's ordinal."""
    kept_request = dict(request)
    if body.page is not None:
        kept_request["page"] = body.page
    redacted: dict[str, Any] = (
        redact_credentials(kept_request, body.secrets) if body.secrets else kept_request
    )
    return redacted


def kept(data: bytes, secrets: Iterable[str]) -> bytes:
    """A body as a capture keeps it: each of ``secrets`` redacted, every other byte as it
    came. A JSON body is redacted value by value (a short credential only where it is a whole
    value), and written again only when something was; any other, as its text."""
    given = [secret for secret in secrets if secret]
    if not given:
        return data
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return kept_text(data, given)
    redacted = redact_credentials(value, given)
    if redacted == value:
        return data
    return json.dumps(redacted, ensure_ascii=False).encode()


def kept_text(data: bytes, secrets: Iterable[str]) -> bytes:
    """``data`` as text with each of ``secrets`` redacted (a long one wherever it is, a short
    one only as the whole of it)."""
    given = [secret for secret in secrets if secret]
    if not given:
        return data
    text = data.decode("utf-8", "surrogateescape")
    redacted = redact_credentials(text, given)
    return data if redacted == text else redacted.encode("utf-8", "surrogateescape")


def _scoped(values: Mapping[str, Any], what: str, run: Run) -> None:
    """A body's ``fields`` and an item's ``scope`` are recon's own names: each starts ``_``."""
    wrong = sorted(key for key in values if not key.startswith(RESERVED_PREFIX))
    if wrong:
        raise CollectionError(
            f"collector {run.entry.name!r} gave {what} named {', '.join(wrong)}: an item's "
            f"{what} are recon's own names, each starting {RESERVED_PREFIX!r}",
            fix="report it to the package that registered the collector type",
        )


# --- the crawl ------------------------------------------------------------------------------


def crawl(
    collector: Paged,
    run: Run,
    unpacking: Unpacking,
    shaping: Shaping,
    resume: Resume | None = None,
) -> Generator[Event, None, None]:
    """A :class:`Paged` type's crawl of ``run``'s source, as events: each page marked before it
    is asked for, its records positioned ``{page, i}``, a crawl cut short said, and from
    ``resume`` (a stream's crawl that goes on), its page first, by its token, its records kept
    after the last one kept from it. A resumed token the source refuses (``run_failed`` on
    that first page) begins the crawl again from its first page."""
    spec = run.entry.paginate
    assert spec is not None  # the core crawls only an entry with paginate:
    page = 0
    token: Any = FIRST_PAGE if spec.type == "page" else None
    crawler = Crawl(spec)
    seek = None
    #: The raw records of the page a resumed crawl holds while it looks for its boundary.
    held: dict[int, Raw] = {}
    if resume is not None:
        page, token, seek = resume.page, resume.token, Seek(resume)
        offset = token if spec.type == "offset" and isinstance(token, int) else 0
        first = token if spec.type in ("link", "header") else None
        crawler = Crawl(spec, first=first, pages=page, offset=offset)
    auth_param = run.source.auth.param if run.source is not None else ""
    while True:
        yield PageMark(page, _said_token(token, auth_param))
        try:
            asked = collector.page(run, token)
        except CollectionError as exc:
            if not _refused(resume, page, token, exc):
                raise
            yield Moved("restarted", page)  # the token went stale: the crawl begins again
            yield from crawl(collector, run, unpacking, shaping)
            return
        if not isinstance(asked.body, Body):
            raise CollectionError(
                f"collector {run.entry.name!r} answered page {page} with no Body",
                fix="report it to the package that registered the collector type",
            )
        if asked.token is not None:
            crawler.saw(asked.token)
        raws = list(unpacking.body(run, replace(asked.body, page=page)))
        kept_raws = raws
        if seek is not None and not seek.done:
            known = {**held, **{id(raw.data): raw for raw in raws}}
            records = [raw.data for raw in raws]
            kept_records = seek.kept(page, records, _contents(raws, shaping))
            kept_raws = [_at(known, entry) for entry in kept_records]
            held = {id(raw.data): raw for raw in raws}
            moved = _shifted(seek, resume)
            if moved is not None:
                yield moved
        resuming = seek is not None and not seek.done
        yield from kept_raws
        found = None if spec.type in COUNTED else asked.next
        step = crawler.after(len(raws), found)
        if isinstance(step, Stop):
            if resuming and seek is not None:  # the resumed page was the last
                yield from (_at(held, entry) for entry in seek.end())
                moved = _shifted(seek, resume)
                if moved is not None:
                    yield moved
            if step.truncated_by is not None:
                yield Truncated(step.truncated_by)
            return
        if not resuming:
            resume = None
        token, page = step.token, page + 1


def _said_token(token: Any, auth_param: str) -> Any:
    """A page's token as the page log keeps it: a URL as recon reports one."""
    return reported_url(token, auth_param) if isinstance(token, str) and "://" in token else token


def _refused(resume: Resume | None, page: int, token: Any, exc: CollectionError) -> bool:
    """Whether a request failed as a resumed page's token the source no longer takes: the
    crawl's first request of a run that goes on (a page past the first, so it has a token),
    refused with neither a retry's nor a credential's status (``run_failed``)."""
    return (
        resume is not None
        and page == resume.page
        and token == resume.token
        and token is not None
        and exc.kind == "run_failed"
    )


def _contents(raws: list[Raw], shaping: Shaping) -> list[dict[str, Any]]:
    """Each record's content as the store keeps it (shaped, written as JSON and read back),
    so a record asked for again compares with the one kept."""
    return [content(json.loads(json.dumps(shaping(raw.data, raw.scope), default=str)))
            for raw in raws]  # fmt: skip


def _at(known: Mapping[int, Raw], kept_entry: tuple[int, int, dict[str, Any]]) -> Raw:
    """The raw record a :class:`Seek` kept, positioned where it was found."""
    at_page, index, record = kept_entry
    raw = known.get(id(record))
    if raw is None:
        return Raw(record, page_position(at_page, index), bodied=True)
    return replace(raw, position=page_position(at_page, index))


def _shifted(seek: Seek, resume: Resume | None) -> Moved | None:
    """Once the boundary of the resumed page is settled, that it moved, if it did."""
    if seek.done and seek.by != 0 and resume is not None:
        return Moved("shifted", resume.page, seek.by)
    return None


def run_events(
    collector: Any,
    run: Run,
    unpacking: Unpacking,
    shaping: Shaping,
    *,
    position: Position | None = None,
    resume: Resume | None = None,
) -> Generator[Event, None, None]:
    """A run's events: a crawl recon drives, for an entry with ``paginate:`` of a
    :class:`Paged` type (from ``resume``), else what ``collect`` gives (after ``position``).
    The table's caller says why it found nothing when it ends (:meth:`Unpacking.end`)."""
    if run.entry.paginate is not None and isinstance(collector, Paged):
        yield from crawl(collector, run, unpacking, shaping, resume)
        return
    yield from unpacking.events(run, collector.collect(run, position))
