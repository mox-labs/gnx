"""Pagination: a collector entry with ``paginate:`` reads its source page by page, and how the
crawl stops is always recorded.

``paginate: {type, pages, next, param}`` (:class:`PaginateSpec`) names how the next page is
asked for: ``link``, a URL at the body path ``next``; ``header``, the ``Link`` header's
``rel="next"`` URL; ``cursor``, a token at the body path ``next``, sent as the query parameter
``param``; ``offset``, the records read so far, sent as ``param``; ``page``, the page's number
from 1, sent as ``param`` (the first page's too). Paths are ``extract:``'s.

A crawl stops (:class:`Crawl`, pure) at the first of:

- **no next token**: the end, the whole table read;
- **``pages`` pages read** with a next token still given: ``truncated``, by ``pages``;
- **a token already used**, any earlier one and not only the last: ``truncated``, by
  ``loop``; the page it names is not asked for again;
- **empty pages**: for ``offset`` and ``page``, one empty page is the end; for the others, which
  name their next page themselves, :data:`EMPTY_PAGES` empty pages in a row that still link on
  stop it, ``truncated``, by ``empty``.

A truncated table is sealed ``ok`` and says it is truncated: it is not a whole snapshot of
its source. Each record of a paginated table carries ``_position``, ``{page, i}``: its page's
ordinal in its crawl, from 0, and its index on that page; the page log (:class:`PageLog`)
keeps each page's token once, said before the page is asked for, so a token costs an entry a
page rather than a record.

**Resume** (ruling P4) is a stream's: a snapshot table is all or nothing. A crawl that a
stream's run goes on with starts at a :class:`Resume`: the last page its page log names, by
its token, and the last record kept from that page, if any. The crawl asks for that page
again, and finds where it left off by that record, its boundary, compared by
:func:`content`: at its index, as it was; elsewhere on that page or the next, as the source
moved under the crawl (a record inserted or removed ahead of it), so the crawl goes on after it
and keeps nothing twice; or nowhere (the record changed, or moved further), when the crawl goes
on by its index and says so. A token the source no longer takes begins the crawl again from
its first page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: How the next page is asked for (:class:`PaginateSpec`).
PaginateType = Literal["link", "header", "cursor", "offset", "page"]

#: What cut a crawl short: its cap of ``pages``, a token it had used already, or empty pages
#: in a row that still linked on. Named by the key or the rule, as a window's ``closed_by`` is.
TruncatedBy = Literal["pages", "loop", "empty"]

#: By default, the most pages one table reads: a cap, so a crawl that never ends still does.
DEFAULT_PAGES: Final = 100

#: Empty pages in a row, each still naming a next one, that stop a ``link``, ``header`` or
#: ``cursor`` crawl: a source that pages past its end forever stops here, and one with a gap
#: of a page or two in its results is read past it.
EMPTY_PAGES: Final = 3

#: The keys of ``paginate:`` each type takes besides ``type`` and ``pages``, each needed.
_KEYS: Final[dict[str, frozenset[str]]] = {
    "link": frozenset({"next"}),
    "header": frozenset(),
    "cursor": frozenset({"next", "param"}),
    "offset": frozenset({"param"}),
    "page": frozenset({"param"}),
}

#: The types whose next page is a count (records, or pages): a source that loses a record
#: while it is read shifts the next onto a page already read, so it is skipped.
COUNTED: Final = frozenset({"offset", "page"})

#: The number of a ``page`` crawl's first page.
FIRST_PAGE: Final = 1


class PaginateSpec(BaseModel):
    """``paginate:`` on a collector entry of a type that reads it (``CollectorType.paginates``).

    ``type`` says how the next page is asked for; ``pages`` is the most pages read, 100 by
    default. ``next`` is the path, in ``extract:``'s syntax, of the next page's URL (``link``)
    or its cursor (``cursor``) in a page's body. ``param`` is the query parameter the cursor
    (``cursor``), the offset (``offset``) or the page's number (``page``) is sent as. Each type
    takes the keys it reads and needs each of them: where an API puts its next page is the
    API's, so recon guesses no default, and a wrong guess would end the crawl at its first page
    as if it were the end."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: PaginateType
    pages: int = Field(default=DEFAULT_PAGES, ge=1)
    next: str | None = None
    param: str | None = None

    @model_validator(mode="after")
    def _the_keys_its_type_reads(self) -> PaginateSpec:
        wanted = _KEYS[self.type]
        for key in ("next", "param"):
            given = getattr(self, key) is not None
            if key in wanted and not given:
                raise ValueError(f"paginate type {self.type} needs {key}")
            if key not in wanted and given:
                raise ValueError(f"paginate type {self.type} takes no {key}")
        return self


@dataclass(frozen=True)
class Next:
    """Go on: ask for the page this token names."""

    token: Any


@dataclass(frozen=True)
class Stop:
    """The crawl is over: at its end (``truncated_by`` None), or cut short."""

    truncated_by: TruncatedBy | None


class Crawl:
    """The stopping rules of one table's crawl. Pure: it is told each page's record count and
    the next token the page gave (``None``, or ``""``, when it gave none), and says what to do.
    ``first`` is the token of the first page, when it has one (a ``link`` crawl's URL), so a
    page linking back to it is a loop too. A crawl a stream goes on with starts at its
    resumed page: ``pages`` read before it (so ``paginate.pages`` caps the whole crawl, across
    its windows and runs), and for ``offset``, ``offset`` records before it."""

    def __init__(
        self, spec: PaginateSpec, *, first: Any = None, pages: int = 0, offset: int = 0
    ) -> None:
        self._spec = spec
        self._seen: set[tuple[str, Any]] = set() if first is None else {_key(first)}
        self._empty = 0
        #: The pages read so far, and an ``offset`` crawl's records read so far.
        self.pages = pages
        self.offset = offset

    def after(self, records: int, found: Any = None) -> Next | Stop:
        """What follows a page of ``records`` records that named ``found`` as the next."""
        self.pages += 1
        if self._spec.type in COUNTED:
            if records == 0:
                return Stop(None)
            self.offset += records
            token: Any = self.offset if self._spec.type == "offset" else self.pages + FIRST_PAGE
        else:
            self._empty = 0 if records else self._empty + 1
            if found is None or found == "":
                return Stop(None)
            token = found
            if _key(token) in self._seen:
                return Stop("loop")
            if self._empty >= EMPTY_PAGES:
                return Stop("empty")
            self._seen.add(_key(token))
        if self.pages >= self._spec.pages:
            return Stop("pages")
        return Next(token)


def _key(token: Any) -> tuple[str, Any]:
    """A token as the crawl remembers it: ``2`` and ``"2"`` are two tokens."""
    return type(token).__name__, token


class PageLog(Protocol):
    """Where a collector says what pages it read and why its crawl stopped short
    (``CollectorContext.pages``), for the table or the stream being collected.

    :meth:`page` once a page, before the page is asked for, with its ordinal in the crawl and
    the token that asks for it (None for the first page, which none does): False when the page
    is not wanted (the stream it is read for has ended), and the crawl stops without asking
    for it. :meth:`truncated` once, when the crawl stopped short. A crawl that goes on from a
    :class:`Resume` says :meth:`restarted` when the source refused the resumed page's token,
    and :meth:`shifted` when that page's last record kept was not where it was left."""

    def page(self, index: int, token: Any) -> bool: ...

    def truncated(self, by: TruncatedBy) -> None: ...

    def restarted(self, index: int) -> None:
        """The source refused the token of page ``index``, the crawl's resumed page (an
        expired cursor, a page gone): the crawl began again from its first page."""
        ...

    def shifted(self, index: int, by: int | None) -> None:
        """Page ``index``, the crawl's resumed page, moved under the crawl: its last record
        kept was found ``by`` records later (records were inserted ahead of it) or earlier
        (negative: removed), and the crawl went on after it; or was not found (None), and
        the crawl went on by its index."""
        ...


class NoPages:
    """The :class:`PageLog` of a run that keeps no page log: every page is wanted."""

    def page(self, index: int, token: Any) -> bool:
        return True

    def truncated(self, by: TruncatedBy) -> None:
        return None

    def restarted(self, index: int) -> None:
        return None

    def shifted(self, index: int, by: int | None) -> None:
        return None


@dataclass(frozen=True)
class Resume:
    """Where a crawl a stream goes on with starts: ``page``, the last page its page log names,
    asked for again by ``token``; ``i``, the index on that page of the last record kept from
    it (-1 when none was: the page was named and never read), and ``boundary``, that record's
    :func:`content`, by which the crawl finds where it left off. A collector of a type that
    ``paginates`` is handed it as ``collect``'s ``after``, as :meth:`document` says it."""

    page: int
    token: Any
    i: int = -1
    boundary: dict[str, Any] | None = None

    def document(self) -> dict[str, Any]:
        return {"page": self.page, "token": self.token, "i": self.i, "boundary": self.boundary}

    @classmethod
    def of(cls, after: Any) -> Resume | None:
        """The resume point ``after`` says (:meth:`document`'s shape); None for anything else,
        so a crawl handed something it cannot go on from starts at its first page."""
        if not isinstance(after, dict) or not isinstance(after.get("page"), int):
            return None
        i, mark = after.get("i", -1), after.get("boundary")
        return cls(
            page=after["page"],
            token=after.get("token"),
            i=i if isinstance(i, int) else -1,
            boundary=mark if isinstance(mark, dict) else None,
        )


def content(record: dict[str, Any]) -> dict[str, Any]:
    """A record's content, as a crawl compares a page's records with the last one it kept:
    its fields but recon's own (those starting ``_``, ``_position`` among them), as change
    detection leaves them out of its hash. Compared whole, not hashed: no hash of it is kept
    or handed on, and two records compare the same only when they are."""
    return {key: value for key, value in record.items() if not key.startswith("_")}


def found_at(contents: list[dict[str, Any]], mark: dict[str, Any], i: int) -> int | None:
    """Where on a page (its records' :func:`content`) the record ``mark`` is: at ``i`` when it
    is there, else its one place; None when it is not there, or is there more than once
    (Dijkstra F: of two records alike, which is the boundary cannot be told, and going on
    after the wrong one keeps records twice)."""
    if 0 <= i < len(contents) and contents[i] == mark:
        return i
    return contents.index(mark) if contents.count(mark) == 1 else None


#: One record a resumed crawl keeps: its page's ordinal, its index there, and the record.
Kept = tuple[int, int, dict[str, Any]]


class Seek:
    """Where a resumed crawl left off, found among the records it reads again (ruling P4's
    boundary check). Pure: it is told the records of the resumed page, and of the page after
    it while the boundary is not found, each with its :func:`content`, and says which to keep.

    The boundary (``resume.boundary``, the last record kept from the resumed page) is looked
    for at its index ``resume.i``, then anywhere on the page, then on the next page: a record
    inserted ahead of it moves it later, onto the next page once it was a page's last, and a
    record removed moves it earlier. Found, the crawl keeps what follows it, and ``by`` is how
    far it moved (0: where it was); not found on either page, the crawl keeps what follows its
    index, and ``by`` is None: so too when it is off its index and its content is on the page
    more than once, as no one of them can be told for it. ``done`` once it is settled. A
    resume with no boundary (none of its page's records was kept, ``i`` -1) keeps what follows
    its index at once."""

    def __init__(self, resume: Resume) -> None:
        self._resume = resume
        self._held: tuple[int, list[dict[str, Any]]] | None = None
        self.done = False
        self.by: int | None = 0

    def kept(
        self, page: int, records: list[dict[str, Any]], contents: list[dict[str, Any]]
    ) -> list[Kept]:
        """The records of ``page`` (with their contents) to keep, and of the page held before
        it when the boundary is not found on it either. Told the resumed page first."""
        mark, i = self._resume.boundary, self._resume.i
        if self.done:
            return _after(page, records, -1)
        if mark is None:  # nothing to find: the resumed page's records after its index
            self.done = True
            return _after(page, records, i)
        if self._held is None:  # the resumed page
            at = found_at(contents, mark, i)
            if at is None and mark in contents:  # alike records: not to be told apart
                self.done, self.by = True, None
                return _after(page, records, i)
            if at is None:
                self._held = (page, records)
                return []
            self.done, self.by = True, at - i
            return _after(page, records, at)
        held, self._held = self._held, None
        self.done = True
        at = found_at(contents, mark, -1)
        if at is not None:
            self.by = len(held[1]) + at - i
            return _after(page, records, at)
        self.by = None
        return _after(*held, i) + _after(page, records, -1)

    def end(self) -> list[Kept]:
        """At the crawl's end with the boundary still looked for (the resumed page was its
        last): the resumed page's records after its index."""
        if self._held is None:
            return []
        held, self._held = self._held, None
        self.done, self.by = True, None
        return _after(*held, self._resume.i)


def _after(page: int, records: list[dict[str, Any]], at: int) -> list[Kept]:
    return [(page, index, record) for index, record in enumerate(records) if index > at]


def position(page: int, index: int) -> dict[str, int]:
    """A paginated record's ``_position``: its page's ordinal, and its index on the page."""
    return {"page": page, "i": index}


def crawl_floor_s(pages: int, *, burst: int, rps: float) -> float:
    """The least seconds a crawl of ``pages`` pages takes at a rate limit of ``rps`` with
    ``burst`` tokens held: the pages past the burst, each waiting for a token."""
    return max(0, pages - burst) / rps


#: One link of a ``Link`` header (RFC 8288): its target, then its parameters.
_LINK = re.compile(r"<([^>]*)>((?:\s*;\s*(?:[^;,\"]|\"[^\"]*\")*)*)")
_REL = re.compile(r"""(?i)(?:^|;)\s*rel\s*=\s*(?:"([^"]*)"|([^\s;,]+))""")


def next_link(header: str) -> str | None:
    """The target of the link whose relation is ``next`` in a ``Link`` header; None when it
    has none."""
    for target, params in _LINK.findall(header or ""):
        for quoted, bare in _REL.findall(params):
            if "next" in (quoted or bare).lower().split():
                return str(target)
    return None
