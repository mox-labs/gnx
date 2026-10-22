"""Running an experiment as one matrix run, and reading runs back from matrix's record.

matrix's store is the only store: a run is a directory of typed JSONL tables written as the
run goes, and ix reads a run back through it, in any state. Nothing here keeps a file of
ix's own.

matrix's errors stop here. A run that fails or is stopped is not an error to the caller: its
record says how it ended, and :meth:`Runs.record` reads that. What matrix could not do at
all (wire the components, open the record, read a damaged one) is raised as the ix error of
the same kind.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

from matrix import (
    JsonlConstructStore,
    Limits,
    MatrixError,
    RunError,
    compose,
)
from matrix import NotFoundError as MatrixNotFoundError
from pydantic import ValidationError

from ix.adapters._out.matrix_errors import carried_over
from ix.domain import schema
from ix.domain.errors import ConfigError, IxError, NotFoundError
from ix.domain.report import RunSummary
from ix.domain.run import Interaction, Reading, RunManifest, RunRecord, RunStatus, Trial

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from matrix import Event, MatrixConfig, Registry

    from ix.domain.ports import Traced

#: The file matrix's store keeps a run's events in, and the kind of its rows.
EVENT_LOG = "events.jsonl"
EVENTS = "matrix.v1.event"

#: How long a trial in flight has to end after a stop request before it is cancelled: time
#: for a load tool and a subject to be stopped politely.
GRACE_S = 15.0

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
    root: str,
    registry: Registry,
    config: MatrixConfig,
    needs: Mapping[str, Any],
    manifest: RunManifest,
    trials: Sequence[Trial],
    on_event: Callable[[str, Mapping[str, Any]], None] | None = None,
    tracer: Traced | None = None,
) -> None:
    """Carry out one experiment run as one matrix run of ``config`` (its components and
    observers, resolved through ``registry`` and built with ``needs``), recorded under
    ``root``, inside an ``ix.run`` span when there is a ``tracer``.

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
                store=JsonlConstructStore(root),
                run_id=manifest.run,
                limits=Limits(grace_s=GRACE_S),
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
    """The runs recorded under one directory."""

    def __init__(self, root: str) -> None:
        self.root = root
        self._store = JsonlConstructStore(root)

    @classmethod
    def at(cls, path: str) -> tuple[Runs, str] | None:
        """The runs that the run recorded in directory ``path`` is one of, and its id; None
        when ``path`` is not a run's directory."""
        directory = Path(path).resolve()
        runs = cls(str(directory.parent))
        try:
            runs._store.describe(directory.name)
        except MatrixNotFoundError:
            return None
        except MatrixError as error:
            raise carried_over(f"{path} cannot be read", error) from error
        return runs, directory.name

    def ids(self) -> list[str]:
        """Run ids, oldest first."""
        try:
            return self._store.runs()
        except MatrixError as error:
            raise carried_over(f"the runs in {self.root} cannot be listed", error) from error

    def record(self, run: str) -> RunRecord:
        """One run as recorded, in whatever state it is. Raises :class:`NotFoundError` when
        there is no such run, or when the directory holds a run that is not an experiment's."""
        try:
            described = self._store.describe(run)
            construct = self._store.load(run)
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

    def summaries(self) -> list[RunSummary]:
        """Every experiment run under the root, oldest first. A directory that is not one
        is left out."""
        found: list[RunSummary] = []
        for run in self.ids():
            try:
                record = self.record(run)
            except IxError:
                continue
            found.append(
                RunSummary(
                    run=run,
                    location=record.location,
                    experiment=record.manifest.experiment,
                    status=record.status,
                    gated=record.gated,
                    mode="dry" if record.stood_in else "gated" if record.gated else "exploratory",
                    started_at=record.manifest.started_at,
                    planned=len(record.trials),
                    carried_out=len(record.interactions),
                )
            )
        return found
