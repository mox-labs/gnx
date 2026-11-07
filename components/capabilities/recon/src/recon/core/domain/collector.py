"""The collector contract (v2): what a collector type implements, and what recon does with what
it gives.

**The decision it hides.** How to talk to one kind of source. A collector type knows its
source's protocol (an HTTP API's paging, a command line, an MCP server, a file followed as it
grows); recon knows everything else, once, for every type: it captures what the type fetched,
converts each body by its media type, finds the records in it (``extract:``), shapes them
(``normalize:``), lands them, and drives a crawl's stopping, resumption and shift detection.
A type that does none of that cannot forget it.

**The members** (:class:`Collector`, a structural Protocol: a type implements it without
importing recon's classes):

- ``effects``: what a run can touch (:data:`Effect`), or None for unknown;
- ``records``: :data:`RecordsKind`, whether the type's records end, and who paces them;
- ``accepts``: the source kinds a run of the type runs against, or None for any (and none);
- ``describe(run)``: what a planned run would do, for a reviewer; pure;
- ``check(run)``: the problems in one run, paths relative to its entry; pure;
- ``collect(run, resume)``: the run's items (:data:`Item`), lazily;
- ``close()``: let go of what the collector holds, once, when the act ends.

Two optional protocols, each found with ``isinstance`` when recon first builds the type, never
by a member's name: :class:`Paged` (recon drives the crawl; only such a type takes
``paginate:``) and :class:`Preflight` (a checked dry run asks what a run needs of the host).

**Items.** A run gives :class:`Body` (bytes of a media type: recon captures, converts, extracts
and normalizes them) or :class:`Record` (already structured: recon still extracts, normalizes
and captures it), and says what else happened as :class:`PageMark`, :class:`Gap`,
:class:`Dropped` and :class:`Truncated`.

**Building.** A collector type is an entry at the ``recon.collector`` point of matrix's
registry (``recon._matrix``), built once for each act (a survey, a stream's run, a checked dry
run) as ``build(**needs)`` with the needs its registration names (:data:`NEEDS`). The entry's
``config`` is the schema of the type's own keys in a collector entry: recon validates an entry's
keys against it and hands the validated value to every member in :attr:`Run.settings`, filled
for the run (a range's bounds). Building must be pure: recon builds every installed type once
per session with stand-in needs that refuse to be used, to read what it declares. One
collector serves every run of its type in an act, so it may hold a session across them (a
connection, a server it started) and end it in ``close()``.

**Source kinds** are entries at ``recon.source`` (:class:`SourceKind`), and **converters** at
``recon.converter`` (:class:`Converter`): a collector names the kinds it accepts, and a body's
media type names its converter.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, TypeVar, runtime_checkable

from recon.core.domain.errors import Problem
from recon.core.domain.stream import Gap

if TYPE_CHECKING:
    from pydantic import BaseModel

    from recon.core.domain.models import CollectorEntry, SourceEntry
    from recon.core.domain.paginate import TruncatedBy

#: What running a collector can touch. Reported by ``survey --dry-run`` for every planned
#: run; a type that declares ``effects=None`` is shown as ``unknown``, never as none.
Effect = Literal["network", "subprocess", "filesystem"]

#: Whether a type's records end, and who paces them: ``finite`` (each run ends: a snapshot
#: table), ``unbounded_recon_paced`` (they do not end, and recon reads the next when it is
#: ready: a file followed as it grows) or ``unbounded_source_paced`` (they do not end, and the
#: source sends whether recon is ready or not: server-sent events). An entry of an unbounded
#: type is a stream; its pacing decides the stream's default overflow.
RecordsKind = Literal["finite", "unbounded_recon_paced", "unbounded_source_paced"]

#: A normalize transform: the resolved value in, the normalized value out. Registered at the
#: ``recon.transform`` point and named in a normalize spec as ``path|$name``.
Transform = Callable[[Any], Any]

#: What a planned run would do, as a reviewer reads it (:meth:`Collector.describe`): for a
#: request ``method``, ``url`` (every query parameter, a credential as ``REDACTED``),
#: ``headers`` and ``body``; for commands ``commands`` and ``cwd``; for a server it starts or
#: reaches ``server``. A scheduled mission's plan reads ``commands`` and ``server``.
Intent = Mapping[str, Any]

M = TypeVar("M", bound="BaseModel")


@dataclass(frozen=True)
class Run:
    """One table's run: the entry it is for (named for its output), the source it runs against
    (None for a run with no source), and ``settings``, the type's own keys of the entry as its
    schema validated them, filled for this run (a ranged table's step). None when the type
    declares no schema."""

    entry: CollectorEntry
    source: SourceEntry | None
    settings: BaseModel | None = None

    @property
    def output(self) -> str:
        """The table the run lands: its entry's name."""
        return self.entry.name

    def settings_as(self, model: type[M]) -> M:
        """The run's settings as ``model``, the schema the type registered."""
        if not isinstance(self.settings, model):
            raise TypeError(
                f"run {self.entry.name!r} has settings {type(self.settings).__name__}, not "
                f"{model.__name__}: register the type with config={model.__name__}"
            )
        return self.settings


@dataclass(frozen=True)
class Position:
    """Where a stream's source goes on from: the position (``Record.position``) of the last
    record the stream kept, any JSON value the type gave. A stream resumed after a stop
    passes it to ``collect``, and the type gives what comes after it, so nothing is collected
    twice. (The range checkpoint, committed with a table at its seal, is another thing.)"""

    value: Any


@dataclass(frozen=True)
class BodyEnd:
    """What a body read as it arrives (:attr:`Body.data` a generator) returns when it ends:
    the ``status`` and the ``request`` its capture records, known only then (a command's exit
    code). A field left None keeps the :class:`Body`'s."""

    status: int | None = None
    request: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Body:
    """Bytes of a media type, as the type fetched them. recon captures them (with
    ``preserve_raw``), converts them with the converter ``converter`` names (a converter's
    config name: ``json``, ``xml``, ``lines``, ``document``, or a type URL), or else the one
    ``media_type`` picks, finds the records in what that gives (``extract:``) and shapes each
    (``normalize:``).

    ``data`` is the whole body, or the body as it arrives (an iterator of byte chunks: a
    command's stdout), which recon converts and captures as it flows; a generator may return
    a :class:`BodyEnd`. The capture records ``request`` (what was asked, as the capture may
    keep it), ``kind`` (the capture's ``kind``; None: the entry's type), ``status`` and
    ``headers`` (the answer's). ``secrets`` are credential values the type sent or resolved:
    recon keeps them out of the capture (its body and its request) and out of nothing else, so
    a record that holds one keeps it. ``origin`` names where the body is from (a URL) in a
    conversion's error. ``page`` is the body's page in a crawl the type ran itself: each of its
    records is positioned ``{page, i}``. ``fields`` are recon's own fields (each named ``_``)
    every record of the body carries, written with it (``_pattern``); ``scope`` is what a
    normalize spec may also read (each named ``_``), never written by itself. ``unfit`` is what
    recon does when ``extract:`` does not fit the body: ``fail`` (the default), as the source's
    error it most likely is (a page past the first that holds an error object); ``skip``, for
    one of many bodies of unrelated shape (a capture log's), which gives nothing, the table
    ending empty with the first reason only when none of its bodies fit."""

    media_type: str
    data: bytes | Iterator[bytes]
    request: Mapping[str, Any] = field(default_factory=dict)
    converter: str | None = None
    kind: str | None = None
    status: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    secrets: tuple[str, ...] = ()
    origin: str = ""
    page: int | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    scope: Mapping[str, Any] = field(default_factory=dict)
    unfit: Literal["fail", "skip"] = "fail"


@dataclass(frozen=True)
class Record:
    """A record the type has already structured (a line of a file, a server-sent event, a
    capture replayed). recon finds the records in it (``extract:``), shapes each
    (``normalize:``) and captures it. ``position`` is where it is in its source, for a
    stream to resume after (:class:`Position`); ``scope`` what a normalize spec may also read
    (each key named ``_``: the capture type's ``_capture``), never written by itself."""

    data: Mapping[str, Any]
    position: Any = None
    scope: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PageMark:
    """The type is about to ask for page ``index`` of a crawl it runs itself, by ``token``
    (None for the first page): said before it is asked for, so a stream that has ended stops
    the crawl there (recon stops reading the items)."""

    index: int
    token: Any = None


@dataclass(frozen=True)
class Dropped:
    """The type dropped one record rather than hold it (larger than the stream's
    ``max_record_bytes``): counted in the window being filled."""


@dataclass(frozen=True)
class Truncated:
    """The type's own crawl stopped short, ``by`` its cap, a loop or empty pages: the table is
    not a whole snapshot of its source."""

    by: TruncatedBy


#: What a run gives. A :class:`~recon.core.domain.stream.Gap` is a stretch its source was not
#: read (a stream's; a snapshot keeps none).
Item = Body | Record | PageMark | Gap | Dropped | Truncated


@runtime_checkable
class Collector(Protocol):
    """A collector type's collector: see the module docstring."""

    effects: frozenset[Effect] | None
    records: RecordsKind
    accepts: frozenset[str] | None

    def describe(self, run: Run) -> Intent:
        """What the run would do, for a reviewer. Pure: no I/O."""
        ...

    def check(self, run: Run) -> list[Problem]:
        """The problems in the run, paths relative to its entry (``endpoint``,
        ``params.query``), for what the schema cannot say (a run with no source when the type
        needs one). Pure: no I/O."""
        ...

    def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
        """The run's items, consumed lazily and closed if the consumer stops. ``resume`` is
        where a stream's run goes on from; None for a fresh one."""
        ...

    def close(self) -> None:
        """Let go of what the collector holds, once, however its act ended; from another
        thread, end a read in progress (a stream stops so)."""
        ...


@dataclass(frozen=True)
class Page:
    """One page a :class:`Paged` type was asked for: its ``body``, the ``next`` token the
    page names (None or ``""`` when it names none; for ``offset`` and ``page`` crawls, which
    count, recon computes the next itself), and ``token``, what names this page when it was
    asked for with none (a ``link`` crawl's first page, by its URL), so a page linking back to
    it is a loop."""

    body: Body
    next: Any = None
    token: Any = None


@runtime_checkable
class Paged(Protocol):
    """A type that reads its source page by page as recon asks (``paginate:``,
    :mod:`recon.core.domain.paginate`). recon runs the crawl: it asks for the first page with
    no token, then each next, stops by :class:`~recon.core.domain.paginate.Crawl`'s rules, keeps
    the page log, positions each record ``{page, i}``, and, for a stream's crawl resumed after
    a stop, asks again for the page it left off on and finds the last record it kept there
    (a page that moved is said ``shifted``; a token the source refuses with ``run_failed``
    begins the crawl again, said ``restarted``). How a page is asked for by its token is the
    type's own."""

    def page(self, run: Run, token: Any) -> Page:
        """The page ``token`` names (None: the first)."""
        ...


@dataclass(frozen=True)
class Requirement:
    """Something a planned run needs of this host, or of what it reaches, and whether it holds
    there now. ``need`` says what, in words that stay the same whether it holds or not (``the
    server of source 'papers' lists the tool 'search'``); ``why`` says what was found when it
    does not, and ``fix`` how to make it hold."""

    need: str
    holds: bool
    why: str = ""
    fix: str = ""


@runtime_checkable
class Preflight(Protocol):
    """A type whose runs need something of the host a plan cannot see (a server that starts, a
    tool it lists). ``preflight`` says in words what checking does; a checked dry run
    (``--check``) builds the type, asks ``requirements(run)`` of each run, and closes it. An
    unchecked plan builds nothing for it."""

    preflight: str

    def requirements(self, run: Run) -> list[Requirement]:
        """What the run needs, each checked now."""
        ...


@runtime_checkable
class SourceKind(Protocol):
    """A catalog source kind (a source's ``type:``), the entry at ``recon.source``: its config
    is the schema of the kind's own keys (None: it takes none, and needs ``url:``)."""

    def check(self, source: SourceEntry) -> list[Problem]:
        """The problems in a source of the kind whose own keys the schema accepted, paths
        relative to the source (``command``, ``url``). Pure."""
        ...

    def env(self, source: SourceEntry) -> dict[str, str]:
        """The environment variables such a source reads beyond ``auth.token``, each with
        where it names it (``{"GITHUB_TOKEN": "env.GITHUB_TOKEN"}``): the mission declares
        them for every type whose runs use the source. Pure."""
        ...


@runtime_checkable
class Converter(Protocol):
    """How bytes of a media type become values recon finds records in, the entry at
    ``recon.converter``. ``media_types`` are the ones it converts: ``type/subtype``, a
    structured suffix ``+json``, or ``type/*``; recon picks the most specific."""

    media_types: frozenset[str]

    def convert(self, chunks: Iterable[bytes], media_type: str, origin: str) -> Iterator[Any]:
        """The values the bytes hold, as they arrive: a JSON body's one value, a text body's
        line by line, a document's one ``{url, title, content, content_type}``. ``origin``
        (a URL, a capture's place) is named in an error. Raises CollectionError for bytes
        that are not what their media type says, and NoRecordsError for a body that holds
        nothing for a reason it can name."""
        ...


#: The needs recon supplies a collector type's build, each a keyword argument of its
#: ``build``, and the port of what it is given. A registration names those it takes.
NEEDS: Final[dict[str, str]] = {
    "requester": "the act's Requester (recon.core.domain.http): rate-limited HTTP, one span a "
    "request, within the act's deadline",
    "shell": "a ShellRunner (recon.core.domain.shell): runs a command, its process group "
    "killed when it is let go",
    "clock": "the Clock port (recon.core.domain.clock), which a type that waits waits on",
    "telemetry": "the act's Telemetry: a span opened there nests under the table's",
    "deadline": "the act's Deadline: what is left of the act's and the table's bounds in time",
    "rate_limiter": "the act's RateLimiter: one bucket per source, which a type that reaches a "
    "source other than through the requester draws from",
    "credentials": "Credentials (recon.core.domain.credentials): a source's credential, "
    "resolved the one way every kind's is",
    "env": "the environment variables the mission declares for the type, and no other",
    "user_agent": "what an HTTP request sends when its source sets none",
    "project": "the project root, the person's own directory: where a process the mission "
    "starts without naming one runs",
    "capture_logs": "CaptureLogs (recon.core.domain.capture): the capture logs of the mission "
    "a glob names",
}


@dataclass(frozen=True)
class BrokenPlugin:
    """An extension that could not be loaded or built. Reported, never fatal to the others."""

    group: str
    name: str
    value: str
    error: str

    def describe(self) -> str:
        return f"{self.group}:{self.name} ({self.value}) failed to load: {self.error}"


@dataclass(frozen=True)
class InstalledType:
    """What recon knows of one registered collector type: its config name, the package that
    registered it, its summary, its settings schema, how to build it and with what, and the
    collector recon built with stand-in needs, whose declared members the plan reads."""

    name: str
    build: Callable[..., Collector]
    needs: frozenset[str]
    declared: Collector
    settings: type[BaseModel] | None = None
    summary: str = ""
    package: str = "unknown"

    @property
    def paged(self) -> bool:
        return isinstance(self.declared, Paged)

    @property
    def preflight(self) -> str:
        """What a checked dry run does for the type; "" when it has no preflight."""
        return self.declared.preflight if isinstance(self.declared, Preflight) else ""

    @property
    def unbounded(self) -> bool:
        return self.declared.records != "finite"


@dataclass(frozen=True)
class InstalledKind:
    """What recon knows of one registered source kind."""

    name: str
    kind: SourceKind
    settings: type[BaseModel] | None = None
    summary: str = ""
    package: str = "unknown"


@dataclass(frozen=True)
class InstalledConverter:
    """What recon knows of one registered converter."""

    name: str
    converter: Converter
    summary: str = ""
    package: str = "unknown"


@dataclass(frozen=True)
class Plugins:
    """Everything installed at recon's points, by the name a config uses, and what failed."""

    collector_types: dict[str, InstalledType]
    source_kinds: dict[str, InstalledKind]
    converters: dict[str, InstalledConverter]
    transforms: dict[str, Transform]
    broken: list[BrokenPlugin]

    def accepts(self, type_name: str) -> frozenset[str] | None:
        """The source kinds an installed type accepts; None for any, or a type not
        installed."""
        found = self.collector_types.get(type_name)
        return found.declared.accepts if found is not None else None


class PlainKind:
    """A source kind with no keys of its own to judge and no variable of its own to declare
    (``api``, ``local``, ``file``): its sources need ``url:``, which the plan checks."""

    def check(self, source: SourceEntry) -> list[Problem]:
        return []

    def env(self, source: SourceEntry) -> dict[str, str]:
        return {}


def source_needed(run: Run, collects: str = "") -> list[Problem]:
    """The problem of a run with no source, for a type whose runs each need one: at the entry's
    ``source``, as the plan says every missing key. ``collects`` names the type in it."""
    if run.source is not None:
        return []
    return [
        Problem(
            "source",
            f"a {collects or run.entry.type} collector needs a source",
            kind="missing_key",
            fix="add a source under catalog: and name it here (or omit source: to fan out over "
            "the catalog's sources it can use)",
        )
    ]
