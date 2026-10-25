# Reading an archive while it is written

A survey's archive can be read while the survey writes it: `recon watch` on the command line, `recon.read_since` and `recon.watch` in a program. This page is the contract of each, with every default and every refusal.

## Contents

- [An archive's state](#an-archives-state)
- [A table while it is written](#a-table-while-it-is-written)
- [`recon watch`](#recon-watch), and polling, for an agent
- [`recon.v1.records-since`](#reconv1records-since)
- [Field names](#field-names)
- [In a program](#in-a-program)
- [The survey's side](#the-surveys-side)

## An archive's state

`recon status`, `recon query`, `recon watch` and `recon.read_since` report an archive's `state`:

| `state` | Means | Changes? |
|---|---|---|
| `writing` | a survey on this host is writing it now | yes, to `complete` or `incomplete` |
| `complete` | its survey finished it, every table succeeded | never |
| `incomplete` | no survey holds it, and it was never completed: a table failed, or the survey was stopped or crashed | never |

A state, where a table's `ok` or `error` is an outcome. Beside it, `ended` says how its writer ended, or null while `writing`:

| `ended` | Means | Written by |
|---|---|---|
| `finished` | it ran to its end and wrote `meta.yaml`, so an `incomplete` archive's tables failed (or it could not be marked complete) | the writer, in `meta.yaml` |
| `stopped` | it was stopped on request (Ctrl-C, SIGTERM, a stream's stop) and wrote `meta.yaml` saying so, with the tables that ended before the stop | the writer, in `meta.yaml` |
| `crashed` | it left no `meta.yaml`, or only the one it writes as it compares its tables (`ended: null`, with the tables compared so far): it was killed (SIGKILL, its host went down), or could not write one (a full disk; that survey said so as it ended, with an `archive_unfinished` warning and exit 9). The two read alike because the record that would tell them apart is the one missing | no one: read from that absence |

A survey stopped on request exits `interrupted` (130), the exit kind every gnx tool shares; its archive reads `ended: stopped`. A stream's stop is how its run ends, so `recon stream` exits 0 (`recon --skill -r streaming`).

The survey holds an `flock` on `writer.lock` in the archive while it writes, and removes the file when it is done, so a survey that died reads `incomplete` at once, never `writing`. An archive is made whole, its lock and marker in place, under a hidden name (`.<archive id>.<pid>.making`) and renamed into place, so none is ever listed half made. A survey killed while making one leaves that hidden directory: `recon status` names each whose survey is gone in an `archive_abandoned` warning, with the command that removes it, and removes nothing itself. Whether a survey is alive is read from that lock, which a reader on the survey's own host sees reliably; on a shared filesystem read from another host, it may not be.

## A table while it is written

- A table's records reach its file as whole lines, at most 64 KiB or 250 ms after the collector gives them. A reader never sees part of a record.
- A change table (`<output>.changes`) is read only once it is sealed, never as it is written: one whose writer died mid-write told nothing, and the next survey tells its rows, so a reader that saw them early would see them twice.
- A table is **sealed** when its writer renames it into place as it ends: it gains and loses no line after that. A table a survey that died was writing is never sealed: it stays unsealed, and `query` never reads it.
- A table that **fails** is **retracted**: its file is removed, and the records read from it while it was written are void. The table's error is in `meta.yaml`. A stream's window is never retracted: one whose source failed is sealed with the records it got (`recon --skill -r streaming`), and `recon watch --stream` follows a stream's windows one after another.
- `recon query` reads sealed tables only. On an archive being written it sees the tables finished so far, and the table being written is not a view yet.

## `recon watch`

```
recon watch <mission> [--archive <id>] [--cursor <cursor>] [--interval SECONDS] [--once] [--json]
```

| Flag | Default | Does |
|---|---|---|
| `--archive` | the mission's latest archive | the archive to read, by the id `recon status <mission>` lists |
| `--cursor` | none: from the archive's first record | read on from where an earlier read left off |
| `--interval` | 1 second | the wait between two reads, once caught up with a survey still writing |
| `--max-bytes` | 1 MiB | the most bytes of records one read prints, cut at a whole record, at least one; a read it cuts is followed at once |
| `--once` | off | read once, print it, and return, whatever the state |
| `--json` | off | each read as one `recon.v1.records-since` document a line |

`recon watch` returns after the read that finds the archive no longer `writing` with nothing left to read: at once, after printing it whole, for a finished archive. Until then it holds the command, as long as the survey runs, and `recon watch --stream` of a stream run `--forever` never returns.

**Polling, for an agent.** A tool call that must return reads once at a time: `recon watch <mission> --once --json` prints one read and returns, whatever the state; the next call passes its `cursor` as `--cursor`, with its `archive` as `--archive` (or the same `--stream`), and so on. Read again at once while a read says `more`; once one says `state` is no longer `writing` and has no `more`, the archive is read to its end. A stream's read says its window's `state`, so whether a writer still extends the stream is `recon status <mission> --json`'s to say (its `streams[].state`). Each call is bounded by `--max-bytes`.

Without `--json`, each record is a JSON line on stdout, `{"table": ..., "record": ...}`, and stderr says each table read to its end or retracted, and the archive's state at the end. It exits 0 whatever the archive's state: the state is in the output.

Refused, with exit 2 (`usage`) and a fix:

- a `--cursor` recon did not give, or gave for another archive (the fix names that archive's `--archive`);
- an `--interval` of 0 or less, or a `--max-bytes` below 1.

## `recon.v1.records-since`

| Field | Holds |
|---|---|
| `mission`, `archive`, `path` | the archive read: its id (what `--archive` takes), and its directory |
| `state` | `writing`, `complete` or `incomplete`, read before the records |
| `ended` | `finished`, `stopped`, `crashed`, or null while `writing` |
| `table_records` | `{"table", "record"}` each: the whole records written since the cursor, table by table in output-name order, each table's in the order written |
| `count` | how many records |
| `cursor` | opaque: pass it, unchanged, to the next read of the same archive |
| `exhausted` | the tables read to their end: sealed, and every record of them read along this chain of cursors, so none come after and what was read of them stands |
| `retracted` | the tables that failed after records of theirs were read: void those records |
| `more` | a bound (`max_records`, `max_bytes`) cut this read before all that was written was read: read again at once |
| `stream`, `retired_range` | a read of a stream (`--stream`): its name, and the windows `retain:` retired before the cursor reached them, `{first, last, count}`, or null; see `streaming` |

Along a chain of cursors each table is told exhausted, or retracted, once. `sealed` is a table's own state, that its writer renamed it into place; `exhausted` is a reader's, that it has read all of a sealed table. Because `state` is read before the records, a read that says the archive is no longer `writing` and has no `more` holds the archive's last records.

## Field names

A field name has one shape in every recon document. `archive` is an archive's id (a stream window's is its id), what `--archive` takes; `path` is the directory of what the document is about (`archive_path`, in a plan, the directory a survey would write). `records`, `windows` and `captures` are counts, as in `meta.yaml`; a list of records read is `table_records`, a survey's windows of its streams are `stream_windows`, and a run of windows a query read is `window_range`, `{first, last, count}`. `first` and `last` are the two ends of a span: window ids in a range of windows (a whole window outcome is `last_window`), and in a window's `meta.yaml` a source's positions (`sources[]`) or orders of arrival (`arrivals`). `source` is a catalog source's name; where a plan's setting came from is its `origin`. `streams` lists a mission's streams; a plan's stream directories are `writes.stream_paths`, and whether it keeps captures is `writes.preserve_raw`. `skipped` lists the catalog sources a plan passed over; the windows a read of a stream jumped are its `retired_range`. `status` is an outcome: a survey's `complete` or `incomplete`, a table's `ok` or `error`. The one exception is the capture line, `recon.v1.capture` (`recon --skill -r capture-format`): other tools write it, and it keeps the names its schema gives, its `status` a response's HTTP status or a command's exit code.

## In a program

```python
import recon

read = recon.read_since("my-scan")  # from the first record
read = recon.read_since("my-scan", read.cursor, archive=read.archive)

for read in recon.watch("my-scan", interval_s=0.5):  # returns when the survey ends
    for row in read.table_records:
        print(row["table"], row["record"])
```

- `read_since(name, cursor=None, *, archive=None, max_records=1000, max_bytes=1048576, root=None)`: one read of at most `max_records` records and `max_bytes` bytes of them, cut at a whole record and never fewer than one. A `max_records` or `max_bytes` below 1 is a `UsageError`, and so is a cursor recon did not give for that archive.
- `watch(name, cursor=None, *, archive=None, interval_s=1.0, max_records=1000, max_bytes=1048576, root=None)`: each read in turn, at once while a bound cuts them (`more`), and waiting `interval_s` whenever a read caught up while a survey still writes. An `interval_s` of 0 or less is a `UsageError`, raised when the first read is asked for.

Reading writes nothing: an archive is byte for byte the same whether or not it was read while it was written.

## The survey's side

| Setting | Default | Where |
|---|---|---|
| `flush_bytes`, `flush_s` | 64 KiB, 0.25 s | `ArchiveFiles(clock, flush_bytes=, flush_s=)`: how long a table's records may wait before a reader sees them |
| `progress_s` | 1.0 s | `Survey(progress_s=)`: the least time between two `recon.collect.progress` events of a table |
| `queue`, `drain_s` | 4096 events, 10.0 s | `ThreadChannel(queue=, drain_s=)`, given to `root(channel=)`: the bound on events waiting for OpenTelemetry and `on_event`, and how long the survey's end waits for them |

While a table is written, its `recon.collect` span has a `recon.collect.progress` event at most once per `progress_s`, carrying `recon.records`, the records written so far. OpenTelemetry and `on_event` receive through the channel, so neither slows the writing of the archive; an event the channel could not deliver is counted in an `events_dropped` warning in the survey's document, and a receiver that had not returned from an event when the survey ended is named in a `telemetry_stuck` warning. The survey's end waits `drain_s` at most, however a receiver behaves.
