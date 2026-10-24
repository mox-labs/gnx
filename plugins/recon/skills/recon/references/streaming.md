# Streams: sources that do not end

A file that keeps growing, or a server that keeps sending events, never finishes, so a survey cannot wait for it. recon collects such a source as a **stream**: its records go into **windows**, each window an ordinary archive (a `meta.yaml`, one table, a state, a query), sealed when the window ends and chained to the one before. This page is the contract: every key and flag, its default, and every refusal with its fix.

## Contents

- [When to use a stream](#when-to-use-a-stream)
- [A stream's entry](#a-streams-entry)
- [The collector types](#the-collector-types)
- [Running a stream](#running-a-stream)
- [How a window ends](#how-a-window-ends)
- [Flow: the buffer and its overflow](#flow-the-buffer-and-its-overflow)
- [Stopping, crashing and resuming](#stopping-crashing-and-resuming)
- [Reading a stream](#reading-a-stream)
- [What a window records](#what-a-window-records)
- [In a program](#in-a-program)
- [A collector type of your own](#a-collector-type-of-your-own)

## When to use a stream

Use one for a source whose records do not end: a log file followed as it grows (`tail`), server-sent events (`sse`), a command that never exits (`cli` with `window:`). Use a survey for a source that answers and is done. Every stream is followed exactly as a survey's archive is: `recon watch`, `recon query`, `recon status`.

## A stream's entry

An entry is a stream when it has `window:`, or when its type's records do not end (`tail`, `sse`). There is no `mode:` key.

```yaml
catalog:
  - {name: app, type: file, url: logs/app.log}
collectors:
  - name: log
    type: tail
    source: app
    window: {seconds: 300}
    retain: {windows: 288}
```

| Key | Holds | Default |
|---|---|---|
| `window` | `{seconds?, records?, bytes?}`: a window ends when the first is reached. `seconds` is a bare number of seconds, as `deadline:` is, at least 1; a time window is half-open, `[open, open + seconds)`, from the window's open | `{seconds: 300}` for a type that never ends; at least one bound when written |
| `buffer.records` | the most records that wait between the source and the window being written | 10000 |
| `buffer.bytes` | the most bytes of them; one more record is taken while fewer wait, so a stream holds at most `bytes` + `max_record_bytes` | 16777216 (16 MiB) |
| `buffer.max_record_bytes` | a record larger than this is dropped as it arrives, and counted | 1048576 (1 MiB) |
| `buffer.overflow` | what a full buffer does with one more record: `block`, `drop_newest` or `drop_oldest` | `block` for a type recon reads at its own pace (`tail`, `cli`), `drop_newest` for one whose source sends regardless (`sse`) |
| `retain` | `all`, or `{windows?, seconds?, bytes?}`: which sealed windows to keep | `all` |

`recon survey <name> --dry-run --json` (and `recon stream ... --dry-run`) reports every one of these in `provenance`: `{path, value, source, note}`, `source` being `mission` (the config set it), `type` (the collector type's declaration decided it) or `recon` (recon's default). A stream that keeps every window because its entry says nothing is a `retain_unset` warning with its growth and a fix; write `retain: all` to keep every window and say so.

Refused by the plan, each with its path and a fix: a `window:` with no bound, or of `seconds` under 1 (each window's end is a marker in the buffer, outside its bound); `buffer:` or `retain:` on an entry that is no stream; a stream that would run against more than one source (pin one with `source:`); `deadline:` on a stream (a window ends by `window:`, as a success; a deadline would fail it); a `normalize:` column named `_position`; `max_record_bytes` above `buffer.bytes`; a `retain:` that is neither `all` nor a mapping of `windows`, `seconds`, `bytes`.

`retain:` removes the oldest sealed windows its rules do not keep, after each window is sealed: `windows: N` keeps the newest N; `seconds: S` keeps those closed within S seconds of the newest's close; `bytes: B` keeps the newest whose sizes fit in B. With more than one rule, a window is kept only while every rule keeps it. The newest window is always kept, and a window never sealed (what a dead writer left) is never removed. Each removed window leaves a tombstone, a line of `retired.jsonl` beside the windows saying what it was and why it went, so a hole in the chain reads as retention, not loss.

## The collector types

| Type | Source | Each record is | Position (`_position`) |
|---|---|---|---|
| `tail` | a `file` source: `url` is the file, relative to the project root | each line: its JSON object, else `{line}` (and so is a JSON line holding `_position`) | `{inode, offset}`: the file and the byte after the line |
| `sse` | an `api` source, with `endpoint` (and `params`) as an `api` collector's | each event with data: `{id, event, data}`, `data` parsed when it is JSON | the event's id |

`tail` takes `start: end` (the default, as `tail -f`) or `beginning`, where a stream with no position to go on from begins the file, and `interval` (1), the seconds between two reads that find nothing new. A rotated file (renamed away, a new one in its place) is read to its end, then the new one from its start; a file truncated in place, or a position the file no longer holds, is read from its start and reported as a gap. A line longer than the source's `max_bytes` fails the stream.

`sse` asks for `text/event-stream` with the source's credential as its `auth` says. When the connection ends or breaks off it connects again with `Last-Event-ID`, after a full-jitter wait of up to `min(30, retry * 2^n)` seconds (`retry` is the server's, 1 until it says; `n` the connections in a row that brought no event), and records the stretch as a gap. The waits are not seeded, so processes streaming from one server do not reconnect in step; the gaps say when each was. A stop ends a wait at once. `reconnect` (5) is the connects in a row allowed to fail; one more fails the stream (`transient`). A connection that is made and later ends spends none of it: a read times out after the source's `timeout` of silence, which is a reconnect, so a healthy quiet stream runs for as long as it is asked. 401 or 403 fails it at once (`auth`), and so does any other refusal (`run_failed`). An event whose data passes `max_record_bytes`, or with a line past it, is dropped as its lines arrive (a long line is let go to its newline, never held), and counted dropped in its window; so the source's `max_bytes` never comes into it.

## Running a stream

```
recon stream <mission> --stream <name> [--windows N | --until TIME | --forever] [--dry-run] [--json]
```

| Flag | Default | Does |
|---|---|---|
| `--stream` (or `--collector`) | required | the stream to run: a collector of the mission that is a stream |
| `--windows` | 1, when neither `--until` nor `--forever` is given | stop after sealing N windows |
| `--until` | none | stop at TIME (ISO 8601, UTC unless it names its offset): the window it cuts is sealed `until` |
| `--forever` | off | run until stopped |
| `-n`, `--dry-run` | off | print the plan and run nothing, refusing what the run would; `provenance` also says how long the run is and why (`stream.windows`, `stream.until`, `stream.forever`, `source` `command` or `recon`), and when it returns at the latest: `stream.returns`, seconds from its start (one 300 s window: 300), or null when only a stop or the source ends it |
| `-c`, `--replace` | | as `recon survey`'s |

It always returns unless told otherwise, and says when as it starts (stderr; `{"kind": "stream", "returns_s", "returns"}` to `on_event`): one window of the default 300 s holds the command up to five minutes, so set a shorter `window:` or run it in the background when that is too long to wait. Ctrl-C or SIGTERM is a stop, not a failure: the window being written is sealed `signal` once what waits in the buffer is written, however busy the writer is when it comes, and the command returns. A second one seals it at once: what waits, and what later windows had counted, is counted dropped in it. A third is an interrupt, as for any other command. One process runs one stream; a second writer of the same stream is refused with `busy` (exit 11). Run again, a stream goes on from its last window.

Refused with exit 2 (`usage`) and a fix: `--forever` with `--windows` or `--until`; `--windows` below 1; an `--until` that has passed. A collector that is no stream of the mission is `not_found` (exit 4), naming the mission's streams.

Under `--json`, one `recon.v1.stream` document: `mission`, `stream`, `path`, `state` (the stream's, after the run), `windows` (sealed by this run), `records`, `dropped`, `first` (the first window's id), `last` (the last window sealed, a window outcome) and `warnings`. Without it, each window is a line on stderr as it is sealed. A window whose source failed exits with that failure's kind.

`recon survey <mission>` runs **one** window of each stream entry, after the snapshot tables, and lists each in the survey document's `windows`: a stream run from cron. A window that failed makes the survey `incomplete`.

## How a window ends

A window outcome (in `recon.v1.stream`'s `last` and `recon.v1.survey`'s `windows`) holds `stream`, `index`, `id`, `archive`, `opened_at`, `closed_at`, `closed_by`, `dropped`, `high_water` (the most records that waited) and `table` (its one table: `status`, `records`, `error`).

| `closed_by` | Means | `status` |
|---|---|---|
| `seconds`, `records`, `bytes` | the window's own bound was reached, named by its key in `window:` | `ok` |
| `eof` | the source ended | `ok` |
| `signal` | the stream was stopped | `ok` |
| `until` | the stream reached `--until` | `ok` |
| `error` | the source failed (or reconnects ran out): the window **keeps the records it got**, since a source that sends cannot be asked again, and reads `incomplete` | `error` |

A window that ends is a success, never `transient`. A survey's table that fails is retracted; a window never is.

## Flow: the buffer and its overflow

The source is read on a thread of the stream's own into the buffer, and each window is written from it, so a source that sends while a window is being written is read meanwhile. Each record is stamped by the clock as the buffer takes it and assigned its window there. Every record that arrives is written or dropped, and every drop is counted in the window it fell in: in `meta.yaml` (`stream.sources[0].dropped`), the window's `recon.collect` end event (`recon.dropped`), its progress events, and one `stream_overflow` warning for the run with the totals and a fix.

## Stopping, crashing and resuming

A stream killed mid-window (SIGKILL, a crash) leaves that window unsealed: it reads `incomplete` and keeps every whole line it had; it is never changed, but a torn last line (a write the kill cut short) is moved aside to `<table>.jsonl.tmp.torn`. The next run chains its first window to it (`previous`) and resumes the source after the position of the last whole record kept: exactly once for a source with positions (`tail`, and `sse` whose server honours `Last-Event-ID`). Every restart is recorded as a gap in the first window of the run, from the last window's close (unknown after a crash) to the restart: `resumed: true` when the source was asked to go on from a position, so a source that does not honour it shows where; `resumed: false` for a source without positions (`cli`), which cannot go on from where it was.

## Reading a stream

| | Does |
|---|---|
| `recon watch <mission> --stream <name>` | follows the stream from its latest window (or `--archive <window id>`) into each window after it, exactly as an archive is followed; returns once nothing extends the stream and its last window is read. The cursor carries the window; one whose window `retain:` removed since goes on from the next window kept, and that read's `skipped` (`{first, last, count}`) names the windows it jumped |
| `recon query <mission> "<SQL>" --stream <name>` | each table is the union of its windows' records, by column name; `--since` and `--until` choose the windows that meet `[since, until)` from their ids alone; `--archive` names one window. Windows collected under different configs are refused (`query`, exit 10) with the ranges to narrow to. `recon.v1.query-rows` gains `stream` and `windows`: `{first, last, count}`, the windows it read. Past 300 windows it warns `many_windows`, with how to narrow |
| `recon status <mission> --json` | each mission's `streams`: `name`, `path`, `state` (`writing` while a stream runs, else its last window's), `windows`, `first`, `latest`, `retired` |

A window's id is its index, then when it opened (`00000003-2026-10-24-010203-456789`), so ids sort by order and say when each window began.

## What a window records

`meta.yaml` holds what a survey's does (its `tables`, `config_sha256`, times) and `stream`:

| Field | Holds |
|---|---|
| `name`, `index`, `previous` | the stream, the window's index, and the window before it (null for the first) |
| `opened_at`, `closed_at`, `closed_by` | the window's span and how it was closed |
| `window` | the window spec in effect |
| `sources` | one entry: `source`, `output`, `first` and `last` (the positions of its first and last record; `last` carries the one before through a window with no record), `records`, `dropped`, `gaps` (`{from, to, resumed}` each), `arrivals` (`{first, last}`: the order of its first and last record admitted) |
| `reproducible` | true when the window kept a capture and dropped nothing |

Every record written carries `_position` when its source gives one, in the same line, so a stream resumes from its last whole line. With `preserve_raw: true`, each window keeps one capture of kind `stream` (`recon --skill -r capture-format`): each record as its collector gave it, before `normalize:`, with its order and its offset from the window's open, from which the window replays to the same bytes. The same arrivals give the same windows and the same `meta.yaml` and event log, whatever thread wrote them.

## In a program

```python
import recon

done = recon.stream("my-feed", "log", windows=12)  # one window unless told otherwise
failure = recon.stream_failure(done)  # None, or the error its last window ended on

for read in recon.watch("my-feed", stream="log"):
    for row in read.records:
        print(read.archive_id, row["record"])

rows = recon.query("my-feed", "SELECT count(*) AS n FROM log", stream="log")
```

`recon.stream(name, collector, *, windows=None, until=None, forever=False, config=None, replace=False, on_event=None, stop=None, root=None)` returns `recon.v1.stream` (`recon.StreamDocument`, its `last` a `recon.WindowOutcome`); `until` is a `datetime` with its zone. `stop` is a `recon.StreamStop`: `stop.ask()`, from any thread or a signal handler, stops the stream as a signal stops the command, and it returns. A `KeyboardInterrupt` in the calling thread stops it too, but one that lands while the store writes a record seals the window at once; ask a `StreamStop` from your handler to drain instead. `recon.read_since`, `recon.watch` and `recon.query` take `stream=`; `recon.query` takes `since=` and `until=` with it.

## A collector type of your own

A type registers through the `recon.collectors` entry-point group, as `tail` and `sse` do, its contract in `recon.domain.collector`. A type whose records do not end declares `unbounded=True` on its `CollectorType`, and `paced_by="source"` when its source sends whether recon is ready or not (the default, `"recon"`, is for one recon reads at its own pace): the plan then makes each of its entries a stream, with the default window and overflow those declare. Its collector:

- yields records from `collect` for as long as the stream runs, on the stream's own thread, and opens no telemetry span;
- puts each record's position, when it has one, under `_position` (any JSON value), and takes a keyword `after` in `collect`: the position of the last record kept, to go on after it;
- reports a stretch when its source was not read through `ctx.gaps.gap(Gap(start, end, resumed=...))`, and waits on `ctx.clock`;
- has a `close()` that ends a read in progress from another thread, so a stream stops promptly. A source that has not returned its next record within 5 seconds of the stream stopping is a `source_stuck` warning, and its thread is left.
