"""Running an experiment as one matrix run, and reading runs back from matrix's record.

matrix's store is the only store: a run is a directory of typed tables written as the run
goes, and ix reads a run back through it, in any state. Which store is the composition
root's choice, built at matrix's ``store`` point and handed in; nothing here names one, and
nothing here keeps a file of ix's own.

matrix's errors stop here. A run that fails or is stopped is not an error to the caller: its
record says how it ended, and :meth:`Runs.record` reads that. What matrix could not do at
all (wire the components, open the record, read a damaged one) is raised as the ix error of
the same kind.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import time
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

from matrix import (
    ComponentLimits,
    Limits,
    MatrixError,
    RunError,
    compose,
)
from matrix import ConfigError as MatrixConfigError
from matrix import NotFoundError as MatrixNotFoundError
from pydantic import ValidationError

from ix.adapters._out.compose import DOWN_TIMEOUT_S
from ix.adapters._out.matrix_errors import carried_over
from ix.domain import schema
from ix.domain.errors import ConfigError, IxError, NotFoundError, UsageError
from ix.domain.report import RunSummary
from ix.domain.run import Interaction, Reading, RunManifest, RunRecord, RunStatus, Trial

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from matrix import ConstructStore, Event, MatrixConfig, Registry

    from ix.domain.ports import Traced

#: The file matrix's store keeps a run's events in, and the kind of its rows.
EVENT_LOG = "events.jsonl"
EVENTS = "matrix.v1.event"

#: How long a trial in flight has to end after a stop request before it is cancelled: time
#: for a load tool and a subject to be stopped politely.
GRACE_S = 15.0

# A run's components get no matrix deadline (``deadline_s=None``, matrix's "no deadline"):
# one experiment run is one matrix run, so its trials component lasts as long as all of the
# experiment's trials, and matrix's default of an hour would cut a long experiment mid-way.
# What bounds a trial is ix's: each probe's ``budget_s``, where its kind declares one.

#: How long the environment's releases have when the run ends: a compose stack's ``down``
#: stops each container and removes its volumes, which outlasts matrix's default.
ENVIRONMENT_CLEANUP_S = DOWN_TIMEOUT_S + 30.0

#: How often a follower reads a running run's record and events again.
POLL_S = 0.25

#: The suffix of the file beside a run's directory that holds what the process of a run
#: started detached wrote to its standard error: ``<root>/<run id>.log``. matrix's store
#: lists directories that hold a ``run.json``, so it is no run to it.
LOG_SUFFIX = ".log"

_STATUS: dict[str, RunStatus] = {
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "interrupted",
    "interrupted": "interrupted",
}


class _Relay:
    """Hands a run's ``ix.*`` events to a callback. Telemetry: it may miss some."""

    def __init__(self, on_event: Callable[[str, Mapping[str, Any]], None]) -> None:
        self._on_event = on_event

    def on_event(self, event: Event) -> None:
        if event.name.startswith("ix."):
            self._on_event(event.name, event.fields)


async def execute(
    *,
    store: ConstructStore,
    registry: Registry,
    config: MatrixConfig,
    needs: Mapping[str, Any],
    manifest: RunManifest,
    trials: Sequence[Trial],
    on_event: Callable[[str, Mapping[str, Any]], None] | None = None,
    tracer: Traced | None = None,
) -> None:
    """Carry out one experiment run as one matrix run of ``config`` (its components and
    observers, resolved through ``registry`` and built with ``needs``), recorded in
    ``store``, inside an ``ix.run`` span when there is a ``tracer``.

    Returns when the run ended, whether it completed or a component failed: the record
    says which. A stop request (the caller cancelled, Ctrl-C, SIGTERM) propagates as the
    cancellation it is, after matrix stopped the components and called every release; the
    record is complete by then too.
    """
    span = (
        tracer.trace("ix.run", **{"ix.run.id": manifest.run, "ix.experiment": manifest.experiment})
        if tracer is not None
        else nullcontext()
    )
    with span:
        try:
            container = compose(config, registry=registry, context=needs)
            wiring = container.wire()
            relay = [] if on_event is None else [_Relay(on_event)]
            # matrix's executor for the composed run: signals become the run's cancellation,
            # and each session row records the trace and span it ended in.
            await container.executor(relay).run(
                wiring,
                {schema.RUN: manifest, schema.TRIAL: list(trials)},
                store=store,
                run_id=manifest.run,
                limits=Limits(
                    deadline_s=None,
                    grace_s=GRACE_S,
                    components={"environment": ComponentLimits(cleanup_s=ENVIRONMENT_CLEANUP_S)},
                ),
            )
        except RunError:
            return  # a component failed: the record holds what happened and what was kept
        except MatrixError as error:
            raise carried_over("the run could not be carried out", error) from error


def _errors(described: Mapping[str, Any]) -> tuple[str, ...]:
    """What went wrong in a run, one sentence each: every component that failed, and every
    deferred release that failed or was abandoned (something it started may be left
    running)."""
    failed = [
        f"{failure.get('component')}: {failure.get('message')}"
        for failure in described.get("failures", [])
    ]
    left = [
        f"{release.get('component')}: a release {release.get('status')}: {release.get('error')}"
        for release in described.get("releases", [])
        if release.get("status") != "released"
    ]
    return (*failed, *left)


class Runs:
    """The runs recorded under one directory, read through ``store``: the store the root
    built for that directory."""

    def __init__(self, root: str, store: ConstructStore) -> None:
        self.root = root
        self.store = store

    @classmethod
    def at(cls, path: str, store: Callable[[str], ConstructStore]) -> tuple[Runs, str] | None:
        """The runs that the run recorded in directory ``path`` is one of, and its id; None
        when ``path`` is not a run's directory. ``store`` builds the store for a
        directory."""
        directory = Path(path).resolve()
        runs = cls(str(directory.parent), store(str(directory.parent)))
        try:
            runs.store.describe(directory.name)
        except MatrixNotFoundError:
            return None
        except MatrixError as error:
            raise carried_over(f"{path} cannot be read", error) from error
        return runs, directory.name

    def taken(self, run: str) -> bool:
        """Whether a run of id ``run`` is recorded here, or its directory exists. Raises
        :class:`UsageError` when ``run`` is no id the store can keep a run under."""
        try:
            self.store.describe(run)
        except MatrixNotFoundError:
            return Path(self.root, run).exists()
        except MatrixConfigError as error:
            raise UsageError(
                f"{run!r} cannot be a run's id: {error.message}",
                fix="give --run-id letters, digits, '.', '_' and '-' only",
                details={"value": run},
            ) from error
        except MatrixError:
            return True  # a record there, damaged: the id is taken all the same
        return True

    def recorded(self, run: str) -> bool:
        """Whether the record of ``run`` has been opened here."""
        try:
            self.store.describe(run)
        except MatrixNotFoundError:
            return False
        except MatrixError as error:
            raise carried_over(f"run {run!r} in {self.root} cannot be read", error) from error
        return True

    def log(self, run: str) -> Path:
        """Where the process of ``run``, started detached, writes its standard error."""
        return Path(self.root, run + LOG_SUFFIX)

    def writer(self, run: str) -> tuple[str, int | None]:
        """The host ``run`` is written on, and the id of the process writing it while it runs
        on this host: None once it has ended, or when another host writes it. Reading it
        finishes the record of a run whose writer on this host is gone, as ``interrupted``
        (matrix's reader does)."""
        described = self._described(run)
        writer = described.get("writer") or {}
        host, pid = str(writer.get("host") or ""), writer.get("pid")
        if described.get("status") != "running" or host != socket.gethostname():
            return host, None
        return host, pid if isinstance(pid, int) else None

    def running(self, run: str) -> bool:
        """Whether ``run``'s record still says it is running (a writer gone from this host
        has been read as having ended)."""
        return self._described(run).get("status") == "running"

    def events(self, run: str, *, follow: bool = False) -> Iterator[dict[str, Any]]:
        """``run``'s events, oldest first, each the ``matrix.v1.event`` document as its line
        holds it; a line not yet whole, or not JSON, is not one. With ``follow``, it goes on
        yielding each event as it is written until the record says the run ended, and then
        every event written before that: it ends however the run ended, its writer killed
        included. Raises :class:`UsageError`, before yielding anything, for ``follow`` on a
        run still written on another host: whether its writer there is alive cannot be read
        from here, so following it might never end. Without ``follow`` such a run's events
        are read as they stand."""
        if follow:
            host, pid = self.writer(run)
            if pid is None and self.running(run):
                raise UsageError(
                    f"run {run!r} is written by a process on {host or 'another host'}, whose "
                    "end this host cannot see, so following it might never end",
                    fix="read its events as they stand without --follow, or ask `ix status` "
                    "again later",
                    details={"path": f"{self.root}/{run}"},
                )
        return self._events(run, follow=follow)

    def _events(self, run: str, *, follow: bool) -> Iterator[dict[str, Any]]:
        path = Path(self.root, run, EVENT_LOG)
        position = 0
        while True:
            ended = not follow or not self.running(run)
            position, found = _whole_lines(path, position)
            yield from found
            if ended:
                return
            time.sleep(POLL_S)

    def stop(self, run: str, *, wait_s: float) -> None:
        """Ask the process writing ``run`` to stop, as Ctrl-C or SIGTERM does: matrix stops
        the trial in flight, calls every release and finishes the record. Returns once the
        record says the run ended, or after ``wait_s`` while it is still releasing what it
        started. Does nothing to a run that has ended. Raises :class:`UsageError` for a run
        written on another host, and :class:`ConfigError` when its process may not be
        signalled from here."""
        host, pid = self.writer(run)
        if pid is None:
            if not self.running(run):
                return  # it has ended: its record says how
            raise UsageError(
                f"run {run!r} is written by a process on {host or 'another host'}, which this "
                "host cannot stop",
                fix=f"run `ix stop` on {host}" if host else "run `ix stop` where it runs",
                details={"path": f"{self.root}/{run}"},
            )
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return  # it ended between the two reads: its record says how
        except PermissionError as error:
            raise ConfigError(
                f"the process writing run {run!r} (pid {pid}) may not be signalled from here",
                fix="stop it as the user it runs as",
                details={"path": f"{self.root}/{run}", "value": pid},
            ) from error
        until = time.monotonic() + wait_s
        while self.running(run) and time.monotonic() < until:
            time.sleep(POLL_S)

    def _described(self, run: str) -> dict[str, Any]:
        try:
            return self.store.describe(run)
        except MatrixNotFoundError as error:
            raise NotFoundError(
                f"no run {run!r} in {self.root}",
                fix="`ix runs <experiment>` lists the runs",
                details={"path": self.root, "value": run},
            ) from error
        except MatrixError as error:
            raise carried_over(f"run {run!r} in {self.root} cannot be read", error) from error

    def ids(self) -> list[str]:
        """Run ids, oldest first."""
        try:
            return self.store.runs()
        except MatrixError as error:
            raise carried_over(f"the runs in {self.root} cannot be listed", error) from error

    def record(self, run: str) -> RunRecord:
        """One run as recorded, in whatever state it is. Raises :class:`NotFoundError` when
        there is no such run, or when the directory holds a run that is not an experiment's."""
        try:
            described = self.store.describe(run)
            construct = self.store.load(run)
        except MatrixNotFoundError as error:
            raise NotFoundError(
                f"no run {run!r} in {self.root}",
                fix="`ix runs <experiment>` lists the runs",
                details={"path": self.root, "value": run},
            ) from error
        except MatrixError as error:
            raise carried_over(
                f"run {run!r} in {self.root} cannot be read",
                error,
                fix="the record is damaged; run the experiment again",
            ) from error
        manifests = construct.rows(schema.RUN)
        if not manifests:
            raise NotFoundError(
                f"{self.root}/{run} is not the record of an experiment run: it has no "
                f"{schema.RUN} row",
                fix="`ix runs <experiment>` lists the runs",
            )
        location = f"{self.root}/{run}"
        try:
            readings = tuple(Reading.model_validate(row) for row in construct.rows(schema.READING))
        except ValidationError as error:
            raise ConfigError(
                f"run {run!r} in {self.root} holds a reading ix never writes: "
                f"{error.errors()[0]['msg']}",
                fix="the record is damaged; run the experiment again",
                details={"path": f"{location}/{schema.READING}.jsonl"},
            ) from error
        return RunRecord(
            run=run,
            location=location,
            status=_STATUS.get(str(described.get("status")), "failed"),
            manifest=RunManifest.model_validate(manifests[0]),
            trials=tuple(Trial.model_validate(row) for row in construct.rows(schema.TRIAL)),
            interactions=tuple(
                Interaction.model_validate(row) for row in construct.rows(schema.INTERACTION)
            ),
            readings=readings,
            ended_at=str(described.get("ended") or ""),
            errors=_errors(described),
            tables={
                **{
                    kind: f"{location}/{info['file']}"
                    for kind, info in described.get("tables", {}).items()
                },
                # matrix's local event log, kept beside the tables: the run's telemetry.
                **(
                    {EVENTS: f"{location}/{EVENT_LOG}"}
                    if Path(location, EVENT_LOG).is_file()
                    else {}
                ),
            },
        )

    def records(self, *, latest_first: bool = False) -> Iterator[RunRecord]:
        """Every experiment run under the root, oldest first (or latest first), each read
        once and when asked. A directory that is not one is left out."""
        ids = self.ids()
        for run in reversed(ids) if latest_first else ids:
            try:
                yield self.record(run)
            except IxError:
                continue

    @staticmethod
    def summary(record: RunRecord) -> RunSummary:
        """What a list of runs can say of ``record`` without reporting it: its id, state
        and counts."""
        return RunSummary(
            run=record.run,
            location=record.location,
            experiment=record.manifest.experiment,
            status=record.status,
            gated=record.gated,
            mode="dry" if record.stood_in else "gated" if record.gated else "exploratory",
            started_at=record.manifest.started_at,
            planned=len(record.trials),
            carried_out=len(record.interactions),
        )


def _whole_lines(path: Path, position: int) -> tuple[int, list[dict[str, Any]]]:
    """The JSON objects on the whole lines of ``path`` from byte ``position`` on, and the
    position after the last whole line: a line still being written is read next time. A
    file not there yet has none."""
    try:
        with path.open("rb") as stream:
            stream.seek(position)
            data = stream.read()
    except FileNotFoundError:
        return position, []
    whole = data.rfind(b"\n") + 1
    found: list[dict[str, Any]] = []
    for line in data[:whole].splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            found.append(parsed)
    return position + whole, found
