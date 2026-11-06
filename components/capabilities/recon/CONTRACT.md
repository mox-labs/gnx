# recon — contracts

What each surface touched in 0.17.0 promises: the decision it hides, its members, its defaults, how it fails, and how stable it is (ruling G6). The surfaces recon 0.17.0 did not touch (the collector contract, `root()`, the component face) are described where they live and get theirs as they are refactored.

"Stable" means: a change that breaks a caller is a major version of the document schema (`recon.v2.*`) or, for a library call or a default, a line under "Changed (breaking)" in the CHANGELOG, never silent. A field or kind added is not breaking: a reader ignores a field it does not know, and handles a kind it does not know by the exit code.

## Contents

- [Ranges: `range:`](#ranges-range)
- [The plan](#the-plan)
- [Queries over archives](#queries-over-archives)
- [Archives, by reference](#archives-by-reference)
- [The storage ports](#the-storage-ports)
- [The mission store](#the-mission-store)

## Ranges: `range:`

**Hides:** which source time a ranged table asks for on each run, and how that survives failures, kills, overlapping runs and retention. A mission says what kind of time it wants; recon decides the interval, the steps and the checkpoint.

**Members.** On a snapshot collector entry, `range:` with `start`, `end`, `last`, `step`, `settle`, `max_steps`, `align` (`recon.domain.ranges.RangeSpec`). The kind is which keys are written: `continuing` (neither `end` nor `last`), `fixed` (`start` and `end`), `rolling` (`last`). The placeholders `{range.start}`, `{range.end}`, `{range.last}`, each with an optional `:iso`, `:epoch`, `:epoch_ms` or `:date`, filled by the core into the type's own settings before the type sees them. Every record of a ranged table carries `_range` (`{start, end}`); every outcome of one (`recon.v1.survey` `tables[]`, the archive's meta, `recon.v1.status`) carries `range` (`start`, `end`, `kind`, `from`, `request`, `steps`). `recon survey --range START..END`, `--rebase OUTPUT`, `--restart OUTPUT`; the library's `interval=(start, end)`, `rebase=`, `restart=` on `recon.plan` and `recon.survey`.

**Defaults** (each in the plan's `provenance` with its origin): `step` 3600 s (a rolling range's: its `last`), `settle` 60 s, `max_steps` 24, `align` none, `start` none (the first run asks for one step back from the horizon). The horizon is `now - settle`, to the whole second, floored to `align`. Intervals are half-open.

**Guarantees.** A continuing or fixed range's checkpoint is the end of the latest range its table committed, by archive order, read from the archives kept and the tombstones of those retired. A range commits with its table, in the archive's meta, when the table is sealed `ok`, not `truncated`, and the meta is recorded with `ended` set: one commit, or none. So the committed ranges of a table, in order, are contiguous and disjoint; nothing committed is asked for again by a run that moves the checkpoint; a run that fails, is killed before its commit, or is cut short commits nothing and the next asks again. A run asks for at most `max_steps` steps. An override (`--range`) and a rolling range move no checkpoint. Proved by `tests/test_ranges_property.py` (random ticks, failures, kills before and after the commit, manual runs, backfills, retention).

**Failure.** Refused at plan time, before anything is fetched: `unresolved` (a placeholder with no `range:`), `range_unused`, `range_ambiguous`, `missing_key` (`end` without `start`), `invalid_value` (`end` not after `start`, a `step` no multiple of `align`, a placeholder nothing fills, `range:` on a stream), `range_deletes`. Refused before a run begins (`config`, exit 3): `range_steps`, `checkpoint_request_changed`. Warned: `range_behind`, `range_current`, `range_complete`, `range_overlaps`. `usage` (exit 2): an output `--rebase` or `--restart` names that no ranged entry makes, an interval with no time zone or no length, `--range` on a mission with no ranged entry.

**Stability:** stable. The request digest's value is not: compare it, never compute it.

## The plan

**Hides:** what checking a config costs. A plan is pure.

**Members.** `recon survey --dry-run`, `recon stream --dry-run`, `recon.plan(...)`, returning `recon.v1.plan`. `--check` (`check=True`) also runs each collector type's preflight (`mcp`: start each stdio server, connect to each remote one, list its tools).

**Defaults.** Unchecked. Without `--check` a plan writes, runs, fetches and connects to nothing; `requirements` and `preflight` are empty and `runnable` is null. A plan of a run whose type declares a preflight says in `provenance` `preflight: false` (origin `recon`) or `true` (origin `command`). Each source a run uses but a `local` one has `catalog.<i>.rate_limit` in `provenance`: as written (`mission`), or recon's default of 1 request a second, burst 1 (`recon`), for a source whose limits it does not know.

**Failure.** `config` (exit 3) lists every problem. A failed requirement is not an error: `runnable: false`, and the survey fails that table as `config`. `--check` without `--dry-run` is `usage` (exit 2).

**Stability:** stable. The rate default is a default: changing it again is breaking.

## Queries over archives

**Hides:** the engine that answers SQL, and where an archive's tables are.

**Members.** `recon query <mission> "SQL" [--archive <id>] [--baseline <id>] [--stream <c> [--since T] [--until T]]`, `recon.query(...)`, returning `recon.v1.query-rows`. Each sealed table by its name; `json.<table>` (each record's JSON text, one column `json`); over one archive, `lines.<table>` (each record with `line`, from 1, as landed: what a row is cited by); with a baseline, `baseline.<table>`.

**Defaults.** The latest archive; with `--stream`, every window of the stream, each table the union of its windows', in order (`--since`, `--until` narrow them).

**Failure.** The SQL is untrusted. It reads the tables of the archives named and nothing else: no path, no other archive, no other file of an archive, no extension, no write, no network (`query`, with a fix naming the views). No table: `not_found`. A table SQL cannot read: a `table_unreadable` warning, and the query runs if it does not use it.

**Stability:** stable.

## Archives, by reference

**Hides:** where and how an archive is kept. An archive is named by its id (and, for a window, its stream), never by a path.

**Members.** Every document names archives by `archive` / `id`; `recon status <mission>` lists them (`missions[0].history`), each with its meta, its tables (and their `range`) and, for a window, its `window` record. `--archive` and `--baseline` take an id.

**Failure.** An unknown id is `not_found`.

**Stability:** stable. The file layout is not a contract and changes without notice.

## The storage ports

**Hides:** durability, exclusion, layout, retention and the medium (owner ruling G5). No port carries a path.

**Members** (`recon.domain.archives`, Protocols): `Landing.hold(mission, stream=)` gives a `Hold`, which `begin(id)`s a `Landed` and `retire(ref, tombstone)`s a sealed archive. `Landed`: `ref`, `write`, `ends`, `keep`, `mark`, `captures`, `events`, `record` (the commit point), `complete`. `Archives`: `archives`, `streams`, `state`, `stream_state`, `meta`, `records`, `sidecar`, `last_record`, `read_since`, `size`, `retired`, `retired_count`, `abandoned`. `ArchiveQuery`: `run(refs, sql, baseline=)`, `unreadable(ref)`. Writing is apart from reading; `ArchiveRef(mission, id, stream, index)` names an archive, ordered by `rank`. The file adapter (`ArchiveFiles`) implements the first two; `DuckdbQuery` the third.

**Defaults.** Holds, locking, retention's mechanics and the layout are the file adapter's alone (`mission_layout.py`).

**Failure.** A second writer of what is held: `busy` (exit 11, the holder's pid). A taken id: `store`. `record` replaces the meta whole or not at all; a writer that dies before it leaves tables no reader counts.

**Stability:** recon's own ports, not an extension point: `root(archives=...)` lets a test stand in for them. They may change in a minor release, said in the CHANGELOG.

## The mission store

**Hides:** where a mission is kept. `MissionStore` names missions by name: `where(name)` is a display string, never a path to build on; `config(name)`, `write_config`, `missions`, `capture_log`, `exposed`, `change_key`, `templates`. Only `project`, `read_config(path)` and `is_directory(path)` take or give a path, the person's own (`tests/test_ports.py` holds this).

**Stability:** as the storage ports.
