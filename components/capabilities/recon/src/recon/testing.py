"""The conformance kit: what every collector type must do, as tests a package runs against its
own type (``recon.extension``).

Mix the suite into a test class and say what to run::

    from recon.testing import CollectorConformance

    class TestSqlType(CollectorConformance):
        type_name = "acme.v1.collector.sql"

        def register(self, registry):
            acme.recon.register(registry)

        def entry(self):
            return {"name": "users", "type": self.type_name, "source": "db", "sql": "..."}

        def source(self):
            return {"name": "db", "type": "acme.v1.source.sqlite", "url": "test.db"}

        def needs(self):
            return {"credentials": ...}  # what its build names beyond the kit's defaults

pytest collects the suite's tests in that class. Each failure is an ``AssertionError`` saying
what the contract promises. A protocol check proves names alone ("simply having operations of
the right names and signatures is not enough"); these check behaviour: that recon reads what the
type declares, that describing and checking a run are pure, that a run's items are items recon
turns into records, that a run let go early and a collector closed from another thread stop,
that a stream's run resumes after a position, and that each optional protocol a type keeps does
what it says. What a type does beyond the contract is for its own tests.

The type is registered in-process, as code using recon registers one
(``recon.open(extensions=[register])``); a built-in's ``register`` is recon's own, so its suite
leaves :meth:`CollectorConformance.register` as it is. :func:`records` is recon's pipeline for
one run (conversion, ``extract:``, ``normalize:``, a crawl recon drives), for a type's own tests.
"""

from __future__ import annotations

import json
import tempfile
import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, get_args

from recon.composition import load_plugins, open_session
from recon.core.application.collectors import converters_of
from recon.core.application.items import Raw, Shaping, Unpacking, run_events
from recon.core.domain.collector import (
    NEEDS,
    Body,
    Collector,
    Dropped,
    Page,
    Paged,
    PageMark,
    Plugins,
    Position,
    Preflight,
    Record,
    RecordsKind,
    Requirement,
    Run,
    Truncated,
)
from recon.core.domain.deadline import Deadline
from recon.core.domain.errors import Problem
from recon.core.domain.models import CollectorEntry, SourceEntry
from recon.core.domain.normalize import BUILTIN_TRANSFORMS
from recon.core.domain.optional import SupportsClose
from recon.core.domain.ratelimit import RateLimiter
from recon.core.domain.stream import POSITION_FIELD, Gap
from recon.core.domain.telemetry import Telemetry

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from recon.core.domain.capture import CaptureLog
    from recon.core.domain.collector import InstalledType

__all__ = ["CollectorConformance", "QuietClock", "each_record", "records"]

#: How long a check waits for a collector to stop, once it is closed from another thread.
STOP_S = 5.0

#: The most items a check reads from a run of a type whose records do not end.
_MOST_ITEMS = 1000


def _expect(holds: bool, promise: str) -> None:
    if not holds:
        raise AssertionError(promise)


class QuietClock:
    """A clock whose time moves only by the waits asked of it, each recorded and skipped, so a
    type that waits between reads (a file followed, a reconnect) runs through its waits, and
    no deadline of the kit's passes."""

    #: When the kit's time starts.
    START = datetime(2026, 1, 1, tzinfo=UTC)

    def __init__(self) -> None:
        self.slept: list[float] = []
        self._lock = threading.Lock()

    def now(self) -> datetime:
        return self.START + timedelta(seconds=self.monotonic())

    def monotonic(self) -> float:
        with self._lock:
            return sum(self.slept)

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.slept.append(seconds)


def each_record(
    collector: Collector,
    run: Run,
    plugins: Plugins | None = None,
    *,
    resume: Position | None = None,
    captures: CaptureLog | None = None,
) -> Generator[dict[str, Any], None, None]:
    """The records recon makes of one run of ``collector``, as an act writes them, each as it
    is made: every item converted (by the converters ``plugins`` holds, recon's own by
    default), found with the entry's ``extract:``, shaped with its ``normalize:`` (the pure
    transforms), positioned; a crawl recon drives for an entry with ``paginate:`` of a
    ``Paged`` type. With ``captures``, each body is captured there as an act with
    ``preserve_raw`` captures it. Closing this closes the run."""
    installed = plugins if plugins is not None else load_plugins()
    unpacking = Unpacking(converters_of(installed), captures)
    shaping = Shaping(run.entry.normalize, dict(BUILTIN_TRANSFORMS))
    events = run_events(collector, run, unpacking, shaping, position=resume)
    try:
        for event in events:
            if isinstance(event, Raw):
                yield shaping.raw(event)
    finally:
        events.close()
    unpacking.end()


def records(
    collector: Collector,
    run: Run,
    plugins: Plugins | None = None,
    *,
    resume: Position | None = None,
    most: int | None = None,
    captures: CaptureLog | None = None,
) -> list[dict[str, Any]]:
    """:func:`each_record`'s records, as a list; ``most`` stops after that many, for a run that
    does not end."""
    found: list[dict[str, Any]] = []
    made = each_record(collector, run, plugins, resume=resume, captures=captures)
    try:
        for record in made:
            found.append(record)
            if most is not None and len(found) >= most:
                break
    finally:
        made.close()
    return found


class CollectorConformance:
    """What every collector type does. Set :attr:`type_name`; define :meth:`entry` (one run
    whose items give at least one record), and :meth:`source`, :meth:`needs` and
    :meth:`register` when the type needs them; :meth:`paged_entry` for a ``Paged`` type."""

    #: The type's config name: a built-in's short name, any other's type URL.
    type_name: ClassVar[str] = ""

    def register(self, registry: Any) -> None:
        """Register the type, as its package's ``matrix.extensions`` entry point does; a
        built-in is registered already."""
        return None

    def entry(self) -> dict[str, Any]:
        """A collector entry, as a mission writes it, whose run gives at least one record."""
        raise NotImplementedError("define entry() to return a collector entry of the type")

    def source(self) -> dict[str, Any] | None:
        """The catalog source the entry's run runs against; None for a run with none."""
        return None

    def needs(self) -> dict[str, Any]:
        """What the type's build is given beyond the kit's defaults (a clock that never waits,
        telemetry with no sink, a deadline and a rate limiter of their own, no environment, a
        user agent, no project): what reaches the source, as fakes (a ``requester``, a
        ``shell``, ``credentials``, ``capture_logs``)."""
        return {}

    def paged_entry(self) -> dict[str, Any] | None:
        """For a ``Paged`` type: an entry with ``paginate:`` whose crawl gives records."""
        return None

    # --- what the checks share ---

    def _plugins(self) -> Plugins:
        return load_plugins([self.register] if type(self).register is not _BASE_REGISTER else [])

    def _installed(self) -> InstalledType:
        plugins = self._plugins()
        broken = [b.describe() for b in plugins.broken if self.type_name in (b.name, b.value)]
        _expect(not broken, f"recon reads the type's registration: {'; '.join(broken)}")
        found = plugins.collector_types.get(self.type_name)
        _expect(found is not None, f"{self.type_name!r} is registered at recon.collector")
        assert found is not None
        return found

    def _run(self, written: Mapping[str, Any] | None = None) -> Run:
        installed = self._installed()
        entry = CollectorEntry.model_validate(dict(written or self.entry()))
        source_written = self.source()
        source = SourceEntry.model_validate(source_written) if source_written else None
        settings = None
        if installed.settings is not None:
            settings = installed.settings.model_validate(entry.settings)
        return Run(entry, source, settings)

    def _built(self) -> Collector:
        installed = self._installed()
        clock = QuietClock()
        defaults: dict[str, Any] = {
            "clock": clock,
            "telemetry": Telemetry([], clock),
            "deadline": Deadline(clock),
            "rate_limiter": RateLimiter(clock),
            "env": {},
            "user_agent": "recon-conformance",
            "project": None,
        }
        given = {**defaults, **self.needs()}
        missing = sorted(need for need in installed.needs if need not in given)
        _expect(not missing, f"needs() gives what the type's build names: {', '.join(missing)}")
        built = installed.build(**{need: given[need] for need in installed.needs})
        _expect(isinstance(built, Collector), "its build gives a Collector")
        return built

    # --- the checks ---

    def test_recon_reads_what_it_declares(self) -> None:
        declared = self._installed().declared
        _expect(isinstance(declared, Collector), "it keeps the Collector protocol")
        _expect(
            declared.records in get_args(RecordsKind),
            f"records is one of {', '.join(get_args(RecordsKind))}",
        )
        _expect(
            declared.effects is None or isinstance(declared.effects, frozenset),
            "effects is None (unknown) or a frozenset of effects",
        )
        _expect(
            declared.accepts is None or isinstance(declared.accepts, frozenset),
            "accepts is None (any kind) or a frozenset of source kinds",
        )
        _expect(set(self._installed().needs) <= set(NEEDS), "it names only needs recon supplies")

    def test_its_schema_validates_the_entry_and_refuses_an_unknown_key(self) -> None:
        installed = self._installed()
        entry = CollectorEntry.model_validate(self.entry())
        if installed.settings is None:
            _expect(not entry.settings, "a type with no schema takes no key of its own")
            return
        installed.settings.model_validate(entry.settings)
        unknown = {**entry.settings, "conformance_unknown_key": 1}
        try:
            installed.settings.model_validate(unknown)
        except ValueError:
            return
        raise AssertionError("its schema refuses a key it does not know")

    def test_describe_and_check_are_pure_and_say_what_they_promise(self) -> None:
        declared = self._installed().declared
        run = self._run()
        described = declared.describe(run)
        _expect(isinstance(described, Mapping), "describe gives a mapping")
        json.dumps(dict(described))  # a plan is JSON: what it shows must be
        _expect(dict(declared.describe(run)) == dict(described), "describe is pure: same run, same")
        problems = declared.check(run)
        _expect(isinstance(problems, list), "check gives a list")
        _expect(all(isinstance(p, Problem) for p in problems), "each problem is a Problem")
        _expect(not problems, f"the kit's entry is a valid run: {problems}")
        _expect(declared.check(run) == problems, "check is pure: same run, same")

    def test_the_plan_accepts_its_entry_and_shows_its_run(self) -> None:
        source = self.source()
        config = {"collectors": [self.entry()], "catalog": [source] if source else []}
        extensions = [self.register] if type(self).register is not _BASE_REGISTER else []
        with tempfile.TemporaryDirectory() as project:
            plan = open_session(Path(project), extensions=extensions).observation.plan(
                "conformance", config=config
            )
        _expect(plan.valid and not plan.problems, f"the plan holds no problem: {plan.problems}")
        _expect(len(plan.runs) == 1, "the plan shows the entry's one run")

    def test_a_run_gives_items_recon_turns_into_records(self) -> None:
        collector = self._built()
        run = self._run()
        unbounded = self._installed().declared.records != "finite"
        try:
            items = list(_items(collector.collect(run, None), unbounded))
            for item in items:
                _expect(
                    isinstance(item, Body | Record | PageMark | Gap | Dropped | Truncated),
                    f"each item is a Body, Record, PageMark, Gap, Dropped or Truncated: {item!r}",
                )
                scope = item.scope if isinstance(item, Body | Record) else {}
                fields = item.fields if isinstance(item, Body) else {}
                _expect(
                    all(key.startswith("_") for key in (*scope, *fields)),
                    "an item's scope and fields are recon's own names, each starting _",
                )
        finally:
            collector.close()
        collector = self._built()
        try:
            found = records(collector, run, self._plugins(), most=1 if unbounded else None)
        finally:
            collector.close()
        _expect(bool(found), "recon makes at least one record of the kit's run")
        _expect(all(isinstance(r, dict) for r in found), "each record is an object")

    def test_a_run_let_go_early_is_let_go_and_the_collector_closes(self) -> None:
        collector = self._built()
        items = collector.collect(self._run(), None)
        try:
            next(iter(items), None)
        finally:
            if isinstance(items, SupportsClose):
                items.close()
            collector.close()

    def test_it_closes_without_having_run(self) -> None:
        self._built().close()

    def test_close_from_another_thread_ends_its_work(self) -> None:
        collector = self._built()
        run = self._run()
        first = threading.Event()
        ended = threading.Event()
        failure: list[BaseException] = []

        def read() -> None:
            try:
                for item in collector.collect(run, None):
                    if isinstance(item, Body | Record):
                        first.set()
            except BaseException as exc:  # a read ended by close may raise: that is its end
                failure.append(exc)
            finally:
                first.set()
                ended.set()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        first.wait(STOP_S)
        collector.close()
        _expect(ended.wait(STOP_S), f"close() ends a run in progress within {STOP_S:g}s")

    def test_a_resumed_run_gives_what_comes_after_its_position(self) -> None:
        run = self._run()
        collector = self._built()
        try:
            given = [
                item
                for item in _items(collector.collect(run, None), stop_after=2)
                if isinstance(item, Record) and item.position is not None
            ]
        finally:
            collector.close()
        if not given:
            return  # its records carry no position: a stream of it resumes from its start
        collector = self._built()
        try:
            again = records(collector, run, self._plugins(), resume=Position(given[0].position),
                            most=1)  # fmt: skip
        finally:
            collector.close()
        _expect(
            not again or again[0].get(POSITION_FIELD) != given[0].position,
            "a run resumed after a record's position does not give that record again",
        )

    def test_each_optional_protocol_it_keeps_does_what_it_says(self) -> None:
        declared = self._installed().declared
        if isinstance(declared, Preflight):
            _expect(isinstance(declared.preflight, str) and bool(declared.preflight),
                    "a Preflight type says what checking does, in words")  # fmt: skip
        if not isinstance(declared, Paged):
            return
        written = self.paged_entry()
        _expect(written is not None, "a Paged type's suite gives paged_entry()")
        assert written is not None
        run = self._run(written)
        collector = self._built()
        try:
            assert isinstance(collector, Paged)
            page = collector.page(run, None)
            _expect(isinstance(page, Page), "page() gives a Page")
            _expect(isinstance(page.body, Body), "a Page holds the page's Body")
        finally:
            collector.close()
        collector = self._built()
        try:
            crawled = records(collector, run, self._plugins())
        finally:
            collector.close()
        _expect(bool(crawled), "recon's crawl of the paged entry gives records")
        _expect(
            all(isinstance(r.get(POSITION_FIELD), dict) for r in crawled),
            "each record of a crawl recon drives is positioned {page, i}",
        )

    def test_a_preflight_answers_with_requirements(self) -> None:
        declared = self._installed().declared
        if not isinstance(declared, Preflight):
            return
        collector = self._built()
        try:
            assert isinstance(collector, Preflight)
            found = collector.requirements(self._run())
        finally:
            collector.close()
        _expect(isinstance(found, list), "requirements gives a list")
        _expect(all(isinstance(r, Requirement) for r in found), "each is a Requirement")


_BASE_REGISTER: Callable[..., None] = CollectorConformance.register


def _items(
    items: Iterator[Any], unbounded: bool = False, *, stop_after: int | None = None
) -> Iterator[Any]:
    """``items``, all of them for a run that ends; for one that does not, up to its first
    record (or ``stop_after`` records), closed then."""
    wanted = stop_after if stop_after is not None else (1 if unbounded else None)
    seen = 0
    try:
        for count, item in enumerate(items):
            yield item
            if isinstance(item, Body | Record):
                seen += 1
            if wanted is not None and seen >= wanted:
                return
            if count >= _MOST_ITEMS:
                raise AssertionError(f"a run gave {_MOST_ITEMS} items and no record")
    finally:
        if isinstance(items, SupportsClose):
            items.close()
