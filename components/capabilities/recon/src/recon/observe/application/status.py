"""Status: every mission with its archives, streams and subscriptions, and what is wrong with
them (an archive a dead writer left, a mission others can read, a subscription holding back
retention), as one document."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from recon.core import (
    ArchiveStatus,
    ChangeSummary,
    MissionStatus,
    NotFoundError,
    Problem,
    RangeOutcome,
    ReconError,
    StatusDocument,
    StreamStatus,
    TableError,
    TableStatus,
    WindowRecord,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from recon.core import ArchiveRef, Archives, Collection, MissionStore, Prepared
    from recon.observe.application.retention import Retention
    from recon.observe.application.subscriptions import Subscriptions


class Status:
    """The status of a project's missions. ``prepare`` plans a mission as a survey would;
    without ``subscriptions``, no subscription is listed."""

    def __init__(
        self,
        *,
        collection: Collection,
        missions: MissionStore,
        archives: Archives,
        retention: Retention,
        prepare: Callable[[str], Prepared],
        subscriptions: Subscriptions | None = None,
    ) -> None:
        self._collection = collection
        self._missions = missions
        self._archives = archives
        self._retention = retention
        self._prepare = prepare
        self._subscriptions = subscriptions

    def status(self, mission: str | None = None) -> StatusDocument:
        """Every mission with its latest archive, and the installed plugins. Given a mission,
        that mission alone, still in ``missions`` (one document shape for both), with every
        one of its archives as its ``history``."""
        if mission is None:
            names = self._missions.missions()
        elif self._missions.config_text(mission) is None:
            known = self._missions.missions()
            raise NotFoundError(
                f"no mission {mission!r}; have: {', '.join(known) or '(none)'}",
                fix="recon status",
                details={"value": mission, "allowed": known},
            )
        else:
            names = [mission]
        return StatusDocument(
            project=str(self._missions.project),
            missions=[self._mission_status(name, history=mission is not None) for name in names],
            plugins=self._collection.installed(),
            warnings=[
                w.document()
                for name in names
                for w in self._abandoned(name) + self._exposed(name) + self.behind(name)
            ],
        )

    def behind(self, name: str, prepared: Prepared | None = None) -> list[Problem]:
        """A ``subscription_behind`` warning for each declared subscription holding back what
        ``retain:`` would let go; none when the config cannot be planned."""
        if prepared is None:
            try:
                prepared = self._prepare(name)
            except ReconError:
                return []
        return self._retention.behind(name, prepared)

    def _exposed(self, name: str) -> list[Problem]:
        """A ``mission_exposed`` warning when a path of the mission (one an older recon made,
        or one changed since) is open to other users; recon changes no mode it did not set."""
        found = self._missions.exposed(name)
        if found is None:
            return []
        return [
            Problem(
                "",
                f"mission {name}: {found.where} can be reached by users other than its owner, "
                "and what a mission collects is its owner's alone",
                kind="mission_exposed",
                fix=found.fix,
                got=found.where,
            )
        ]

    def _abandoned(self, name: str) -> list[Problem]:
        return [
            Problem(
                "",
                f"mission {name}: a survey or stream that died while making an archive left "
                f"{left.where}, which no listing shows and nothing reads",
                kind="archive_abandoned",
                fix=f"nothing was collected into it; remove it: {left.fix}",
                got=left.where,
            )
            for left in self._archives.abandoned(name)
        ]

    def _mission_status(self, name: str, *, history: bool = False) -> MissionStatus:
        archives = self._archives.archives(name)
        return MissionStatus(
            name=name,
            config=self._missions.where(name),
            archives=len(archives),
            latest=self._archive_status(archives[-1]) if archives else None,
            captures=self._missions.capture_count(name),
            history=[self._archive_status(a) for a in archives] if history else None,
            streams=[
                self._stream_status(name, s, history=history) for s in self._archives.streams(name)
            ],
            subscriptions=self._subscription_statuses(name),
        )

    def _subscription_statuses(self, name: str) -> list[Any]:
        """The mission's subscriptions, as far as its config can be planned: one that cannot
        lists only those something is kept of."""
        if self._subscriptions is None:
            return []
        try:
            plan = self._prepare(name).plan
        except ReconError:
            plan = None
        return self._subscriptions.statuses(name, plan)

    def _stream_status(self, name: str, stream: str, *, history: bool) -> StreamStatus:
        windows = self._archives.archives(name, stream=stream)
        return StreamStatus(
            name=stream,
            state=self._archives.stream_state(name, stream),
            windows=len(windows),
            first=windows[0].id if windows else None,
            latest=self._archive_status(windows[-1]) if windows else None,
            retired=self._archives.retired_count(name, stream),
            history=[self._archive_status(w) for w in windows] if history else None,
        )

    def _archive_status(self, archive: ArchiveRef) -> ArchiveStatus:
        meta = self._archives.meta(archive)
        state = self._archives.state(archive)
        return ArchiveStatus(
            id=archive.id,
            state=state,
            ended=self._collection.ended(archive, state),
            started_at=_text(meta.get("started_at")),
            finished_at=_text(meta.get("timestamp")),
            format_version=meta.get("format_version"),
            recon_version=meta.get("recon_version"),
            config_sha256=meta.get("config_sha256"),
            tables=[
                TableStatus(
                    name=t.get("name"),
                    type_url=t.get("type_url"),
                    status=t.get("status"),
                    records=t.get("records"),
                    error=_table_error(t.get("error")),
                    truncated=t.get("truncated") is True,
                    changes=_read_back(ChangeSummary, t.get("changes")),
                    range=_read_back(RangeOutcome, t.get("range")),
                )
                for t in meta["tables"]
            ],
            window=_read_back(WindowRecord, meta.get("stream")),
        )


def _read_back[D: BaseModel](shape: type[D], recorded: Any) -> D | None:
    """What an archive's meta recorded, read as ``shape``; None when it recorded nothing, or
    something this recon does not read as one (an older or newer shape of it)."""
    if not isinstance(recorded, dict):
        return None
    try:
        return shape.model_validate(recorded)
    except ValidationError:
        return None


def _table_error(error: Any) -> TableError | None:
    """A table's error as an archive's meta holds it, read for the fields this recon knows:
    one a newer recon wrote may carry more, and is not refused for them."""
    if not isinstance(error, dict):
        return None
    fix = error.get("fix")
    return TableError(
        kind=str(error.get("kind", "unknown")),
        message=str(error.get("message", "")),
        fix=fix if isinstance(fix, str) else None,
    )


def _text(value: Any) -> str | None:
    """A time read back from an archive's meta, where its format may have parsed it into a
    date."""
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)
