"""recon's one seam to matrix: the only module of recon that names it (``layers.json``'s
``single_importer`` rule), so a move of the harness contract to another one (slick's) changes
this module and no other (owner ruling, REQ §2; RUBRIC H6).

**The registry** (rulings R2, R14). recon's extension points are matrix ``Registry`` points,
owned by the distribution ``recon``:

- ``recon.collector``: a collector type, named in a config as ``type:``. The entry is
  ``build(**needs)`` giving a :class:`~recon.core.domain.collector.Collector`, with the needs it
  names (``recon.core.domain.collector.NEEDS``); its ``config`` is the schema of the type's own
  keys in a collector entry, handed to every member in ``Run.settings``;
- ``recon.source``: a source kind, named in a catalog as ``type:``: ``build()`` giving a
  :class:`~recon.core.domain.collector.SourceKind`, its ``config`` the kind's own keys;
- ``recon.converter``: a converter, picked by a body's media type: ``build()`` giving a
  :class:`~recon.core.domain.collector.Converter`;
- ``recon.transform``: a transform, named in a normalize spec as ``path|$name``: ``build()``
  giving the callable.

recon's built-ins register at them from recon's own ``matrix.extensions`` entry point
(:func:`register`), each adapter module through its own ``register(registry)``, as a third
party's do from theirs: a built-in by a short name (``type: api``, ``$html2text``:
``recon.v1.collector.api``, ``recon.v1.transform.html2text``), anyone else's by its full type
URL (``type: acme.v1.collector.sql``), ruling R3. Code using recon registers its own in-process
(``recon.open(extensions=[register])``), at the same points and under the same rules.

matrix's own rules hold: a type URL with no version segment is refused, two registrations of
one type URL are refused naming both, and an extension that fails to load is quarantined whole,
every entry it registered with it (``recon types --json`` and ``recon status`` say which).
Discovery loads every installed ``matrix.extensions`` entry point, so a session imports each
installed package's registration (review C3).

Reading the entries back (:func:`plugins`), recon builds each collector type once with stand-in
needs that refuse to be used, to read what it declares and which optional protocols it keeps
(``Paged``, ``Preflight``); an entry that does not keep its point's contract is reported broken
and left out, and so is a converter that claims a media type another already converts (recon's
own keep theirs).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from matrix import ConfigError, MatrixError, Registry, default_registry
from pydantic import BaseModel, ConfigDict

from recon.core.domain.collector import (
    NEEDS,
    BrokenPlugin,
    Collector,
    Converter,
    InstalledConverter,
    InstalledKind,
    InstalledType,
    Plugins,
    SourceKind,
)
from recon.core.domain.converters import claim_problem, claimed
from recon.core.domain.documents import Act, Change, Span
from recon.core.domain.errors import ReconError, UsageError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from matrix import Context, Entry, Inputs

    from recon.composition import Recon, StreamStop
    from recon.core.domain.collector import Transform

#: recon's extension points on matrix's registry.
COLLECTOR = "recon.collector"
SOURCE = "recon.source"
CONVERTER = "recon.converter"
TRANSFORM = "recon.transform"
POINTS = (COLLECTOR, SOURCE, CONVERTER, TRANSFORM)

#: The values a collector's ``records`` may hold, and its ``effects``.
_RECORDS = frozenset({"finite", "unbounded_recon_paced", "unbounded_source_paced"})
_EFFECTS = frozenset({"network", "subprocess", "filesystem"})

#: The group a broken extension is reported under: matrix's.
GROUP = "matrix.extensions"

#: What matrix records as the origin of an entry registered directly, not from an entry point.
_DIRECT = "(direct)"

#: What recon calls an extension given to ``recon.open``.
IN_PROCESS = "in_process"

#: An in-process extension: called with the session's registry, it registers at recon's points.
type Register = Callable[[Registry], None]


def type_url(point: str, name: str) -> str:
    """A built-in's type URL from its short name (``api`` at ``recon.collector`` is
    ``recon.v1.collector.api``; a type URL's segments are kebab-case, so ``inverted_index``
    is ``recon.v1.transform.inverted-index``); a full type URL passes through."""
    if "." in name:
        return name
    return f"recon.v1.{point.removeprefix('recon.')}.{name.replace('_', '-')}"


def short(point: str, url: str) -> str:
    """The name a config uses for the entry at ``url``: a built-in's short name (its last
    segment, as a config spells it), anyone else's type URL as it stands."""
    prefix = f"recon.v1.{point.removeprefix('recon.')}."
    rest = url.removeprefix(prefix)
    return rest.replace("-", "_") if url.startswith(prefix) and "." not in rest else url


def register(registry: Registry) -> None:
    """recon's ``matrix.extensions`` entry point: its points, and its built-ins at them."""
    from recon.composition import builtins

    for point in POINTS:
        registry.add_point(point)
    register_components(registry)
    registers, transforms = builtins()
    for register_builtin in registers:
        register_builtin(registry)
    for name, transform in transforms.items():
        summary = (transform.__doc__ or "").strip().split("\n", 1)[0]
        registry.register(TRANSFORM, type_url(TRANSFORM, name), _giving(transform),
                          summary=summary)  # fmt: skip


def plugins(extensions: Iterable[Register] = (), *, stand_ins: Mapping[str, Any]) -> Plugins:
    """Everything at recon's points: what every installed ``matrix.extensions`` entry point
    registers, recon's own among them, then what each of ``extensions`` registers in-process;
    each collector type built with ``stand_ins`` to read what it declares; with each extension
    and entry that failed, and whose each entry is (its distribution, or ``in_process``). An
    extension given that raises is not quarantined: its error is the caller's, and one matrix
    refuses (a type URL with no version, one registered already, a point recon does not have)
    is a UsageError saying why."""
    registry = default_registry()
    for extend in extensions:
        try:
            extend(registry)
        except MatrixError as exc:  # matrix's refusal, said as recon's
            raise UsageError(
                f"an extension given could not register: {exc.message}",
                fix=exc.fix
                or "register at recon.collector, recon.source, recon.converter or "
                "recon.transform, under a type URL of your own (<namespace>.v<N>.<name>)",
            ) from exc
    broken = [
        BrokenPlugin(GROUP, failure.extension, failure.origin, failure.error)
        for failure in registry.failures
    ]
    types: dict[str, InstalledType] = {}
    for entry in _entries(registry, COLLECTOR):
        name = short(COLLECTOR, entry.type_url)
        unknown = sorted(entry.needs - set(NEEDS))
        why = _settings_problem(entry.config) or (
            f"it needs {', '.join(unknown)}, which recon does not supply; recon supplies "
            f"{', '.join(sorted(NEEDS))}"
            if unknown
            else None
        )
        if why is not None:
            broken.append(BrokenPlugin(GROUP, name, entry.type_url, why))
            continue
        given = {need: stand_ins[need] for need in entry.needs}
        declared = _built(partial(entry.build, **given), entry.type_url, broken)
        if declared is None:
            continue
        why = _collector_problem(declared)
        if why is not None:
            broken.append(BrokenPlugin(GROUP, name, entry.type_url, why))
            continue
        types[name] = InstalledType(
            name=name, build=entry.build, needs=entry.needs, declared=declared,
            settings=entry.config, summary=entry.summary, package=_package(entry.origin),
        )  # fmt: skip
    kinds: dict[str, InstalledKind] = {}
    for entry in _entries(registry, SOURCE):
        name = short(SOURCE, entry.type_url)
        why = _settings_problem(entry.config) or _needless(entry.needs)
        built = None if why else _built(entry.build, entry.type_url, broken)
        if why is None and built is not None and not isinstance(built, SourceKind):
            why = f"expected a recon SourceKind (check, env), got {type(built).__name__}"
        if why is not None:
            broken.append(BrokenPlugin(GROUP, name, entry.type_url, why))
        if why is not None or built is None:
            continue
        kinds[name] = InstalledKind(name, built, entry.config, entry.summary,
                                    _package(entry.origin))  # fmt: skip
    converters = _converters(registry, broken)
    transforms: dict[str, Transform] = {}
    for entry in registry.entries(TRANSFORM) if TRANSFORM in registry.points else ():
        name = short(TRANSFORM, entry.type_url)
        built = _built(entry.build, entry.type_url, broken)
        if built is None:
            continue
        if not callable(built):
            error = f"expected a callable, got {type(built).__name__}"
            broken.append(BrokenPlugin(GROUP, name, entry.type_url, error))
            continue
        transforms[name] = built
    return Plugins(types, kinds, converters, transforms, broken)


def _entries(registry: Registry, point: str) -> list[Entry]:
    return registry.entries(point) if point in registry.points else []


def _package(origin: str) -> str:
    return IN_PROCESS if origin == _DIRECT else origin


def _needless(needs: frozenset[str]) -> str | None:
    if needs:
        return f"it needs {', '.join(sorted(needs))}; an entry at this point is built with none"
    return None


def _settings_problem(config: type[BaseModel] | None) -> str | None:
    """Why an entry's ``config`` cannot be the schema of a mission's keys: it must refuse a key
    it does not know, so a misspelt one is a plan problem."""
    if config is not None and config.model_config.get("extra") != "forbid":
        return (
            f"its config {config.__name__} lets unknown keys through: give it "
            "model_config = ConfigDict(extra='forbid'), so a misspelt key is a plan problem"
        )
    return None


def _collector_problem(declared: Any) -> str | None:
    """Why what a ``recon.collector`` entry built is not a Collector, or None."""
    if not isinstance(declared, Collector):
        lacking = [m for m in ("effects", "records", "accepts", "describe", "check", "collect",
                               "close") if not hasattr(declared, m)]  # fmt: skip
        return f"its build gave a {type(declared).__name__}, which lacks {', '.join(lacking)}"
    if declared.records not in _RECORDS:
        return f"its records is {declared.records!r}; one of {', '.join(sorted(_RECORDS))}"
    effects = declared.effects
    if effects is not None and not (isinstance(effects, frozenset) and effects <= _EFFECTS):
        return f"its effects is {effects!r}; None, or a frozenset of {', '.join(sorted(_EFFECTS))}"
    accepts = declared.accepts
    if accepts is not None and not (
        isinstance(accepts, frozenset) and all(isinstance(k, str) for k in accepts)
    ):
        return f"its accepts is {accepts!r}; None, or a frozenset of source kinds"
    return None


def _converters(registry: Registry, broken: list[BrokenPlugin]) -> dict[str, InstalledConverter]:
    """Each converter at ``recon.converter``, recon's own first; one that claims a media type
    another already converts is left out, said why."""
    found: list[InstalledConverter] = []
    ordered = sorted(_entries(registry, CONVERTER),
                     key=lambda e: (not e.type_url.startswith("recon."), e.type_url))  # fmt: skip
    for entry in ordered:
        name = short(CONVERTER, entry.type_url)
        why = _needless(entry.needs)
        built = None if why else _built(entry.build, entry.type_url, broken)
        if why is None and built is not None:
            if not isinstance(built, Converter) or not isinstance(built.media_types, frozenset):
                why = (
                    f"expected a recon Converter (media_types, convert), got {type(built).__name__}"
                )
            else:
                bad = [c for c in sorted(built.media_types) if claim_problem(c)]
                why = f"it claims {', '.join(bad)}: {claim_problem(bad[0])}" if bad else None
        if why is None and built is not None:
            _, refused = claimed([*((c.name, c.converter) for c in found), (name, built)])
            why = refused[0] if refused else None
        if why is not None:
            broken.append(BrokenPlugin(GROUP, name, entry.type_url, why))
        if why is not None or built is None:
            continue
        found.append(InstalledConverter(name, built, entry.summary, _package(entry.origin)))
    return {converter.name: converter for converter in found}


def _built(build: Callable[[], Any], url: str, broken: list[BrokenPlugin]) -> Any:
    """What an entry builds; None, said as broken, when building it raises."""
    try:
        return build()
    except Exception as exc:  # one extension's failure must not stop the others
        broken.append(BrokenPlugin(GROUP, url, url, f"{type(exc).__name__}: {exc}"))
        return None


def _giving(value: object) -> Callable[[], Any]:
    """An entry's build that gives ``value``: a built-in, made when recon registers it."""

    def build() -> Any:
        return value

    return build


# --- the component face ------------------------------------------------------------------

#: The kinds recon's flow members exchange, each a payload schema of recon's (ruling R4).
SPAN = "recon.v1.span"
ACT = "recon.v1.act"
CHANGE = "recon.v1.change"

#: The three flow members, at matrix's ``component`` point.
PLAN = "recon.v1.component.plan"
COLLECT = "recon.v1.component.collect"
CHANGES = "recon.v1.component.changes"


class Kinds(BaseModel):
    """The kinds a member requires and provides, each renamable: matrix lets one component
    provide a kind (ruling R7: renamable until fan-in is legal), so two recon members of one
    flow name theirs apart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    span: str = SPAN
    act: str = ACT
    change: str = CHANGE


class MemberConfig(BaseModel):
    """A recon flow member's config: the mission it collects, the project it is in (default:
    the directory the host composed for), and its kinds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mission: str
    root: str | None = None
    kinds: Kinds = Kinds()


class CollectConfig(MemberConfig):
    """``records: true`` hands each sealed table's records over too, by value, as rows of the
    kind its entry's ``type_url`` names; by default only each act's row, which names the
    archive the records are in (ruling R4: by reference)."""

    records: bool = False


class PlanMember:
    """``recon.v1.component.plan`` (the observe layer): what each table of the mission is to
    collect now, as ``recon.v1.span`` rows. A harness that schedules recon itself leaves it out
    and gives the spans as a run input."""

    def __init__(self, session: Recon, config: MemberConfig) -> None:
        self._session = session
        self._config = config
        self.requires: tuple[str, ...] = ()
        self.provides = (config.kinds.span,)

    async def run(self, inputs: Inputs, ctx: Context) -> Mapping[str, Any]:
        spans = await asyncio.to_thread(self._session.observation.spans, self._config.mission)
        return {self._config.kinds.span: [span.document() for span in spans]}


class CollectMember:
    """``recon.v1.component.collect`` (the core): the acts the spans ask for, each act's row
    (``recon.v1.act``) handed over as it seals, so a killed run keeps every act it sealed. The
    core is synchronous and runs on a worker thread of its own (ruling R16); the run's stop asks
    a stream's window to seal."""

    def __init__(
        self, session: Recon, config: CollectConfig, *, record_kinds: Sequence[str]
    ) -> None:
        self._session = session
        self._config = config
        self.requires = (config.kinds.span,)
        self.provides = (config.kinds.act, *record_kinds)

    async def run(self, inputs: Inputs, ctx: Context) -> None:
        from recon.composition import StreamStop

        spans = [Span.model_validate(row) for row in inputs[self._config.kinds.span]]
        hand = _Handing(asyncio.get_running_loop(), ctx)
        types = _type_urls(self._session, self._config.mission) if self._config.records else {}
        stop = StreamStop()

        def sealed(act: Act) -> None:
            hand.over(self._config.kinds.act, act.document())
            for table in act.tables if self._config.records else ():
                if table.status == "ok" and table.name in types:
                    for record in self._session.collection.records(act, table.name):
                        hand.over(types[table.name], record)

        watch = asyncio.ensure_future(_stopping(ctx, stop))
        try:
            await asyncio.to_thread(self._session.collection.collect, self._config.mission,
                                    spans, sink=sealed, stop=stop)  # fmt: skip
        finally:
            watch.cancel()


class ChangesMember:
    """``recon.v1.component.changes`` (the observe layer): what changed in each act's tables,
    derived into its archive after its seal, as ``recon.v1.change`` rows."""

    def __init__(self, session: Recon, config: MemberConfig) -> None:
        self._session = session
        self._config = config
        self.requires = (config.kinds.act,)
        self.provides = (config.kinds.change,)

    async def run(self, inputs: Inputs, ctx: Context) -> None:
        for row in inputs[self._config.kinds.act]:
            act = Act.model_validate(row)
            changes = await asyncio.to_thread(self._session.observation.changes_of,
                                              self._config.mission, act)  # fmt: skip
            for change in changes:
                ctx.provide(self._config.kinds.change, change.document())


class _Handing:
    """Hands rows over from the worker thread an act runs on: each on the run's loop, waited
    for, so a row is durable in the run's store when ``over`` returns (matrix's ``provide``)."""

    def __init__(self, loop: asyncio.AbstractEventLoop, ctx: Context) -> None:
        self._loop = loop
        self._ctx = ctx

    def over(self, kind: str, row: Any) -> None:
        done: concurrent.futures.Future[None] = concurrent.futures.Future()

        def provide() -> None:
            try:
                self._ctx.provide(kind, row)
            except BaseException as exc:  # the worker raises it, as provide would have
                done.set_exception(exc)
            else:
                done.set_result(None)

        self._loop.call_soon_threadsafe(provide)
        done.result()


async def _stopping(ctx: Context, stop: StreamStop) -> None:
    """Ask ``stop`` once the run asks the member to finish now."""
    await ctx.stopping.wait()
    stop.ask()


def _type_urls(session: Recon, mission: str) -> dict[str, str]:
    """The kind each snapshot output's records are, by output: its entry's ``type_url``."""
    prepared = session.collection.prepare(mission)
    entries = {entry.name: entry for entry in prepared.config.collectors}
    return {run.output: entries[run.collector].type_url for run in prepared.plan.runs
            if run.stream is None}  # fmt: skip


def _member[M](make: Callable[[Recon], M], config: MemberConfig, cwd: str | None) -> M:
    """A member over the session of its project, refusing at build time (matrix's
    ``ConfigError``, B9) a mission its config cannot plan."""
    from recon.composition import open_session

    try:
        session = open_session(Path(config.root or cwd or Path.cwd()))
        session.collection.prepare(config.mission)
        return make(session)
    except ReconError as exc:
        raise ConfigError(f"recon mission {config.mission!r}: {exc.message}", fix=exc.fix) from exc


def _plan(config: MemberConfig, *, cwd: str | None) -> PlanMember:
    return _member(lambda session: PlanMember(session, config), config, cwd)


def _collect(config: CollectConfig, *, cwd: str | None) -> CollectMember:
    def make(session: Recon) -> CollectMember:
        kinds = sorted(set(_type_urls(session, config.mission).values())) if config.records else []
        return CollectMember(session, config, record_kinds=kinds)

    return _member(make, config, cwd)


def _changes(config: MemberConfig, *, cwd: str | None) -> ChangesMember:
    return _member(lambda session: ChangesMember(session, config), config, cwd)


def register_components(registry: Registry) -> None:
    """recon's three flow members at matrix's ``component`` point, and the payload schemas of
    the kinds they exchange."""
    members = (
        (PLAN, _plan, MemberConfig,
         "What each table of a recon mission is to collect now; provides recon.v1.span"),
        (COLLECT, _collect, CollectConfig,
         "Runs the acts the spans ask for; provides recon.v1.act as each seals"),
        (CHANGES, _changes, MemberConfig,
         "What changed in each act's tables, derived after the seal; provides recon.v1.change"),
    )  # fmt: skip
    for url, build, model, summary in members:
        registry.register("component", url, build, config=model, needs={"cwd"}, summary=summary)
    for kind, payload in ((SPAN, Span), (ACT, Act), (CHANGE, Change)):
        registry.register_payload(kind, payload)
