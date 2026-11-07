# recon — contracts

What each surface touched in 0.17.0 to 0.19.0 promises: the decision it hides, its members, its defaults, how it fails, and how stable it is (ruling G6).

"Stable" means: a change that breaks a caller is a major version of the document schema (`recon.v2.*`) or, for a library call or a default, a line under "Changed (breaking)" in the CHANGELOG, never silent. A field or kind added is not breaking: a reader ignores a field it does not know, and handles a kind it does not know by the exit code.

## Contents

- [The two layers](#the-two-layers)
- [Opening recon, and what a mission may name](#opening-recon-and-what-a-mission-may-name)
- [Extension points](#extension-points)
- [The collector contract](#the-collector-contract)
- [The component face](#the-component-face)
- [Ranges: `range:`](#ranges-range)
- [The plan](#the-plan)
- [Queries over archives](#queries-over-archives)
- [Archives, by reference](#archives-by-reference)
- [The storage ports](#the-storage-ports)
- [The mission store](#the-mission-store)

## The two layers

**Hides:** which layer a concern belongs to. The collect core (`recon.core`) knows one act: fetching one span of a source, capturing it, normalizing it, landing it with its checkpoint in one seal; the plan of an act; SQL over what was landed. The observe layer (`recon.observe`) knows acts over time: which span each run asks for, what changed (derived into each archive after its seal), retention, schedules, subscriptions and their delivery, following, status. The test for a new concern: does it need to know about an act other than the one running? Then it is the observe layer's.

**Members.** `recon.core.__all__`: the core's public names, what the observe layer and any outside driver use, its driving port `Collection` (`prepare`, `read`, `hold`, `snapshot`, `stream`, `query`, `describe`, `preflight`, `init`, `templates`, `capture`) among them. `recon.open(root=None)` gives a session, `recon.Recon`, whose `collection` is the core's port and whose `observation` the observe layer's (`plan`, `survey`, `stream`, `status`, `read_since`, `watch`, `changes_since`, `subscribe`, `ack`, `unsubscribe`, `deliver`, `schedule_*`). The module functions are those operations.

**Guarantees.** The core never imports the observe layer; the observe layer imports nothing of the core but `recon.core`'s names (`tests/test_layering.py`, with a planted violation of each). An act the core runs alone lands and commits whole; what changed is derived after its seal (ruling R9). A table sealed under the mission's config with no change summary is **pending**: a change reader waits on it, and the next survey or stream run of that config derives it in its own archive before anything newer.

**Stability:** beta. `recon.core.__all__` is the surface a later split of the layers into two packages keeps; until then a name may move between the layers in a minor release, said in the CHANGELOG.

## Opening recon, and what a mission may name

**Hides:** where recon's state is and what it was built with. A session is opened over values; nothing is discovered behind a caller's back but the installed plugins, once per session.

**Members.** `recon.open(root=None, *, extensions=None, landing=None)` gives a session (`recon.Recon`). `extensions`: in-process extensions, each a `register(registry)` called with the session's registry ([Extension points](#extension-points)). `landing`: where its acts land and are read back (a `Store`: `Landing` and `Archives`), which recon's query engine must read. A config is the mission's own, a file (`-`: standard input), or a value (a `ReconConfig` or a mapping). `recon.types()` (`recon.v1.types`): each collector type, its origin, effects, `records_kind`, capabilities (`paginate`, `preflight`) and config JSON Schema; each source kind, its origin and config JSON Schema; each transform; each converter and the media types it converts. `recon.schema()` (`recon.v1.mission-config`): the config's JSON Schema. The command line: `--root DIR` (or `RECON_ROOT`), `recon types [--json]`, `recon schema`.

**Defaults.** The project is the git root of the working directory, else the working directory. No extensions. The landing is the project's archive directory. The module functions run over one session per project, opened on first use and kept for the process.

**Failure.** `usage` (exit 2): an extension that takes an installed name, a landing recon cannot query. A value config that is invalid fails as a file does (`config`, exit 3, every problem).

**Guarantees.** A value config is never written as the mission's `config.yaml`; each archive it lands keeps it as data under `config` in its meta, every URL in it reported as recon reports one (user information and signature parameters redacted). The plan's `provenance` gives every effective value of the config's own keys, as written (`mission`) or filled in (`recon`), with a note where unset means something.

**Stability:** beta. `extensions` are `register(registry)` callables on matrix's registry ([Extension points](#extension-points)).

## Extension points

**Hides:** how an extension reaches recon, and the harness contract recon is built on (matrix's registry today, slick's later): one module, `recon/_matrix.py`, names matrix.

**Members.** Four points on matrix's `Registry`, owned by `recon` (`recon.extension.POINTS`): `recon.collector` (a collector type, a config's `type:`: [the collector contract](#the-collector-contract)), `recon.source` (a source kind, a catalog source's `type:`: a `SourceKind`, its `check(source)` and `env(source)`, the entry's `config` the schema of the kind's own keys), `recon.converter` (a `Converter`: the `media_types` it claims and `convert(chunks, media_type, origin)`, picked by a body's name for it or its media type) and `recon.transform` (a callable; a normalize spec names it `path|$name`). A built-in by its short name (`api`, `local`, `json`, `$html2text`), anyone else's by its type URL (`acme.v1.collector.sql`, `$acme.v1.transform.redact`). An installed package registers from its `matrix.extensions` entry point, a callable `register(registry)`; code using recon passes the same callables to `recon.open(extensions=[...])`. At recon's points an entry is built from its needs alone (`build(**needs)`; a source kind, a converter and a transform need none), and its `config`, when it has one, is the schema of what a mission writes for it, which recon validates and hands over. Optional members of a contract are protocols of their own (`recon.core.domain.optional`: `SupportsClose`, `SupportsFollow`, `SupportsAround`, `SupportsTraceHeaders`; and the collector's `Paged` and `Preflight`), read with `isinstance`. Built-ins: collector types `api`, `cli`, `capture`, `mcp`, `tail`, `sse`; source kinds `api`, `local`, `file`, `mcp`; converters `json`, `xml`, `lines`, `document` (markitdown), `mcp_result`.

**Failure.** An installed extension that fails to load is quarantined whole (matrix's rule), every entry it registered with it, and reported (`recon status`, `recon types --json`, a plan's `plugin_broken` warning). An in-process one matrix refuses is `usage` (exit 2). An entry that builds the wrong kind of thing, names a need recon does not supply, or has a `config` that lets unknown keys through is reported broken and left out; so is a converter that claims a media type another already converts (recon's own keep theirs). A normalize column whose name starts with `_` is a plan problem: the prefix is recon's own (ruling R18).

**Stability:** experimental for the collector contract (below); the point names and the naming rule are kept.

## The collector contract

**Hides:** how to talk to one kind of source (S1). A collector type knows its source's protocol; recon knows, once, for every type (ruling R-NS): raw capture, conversion by media type, finding the records (`extract:`), shaping them (`normalize:`), landing, and a crawl's stopping, resumption and shift detection. A type does none of these, so none can forget one.

**Members** (`recon.extension`, structural Protocols in `recon.core.domain.collector`). A `Collector` has `effects` (a frozenset of `network`, `subprocess`, `filesystem`, or None for unknown), `records` (`finite`, `unbounded_recon_paced` or `unbounded_source_paced`), `accepts` (the source kinds its runs use; None: any, and none), `describe(run)` and `check(run)` (pure: what a planned run does, and its problems at paths under the entry), `collect(run, resume)` (an iterator of items) and `close()`. A `Run` is one table's: its `entry` (named for its output), its `source`, and `settings`, the type's own keys validated by its schema, filled for the run (a range's step). Items: `Body(media_type, data, request, ...)` (bytes, whole or as they arrive; its `converter` by name, its capture's `kind`, `status`, `headers`, the `secrets` recon keeps out of the capture, `origin`, a crawl `page`, `fields` every record carries, `scope` normalize may read, `unfit` for a misfit `extract:`), `Record(data, position, scope)`, `PageMark(index, token)`, `Gap`, `Dropped`, `Truncated(by)`; a streamed body's generator may return a `BodyEnd(status, request)`. A stream resumes with `Position(value)`, the position of the last record kept. Optional: `Paged` (`page(run, token) -> Page(body, next, token)`: recon runs the crawl, and only such a type takes `paginate:`) and `Preflight` (`preflight` in words, `requirements(run) -> [Requirement]`). Built as `build(**needs)` with the needs its registration names, from `NEEDS`: `requester`, `shell`, `clock`, `telemetry`, `deadline`, `rate_limiter`, `credentials`, `env`, `user_agent`, `project`, `capture_logs`. The conformance kit, `recon.testing.CollectorConformance`, checks a type by its behaviour; `recon.testing.records` is recon's pipeline for one run.

**Defaults.** One collector of a type serves every run of the type in an act (a survey, a stream's run, a checked dry run), built once for it and closed once when it ends, however it ends. A type is also built once per session with stand-in needs that refuse to be used, to read what it declares. `env` holds only the variables the mission declares for the type's sources; `credentials` resolves a source's references (`file:` relative to the mission). A body's converter is the one it names, else the one its media type picks (`type/subtype`, then `+suffix`, then `type/*`). A value `extract:` does not fit ends the table with the reason (`NoRecordsError`: kept empty and warned before its first record, failed after), as the source's error it most likely is; a body that says `unfit="skip"` (the capture type's, one of a log's many tools' results) gives nothing instead, and the table is empty with the first reason only when none of its bodies fit. A record from a body is positioned `{page, i}` when the body is a page; with no `normalize:` a record is written as found (its `fields`, never its `scope`).

**Failure.** A build that raises, or gives no `Collector`, is `plugin_broken` and fails its tables as `config`; others build. A `close()` that raises is a `plugin_broken` warning, and every other collector is still closed. Anything `collect`, `page` or a converter raises fails the table (its `kind` and `fix` carried, `run_failed` for an exception that is not recon's), but `NoRecordsError` before a record, which keeps the table empty and warns. An item that is not an item, or a `fields` or `scope` key not starting `_`, fails the table naming the type. A body that names no installed converter, or whose media type none claims, fails it as `config`. A resumed crawl's first page refused with `run_failed` begins the crawl again (`paginate_restarted`).

**Stability:** provisional. The contract may change in a minor release, said in the CHANGELOG, until three independent collector types (not recon's own, not test doubles) pass the kit; it freezes at 1.0.

## The component face

**Hides:** how a harness drives recon: matrix's component contract, kept in `recon/_matrix.py` alone.

**Members.** At matrix's `component` point: `recon.v1.component.plan` (requires nothing; provides `recon.v1.span`, one row per table: `snapshot`, `window`, or a range's `continuing`, `fixed` or `rolling` span with its `steps`), `recon.v1.component.collect` (requires `recon.v1.span`; provides `recon.v1.act`, one row per act as it seals: its archive, its stream when a window, its tables as sealed, each range the checkpoint it committed), `recon.v1.component.changes` (requires `recon.v1.act`; provides `recon.v1.change`, each change row with its mission and output). Config: `mission`, `root` (default: the directory the host composed for, matrix's `cwd` need), `kinds` (`span`, `act`, `change`, each renamable); `collect` takes `records: true` too. Payload schemas `recon.v1.span`, `recon.v1.act`, `recon.v1.change` (`recon.core.domain.documents`).

**Defaults.** By reference: an act's row names its archive, and its records stay there; `records: true` hands each sealed table's records over as rows of its entry's `type_url`.

**Guarantees.** An act's row is durable in the run's store when the act's archive is sealed, so a run killed mid-way keeps every act it sealed; the run's stop asks a stream's window to seal. A kind with no provider is a run input: a harness that schedules recon itself gives `recon.v1.span` rows and leaves `plan` out, and every ranged table of the mission must have one.

**Failure.** A member whose mission cannot be planned is refused when it is built (matrix's `ConfigError`). A span of another mission or output, or a ranged table given none, is `usage` when the run reaches `collect`.

**Stability:** beta. The kinds and their payloads are open vocabularies: a reader ignores a field it does not know.

## Ranges: `range:`

**Hides:** which source time a ranged table asks for on each run, and how that survives failures, kills, overlapping runs and retention. A mission says what kind of time it wants; recon decides the interval, the steps and the checkpoint.

**Members.** On a snapshot collector entry, `range:` with `start`, `end`, `last`, `step`, `settle`, `max_steps`, `align` (`recon.core.domain.ranges.RangeSpec`). The kind is which keys are written: `continuing` (neither `end` nor `last`), `fixed` (`start` and `end`), `rolling` (`last`). The placeholders `{range.start}`, `{range.end}`, `{range.last}`, each with an optional `:iso`, `:epoch`, `:epoch_ms` or `:date`, filled by the core into the type's own settings before the type sees them. Every record of a ranged table carries `_range` (`{start, end}`); every outcome of one (`recon.v1.survey` `tables[]`, the archive's meta, `recon.v1.status`) carries `range` (`start`, `end`, `kind`, `from`, `request`, `steps`). `recon survey --range START..END`, `--rebase OUTPUT`, `--restart OUTPUT`; the library's `interval=(start, end)`, `rebase=`, `restart=` on `recon.plan` and `recon.survey`.

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

**Members** (`recon.core.domain.archives`, Protocols): `Landing.hold(mission, stream=)` gives a `Hold`, which `begin(id)`s a `Landed`, `derive(ref)`s a sealed archive (a `Landed` again, for what is derived from its own tables), and `retire(ref, tombstone)`s a sealed archive. `Landed`: `ref`, `write`, `ends`, `keep`, `mark`, `captures`, `events`, `record` (the commit point), `complete`. `Archives`: `archives`, `streams`, `state`, `stream_state`, `meta`, `records`, `sidecar`, `last_record`, `read_since`, `size`, `retired`, `retired_count`, `abandoned`. `ArchiveQuery`: `run(refs, sql, baseline=)`, `unreadable(ref)`. Writing is apart from reading; `ArchiveRef(mission, id, stream, index)` names an archive, ordered by `rank`. The file adapter (`ArchiveFiles`) implements the first two; `DuckdbQuery` the third.

**Defaults.** Holds, locking, retention's mechanics and the layout are the file adapter's alone (`mission_layout.py`).

**Failure.** A second writer of what is held: `busy` (exit 11, the holder's pid). A taken id: `store`. `derive` of an archive never sealed: `not_found`; of one another writer holds: `busy`. While derived from, an archive reads `writing`; what it was marked (complete or not) and every table it sealed are left as they were. `record` replaces the meta whole or not at all; a writer that dies before it leaves tables no reader counts.

**Stability:** recon's own ports, not an extension point: `root(archives=...)` lets a test stand in for them. They may change in a minor release, said in the CHANGELOG.

## The mission store

**Hides:** where a mission is kept. `MissionStore` names missions by name: `where(name)` is a display string, never a path to build on; `config(name)`, `write_config`, `missions`, `capture_log`, `exposed`, `change_key`, `templates`. Only `project`, `read_config(path)` and `is_directory(path)` take or give a path, the person's own (`tests/test_ports.py` holds this).

**Stability:** as the storage ports.
