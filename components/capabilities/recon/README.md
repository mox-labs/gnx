# recon

> Heterogeneous sources in, structured data out. Claude queries the result.

Recon fetches from HTTP APIs, runs CLI tools, scrapes web pages, and reads capture logs that other tools wrote, then reshapes every response into uniform JSONL that Claude can query with SQL. The reshape is declarative, not code. Write a YAML config once; recon plans it, then runs it mechanically — no LLM in the loop.

## Why

Claude reasons well but spends inference cycles badly when asked to babysit HTTP. Parsing JSON, retrying 429s, normalizing nested fields, handling pagination math — all of it is mechanical work that costs the same inference budget as actual reasoning. Recon takes that off Claude's critical path.

What's left for Claude is the work only Claude can do: deciding what matters, spotting patterns across sources, connecting findings that nobody wrote down as findings.

A recon archive with 5,000 records is queryable without loading 5,000 records into Claude's prompt. That's the deeper capability: **recon extends Claude's effective context through structured indirection**. The data lives in a file Claude can ask questions of, not in the prompt.

For the full argument, see [docs/explanation/capability.md](docs/explanation/capability.md).

## Install

recon is a package in the [gnx](https://github.com/mox-labs/gnx) repository:

```bash
uv tool install "recon @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/recon"
recon --version
```

Inside a gnx checkout, `uv tool install --editable components/capabilities/recon` installs your working copy instead.

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

Missions live in `.recon/<name>/` at the root of the git repository (the current directory outside one). For the probe → normalize → survey workflow, see [docs/how-to/first-survey.md](docs/how-to/first-survey.md).

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
| Literature review | Semantic Scholar, arXiv, OpenAlex, Zenodo | *(a catalog owned by the consuming plugin — `recon init --from`)* |
| Dependency audits, issue triage, API snapshots | `cargo tree`, `gh issue list`, any REST endpoint | *(see `recon --skill -r config-patterns`)* |

The common shape: *something out there has data in some response format, and Claude needs to reason over it as structured records.* Recon bridges that.

## Commands

```
recon init <mission> [-t <template> | --from <file>]   scaffold a mission
recon survey <mission> [-n|--dry-run] [-c <file> [--replace]]
                                                       plan, then run the collection
recon status                                           missions, archives, installed plugins
recon query <mission> "SQL" [--run <ts>]               DuckDB over an archive's tables
recon templates                                        list built-in templates
recon capture <mission>                                append a hook payload (stdin) as a capture
recon --skill [-r <reference>]                         print the skill (for Claude)
```

Every command takes `--json`: one JSON document on stdout whose `schema` names its shape (`recon.v1.survey`, `recon.v1.plan`, `recon.v1.status`, `recon.v1.query-rows`, `recon.v1.templates`, `recon.v1.mission`, `recon.v1.capture`); collections are always wrapped in an object.

### Exit codes

Shared with the other gnx capabilities (hardline, ix):

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | failure not otherwise classified |
| 2 | usage: a bad flag or argument |
| 3 | config: the input must change — an invalid config (every problem listed, with its path), a config `-c` would overwrite (`conflict`), SQL that fails (`query`) |
| 4 | not found: a mission, archive or template |
| 5 | transient: retry later |
| 6 | auth: a credential was refused |

A survey in which any table failed still writes its archive (marked `.incomplete`) and still reports every table's status, then exits **6** if any table failed on credentials, **5** if every failure was transient, and **1** otherwise. With `--json`, an error is one line on stderr: `{"error": {"kind", "message", "fix", ...}}`.

## Archives

```
.recon/<mission>/archive/<timestamp>/
  <output>.jsonl       one table per collector × source; records exactly as normalized
  meta.yaml            format_version 2: recon version, config sha256, and per table
                       its type_url, records, status, seconds, error {kind, message}
  .incomplete          present when any table failed
  captures.jsonl, raw/ the capture log, when preserve_raw: true
```

A collector may declare `type_url:` (dotted grammar, default `recon.v1.records`) so a consumer can tell what a table holds without reading collector code. `status` and `query` still read archives written by recon 0.8 (`format_version: 1`).

## Capture logs

`preserve_raw: true` keeps every fetched response in an append-only capture log with content-addressed bodies. A `capture` collector reads capture logs and normalizes their bodies, which lets recon re-normalize an old survey without refetching, and normalize data some other process fetched. `recon capture` feeds a mission's log from a Claude Code `PostToolUse` hook. The format is an interface other tools can write: [docs/reference/capture-format.md](docs/reference/capture-format.md).

## What recon is not

- **Not an agent.** Recon doesn't use an LLM during execution. It runs the config you wrote. Intelligence lives outside the tool.
- **Not a research autopilot.** Tools like gpt-researcher try to autonomously produce finished reports from a single prompt. Recon produces structured data; synthesis stays with Claude and with the human.
- **Not a stream processor.** Each run produces a timestamped snapshot. Recon does one-shot collection, not pub/sub.

## Design

Hexagonal. The domain holds frozen Pydantic models and the ports: `Collector`, `Requester`, `ShellRunner`, `DocumentConverter`, `CaptureLog`. The application layer plans a config (`plan.py`), runs it (`recon.py`), and reads and writes archives and capture logs. Outbound adapters implement the ports (httpx, subprocess, markitdown). The CLI is the driving adapter; `composition.py` wires them.

Collector types and transforms are extensions, loaded from the entry-point groups `recon.collectors` and `recon.transforms`. The built-ins register through the same groups:

- collector types: `api`, `cli`, `web`, `capture`
- transforms: `$html2text`, `$inverted_index`, `$join`, `$first`, `$markitdown`

A plugin that fails to load is reported by `recon status` and `survey --dry-run` and does not break the others. To write one: [docs/reference/extending.md](docs/reference/extending.md).

268 tests: 260 run offline (domain, application, adapters, the CLI contract through click's runner); 8 are live integration tests against OpenAlex, arXiv, Semantic Scholar and Zenodo.

## Docs

- [docs/explanation/capability.md](docs/explanation/capability.md) — what recon does for Claude and why
- [docs/how-to/first-survey.md](docs/how-to/first-survey.md) — a first mission, end to end
- [docs/how-to/search-api-post-body.md](docs/how-to/search-api-post-body.md) — POST-body search APIs (Exa, Firecrawl, Tavily, Serper)
- [docs/how-to/capture-mcp-results.md](docs/how-to/capture-mcp-results.md) — capture MCP tool results with a hook and query them
- [docs/reference/capture-format.md](docs/reference/capture-format.md) — the `recon.v1.capture` interchange format
- [docs/reference/extending.md](docs/reference/extending.md) — adding a collector type or a transform
- [CHANGELOG.md](CHANGELOG.md)
- `recon --skill` — the full skill Claude uses to author configs; `-r config-patterns`, `-r normalize-spec` for the references

## License

MIT
