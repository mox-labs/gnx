# recon

> Heterogeneous sources in, structured data out. Claude queries the result.

Recon fetches from HTTP APIs, runs CLI tools, scrapes web pages, calls the tools and reads the resources of MCP servers, and reads capture logs that other tools wrote, then reshapes every response into uniform JSONL that Claude can query with SQL. A source that never ends (a log file that keeps growing, a server that keeps sending events) is collected as a stream, into windows that are archives too. The reshape is declarative, not code. Write a YAML config once; recon plans it, then runs it mechanically — no LLM in the loop.

## Why

Claude reasons well but spends inference cycles badly when asked to babysit HTTP. Parsing JSON, retrying 429s, normalizing nested fields — all of it is mechanical work that costs the same inference budget as actual reasoning. Recon takes that off Claude's critical path.

What's left for Claude is the work only Claude can do: deciding what matters, spotting patterns across sources, connecting findings that nobody wrote down as findings.

A recon archive with 5,000 records is queryable without loading 5,000 records into Claude's prompt. That's the deeper capability: **recon extends Claude's effective context through structured indirection**. The data lives in a file Claude can ask questions of, not in the prompt.

For the full argument, see [docs/explanation/capability.md](docs/explanation/capability.md).

## Install

recon is a package in the [gnx](https://github.com/mox-labs/gnx) repository, installed from it at the release's tag; it is not on a package index:

```bash
uv tool install "git+https://github.com/mox-labs/gnx@recon-v0.13.0#subdirectory=components/capabilities/recon"
recon --version
```

Inside a gnx checkout, `uv tool install --editable components/capabilities/recon` installs your working copy instead. The OpenTelemetry SDK and its OTLP exporters come with it, so the `OTEL_*` variables are enough to export telemetry (see [Telemetry](#telemetry)), and so does the MCP client (`fastmcp-slim[client]`), which only a survey that reaches an MCP server loads.

Claude loads the skill on demand via `recon --skill`; the gnx `recon` plugin ships the same skill.

## Quick start

```bash
recon templates                                   # the built-in scaffolds
recon init my-scan --template code-forensics      # .recon/my-scan/config.yaml
$EDITOR .recon/my-scan/config.yaml                # set the placeholders
recon survey my-scan --dry-run                    # plan: validate, show every run, write nothing
recon survey my-scan                              # run: .recon/my-scan/archive/<timestamp>/
recon query my-scan "SELECT line FROM todo_files LIMIT 20"
```

Missions live in `.recon/<name>/` at the root of the git repository (the current directory outside one), and a `local` source's relative `url` is relative to that root, so a mission scans the same directories wherever it is run from. For the probe → normalize → survey workflow, see [docs/how-to/first-survey.md](docs/how-to/first-survey.md).

## Domains

Recon is general-purpose. The built-in templates each showcase a distinct capability — they are examples, not the tool's purpose.

| Domain | What the config collects | Starter template |
|---|---|---|
| Code forensics | `rg`, `git log`, filesystem stats with patterns fan-out | `code-forensics` |
| GitHub audit | Issues, PRs, releases via REST API | `github-audit` |
| Doc site survey | Fetch pages, convert to markdown | `docs-mine` |
| Package registries | Normalize PyPI / npm / crates.io into one schema | `package-registries` |
| Factual grounding | Wikipedia + Wikidata SPARQL | `factual-ground` |
| Content tracking | RSS / Atom feeds via XML parsing | `rss-monitor` |
| MCP servers | A stdio or remote server's tools and resources, called by recon | `mcp-server` |
| MCP tool results | Results a Claude Code hook captured, normalized without refetching (a server behind a browser sign-in) | `mcp-captures` |
| Streams | A log file followed as it grows (`tail`), server-sent events (`sse`), into windows | *(see `recon --skill -r streaming`)* |
| Literature review | Semantic Scholar, arXiv, OpenAlex, Zenodo | *(a catalog owned by the consuming plugin — `recon init -c`)* |
| Dependency audits, issue triage, API snapshots | `cargo tree`, `gh issue list`, any REST endpoint | *(see `recon --skill -r config-patterns`)* |

The common shape: *something out there has data in some response format, and Claude needs to reason over it as structured records.* Recon bridges that.

## Commands

```
recon init <mission> [-t <template> | -c <file>] [--replace]
                                                       scaffold a mission; again with the
                                                       same config, it changes nothing
recon survey <mission> [-n|--dry-run] [-c <file> [--replace]]
                                                       plan, then run the collection
recon status [<mission>]                               missions, archives, installed plugins;
                                                       with a mission, every archive it has
recon query <mission> "SQL" [--archive <id>]           DuckDB over an archive's tables
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
recon --skill [-r <reference>]                         print the skill (for Claude)
```

`-c -` reads the config from stdin. Off a terminal, human output is never wrapped, so a path or a command stays whole on its line. `recon query` reads only the mission's own archives: no other file, no DuckDB extension, no URL. It runs SELECT statements only, so it writes nothing: a `COPY`, `ATTACH`, `CREATE` or other statement is a `query` error ([SECURITY.md](SECURITY.md), boundary 6).

`init` reports its `outcome`: `created`, `unchanged` (the mission already had this config, byte for byte) or `replaced` (`--replace` overwrote a different one). A dry run shows each HTTP run's request as it would be sent: the method, the URL with every query parameter, the headers the mission decides and the body, with a credential `REDACTED` as telemetry has it. It runs and fetches nothing, but for an `mcp` run's preflight, which it names: it starts each stdio server (and connects to each remote one), lists its tools, checks each run's tool and arguments against them, and stops it. What it found is the plan's `requirements`, each with whether it holds, and `runnable`; a survey fails the table whose requirement does not hold, as `config`. `recon query` names the archive its rows came from (`archive_id`); `recon status <mission>` lists that mission's archives as `missions[0].history`, each with its id, path, when it started and finished, its state and its tables. One table SQL cannot read, such as an OpenAlex record keeping its word-keyed `abstract_inverted_index`, is a `table_unreadable` warning at the end of the survey, and does not stop queries of the others: they run with the same warning, and a query of that table fails as `query` with the fix. A failed query's fix is about what failed: the tables there are for a missing table, DuckDB's message and its SQL documentation otherwise. A row is cited by its line in one archive's table file: `SELECT ordinality AS line, * FROM read_json_auto('<archive path>/<table>.jsonl') WITH ORDINALITY`, never a glob across archives, whose columns can differ.

Every command takes `--json`: one JSON document on stdout whose `schema` names its shape (`recon.v1.survey`, `recon.v1.plan`, `recon.v1.status`, `recon.v1.query-rows`, `recon.v1.templates`, `recon.v1.mission`, `recon.v1.capture`); collections are always wrapped in an object. `recon watch --json` is the one command that prints more than one: a `recon.v1.records-since` document a line, one per read, until it returns.

### Exit codes

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
| 9 | `store`: the survey could not finish its archive (`meta.yaml` or the completion marker could not be written); the archive is kept |
| 10 | `query`: the SQL failed or reached outside the mission's archives |
| 11 | `busy`: another recon is writing the stream, or surveying the mission (one survey a mission at a time, so two never overlap); the error's `details` name the other's `pid` |
| 12 | `stale`: a tick's config changed since its schedule was installed; read the change, then `recon schedule install <mission>`, and its ticks run again |
| 130 | `interrupted`: Ctrl-C or SIGTERM stopped the command (`interrupted` is the exit kind every gnx tool shares; the archive of a survey stopped so reads `ended: stopped`). `recon stream` is the exception: a stop is how its run ends, so it seals the window `stopped` and exits 0 |

A survey in which any table failed still writes its archive (marked `.incomplete`) and still reports every table's status, then exits **6** if any table failed on credentials, **5** if every failure was transient, and **8** otherwise. A survey whose `meta.yaml` or completion marker could not be written (a full disk) reports `status: incomplete` with an `archive_unfinished` warning, and exits **9** (`store`) whatever its tables did. Every failure, a usage error and Ctrl-C included, prints one envelope on stderr (one JSON line under `--json`), the shape the capability CLIs agree on: `{"schema": "recon.v1.error", "error": {"kind", "message", "fix", "complete", "problems", "details"}}`. Each problem is `{path, kind, message, fix, allowed, got, source}`, its `path` dotted from the top of the config (`collectors.0.type`) and its `kind` from the shared vocabulary (`unknown_key`, `missing_key`, `invalid_value`, `name_unknown`, ...). `complete` is false when part of the config could not be checked, so more problems may follow the fixes. Warnings travel inside the `--json` document, as problem records in its `warnings` list: among them `extract_missing` (a survey's table of a collector without `extract:` got one record holding a list of objects, the path to set as `extract:` named), `table_unreadable` (a survey wrote, or a query's archive has, a table SQL cannot read) `archive_unfinished` (the survey could not write `meta.yaml` or mark its archive complete), and `extract_unresolved`, `records_not_objects`, `captures_unmatched` and `result_empty`, each for a table kept empty because recon could not find or keep its records (an `extract:` that resolves to nothing in a response that is not empty, items that are not objects, captures no filter kept, an MCP tool or resource that answered with no content), so an empty table never reads as a source that had nothing.

## Archives

```
.recon/<mission>/archive/<timestamp>/
  <output>.jsonl       one table per collector × source; records exactly as normalized
  meta.yaml            format_version 2: recon version, config sha256, table_layout 1
                       (this layout of the tables), resolved (how the project root was
                       found: argument, repository or working_directory; each variable
                       the mission declares, set or unset, never its value), and per
                       table its type_url, records, status, seconds, error {kind,
                       message, fix} (null for a table that succeeded)
  telemetry.jsonl      the survey's recon.v1.event lines (see Telemetry); not a table
  .incomplete          written first, removed once meta.yaml is written and every table
                       succeeded: a failed table, Ctrl-C, SIGTERM, a kill or a meta.yaml
                       that could not be written leaves it
                       (meta.yaml's ended: finished, or stopped on request)
  writer.lock          locked by the survey while it writes the archive, then removed
  captures.jsonl, raw/ the capture log, when preserve_raw: true
```

Everything recon writes for a mission is its owner's alone: each directory it makes under `.recon/` (the mission's, its archives', streams' and captures') is `0700` and each file (the config, tables, `meta.yaml`, logs, captures, locks) `0600`, whatever the umask would allow. A mission's tables hold what its sources answered, a mailbox as readily as a public feed. A directory or file that was there before keeps its mode: `recon status` warns (`mission_exposed`) when a mission's is open to other users, with the `chmod -R go-rwx` that closes it, and changes nothing itself.

A collector may declare `type_url:` (dotted grammar, default `recon.v1.records`) so a consumer can tell what a table holds without reading collector code. `status` and `query` still read archives written by recon 0.8 (`format_version: 1`), and read a `meta.yaml` from a newer recon for the fields they know. Only a directory named as a survey names its archive (`2026-04-01-143022-000000`) is an archive: another directory under `archive/` is left alone. An archive whose `table_layout` this recon does not know (a newer recon wrote it) is refused by `query` with that number, rather than misread.

An archive's `state`, in `status`, `query` and a survey's own `status`, is `writing`, `complete` or `incomplete`: a state, where a table's `ok` or `error` is an outcome. The survey holds an `flock` on `writer.lock` while it writes, so `writing` is a survey running now, on this host, and `incomplete` is one that failed a table, was stopped or crashed; `ended` says which (`finished`: it ran to its end and a table failed; `stopped`: it was stopped on request, Ctrl-C or SIGTERM, and its `meta.yaml` says so; `crashed`: it left no `meta.yaml`, because it was killed or could not write one). A table being written reaches its file a whole line at a time, at most 64 KiB or 250 ms after its collector gives each record; it is sealed when its writer renames it into place as it ends, so a table a dead survey left behind is never sealed, and a table that fails is retracted, its records with it, those a reader already read included. `query` reads only sealed tables, so a query of an archive being written sees the tables finished so far.

`recon watch <mission>` follows an archive while a survey writes it, printing each record as it is written, and returns when the survey ends (at once for a finished archive; `--once` reads once). In a program, `recon.read_since(mission, cursor)` is one read, and `recon.watch` each read in turn: each call returns the whole records written since `cursor` (from the first, without one), at most `max_records` records (1000 by default) and `max_bytes` of them (1 MiB), cut at a whole record and never fewer than one, with `more` when a bound cut the read, from the sealed tables and the one being written alike, with the archive's `state`, the tables `exhausted` (sealed and read to their end) or `retracted` since, and the `cursor` for the next call (`recon.v1.records-since`). The cursor is opaque: pass it on unchanged, with the archive's id as `archive=`; one recon did not give for that archive is a `usage` error. A table not yet sealed may still be retracted, which voids the records read from it. Reading writes nothing, so an archive is byte for byte the same whether or not it was read while being written.

## Capture logs

`preserve_raw: true` keeps every fetched response in an append-only capture log with content-addressed bodies. A `capture` collector reads capture logs and normalizes their bodies, which lets recon re-normalize an old survey without refetching, and normalize data some other process fetched. `recon capture` feeds a mission's log from a Claude Code `PostToolUse` hook. The format is an interface other tools can write: [docs/reference/capture-format.md](docs/reference/capture-format.md), which `recon --skill -r capture-format` also prints.

## Telemetry

recon reports what it did on open standards, with a local record by default. The names below (spans, metrics, events, attribute keys) are not contract and may change without notice; what telemetry never carries, below, is.

**The event log.** Every survey writes `telemetry.jsonl` into its archive, beside `meta.yaml`: one `recon.v1.event` line when a span starts and one when it ends (and one per progress event), `{schema, name, time, span, parent, kind, attributes, status, message, duration_ms}`, the times from recon's clock so a survey against a fixed clock writes the same log twice. A start with no end is where a killed survey stopped. The file is open for the whole survey and each line reaches it as it happens, so it can be read while the survey runs. The spans:

| Span | Attributes |
|---|---|
| `recon.survey` | `recon.mission`, `recon.archive`; at its end `recon.tables`, `recon.tables.failed`, `recon.records` |
| `recon.collect`, one per table | `recon.table`, `recon.collector`, `recon.collector.type`, `recon.source`; at its end `recon.records`. While the table is written, a `recon.collect.progress` event of the span, at most once a second, carries `recon.records`, the records written so far (an OpenTelemetry span event). A stream's window is one too, in the window's own event log, with `recon.stream` and `recon.window` (its index), and `recon.dropped` in its progress and at its end; its times are the window's own (its open, its records' arrivals, its close) |
| `recon.http.request`, one per attempt to send a request (kind client) | OpenTelemetry's HTTP client span attributes, [semantic conventions v1.44.0](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/http/http-spans.md#http-client-span) (stable): `http.request.method`, `server.address`, `server.port`, `url.full`, `http.response.status_code`, and `http.request.resend_count` on a retry |
| `recon.mcp.call`, one per call to an MCP server, a listing included (kind client) | `mcp.method.name` (`tools/call`, `tools/list`, `resources/read`, ...), `gen_ai.tool.name` for a tool call, `recon.source`; never the arguments or the result |
| `recon.query` | `recon.mission`, `recon.archive`; at its end `recon.rows` |

A span that failed has `status: error`, a `message` in recon's own words that quotes nothing from the error (no command, URL or SQL; a failed table's error is in the archive's `meta.yaml`), and `error.type`: a recon error kind (`transient`, `auth`, `run_failed`, ...), or for a request the status code (`503`), the httpx2 exception (`httpx2.ConnectError`) or `body_too_large`. Nothing secret and no body is recorded: `url.full` has user information, the source's `auth.param` value and the conventions' signature parameters replaced by `REDACTED`, and no header, request body or response body is an attribute. A query writes no archive, so its span goes only to OpenTelemetry.

**OpenTelemetry.** recon emits the same spans through the OpenTelemetry API (instrumentation scope `recon`, schema URL `https://opentelemetry.io/schemas/1.44.0`), and two counters derived from the `recon.collect` ends: `recon.collect.records` (`{record}`, by `recon.collector.type`) and `recon.collect.failures` (`{table}`, by `recon.collector.type` and `error.type`). The library writes through `opentelemetry-api`, which does nothing until an SDK is installed. As OpenTelemetry advises for a library ([Libraries](https://opentelemetry.io/docs/concepts/instrumentation/libraries/): "Libraries should only use the OpenTelemetry API"; "All application configuration is hidden from your library through the Tracer API"), `import recon` never installs the SDK: a program that wants recon's telemetry exported configures OpenTelemetry itself, as for any instrumented library, or calls `recon.configure_telemetry()`, which does what the command line does. The `recon` command is an application, so it does install the SDK, which recon depends on with its OTLP exporter, and the standard variables are enough to export, signal by signal. Traces are exported when `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or `OTEL_TRACES_EXPORTER` is set, metrics when `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` or `OTEL_METRICS_EXPORTER` is; recon installs the SDK's provider for each with an OTLP exporter over `http/protobuf`, the one protocol recon exports over (`OTEL_EXPORTER_OTLP_PROTOCOL=grpc` is refused with an `otel_unavailable` warning naming what to set, and the signals it governs are not exported; `OTEL_*_EXPORTER=none` turns a signal off). Everything else (headers, timeouts, `OTEL_SERVICE_NAME`, default `recon`, `OTEL_RESOURCE_ATTRIBUTES`) the SDK reads itself; `OTEL_SDK_DISABLED=true` turns it off. In an install without the SDK, the variables put an `otel_unavailable` warning in the `--json` document of `recon survey`, `recon survey --dry-run` and `recon query` (`configure_telemetry()` returns it). A provider already installed is kept, signal by signal, and recon's spans or metrics go there. Each exported span carries `recon.event.span`, its id in the event log, so a line of `telemetry.jsonl` is the span with that id in the trace whose `recon.survey` span carries the archive's `recon.archive`. While a span is open it is the current one, so a span another library opens during recon's work (an instrumented client, a plugin's own) nests under it; each HTTP request carries its span as a W3C `traceparent` header, so the server's spans can join the trace. Neither happens without an SDK installed. A sink that fails is dropped for the rest of the operation (a `telemetry_failed` warning), and the OpenTelemetry spans it held open are ended as failed rather than lost. The event log is written either way: an archive's contents do not depend on the environment it was surveyed in.

**No receiver slows a survey.** A survey hands OpenTelemetry and its `on_event` callback their events through a bounded queue (4096 events) that a thread of its own drains, so a slow exporter or callback neither slows the writing of the archive nor changes a byte of it; only the event log is written inline, as archive data. When the queue is full an event is dropped rather than waited for, and at the survey's end the receivers get up to 10 seconds to catch up. What they never got is counted in an `events_dropped` warning in the survey's document (its `got` is the count): it is not in `meta.yaml` or the event log, which would then differ between two runs of the same survey. A callback that raises gets nothing more, and is a `telemetry_failed` warning; the survey goes on. A receiver that never returns from an event (an exporter that hangs) cannot hold the survey either: past the 10 seconds it is named in a `telemetry_stuck` warning and left, with the thread delivering to it, until it returns. A span is still current for the work inside it, and each request still carries its `traceparent`, however far behind the queue is.

## What recon is not

- **Not an agent.** Recon doesn't use an LLM during execution. It runs the config you wrote. Intelligence lives outside the tool.
- **Not a research autopilot.** Recon produces structured data; synthesis stays with Claude and with the human.
- **Not a message bus.** A survey asks each source what its mission says, writes the answers into one timestamped archive, and ends. A source that does not end is collected as a stream, into windows each sealed as an archive (`recon stream`), and a stream runs one window unless told otherwise. What either writes can be read while it is written (`recon watch`, `recon watch --stream`, `recon.read_since`), as the archives' own files; recon pushes nothing to subscribers.
- **Not a scheduler.** A mission's `schedule:` is kept by the operating system's own scheduler (launchd, systemd's user timers, or cron), onto which `recon schedule install` projects it; there is no recon daemon. Each tick is one survey, held to the config that was installed (`recon --skill -r schedules`).

## Design

Library first, hexagonal. `import recon` is the API: `recon.init`, `plan`, `survey`, `stream`, `status`, `query`, `read_since`, `templates`, `capture` each return a versioned document (a frozen pydantic record, `recon.SurveyDocument` and its siblings) whose `.document()` is what the CLI prints under `--json`, or raise an error whose `kind` says what to do. Each takes `root=`, the project its missions live in, so a program need not change directory: `recon.survey("m", root=Path("~/src/app").expanduser())`. `ConfigError` is also a `ValueError` and `NotFoundError` a `KeyError`. The CLI calls those names and nothing else.

The domain holds the frozen Pydantic models, the normalize language, and the ports, each hiding a decision likely to change: `MissionStore` (where missions live, and that they are YAML), `ArchiveStore` (how an archive is stored), `ArchiveQuery` (the SQL engine and its sandbox), `Clock`, `Requester` (HTTP), `ShellRunner`, `DocumentConverter`, `CaptureLog` and `EventSink` (where telemetry goes). The application plans a mission (`plan.py`), builds a survey's collectors and holds every type to the same rules (`collectors.py`: only the types the mission uses, each seeing only the environment it declares, `normalize:` applied to every type), runs a survey over the ports (`survey.py`), runs a stream through its window writer, a bounded buffer and a thread that reads its source (`stream.py`), and composes the operations (`use_cases.py`). Outbound adapters implement the ports with YAML files, JSONL directories, DuckDB, httpx2, subprocess, markitdown and OpenTelemetry, and the `mcp` collector type is FastMCP's client on the MCP SDK; `composition.py` is the one place they meet, and holds no policy. Its `root()` binds each port to its adapter unless it is given one (the clock, the plugins, the archive store, the survey's requester, the shell, the converter), so a test's root is recon's own with fakes, and the command line runs over a root passed to it as `main(obj=...)`. `layers.json`, run by `tests/test_layering.py`, holds the import rules, and `tests/test_determinism.py` the contract that the same mission and the same answers give the same records.

Collector types and transforms are extensions, loaded from the entry-point groups `recon.collectors` and `recon.transforms`. The built-ins register through the same groups:

- collector types: `api`, `cli`, `web`, `capture`, `mcp`, and `tail` and `sse`, whose records do not end
- transforms: `$html2text`, `$inverted_index`, `$join`, `$first`, `$markitdown`

A plugin that fails to load is reported by `recon status` and `survey --dry-run` and does not break the others. A collector type declares the keys it takes (a schema the plan validates them by), the source kinds it adds and the keys those take, and may hold a session across its tables, which recon closes when the survey ends; a type that declares a preflight is asked by a dry run what each of its runs needs of the host. To write one: [docs/reference/extending.md](docs/reference/extending.md).

The tests run offline, except the live integration tests against OpenAlex, arXiv, Semantic Scholar and Zenodo, which run only with `RECON_LIVE=1` (`just recon-live`).

## Docs

- [docs/explanation/capability.md](docs/explanation/capability.md) — what recon does for Claude and why
- [docs/how-to/first-survey.md](docs/how-to/first-survey.md) — a first mission, end to end
- [docs/how-to/search-api-post-body.md](docs/how-to/search-api-post-body.md) — POST-body search APIs (Exa, Firecrawl, Tavily, Serper)
- [docs/how-to/collect-from-an-mcp-server.md](docs/how-to/collect-from-an-mcp-server.md) — call an MCP server's tools and read its resources
- [docs/how-to/capture-mcp-results.md](docs/how-to/capture-mcp-results.md) — capture MCP tool results with a hook and query them
- [docs/how-to/watch-a-survey.md](docs/how-to/watch-a-survey.md) — read a survey's records while it runs
- [docs/reference/extending.md](docs/reference/extending.md) — adding a collector type or a transform
- [docs/reference/capture-format.md](docs/reference/capture-format.md) — the capture log format (`recon.v1.capture`), for a tool that writes captures
- [docs/reference/errors.md](docs/reference/errors.md) — every error kind, problem kind and warning kind
- [docs/reference/telemetry.md](docs/reference/telemetry.md) — the event log, the spans and attributes, exporting through OpenTelemetry, and its warnings
- [docs/reference/live-reading.md](docs/reference/live-reading.md) — `recon watch`, `read_since` and archive states: the contract, the defaults, the refusals
- [docs/reference/pagination.md](docs/reference/pagination.md) — `paginate:` on an `api` collector: `link`, `header`, `cursor`, `offset`; every stop, what it records, every default and refusal
- [docs/reference/changes.md](docs/reference/changes.md) — `changes:`: what changed in a table since its baseline, as a table of its own; the key index, the mission's key, every default and refusal
- [docs/reference/streaming.md](docs/reference/streaming.md) — streams: `window:`, `buffer:`, `retain:`, the `tail` and `sse` types, `recon stream`, `--stream`; every default and refusal
- [docs/reference/schedules.md](docs/reference/schedules.md) — schedules: `schedule:`, `recon schedule install`, `list`, `remove`, `status` and `tick`, what is projected onto launchd, systemd and cron, the tick's log; every default and refusal
- [docs/design/2026-10-19-record.md](docs/design/2026-10-19-record.md) — the design judgments behind 0.11.0 and 0.12.0, and recon's claims
- [CHANGELOG.md](CHANGELOG.md)
- `recon --skill` — the skill Claude uses to author missions; `-r normalize-spec`, `-r config-patterns`, `-r capture-format`, `-r live-reading`, `-r streaming`, `-r pagination`, `-r changes`, `-r schedules`, `-r telemetry`, `-r errors` for the references. The plugin's copy is generated from it (`just recon-skill`).

## License

MIT
