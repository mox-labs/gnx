"""Following what is landed: an archive's records as it is written, a stream's across its
windows, and an output's change rows across its archives.

Every read is stateless: the reader holds the cursor, which is opaque, and a read again from
one gives the same records. Nothing here writes, so a reader changes no byte of an archive.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from recon.core import (
    READ_MAX_BYTES,
    READ_MAX_RECORDS,
    WATCH_INTERVAL_S,
    ChangesSince,
    NotFoundError,
    RecordsSince,
    UsageError,
    WindowRange,
    window_index,
)
from recon.observe.application.subscriptions import ChangeReader

if TYPE_CHECKING:
    from collections.abc import Iterator

    from recon.core import ArchiveRef, Archives, Clock, Collection, MissionStore, Prepared

#: A stream's cursor names its format first: one this recon does not know is refused.
_STREAM_CURSOR = "s1"


class Follow:
    """Reads of a mission's archives, streams and change rows, through ``archives``; the core's
    ``collection`` finds an archive or a window by its id, and the mission store gives the
    mission's key, which signs a change cursor."""

    def __init__(
        self, *, collection: Collection, archives: Archives, missions: MissionStore, clock: Clock
    ) -> None:
        self._collection = collection
        self._archives = archives
        self._missions = missions
        self._clock = clock

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
        """The records of the mission's latest archive, or the one named, written since
        ``cursor`` (None: from its first), at most ``max_records`` and ``max_bytes`` of them. The
        archive's state is read before its records, so a read that finds it no longer
        ``writing`` and has no ``more`` holds its last records.

        With ``stream``, of that stream: from its latest window, or the one ``archive``
        names, or where ``cursor`` left off; a read that ends a window that is sealed, with a
        later one there, gives the cursor to that one and ``more``."""
        if stream is not None:
            return self._read_stream(name, stream, cursor, archive, max_records, max_bytes)[0]
        found = self._collection.archive(name, archive)
        return self._read(name, found, cursor, max_records, max_bytes)

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
        """Follow the mission's latest archive, or the one named, from ``cursor``: each
        :meth:`read_since` in turn, waiting ``interval_s`` on the clock whenever a read
        caught up while a survey still writes the archive, and reading again at once while
        a bound cuts the reads (``more``). Ends after the read that finds it no longer
        ``writing`` with no ``more``: at once for a finished archive, read whole. Raises
        UsageError for an ``interval_s`` not above 0.

        With ``stream``, follow that stream from its latest window (or the one ``archive``
        names, or ``cursor``'s) into each window after it, exactly as an archive is followed;
        ends once no writer extends the stream and its last window is read to its end."""
        if not interval_s > 0:
            raise UsageError(
                f"the watch's interval is the seconds between two reads, above 0; got {interval_s}",
                fix="leave the interval at its default (1 second), or give a number above 0",
            )
        if stream is not None:
            read, end = self._read_stream(name, stream, cursor, archive, max_records, max_bytes)
            yield read
            while not end:
                if not read.more:
                    self._clock.sleep(interval_s)
                read, end = self._read_stream(
                    name, stream, read.cursor, None, max_records, max_bytes
                )
                yield read
            return
        followed = self._collection.archive(name, archive)  # once: it does not change
        read = self._read(name, followed, cursor, max_records, max_bytes)
        yield read
        while read.state == "writing" or read.more:
            if not read.more:
                self._clock.sleep(interval_s)
            read = self._read(name, followed, read.cursor, max_records, max_bytes)
            yield read

    def changes_since(
        self,
        name: str,
        output: str,
        cursor: str | None,
        prepared: Prepared,
        *,
        max_records: int = READ_MAX_RECORDS,
        max_bytes: int = READ_MAX_BYTES,
    ) -> ChangesSince:
        """The change rows of ``output`` told since ``cursor`` (None: from the mission's first
        archive), across its archives (a stream's windows), at most ``max_records`` and about
        ``max_bytes`` of them: each row of a change table sealed since, once, with an ``id``
        that names it. Stateless: the caller holds the cursor, and reading again from one gives
        the same rows. Raises NotFoundError for an output no entry with ``changes:`` makes, and
        UsageError for a cursor recon did not give for this output."""
        reader = self.change_reader(name, output, prepared)
        position = None if cursor is None else reader.decode(cursor)
        read = reader.read(position, max_records=max_records, max_bytes=max_bytes)
        return ChangesSince(
            mission=name,
            output=output,
            rows=read.rows,
            count=len(read.rows),
            cursor=reader.encode(read.position),
            more=read.more,
            state=read.state,
            retired_range=read.retired,
        )

    def change_reader(self, name: str, output: str, prepared: Prepared) -> ChangeReader:
        """The reader of ``output``'s change rows in the mission ``name``, found by its plan:
        across its archives, or its stream's windows. Raises NotFoundError for an output no
        entry with ``changes:`` makes."""
        plan = prepared.plan
        if output not in plan.changes:
            known = sorted(plan.changes)
            raise NotFoundError(
                f"no output {output!r} with changes: in mission {name!r}; its outputs with "
                f"changes: {', '.join(known) or '(none)'}",
                fix="name one of them, or give its collector changes: {key: [<field>]} "
                "(recon --skill -r changes)",
                details={"value": output, "allowed": known},
            )
        return ChangeReader(self._archives, name, output, plan.changes[output],
                            self._missions.change_key(name), prepared.sha256)  # fmt: skip

    def _read_stream(
        self,
        name: str,
        stream: str,
        cursor: str | None,
        archive: str | None,
        max_records: int,
        max_bytes: int,
    ) -> tuple[RecordsSince, bool]:
        """One read of a stream, and whether it is the stream's last: no writer extends it,
        and its last window is read to its end. The stream's state is read first."""
        writing = self._archives.stream_state(name, stream) == "writing"
        windows = self._collection.windows(name, stream)
        inner: str | None = None
        skipped: WindowRange | None = None
        if cursor is not None:
            window_id, inner = _stream_cursor_read(cursor, stream)
            index = window_index(window_id) or 0
            later = [w for w in windows if (w.index or 0) >= index]
            if later and later[0].id == window_id:
                window = later[0]
            elif later:  # its window was retired since: go on from the next one kept
                window, inner = later[0], None
                skipped = self._skipped(name, stream, window_id, window.id)
            else:
                window = self._collection.window(name, stream, windows, window_id)
        elif archive is not None:
            window = self._collection.window(name, stream, windows, archive)
        else:
            window = windows[-1]
        read = self._read(name, window, inner, max_records, max_bytes)
        later = [w for w in windows if (w.index or 0) > (window.index or 0)]
        done = read.state != "writing" and not read.more
        if done and later:
            on = _stream_cursor(stream, later[0].id, None)
            update = {"cursor": on, "more": True, "stream": stream, "retired_range": skipped}
            return read.model_copy(update=update), False
        on = _stream_cursor(stream, window.id, read.cursor)
        update = {"cursor": on, "stream": stream, "retired_range": skipped}
        return read.model_copy(update=update), done and not writing

    def _skipped(self, name: str, stream: str, since: str, kept: str) -> WindowRange:
        """The windows a read jumps, from ``since`` (its cursor's, retired) to the one before
        ``kept``: counted from their indexes, the last named by its tombstone."""
        start, end = window_index(since) or 0, window_index(kept) or 0
        names = {
            window_index(str(stone.get("id"))): stone.get("id")
            for stone in self._archives.retired(name, stream=stream)
        }
        last = names.get(end - 1)
        return WindowRange(first=since, last=str(last) if last else None, count=end - start)

    def _read(
        self, name: str, archive: ArchiveRef, cursor: str | None, max_records: int, max_bytes: int
    ) -> RecordsSince:
        """One read of ``archive``, its state read first."""
        state = self._archives.state(archive)
        read = self._archives.read_since(
            archive, cursor, max_records=max_records, max_bytes=max_bytes
        )
        return RecordsSince(
            mission=name,
            archive=archive.id,
            state=state,
            ended=self._collection.ended(archive, state),
            table_records=[{"table": r.table, "record": r.record} for r in read.records],
            count=len(read.records),
            cursor=read.cursor,
            exhausted=read.exhausted,
            retracted=read.retracted,
            more=read.more,
        )


def _stream_cursor(stream: str, window: str, at: str | None) -> str:
    """The cursor of a read of ``stream``: the window, and the store's own cursor in it
    (None: from its start). Opaque: only :func:`_stream_cursor_read` reads it."""
    body = json.dumps({"stream": stream, "window": window, "at": at}, separators=(",", ":"))
    return _STREAM_CURSOR + body.encode().hex()


def _stream_cursor_read(cursor: str, stream: str) -> tuple[str, str | None]:
    """The window and the store's cursor in it that ``cursor`` holds. Raises UsageError for a
    cursor recon did not give for a stream, or gave for another stream."""
    body: Any = None
    if cursor.startswith(_STREAM_CURSOR):
        try:
            body = json.loads(bytes.fromhex(cursor.removeprefix(_STREAM_CURSOR)))
        except ValueError:
            body = None
    fresh = "read without --cursor to start from the stream's latest window"
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("stream"), str)
        or not isinstance(body.get("window"), str)
        or window_index(body["window"]) is None
        or not isinstance(body.get("at"), str | None)
    ):
        raise UsageError(
            "the cursor is not one recon gave for a stream: pass on the cursor the last read "
            "of the stream returned, unchanged",
            fix=fresh,
        )
    if body["stream"] != stream:
        raise UsageError(
            f"the cursor is for stream {body['stream']}, not {stream}",
            fix=f"read stream {body['stream']} with it (--stream {body['stream']}), or {fresh}",
            details={"stream": stream, "cursor_stream": body["stream"]},
        )
    return body["window"], body["at"]
