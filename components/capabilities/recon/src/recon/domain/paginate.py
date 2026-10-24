"""Pagination: a collector entry with ``paginate:`` reads its source page by page, and how the
crawl stops is always recorded.

``paginate: {type, pages, next, param}`` (:class:`PaginateSpec`) names how the next page is
asked for: ``link``, a URL at the body path ``next``; ``header``, the ``Link`` header's
``rel="next"`` URL; ``cursor``, a token at the body path ``next``, sent as the query parameter
``param``; ``offset``, the records read so far, sent as ``param``. Paths are ``extract:``'s.

A crawl stops (:class:`Crawl`, pure) at the first of:

- **no next token**: the end, the whole table read;
- **``pages`` pages read** with a next token still given: ``truncated``, by ``pages``;
- **a token already used**, any earlier one and not only the last: ``truncated``, by
  ``loop``; the page it names is not asked for again;
- **empty pages**: for ``offset``, one empty page is the end; for the others, which name their
  next page themselves, :data:`EMPTY_PAGES` empty pages in a row that still link on stop it,
  ``truncated``, by ``empty``.

A truncated table is sealed ``ok`` and says it is truncated: it is not a whole snapshot of
its source. Each record of a paginated table carries ``_position``, ``{page, i}``: its page's
ordinal from 0, and its index on that page; the page log (:class:`PageLog`) keeps each page's
token once, so a token costs an entry a page rather than a record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: How the next page is asked for (:class:`PaginateSpec`).
PaginateType = Literal["link", "header", "cursor", "offset"]

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
}


class PaginateSpec(BaseModel):
    """``paginate:`` on a collector entry of a type that reads it (``CollectorType.paginates``).

    ``type`` says how the next page is asked for; ``pages`` is the most pages read, 100 by
    default. ``next`` is the path, in ``extract:``'s syntax, of the next page's URL (``link``)
    or its cursor (``cursor``) in a page's body. ``param`` is the query parameter the cursor
    (``cursor``) or the offset (``offset``) is sent as. Each type takes the keys it reads and
    needs each of them: where an API puts its next page is the API's, so recon guesses no
    default, and a wrong guess would end the crawl at its first page as if it were the end."""

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
    page linking back to it is a loop too."""

    def __init__(self, spec: PaginateSpec, *, first: Any = None) -> None:
        self._spec = spec
        self._seen: set[tuple[str, Any]] = set() if first is None else {_key(first)}
        self._empty = 0
        #: The pages read so far, and an ``offset`` crawl's records read so far.
        self.pages = 0
        self.offset = 0

    def after(self, records: int, found: Any = None) -> Next | Stop:
        """What follows a page of ``records`` records that named ``found`` as the next."""
        self.pages += 1
        if self._spec.type == "offset":
            if records == 0:
                return Stop(None)
            self.offset += records
            token: Any = self.offset
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
    """Where a collector of a snapshot table says what pages it read and why its crawl stopped
    short (``CollectorContext.pages``): :meth:`page` once a page, as the page is read, with its
    ordinal from 0 and the token that asked for it (None for the first page, which none did);
    :meth:`truncated` once, when the crawl stopped short. Each is for the table being collected."""

    def page(self, index: int, token: Any) -> None: ...

    def truncated(self, by: TruncatedBy) -> None: ...


class NoPages:
    """The :class:`PageLog` of a run that keeps no page log (a stream's)."""

    def page(self, index: int, token: Any) -> None:
        return None

    def truncated(self, by: TruncatedBy) -> None:
        return None


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
