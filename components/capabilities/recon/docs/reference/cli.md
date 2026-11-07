# The command line, its documents and its archives

Every `recon` command, the `--json` document each prints, the exit codes, the error envelope, and what an archive holds. The guide an agent reads is `recon --skill`; each command's own flags are `recon <command> --help`.

## Commands

```
recon init <mission> [-t <template> | -c <file>] [--replace]
                                                       scaffold a mission; again with the
                                                       same config, it changes nothing
recon survey <mission> [-n|--dry-run [--check]] [-c <file> [--replace]]
             [--range START..END] [--rebase <output>] [--restart <output>]
                                                       plan, then run the collection; a
                                                       ranged entry's backfill or reset
recon status [<mission>]                               missions, archives, installed plugins;
                                                       with a mission, every archive it has
recon query <mission> "SQL" [--archive <id>] [--baseline <id>]
                                                       DuckDB over an archive's tables
recon watch <mission> [--archive <id>] [--cursor <c>] [--interval <s>]
            [--max-bytes <n>] [--once]
                                                       follow an archive as a survey writes it
recon templates                                        list built-in templates
recon capture <mission>                                append a hook payload (stdin) as a capture
recon schedule install|remove <mission> [--platform <p>]
                                                       run a mission by the OS scheduler,
                                                       as its schedule: says; or stop it
recon schedule list|status [<mission>]                what is installed; each schedule's
                                                       last tick, lateness and skips
recon subscribe <mission> <name> [--cursor <c>]        what changed, as a subscription says
recon ack <mission> <name> <cursor>                    move its cursor on, never back
recon unsubscribe <mission> <name>                     forget what it acked
recon deliver <mission> [--subscription <name>]        give each row to its command
recon --skill [-r <reference>]                         print the skill (for Claude)
```

`-c -` reads the config from stdin. Off a terminal, human output is never wrapped, so a path or a command stays whole on its line. `recon query` reads only the mission's own archives: no other file, no DuckDB extension, no URL. It runs SELECT statements only, so it writes nothing: a `COPY`, `ATTACH`, `CREATE` or other statement is a `query` error ([SECURITY.md](../../SECURITY.md), boundary 6).

`init` reports its `outcome`: `created`, `unchanged` (the mission already had this config, byte for byte) or `replaced` (`--replace` overwrote a different one). A dry run shows each HTTP run's request as it would be sent: the method, the URL with every query parameter, the headers the mission decides and the body, with a credential `REDACTED` as telemetry has it. It runs, fetches and connects to nothing. `--dry-run --check` also runs each type's preflight, which it names: for an `mcp` run it starts each stdio server (and connects to each remote one), lists its tools, checks each run's tool and arguments against them, and stops it. What it found is the plan's `requirements`, each with whether it holds, and `runnable` (null unchecked); a survey fails the table whose requirement does not hold, as `config`. `recon query` names the archive its rows came from (`archive_id`); `recon status <mission>` lists that mission's archives as `missions[0].history`, each with its id, when it started and finished, its state and its tables (a stream's windows too, under each stream's `history`, each with its `window` record). One table SQL cannot read, such as an OpenAlex record keeping its word-keyed `abstract_inverted_index`, is a `table_unreadable` warning at the end of the survey, and does not stop queries of the others: they run with the same warning, and a query of that table fails as `query` with the fix. A failed query's fix is about what failed: the tables there are for a missing table, DuckDB's message and its SQL documentation otherwise. A row is cited by its archive, its table and its line there: `recon query <mission> "SELECT line, * FROM lines.<table>" --archive <id>` gives each record its `line`, counted as it was landed; `baseline.<table>`, with `--baseline <id>`, is the same table of an earlier archive, for what is new since it.

Every command takes `--json`: one JSON document on stdout whose `schema` names its shape (`recon.v1.survey`, `recon.v1.plan`, `recon.v1.status`, `recon.v1.query-rows`, `recon.v1.templates`, `recon.v1.mission`, `recon.v1.capture`); collections are always wrapped in an object. `recon watch --json` is the one command that prints more than one: a `recon.v1.records-since` document a line, one per read, until it returns.

## Exit codes

The codes up to 9 are the ones every gnx capability CLI agrees on; recon's own kinds take codes from 10 up. recon's tables are `recon.EXIT_CODES`, `recon.EXIT_MEANINGS` and `recon.EXIT_NEXT`, and `recon --help` prints them:

| Code | Kind |
|---|---|
| 0 | `ok` |
| 1 | `unknown`: a failure recon did not classify |
| 2 | `usage`: a bad flag, argument or mission name |
| 3 | `config`: every problem listed, with its path, kind and fix |
| 4 | `not_found`: a mission, archive or template |
| 5 | `transient`: a 429, a 5xx or a network error; retry later. A table cut by its `deadline:` or `timeout:` fails the same way on every retry: raise the one its error names |
| 6 | `auth`: a credential was refused |
| 7 | `conflict`: `init` or `survey -c` would overwrite a different mission config; `--replace` overwrites it |
| 8 | `run_failed`: a table failed, not only on credentials or transiently; the archive is kept (a table's own unclassified error carries the same kind) |
| 9 | `store`: the survey could not finish its archive (its meta or its completion could not be recorded); the archive is kept |
| 10 | `query`: the SQL failed, or reached past the tables of the archives the query names |
| 11 | `busy`: another recon is writing the stream, or surveying the mission (one survey a mission at a time, so two never overlap); the error's `details` name the other's `pid` |
| 12 | `stale`: a tick's config changed since its schedule was installed; read the change, then `recon schedule install <mission>`, and its ticks run again |
| 130 | `interrupted`: Ctrl-C or SIGTERM stopped the command (`interrupted` is the exit kind every gnx tool shares; the archive of a survey stopped so reads `ended: stopped`). `recon stream` is the exception: a stop is how its run ends, so it seals the window `stopped` and exits 0 |

A survey in which any table failed still writes its archive (it reads `incomplete`) and still reports every table's status, then exits **6** if any table failed on credentials, **5** if every failure was transient, and **8** otherwise. A survey whose meta or completion could not be recorded (a full disk) reports `status: incomplete` with an `archive_unfinished` warning, and exits **9** (`store`) whatever its tables did. Every failure, a usage error and Ctrl-C included, prints one envelope on stderr (one JSON line under `--json`), the shape the capability CLIs agree on: `{"schema": "recon.v1.error", "error": {"kind", "message", "fix", "complete", "problems", "details"}}`. Each problem is `{path, kind, message, fix, allowed, got, source}`, its `path` dotted from the top of the config (`collectors.0.type`) and its `kind` from the shared vocabulary (`unknown_key`, `missing_key`, `invalid_value`, `name_unknown`, ...). `complete` is false when part of the config could not be checked, so more problems may follow the fixes. Warnings travel inside the `--json` document, as problem records in its `warnings` list: among them `extract_missing` (a survey's table of a collector without `extract:` got one record holding a list of objects, the path to set as `extract:` named), `table_unreadable` (a survey wrote, or a query's archive has, a table SQL cannot read) `archive_unfinished` (the survey could not record its meta or mark its archive complete), and `extract_unresolved`, `records_not_objects`, `captures_unmatched` and `result_empty`, each for a table kept empty because recon could not find or keep its records (an `extract:` that resolves to nothing in a response that is not empty, items that are not objects, captures no filter kept, an MCP tool or resource that answered with no content), so an empty table never reads as a source that had nothing.

## Archives

Each survey lands one archive, named by its id (when it began: `2026-04-01-143022-000000`), which every command that reads one takes as `--archive`. An archive holds a table per collector × source, its records exactly as normalized; its meta (format version 2: the recon version, the config's sha256, its `index` among the mission's archives, how the project root was found and whether each declared variable was set, never its value, and per table its `type_url`, `records`, `status`, `seconds`, `error {kind, message, fix}`, and for an entry with `range:` the source time it covers, `range`, which the next survey goes on from: `recon --skill -r ranges`); the survey's event log ([telemetry.md](telemetry.md)); and, with `preserve_raw: true`, its capture log. Where and how an archive is kept is recon's own: read it through `recon status`, `recon query` and `recon watch`, which name it by id, and never by its files, which change without notice. `recon status <mission> --json` gives each archive's meta, and `recon query` reads its tables, an earlier archive's beside them with `--baseline <archive id>` (`baseline.<table>`), each record's JSON text as `json.<table>`, and each record's line, what a row is cited by, as `lines.<table>`.

Everything recon writes for a mission is its owner's alone: each directory it makes under `.recon/` is `0700` and each file `0600`, whatever the umask would allow. A mission's tables hold what its sources answered, a mailbox as readily as a public feed. A directory or file that was there before keeps its mode: `recon status` warns (`mission_exposed`) when a mission's is open to other users, with the `chmod -R go-rwx` that closes it, and changes nothing itself.

A collector may declare `type_url:` (dotted grammar, default `recon.v1.records`) so a consumer can tell what a table holds without reading collector code. `status` and `query` still read archives written by recon 0.8 (`format_version: 1`), and read the meta a newer recon recorded for the fields they know. Only what a survey made as an archive is one: anything else beside the archives is left alone. Archives are listed, compared and read in the order they were made, by `index`, given under the mission's lock, so a clock set back between two surveys never puts the later first; an archive made before indexes has none, and those come first, by when they began. An archive whose `table_layout` this recon does not know (a newer recon wrote it) is refused by `query` with that number, rather than misread.

An archive's `state`, in `status`, `query` and a survey's own `status`, is `writing`, `complete` or `incomplete`: a state, where a table's `ok` or `error` is an outcome. The survey holds a lock on the archive while it writes it, so `writing` is a survey running now, on this host, and `incomplete` is one that failed a table, was stopped or crashed; `ended` says which (`finished`: it ran to its end and a table failed; `stopped`: it was stopped on request, Ctrl-C or SIGTERM, and its meta says so; `crashed`: it recorded no meta, because it was killed or could not record one). A table being written is readable a whole record at a time, at most 64 KiB or 250 ms after its collector gives each record; it is sealed when its writer renames it into place as it ends, so a table a dead survey left behind is never sealed, and a table that fails is retracted, its records with it, those a reader already read included. `query` reads only sealed tables, so a query of an archive being written sees the tables finished so far.

`recon watch <mission>` follows an archive while a survey writes it, printing each record as it is written, and returns when the survey ends (at once for a finished archive; `--once` reads once). In a program, `recon.read_since(mission, cursor)` is one read, and `recon.watch` each read in turn: each call returns the whole records written since `cursor` (from the first, without one), at most `max_records` records (1000 by default) and `max_bytes` of them (1 MiB), cut at a whole record and never fewer than one, with `more` when a bound cut the read, from the sealed tables and the one being written alike, with the archive's `state`, the tables `exhausted` (sealed and read to their end) or `retracted` since, and the `cursor` for the next call (`recon.v1.records-since`). The cursor is opaque: pass it on unchanged, with the archive's id as `archive=`; one recon did not give for that archive is a `usage` error. A table not yet sealed may still be retracted, which voids the records read from it. Reading writes nothing, so an archive is byte for byte the same whether or not it was read while being written.

## Capture logs

`preserve_raw: true` keeps every fetched response in an append-only capture log with content-addressed bodies. A `capture` collector reads capture logs and normalizes their bodies, which lets recon re-normalize an old survey without refetching, and normalize data some other process fetched. `recon capture` feeds a mission's log from a Claude Code `PostToolUse` hook. The format is an interface other tools can write: [capture-format.md](capture-format.md), which `recon --skill -r capture-format` also prints.
