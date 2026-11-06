"""The observe layer's driving port: collecting over time, with memory of what was seen.

:class:`Observation` decides which acts the core runs and when, and derives what consumers
read: the span each ranged table asks for (after what it committed), a survey's snapshot act
and one window of each stream, a stream's windows over time, what changed after each seal,
what retention lets go, schedules, subscriptions and their delivery, and status. It reaches
the core only through ``recon.core``'s public names, as an outside driver would.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from recon.core import (
    READ_MAX_BYTES,
    READ_MAX_RECORDS,
    WATCH_INTERVAL_S,
    ArchiveRef,
    Change,
    ConfigError,
    NotFoundError,
    PlanDocument,
    PlanWrites,
    Problem,
    SourcedValue,
    Span,
    StreamDocument,
    SurveyDocument,
    TableOutcome,
    UsageError,
    changes_output,
    interval_of,
    iso,
    once_each,
)
from recon.observe.application.changes import MEMORY_KEYS, ChangeDetector, Derivation
from recon.observe.application.follow import Follow
from recon.observe.application.ranges import range_runs
from recon.observe.application.retention import Retention
from recon.observe.application.schedules import Schedules, stale
from recon.observe.application.status import Status
from recon.observe.application.subscriptions import Subscriptions

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator
    from datetime import datetime

    from recon.core import (
        Act,
        Archives,
        ChangesSince,
        Clock,
        ConfigFile,
        ConfigGiven,
        DeliverDocument,
        EventCallback,
        Hold,
        Interval,
        MissionStore,
        Plan,
        PlannedStream,
        Platform,
        Prepared,
        ReconConfig,
        RecordsSince,
        ScheduleDocument,
        SchedulesDocument,
        StatusDocument,
        Stop,
        StreamRun,
        SubscriptionDocument,
        TickDocument,
        WindowOutcome,
        WindowSpec,
    )
    from recon.core import Collection as CoreCollection
    from recon.observe.application.ranges import RangeRuns
    from recon.observe.domain.delivery import Deliverer, SubscriptionStore
    from recon.observe.domain.joins import KeyJoin
    from recon.observe.domain.scheduling import ScheduleHost, Scheduler, ScheduleStore


class Observation:
    """recon's observe operations, over the core's ``collection``."""

    def __init__(
        self,
        *,
        collection: CoreCollection,
        missions: MissionStore,
        archives: Archives,
        clock: Clock,
        schedulers: Callable[[Platform | None], Scheduler] | None = None,
        schedule_store: ScheduleStore | None = None,
        schedule_host: ScheduleHost | None = None,
        local: Callable[[datetime], datetime] | None = None,
        key_join: Callable[[str], KeyJoin] | None = None,
        memory_keys: int = MEMORY_KEYS,
        subscriptions: SubscriptionStore | None = None,
        deliverer: Deliverer | None = None,
    ) -> None:
        """``schedulers`` makes the operating system's scheduler of a platform (None: the
        host's own), which ``schedule_*`` run through, with the ``schedule_store`` beside each
        mission, the ``schedule_host``'s recon command and PATH, and ``local``, the clock's time
        as the host's local time; without them, the ``schedule_*`` operations are refused.
        ``key_join`` gives the join of a mission's change detection's comparisons of more than
        ``memory_keys`` keys, by the mission's name (None: all in memory). ``subscriptions``
        keeps what each subscription acked (None: the ``subscribe``, ``ack``, ``unsubscribe``
        and ``deliver`` operations are refused); ``deliverer`` gives a subscription's
        deliveries to its ``command:`` (None: ``deliver`` is refused)."""
        self._collection = collection
        self._missions = missions
        self._archives = archives
        self._clock = clock
        self._key_join = key_join
        self._memory_keys = memory_keys
        self._follow = Follow(collection=collection, archives=archives, missions=missions,
                              clock=clock)  # fmt: skip
        self._schedules = (
            None
            if schedulers is None or schedule_store is None or schedule_host is None
            else Schedules(
                missions=missions,
                store=schedule_store,
                schedulers=schedulers,
                host=schedule_host,
                clock=clock,
                local=local or (lambda at: at),
                prepare=self._planned,
            )
        )
        self._subscriptions = (
            None
            if subscriptions is None or deliverer is None
            else Subscriptions(
                store=subscriptions,
                clock=clock,
                prepare=self._prepare,
                reader=self._follow.change_reader,
                deliverer=deliverer,
                project=missions.project,
            )
        )
        self._retention = Retention(archives, clock, self._subscriptions)
        self._status = Status(
            collection=collection, missions=missions, archives=archives,
            retention=self._retention, prepare=self._prepare, subscriptions=self._subscriptions,
        )  # fmt: skip

    def _prepare(self, name: str) -> Prepared:
        return self._collection.prepare(name)

    def _planned(self, name: str) -> tuple[ConfigFile, Plan]:
        prepared = self._collection.prepare(name)
        return prepared.source, prepared.plan

    # --- plan and survey ----------------------------------------------------------------

    def plan(
        self,
        name: str,
        *,
        config: ConfigGiven = None,
        replace: bool = False,
        stream: str | None = None,
        windows: int | None = None,
        until: datetime | None = None,
        forever: bool = False,
        interval: tuple[datetime, datetime] | None = None,
        rebase: Collection[str] = (),
        restart: Collection[str] = (),
        check: bool = False,
    ) -> PlanDocument:
        """What a survey would do: every run with its effects, and the files it would write.
        Raises ConfigError listing every problem.

        A run of an entry with ``range:`` says the span it would ask for (``range``: its
        ``start``, ``end``, ``kind``, where it began, as ``from``, and its ``steps``), after
        what its table committed, read now; and it describes its first step's request, the
        bounds filled. ``interval``, ``rebase`` and ``restart`` are :meth:`survey`'s.

        Pure: it writes nothing, and runs, fetches and connects to nothing (ruling R11). With
        ``check``, it also asks what the runs need of this host: each collector type that
        declares a ``preflight`` has its collector built, asked what each of its runs needs (an
        ``mcp`` type starts each stdio server a run names, and connects to each remote one, to
        list its tools), and closed; ``preflight`` says what was done, ``requirements`` what was
        found, and ``runnable`` whether it all holds (None unchecked). A plan of a run whose
        type declares one says in ``provenance`` (``preflight``) whether it was checked.

        With ``stream``, what :meth:`stream` would do with ``windows``, ``until`` and
        ``forever``: it refuses what that refuses, and ``provenance`` also says how long the
        run is and why (``stream.windows``, one by default; ``stream.until``;
        ``stream.forever``), and when it returns at the latest (``stream.returns``: seconds
        from its start, or null when only a stop or its source ends it). Without it, a
        mission with streams has ``survey.returns``: a survey runs one window of each, in
        turn, after its snapshot tables."""
        bound = None if stream is None else _stream_bound(
            windows, until, forever, self._clock.now(), name, stream
        )  # fmt: skip
        prepared = self._collection.prepare(name, config, replace)
        plan, config_ = prepared.plan, prepared.config
        asked = None if interval is None else interval_of(*interval)
        ranged = self._ranged(name, config_, self._clock.now(), asked, rebase, restart)
        provenance = list(plan.provenance)
        if stream is not None:
            planned = _planned_stream(plan, name, stream)
            provenance += _run_bound(bound, windows, until, forever)
            returns_s, returns = _returns(planned.window, bound, until, forever, self._clock.now())
            provenance.append(
                SourcedValue(path="stream.returns", value=returns_s, origin="recon", note=returns)
            )
        elif plan.streams:
            provenance.append(_survey_returns(config_, plan.streams))
        provenance += self._collection.preflight_said(config_, check)
        requirements, said, broken = (
            self._collection.preflight(name, config_) if check else ([], [], [])
        )
        broken += self._status.behind(name, prepared)
        return PlanDocument(
            mission=name,
            valid=plan.valid,
            runs=[
                self._collection.describe(run, config_, ranged.runs.get(run.output))
                for run in plan.runs
            ],
            effects=plan.effects(),
            writes=PlanWrites(
                config=prepared.copy_to,
                preserve_raw=config_.preserve_raw,
                stream_names=[s.name for s in plan.streams],
            ),
            problems=[p.document() for p in plan.problems],
            warnings=[w.document() for w in plan.warnings + ranged.warnings + broken],
            skipped=[skipped.document() for skipped in plan.skipped],
            requirements=requirements,
            runnable=all(required.holds for required in requirements) if check else None,
            preflight=said,
            provenance=provenance,
        )

    def _ranged(
        self,
        name: str,
        config: ReconConfig,
        now: datetime,
        interval: Interval | None,
        rebase: Collection[str],
        restart: Collection[str],
    ) -> RangeRuns:
        """The run of each ranged table of ``config``, at ``now``, after what each committed;
        ConfigError when one is refused (a span past ``max_steps``, a checkpoint committed
        under another request), before anything is begun."""
        ranged = range_runs(self._archives, name, config, self._collection.collector_types,
                            now, interval=interval, rebase=rebase, restart=restart)  # fmt: skip
        if ranged.problems:
            raise ConfigError(
                f"mission {name!r}: {len(ranged.problems)} problem(s) with its ranges",
                ranged.problems,
                fix=f"fix each problem, then: recon survey {name} --dry-run",
            )
        return ranged

    def spans(self, name: str, *, config: ConfigGiven = None) -> list[Span]:
        """What each of the mission's tables is to collect now (``recon.v1.span``): a ranged
        table its source time after what it committed, at the clock's time; every other
        snapshot table its source as it is; a stream one window. Pure as a plan is: it reads
        what was committed and writes nothing. ConfigError when a range is refused."""
        prepared = self._collection.prepare(name, config)
        plan, config_ = prepared.plan, prepared.config
        ranged = self._ranged(name, config_, self._clock.now(), None, (), ())
        spans = []
        for run in plan.runs:
            span = ranged.runs.get(run.output)
            if run.stream is not None:
                spans.append(Span(mission=name, output=run.output, source=run.source,
                                  kind="window", stream=run.stream))  # fmt: skip
            elif span is None:
                spans.append(Span(mission=name, output=run.output, source=run.source,
                                  kind="snapshot"))  # fmt: skip
            else:
                spans.append(Span.model_validate({
                    "mission": name, "output": run.output, "source": run.source,
                    "kind": span.kind, "start": iso(span.interval.start),
                    "end": iso(span.interval.end), "from": span.began, "request": span.request,
                    "moves": span.moves, "steps": [step.document() for step in span.steps],
                }))  # fmt: skip
        return spans

    def changes_of(self, name: str, act: Act, *, config: ConfigGiven = None) -> list[Change]:
        """What changed in the tables an act sealed, derived into its archive (after any the
        mission left pending, oldest first, under the hold of its mission or its stream), as
        rows each standing alone (``recon.v1.change``); none when no entry asks for
        ``changes:``. A row read again has the same ``id``."""
        prepared = self._collection.prepare(name, config)
        derivation = self._derivation(name, prepared)
        if derivation is None:
            return []
        ref = ArchiveRef(name, act.archive, act.stream)
        with self._collection.hold(name, stream=act.stream) as hold:
            told = derivation.catch_up(hold, name, stream=act.stream)[0]
            if act.archive not in told:  # not pending: derived already, or now
                tables = [
                    TableOutcome.model_validate(t) for t in self._archives.meta(ref)["tables"]
                ]
                if act.stream is None:
                    derivation.snapshot(hold, ref, tables)
                elif tables:
                    derivation.window(hold, ref, tables[0])
        rows: list[Change] = []
        for table in self._archives.meta(ref).get("tables") or []:
            output = str(table.get("name"))
            summary = table.get("changes")
            if (
                not isinstance(summary, dict)
                or summary.get("error")
                or output not in prepared.plan.changes
            ):
                continue
            reader = self._follow.change_reader(name, output, prepared)
            found = self._archives.records(ref, changes_output(output)) or iter(())
            for row, change in enumerate(found):
                rows.append(Change(
                    id=reader.row_id(act.archive, row, str(change.get("op")), change.get("key")),
                    archive=act.archive, op=change["op"], key=change.get("key"),
                    record=change.get("record"), mission=name, output=output,
                ))  # fmt: skip
        return rows

    def survey(
        self,
        name: str,
        *,
        config: ConfigGiven = None,
        replace: bool = False,
        on_event: EventCallback | None = None,
        interval: tuple[datetime, datetime] | None = None,
        rebase: Collection[str] = (),
        restart: Collection[str] = (),
    ) -> SurveyDocument:
        """Plan, then run every table and write one archive. A survey whose tables failed,
        or whose archive could not be finished, still returns its document (``status:
        incomplete``); :func:`~recon.core.survey_failure` says what that adds up to.
        Each table written that SQL cannot read is a ``table_unreadable`` warning, so it is
        found here rather than by the first query. An entry that is a stream runs one window,
        sealed when it ends (``windows``), after the snapshot tables, in config order: the
        dirt road of a stream, a survey run from cron. The mission is held while it runs, so
        a second survey of it is refused ``busy`` before it writes anything. Once the archive
        is sealed, what changed in each table with ``changes:`` is derived into it, and what
        ``retain:`` lets go is retired.

        An entry with ``range:`` asks for the span after what its table committed, read under
        the hold. ``interval``, ``(start, end)`` each with its time zone, asks every ranged
        entry for that source time instead (a backfill, which moves no checkpoint);
        ``rebase`` names outputs that go on from their checkpoint under a changed request,
        ``restart`` outputs that begin again from ``range.start``. Raises UsageError for an
        interval with no zone or no length, and for an output or interval no entry with
        ``range:`` takes."""
        asked = None if interval is None else interval_of(*interval)
        prepared = self._collection.prepare(name, config, replace)
        # One survey a mission at a time, from before it writes anything until it returns.
        with self._collection.hold(name) as hold:
            return self._survey(hold, name, prepared, on_event, interval=asked,
                                rebase=rebase, restart=restart)  # fmt: skip

    def _scheduled_survey(
        self, name: str, scheduled: dict[str, Any], config_sha256: str
    ) -> tuple[SurveyDocument, DeliverDocument | None, list[dict[str, Any]]]:
        """:meth:`survey` as a tick runs it, its archive's meta saying when the tick
        was due and how late it started (``scheduled``). The mission is held first; then its
        config is read once, and those bytes are what is checked against ``config_sha256``,
        planned and run, so no config swapped after a check runs (Dijkstra, Vector F4). Then,
        the archive sealed and the mission let go, the delivery of its subscriptions that have
        ``deliver:`` (ruling R4), by the same config's plan; None when it has none."""
        with self._collection.hold(name) as hold:
            source = self._collection.read(name)
            prepared = self._collection.prepare(name, source=source)
            if prepared.sha256 != config_sha256:
                raise stale(name, config_sha256)
            document = self._survey(hold, name, prepared, None, scheduled=scheduled)
        delivering = any(s.deliver is not None for s in prepared.config.subscriptions)
        delivered = None
        if self._subscriptions is not None and delivering:
            delivered = self._subscriptions.deliver(name, prepared=prepared)
        return document, delivered, [w.document() for w in self._status.behind(name, prepared)]

    def _survey(
        self,
        hold: Hold,
        name: str,
        prepared: Prepared,
        on_event: EventCallback | None,
        *,
        scheduled: dict[str, Any] | None = None,
        interval: Interval | None = None,
        rebase: Collection[str] = (),
        restart: Collection[str] = (),
    ) -> SurveyDocument:
        """:meth:`survey`, the mission held and its plan valid."""
        plan, config = prepared.plan, prepared.config
        emit = on_event or _ignore
        streams = {stream.index for stream in plan.streams}
        snapshot = config.model_copy(
            update={"collectors": [e for i, e in enumerate(config.collectors) if i not in streams]}
        )
        started = self._clock.now()
        ranged = self._ranged(name, snapshot, started, interval, rebase, restart)
        warnings = list(plan.warnings) + ranged.warnings
        for warning in warnings:
            emit({"kind": "warning", "reason": str(warning), "fix": warning.fix})
        self._collection.keep_config(name, prepared)

        def warn(output: str, warning: Problem) -> None:
            emit({"kind": "table_warning", "name": output, "reason": str(warning),
                  "fix": warning.fix})  # fmt: skip

        # Its key read before anything is written: a key that does not hold is refused with no
        # archive begun.
        derivation = self._derivation(name, prepared, snapshot, warn=warn)
        if derivation is not None:
            warnings += derivation.catch_up(hold, name)[1]
        run = self._collection.snapshot(
            hold, name, prepared, started=started, ranges=ranged.runs, on_event=on_event,
            scheduled=scheduled,
        )  # fmt: skip
        tables = run.tables
        warnings += run.warnings
        if derivation is not None:
            tables, found = derivation.snapshot(hold, run.archive, tables)
            warnings += found
        self._retention.snapshots(hold, name, prepared)
        stream_windows: list[WindowOutcome] = []
        for stream in plan.streams:
            ran, found = self._run_stream(name, prepared, stream, windows=1, until=None,
                                          on_event=on_event)  # fmt: skip
            warnings += found
            stream_windows += [ran.last] if ran.last is not None else []
        failed = any(t.status == "error" for t in tables) or any(
            w.table.status == "error" for w in stream_windows
        )
        return SurveyDocument(
            mission=name,
            archive=run.archive.id,
            status="incomplete" if failed or not run.finished else "complete",
            tables=tables,
            warnings=[w.document() for w in warnings],
            stream_windows=stream_windows,
        )

    def _derivation(
        self,
        name: str,
        prepared: Prepared,
        config: ReconConfig | None = None,
        *,
        warn: Callable[[str, Problem], object] | None = None,
    ) -> Derivation | None:
        """What derives the changes of the mission's tables, under its key, made on first use;
        None when no entry of ``config`` (default: the prepared one) asks for it, so such a
        mission has no key."""
        config = config if config is not None else prepared.config
        if not any(entry.changes is not None for entry in config.collectors):
            return None
        large = self._key_join(name) if self._key_join is not None else None
        detector = ChangeDetector(self._archives, name, self._missions.change_key(name),
                                  large=large, memory_keys=self._memory_keys)  # fmt: skip
        return Derivation(self._archives, detector, config, prepared.sha256, warn=warn)

    def stream(
        self,
        name: str,
        stream: str,
        *,
        windows: int | None = None,
        until: datetime | None = None,
        forever: bool = False,
        config: ConfigGiven = None,
        replace: bool = False,
        on_event: EventCallback | None = None,
        stop: Stop | None = None,
    ) -> StreamDocument:
        """Plan, then run the mission's stream ``stream`` (its collector's name), window after
        window, until ``windows`` are sealed, ``until`` comes, its source ends or fails, or it
        is stopped: ``stop`` asked (as the command line's SIGINT and SIGTERM ask it), or the
        caller's thread interrupted. The window being written is then sealed ``stopped`` once
        what waits is written (a second stop seals it at once), and this returns. With none
        of ``windows``, ``until`` and ``forever``, it runs one window, so it always returns
        unless told otherwise. Raises UsageError for a bound it cannot keep, NotFoundError
        for a name that is no stream of the mission, ReconError (``busy``) when another
        writer holds the stream; a window whose source failed is in the document, and
        :func:`~recon.core.stream_failure` says what it adds up to."""
        now = self._clock.now()
        windows = _stream_bound(windows, until, forever, now, name, stream)
        prepared = self._collection.prepare(name, config, replace)
        plan = prepared.plan
        planned = _planned_stream(plan, name, stream)
        self._collection.keep_config(name, prepared)
        started: list[Problem] = []
        returns_s, returns = _returns(planned.window, windows, until, forever, now)
        if on_event is not None:
            try:
                on_event({"kind": "stream", "name": stream, "returns_s": returns_s,
                          "returns": returns})  # fmt: skip
            except Exception as exc:  # a receiver that fails is said, never the stream's end
                started.append(
                    Problem(
                        "",
                        f"telemetry stopped going to a receiver that failed: {exc!r}",
                        kind="telemetry_failed",
                        fix="the collected data is unaffected; fix the on_event callback",
                    )
                )
                on_event = None
        run, found = self._run_stream(name, prepared, planned, windows=windows, until=until,
                                      on_event=on_event, stop=stop)  # fmt: skip
        found = started + found
        return StreamDocument(
            mission=name,
            stream=stream,
            returns_s=returns_s,
            returns=returns,
            state=self._archives.stream_state(name, stream),
            windows=run.windows,
            records=run.records,
            dropped=run.dropped,
            first=run.first,
            last_window=run.last,
            warnings=[w.document() for w in plan.warnings + found],
        )

    def _run_stream(
        self,
        name: str,
        prepared: Prepared,
        planned: PlannedStream,
        *,
        windows: int | None,
        until: datetime | None,
        on_event: EventCallback | None,
        stop: Stop | None = None,
    ) -> tuple[StreamRun, list[Problem]]:
        """One run of a stream through the core, each window, once sealed, derived from (its
        changes) and retention applied, under the stream's hold: the run, with its last window
        as derived, and what it found worth saying."""
        derivation = self._derivation(name, prepared)
        told: dict[str, TableOutcome] = {}
        warnings: list[Problem] = []
        sizes: dict[str, int] = {}  # each window's size, read once, for retain: {bytes}
        caught_up = derivation is None

        def sealed(hold: Hold, window: WindowOutcome) -> None:
            nonlocal caught_up
            ref = ArchiveRef(name, window.id, planned.name, window.index)
            if derivation is not None:
                if not caught_up:  # the first window: what a writer that died left, then it
                    caught_up = True
                    tables, found = derivation.catch_up(hold, name, stream=planned.name)
                    warnings.extend(found)
                    if window.id in tables:
                        told[window.id] = tables[window.id][0]
                if window.id not in told:
                    told[window.id], found = derivation.window(hold, ref, window.table)
                    warnings.extend(found)
            self._retention.window(
                hold, name, planned.name, planned.retain, window, sizes,
                held=lambda: self._retention.held(name, prepared, planned.name),
            )  # fmt: skip

        run, found = self._collection.stream(
            name, prepared, planned, windows=windows, until=until, on_event=on_event,
            stop=stop, sealed=sealed,
        )  # fmt: skip
        last = run.last
        if last is not None and last.id in told:
            run = replace(run, last=last.model_copy(update={"table": told[last.id]}))
        return run, once_each(found + warnings)

    # --- schedules ---------------------------------------------------------------------

    def _scheduling(self) -> Schedules:
        if self._schedules is None:
            raise UsageError(
                "this recon was built with no scheduler",
                fix="build it with recon.open(), which gives it the host's",
            )
        return self._schedules

    def schedule_install(self, name: str, *, platform: Platform | None = None) -> ScheduleDocument:
        """Project the mission's ``schedule:`` onto the scheduler (the host's, or
        ``platform``'s) and have it load the files; again with the same config it changes
        nothing (``unchanged``). Raises ConfigError for a mission with no ``schedule:``."""
        return self._scheduling().install(name, platform)

    def schedule_remove(self, name: str, *, platform: Platform | None = None) -> ScheduleDocument:
        """Unload and remove the mission's schedule (``removed``), or say there was none
        (``absent``)."""
        return self._scheduling().remove(name, platform)

    def schedule_list(self, *, platform: Platform | None = None) -> SchedulesDocument:
        """The schedules installed for this project's missions."""
        return self._scheduling().list(platform)

    def schedule_status(
        self, mission: str | None = None, *, platform: Platform | None = None
    ) -> SchedulesDocument:
        """Each schedule of the project, or the mission's: installed or not, its config
        changed since or not, when the next tick is due, the last tick, how late the schedule
        is and the ticks skipped in a row."""
        return self._scheduling().status(mission, platform)

    def schedule_tick(self, name: str, *, config_sha256: str) -> TickDocument:
        """One tick of the mission's schedule, what the scheduler starts: a survey under the
        mission lock, logged to ``<mission>/schedule.log``, unless the config is not the one
        whose digest the schedule holds (``stale``), another survey holds the mission
        (``busy``, logged skipped), or it is a missed tick under ``missed: skip``."""
        return self._scheduling().tick(
            name,
            config_sha256,
            lambda scheduled: self._scheduled_survey(name, scheduled, config_sha256),
        )

    # --- subscriptions ----------------------------------------------------------------

    def _subscribing(self) -> Subscriptions:
        if self._subscriptions is None:
            raise UsageError(
                "this recon was built with no subscription store",
                fix="build it with recon.open(), which gives it the mission's files",
            )
        return self._subscriptions

    def subscribe(
        self,
        name: str,
        subscription: str,
        cursor: str | None = None,
        *,
        max_records: int = READ_MAX_RECORDS,
        max_bytes: int = READ_MAX_BYTES,
    ) -> ChangesSince:
        """One read of the subscription the mission declares: the rows of its output its
        ``match:`` keeps, from the cursor it acked last (None acked: from the first archive),
        or from ``cursor`` for a subscriber that keeps its own. It acks nothing: :meth:`ack`
        does, with the read's ``cursor``, once the rows are acted on. Raises NotFoundError for a
        subscription the config does not declare, ReconError ``busy`` while another consumer
        holds it."""
        return self._subscribing().read(
            name, subscription, cursor, max_records=max_records, max_bytes=max_bytes
        )

    def ack(self, name: str, subscription: str, cursor: str) -> SubscriptionDocument:
        """Keep ``cursor`` as what the subscription has acked, so its next read goes on from
        it. Monotone: a cursor before the one acked is refused (UsageError), the same one
        changes nothing (``unchanged``)."""
        return self._subscribing().ack(name, subscription, cursor)

    def unsubscribe(self, name: str, subscription: str) -> SubscriptionDocument:
        """Remove what is kept of the subscription, its acked cursor and dead letters
        (``removed``; ``absent`` when nothing was). A subscription the config still declares
        then reads from the first archive again."""
        return self._subscribing().remove(name, subscription)

    def deliver(self, name: str, *, subscription: str | None = None) -> DeliverDocument:
        """Give each subscription with ``deliver:`` (or the one named) the rows it has not
        acked, to its command: at most ``max_per_tick`` one at a time, the rest as one row
        counting them, all within its ``deadline``. A row a command fails is given again next
        time and set aside after ``attempts``, each said as a warning, never raised. Raises
        NotFoundError for a subscription the config does not declare, UsageError for one with
        no ``deliver:``."""
        return self._subscribing().deliver(name, subscription)

    # --- status and following ---------------------------------------------------------

    def status(self, mission: str | None = None) -> StatusDocument:
        """Every mission with its latest archive, and the installed plugins. Given a mission,
        that mission alone, with every one of its archives as its ``history``."""
        return self._status.status(mission)

    def read_since(
        self,
        name: str,
        cursor: str | None = None,
        *,
        archive: str | None = None,
        stream: str | None = None,
        max_records: int = READ_MAX_RECORDS,
        max_bytes: int = READ_MAX_BYTES,
    ) -> RecordsSince:
        """:meth:`Follow.read_since`."""
        return self._follow.read_since(name, cursor, archive=archive, stream=stream,
                                       max_records=max_records, max_bytes=max_bytes)  # fmt: skip

    def watch(
        self,
        name: str,
        cursor: str | None = None,
        *,
        archive: str | None = None,
        stream: str | None = None,
        interval_s: float = WATCH_INTERVAL_S,
        max_records: int = READ_MAX_RECORDS,
        max_bytes: int = READ_MAX_BYTES,
    ) -> Iterator[RecordsSince]:
        """:meth:`Follow.watch`."""
        return self._follow.watch(
            name, cursor, archive=archive, stream=stream, interval_s=interval_s,
            max_records=max_records, max_bytes=max_bytes,
        )  # fmt: skip

    def changes_since(
        self,
        name: str,
        output: str,
        cursor: str | None = None,
        *,
        max_records: int = READ_MAX_RECORDS,
        max_bytes: int = READ_MAX_BYTES,
    ) -> ChangesSince:
        """:meth:`Follow.changes_since`, under the mission's config as it is now."""
        return self._follow.changes_since(name, output, cursor, self._prepare(name),
                                          max_records=max_records, max_bytes=max_bytes)  # fmt: skip


def _ignore(event: dict[str, Any]) -> None:
    return None


def _stream_bound(
    windows: int | None,
    until: datetime | None,
    forever: bool,
    now: datetime,
    mission: str,
    collector: str,
) -> int | None:
    """The windows a stream's run seals at most (None: no bound by count), refusing a bound it
    cannot keep. With no bound given, one window: a stream returns unless told otherwise."""
    run = f"recon stream {mission} --stream {collector}"
    if forever and (windows is not None or until is not None):
        raise UsageError(
            "--forever runs the stream until it is stopped, so it takes no --windows or --until",
            fix=f"{run} --forever, or bound it with --windows N or --until TIME",
        )
    if windows is not None and windows < 1:
        raise UsageError(
            f"--windows is how many windows to seal, 1 or more; got {windows}",
            fix=f"{run} --windows 1 (the default), or more",
        )
    if until is not None and until.tzinfo is None:
        raise UsageError(
            "until must name its time zone: an ISO 8601 time with its offset",
            fix=f"{run} --until 2026-10-24T18:00:00Z",
        )
    if until is not None and until <= now:
        raise UsageError(
            f"until {until.isoformat()} has passed (it is {now.isoformat()})",
            fix=f"{run} --until <a time still to come>",
        )
    return 1 if windows is None and until is None and not forever else windows


def _planned_stream(plan: Plan, mission: str, collector: str) -> PlannedStream:
    """The mission's stream ``collector``; NotFoundError naming its streams when it has no
    such stream."""
    planned = {stream.name: stream for stream in plan.streams}
    if collector in planned:
        return planned[collector]
    known = sorted(planned)
    raise NotFoundError(
        f"no stream {collector!r} in mission {mission!r}; its streams: "
        f"{', '.join(known) or '(none)'}",
        fix=f"recon stream {mission} --stream <one of: {', '.join(known)}>"
        if known
        else "give a collector window: to make it a stream (recon --skill -r streaming)",
        details={"value": collector, "allowed": known},
    )


def _run_bound(
    bound: int | None, windows: int | None, until: datetime | None, forever: bool
) -> list[SourcedValue]:
    """How long a stream's run is, and why: the plan's say of ``--windows``, ``--until`` and
    ``--forever``."""
    said: list[SourcedValue] = []
    if bound is not None:
        given = windows is not None
        said.append(
            SourcedValue(
                path="stream.windows",
                value=bound,
                origin="command" if given else "recon",
                note=""
                if given
                else "no --windows, --until or --forever: one window, so the run returns",
            )
        )
    if until is not None:
        said.append(SourcedValue(path="stream.until", value=until.isoformat(), origin="command"))
    if forever:
        said.append(
            SourcedValue(
                path="stream.forever",
                value=True,
                origin="command",
                note="runs until stopped: Ctrl-C or SIGTERM seals the window being written",
            )
        )
    return said


def _returns(
    window: WindowSpec, bound: int | None, until: datetime | None, forever: bool, now: datetime
) -> tuple[float | None, str]:
    """When a stream's run returns at the latest, in seconds from its start (None when no time
    bounds it), and that said for a person or an agent deciding whether to wait for it."""
    if forever:
        return None, "only when stopped (Ctrl-C or SIGTERM), or when its source ends"
    limits = [] if bound is None or window.seconds is None else [bound * window.seconds]
    if until is not None:
        limits.append(max(0.0, (until - now).total_seconds()))
    if not limits:
        return None, (
            f"after {bound} window(s), each ended by its records, bytes or pages: when the "
            "source has sent them, or it ends, or the stream is stopped"
        )
    most = min(limits)
    return most, (
        f"returns within {most:g}s; sooner when its source ends or a window's records, bytes "
        "or pages end it first. To not wait: a shorter window: seconds, or run it in the "
        "background"
    )


def _survey_returns(config: ReconConfig, streams: list[PlannedStream]) -> SourcedValue:
    """When a survey of ``config`` returns at the latest, in seconds from its start: its
    snapshot tables (as long as the mission's ``deadline:`` lets them), then one window of
    each stream, in turn. Null when one of them has no time bound."""
    snapshot = len(streams) < len(config.collectors)
    spans = [stream.window.seconds for stream in streams]
    each = ", ".join(
        f"{stream.name} {stream.window.seconds:g}s"
        if stream.window.seconds is not None
        else f"{stream.name} no time bound"
        for stream in streams
    )
    note = f"one window of each of {len(streams)} stream(s), in turn ({each})"
    first: float | None = 0.0
    if snapshot:
        first = config.deadline
        note += ", after the snapshot tables, " + (
            f"which deadline: bounds at {first:g}s"
            if first is not None
            else "which no deadline: bounds: set deadline: to bound them"
        )
    if None in spans:
        note += (
            "; a window with no seconds: ends on its records, bytes or pages, or its source's end"
        )
    timed = [span for span in spans if span is not None]
    value = None if first is None or len(timed) < len(spans) else first + sum(timed)
    return SourcedValue(path="survey.returns", value=value, origin="recon", note=note)
