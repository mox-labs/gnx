# recon

> Heterogeneous sources in, structured data out. Claude queries the result.

Recon fetches from HTTP APIs, runs CLI tools, scrapes web pages, and reads capture logs that other tools wrote, then reshapes every response into uniform JSONL that Claude can query with SQL. The reshape is declarative, not code. Write a YAML config once; recon plans it, then runs it mechanically — no LLM in the loop.

## Why

Claude reasons well but spends inference cycles badly when asked to babysit HTTP. Parsing JSON, retrying 429s, normalizing nested fields — all of it is mechanical work that costs the same inference budget as actual reasoning. Recon takes that off Claude's critical path.

What's left for Claude is the work only Claude can do: deciding what matters, spotting patterns across sources, connecting findings that nobody wrote down as findings.

A recon archive with 5,000 records is queryable without loading 5,000 records into Claude's prompt. That's the deeper capability: **recon extends Claude's effective context through structured indirection**. The data lives in a file Claude can ask questions of, not in the prompt.

For the full argument, see [docs/explanation/capability.md](docs/explanation/capability.md).

## Install

recon is a package in the [gnx](https://github.com/mox-labs/gnx) repository, installed from it at the release's tag; it is not on a package index:

```bash
uv tool install "git+https://github.com/mox-labs/gnx@recon-v0.11.0#subdirectory=components/capabilities/recon"
recon --version
```

Inside a gnx checkout, `uv tool install --editable components/capabilities/recon` installs your working copy instead. To export telemetry over OTLP, install the `otel` extra: `uv tool install "recon[otel] @ git+https://github.com/mox-labs/gnx@recon-v0.11.0#subdirectory=components/capabilities/recon"` (see [Telemetry](#telemetry)).

Claude loads the skill on demand via `recon --skill`; the gnx `recon` plugin ships the same skill.

## Quick start

```bash
recon templates                                   # the built-in scaffolds
recon init my-scan --template code-forensics      # .recon/my-scan/config.yaml
$EDITOR .recon/my-scan/config.yaml                # set the placeholders
recon survey my-scan --dry-run                    # plan: validate, show every run, touch nothing
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
| MCP tool results | Results a Claude Code hook captured, normalized without refetching | `mcp-captures` |
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
recon templates                                        list built-in templates
recon capture <mission>                                append a hook payload (stdin) as a capture
recon --skill [-r <reference>]                         print the skill (for Claude)
```

`-c -` reads the config from stdin. Off a terminal, human output is never wrapped, so a path or a command stays whole on its line. `recon query` reads only the mission's own archives: no other file, no DuckDB extension, no URL. It runs SELECT statements only, so it writes nothing: a `COPY`, `ATTACH`, `CREATE` or other statement is a `query` error ([SECURITY.md](SECURITY.md), boundary 6).

`init` reports its `outcome`: `created`, `unchanged` (the mission already had this config, byte for byte) or `replaced` (`--replace` overwrote a different one). A dry run shows each HTTP run's request as it would be sent: the method, the URL with every query parameter, the headers the mission decides and the body, with a credential `REDACTED` as telemetry has it. `recon query` names the archive its rows came from (`archive_id`); `recon status <mission>` lists that mission's archives as `missions[0].history`, each with its id, path, when it started and finished, its state and its tables. One table SQL cannot read, such as an OpenAlex record keeping its word-keyed `abstract_inverted_index`, is a `table_unreadable` warning at the end of the survey, and does not stop queries of the others: they run with the same warning, and a query of that table fails as `query` with the fix. A failed query's fix is about what failed: the tables there are for a missing table, DuckDB's message and its SQL documentation otherwise. A row is cited by its line in one archive's table file: `SELECT ordinality AS line, * FROM read_json_auto('<archive path>/<table>.jsonl') WITH ORDINALITY`, never a glob across archives, whose columns can differ.

Every command takes `--json`: one JSON document on stdout whose `schema` names its shape (`recon.v1.survey`, `recon.v1.plan`, `recon.v1.status`, `recon.v1.query-rows`, `recon.v1.templates`, `recon.v1.mission`, `recon.v1.capture`); collections are always wrapped in an object.

### Exit codes

The codes up to 9 are the ones every gnx capability CLI agrees on; recon's own kinds take codes from 10 up. recon's tables are `recon.EXIT_CODES`, `recon.EXIT_MEANINGS` and `recon.EXIT_NEXT`, and `recon --help` prints them:

| Code | Kind |
|---|---|
| 0 | `ok` |
| 1 | `unknown`: a failure recon did not classify |
| 2 | `usage`: a bad flag, argument or mission name |
| 3 | `config`: every problem listed, with its path, kind and fix |
| 4 | `not_found`: a mission, archive or template |
| 5 | `transient`: retry later |
| 6 | `auth`: a credential was refused |
| 7 | `conflict`: `init` or `survey -c` would overwrite a different mission config; `--replace` overwrites it |
| 8 | `run_failed`: a table failed, not only on credentials or transiently, or the archive could not be finished; the archive is kept (a table's own unclassified error carries the same kind) |
| 10 | `query`: the SQL failed or reached outside the mission's archives |
| 130 | `interrupted`: Ctrl-C or SIGTERM |

A survey in which any table failed still writes its archive (marked `.incomplete`) and still reports every table's status, then exits **6** if any table failed on credentials, **5** if every failure was transient, and **8** otherwise. A survey whose every table succeeded but whose `meta.yaml` or completion marker could not be written (a full disk) reports `status: incomplete` with an `archive_unfinished` warning, and exits **8** too. Every failure, a usage error and Ctrl-C included, prints one envelope on stderr (one JSON line under `--json`), the shape the capability CLIs agree on: `{"schema": "recon.v1.error", "error": {"kind", "message", "fix", "complete", "problems", "details"}}`. Each problem is `{path, kind, message, fix, allowed, got, source}`, its `path` dotted from the top of the config (`collectors.0.type`) and its `kind` from the shared vocabulary (`unknown_key`, `missing_key`, `invalid_value`, `name_unknown`, ...). `complete` is false when part of the config could not be checked, so more problems may follow the fixes. Warnings travel inside the `--json` document, as problem records in its `warnings` list: among them `fan_out_mixed` (a plan's unpinned collector would run against sources of different types), `extract_missing` (a survey's table of a collector without `extract:` got one record holding a list of objects, the path to set as `extract:` named), `table_unreadable` (a survey wrote, or a query's archive has, a table SQL cannot read) `archive_unfinished` (the survey could not write `meta.yaml` or mark its archive complete), and `extract_unresolved`, `records_not_objects` and `captures_unmatched`, each for a table kept empty because recon could not find or keep its records (an `extract:` that resolves to nothing in a response that is not empty, items that are not objects, captures no filter kept), so an empty table never reads as a source that had nothing.

## Archives

```
.recon/<mission>/archive/<timestamp>/
  <output>.jsonl       one table per collector × source; records exactly as normalized
  meta.yaml            format_version 2: recon version, config sha256, and per table
                       its type_url, records, status, seconds, error {kind, message,
                       fix} (null for a table that succeeded)
  telemetry.jsonl      the survey's recon.v1.event lines (see Telemetry); not a table
  .incomplete          written first, removed once meta.yaml is written and every table
                       succeeded: a failed table, Ctrl-C, SIGTERM, a kill or a meta.yaml
                       that could not be written leaves it
  captures.jsonl, raw/ the capture log, when preserve_raw: true
```

A collector may declare `type_url:` (dotted grammar, default `recon.v1.records`) so a consumer can tell what a table holds without reading collector code. `status` and `query` still read archives written by recon 0.8 (`format_version: 1`).

## Capture logs

`preserve_raw: true` keeps every fetched response in an append-only capture log with content-addressed bodies. A `capture` collector reads capture logs and normalizes their bodies, which lets recon re-normalize an old survey without refetching, and normalize data some other process fetched. `recon capture` feeds a mission's log from a Claude Code `PostToolUse` hook. The format is an interface other tools can write: [docs/reference/capture-format.md](docs/reference/capture-format.md), which `recon --skill -r capture-format` also prints.

## Telemetry

recon reports what it did on open standards, with a local record by default.

**The event log.** Every survey writes `telemetry.jsonl` into its archive, beside `meta.yaml`: one `recon.v1.event` line when a span starts and one when it ends, `{schema, name, time, span, parent, kind, attributes, status, message, duration_ms}`, the times from recon's clock so a survey against a fixed clock writes the same log twice. A start with no end is where a killed survey stopped. The spans:

| Span | Attributes |
|---|---|
| `recon.survey` | `recon.mission`, `recon.archive`; at its end `recon.tables`, `recon.tables.failed`, `recon.records` |
| `recon.collect`, one per table | `recon.table`, `recon.collector`, `recon.collector.type`, `recon.source`; at its end `recon.records` |
| `recon.http.request`, one per attempt to send a request (kind client) | OpenTelemetry's HTTP client span attributes, [semantic conventions v1.44.0](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/http/http-spans.md#http-client-span) (stable): `http.request.method`, `server.address`, `server.port`, `url.full`, `http.response.status_code`, and `http.request.resend_count` on a retry |
| `recon.query` | `recon.mission`, `recon.archive`; at its end `recon.rows` |

A span that failed has `status: error`, a `message` in recon's own words that quotes nothing from the error (no command, URL or SQL; a failed table's error is in the archive's `meta.yaml`), and `error.type`: a recon error kind (`transient`, `auth`, `run_failed`, ...), or for a request the status code (`503`), the httpx exception (`httpx.ConnectError`) or `body_too_large`. Nothing secret and no body is recorded: `url.full` has user information, the source's `auth.param` value and the conventions' signature parameters replaced by `REDACTED`, and no header, request body or response body is an attribute. A query writes no archive, so its span goes only to OpenTelemetry.

**OpenTelemetry.** recon emits the same spans through the OpenTelemetry API (instrumentation scope `recon`, schema URL `https://opentelemetry.io/schemas/1.44.0`), and two counters derived from the `recon.collect` ends: `recon.collect.records` (`{record}`, by `recon.collector.type`) and `recon.collect.failures` (`{table}`, by `recon.collector.type` and `error.type`). `opentelemetry-api` is a dependency, and does nothing until an SDK is installed. As OpenTelemetry advises for a library ([Libraries](https://opentelemetry.io/docs/concepts/instrumentation/libraries/): "Libraries should only use the OpenTelemetry API"; "All application configuration is hidden from your library through the Tracer API"), `import recon` never installs the SDK: a program that wants recon's telemetry exported configures OpenTelemetry itself, as for any instrumented library, or calls `recon.configure_telemetry()`, which does what the command line does. The `recon` command is an application, so it does install the SDK: with the `otel` extra installed, the standard variables are enough to export: set any of `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`, `OTEL_TRACES_EXPORTER` or `OTEL_METRICS_EXPORTER`, and recon installs the SDK's providers with OTLP exporters (`http/protobuf` unless `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`; `OTEL_*_EXPORTER=none` turns a signal off). Everything else (headers, timeouts, `OTEL_SERVICE_NAME`, default `recon`, `OTEL_RESOURCE_ATTRIBUTES`) the SDK reads itself; `OTEL_SDK_DISABLED=true` turns it off. Set without the extra, the variables put an `otel_unavailable` warning in the `--json` document of `recon survey`, `recon survey --dry-run` and `recon query` (`configure_telemetry()` returns it). Providers already installed are kept, and recon's spans go there. The event log is written either way: an archive's contents do not depend on the environment it was surveyed in.

## What recon is not

- **Not an agent.** Recon doesn't use an LLM during execution. It runs the config you wrote. Intelligence lives outside the tool.
- **Not a research autopilot.** Recon produces structured data; synthesis stays with Claude and with the human.
- **Not a stream processor.** Each run produces a timestamped snapshot. Recon does one-shot collection, not pub/sub.

## Design

Library first, hexagonal. `import recon` is the API: `recon.init`, `plan`, `survey`, `status`, `query`, `templates`, `capture` each return a versioned document (a frozen pydantic record, `recon.SurveyDocument` and its siblings) whose `.document()` is what the CLI prints under `--json`, or raise an error whose `kind` says what to do. Each takes `root=`, the project its missions live in, so a program need not change directory: `recon.survey("m", root=Path("~/src/app").expanduser())`. `ConfigError` is also a `ValueError` and `NotFoundError` a `KeyError`. The CLI calls those names and nothing else.

The domain holds the frozen Pydantic models, the normalize language, and the ports, each hiding a decision likely to change: `MissionStore` (where missions live, and that they are YAML), `ArchiveStore` (how an archive is stored), `ArchiveQuery` (the SQL engine and its sandbox), `Clock`, `Requester` (HTTP), `ShellRunner`, `DocumentConverter`, `CaptureLog` and `EventSink` (where telemetry goes). The application plans a mission (`plan.py`), runs a survey over the ports (`survey.py`), and composes the operations (`use_cases.py`). Outbound adapters implement the ports with YAML files, JSONL directories, DuckDB, httpx, subprocess, markitdown and OpenTelemetry; `composition.py` is the one place they meet. `tests/test_layering.py` holds the import rules, and `tests/test_determinism.py` the contract that the same mission and the same answers give the same records.

Collector types and transforms are extensions, loaded from the entry-point groups `recon.collectors` and `recon.transforms`. The built-ins register through the same groups:

- collector types: `api`, `cli`, `web`, `capture`
- transforms: `$html2text`, `$inverted_index`, `$join`, `$first`, `$markitdown`

A plugin that fails to load is reported by `recon status` and `survey --dry-run` and does not break the others. To write one: [docs/reference/extending.md](docs/reference/extending.md).

The tests run offline, except the live integration tests against OpenAlex, arXiv, Semantic Scholar and Zenodo, which run only with `RECON_LIVE=1` (`just recon-live`).

## Docs

- [docs/explanation/capability.md](docs/explanation/capability.md) — what recon does for Claude and why
- [docs/how-to/first-survey.md](docs/how-to/first-survey.md) — a first mission, end to end
- [docs/how-to/search-api-post-body.md](docs/how-to/search-api-post-body.md) — POST-body search APIs (Exa, Firecrawl, Tavily, Serper)
- [docs/how-to/capture-mcp-results.md](docs/how-to/capture-mcp-results.md) — capture MCP tool results with a hook and query them
- [docs/reference/extending.md](docs/reference/extending.md) — adding a collector type or a transform
- [docs/reference/capture-format.md](docs/reference/capture-format.md) — the capture log format (`recon.v1.capture`), for a tool that writes captures
- [docs/design/2026-10-19-record.md](docs/design/2026-10-19-record.md) — the design judgments behind 0.11.0
- [CHANGELOG.md](CHANGELOG.md)
- `recon --skill` — the skill Claude uses to author missions; `-r normalize-spec`, `-r config-patterns`, `-r capture-format` for the references. The plugin's copy is generated from it (`just recon-skill`).

## License

MIT
