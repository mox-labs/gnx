---
name: recon
description: "This skill should be used when the user asks to 'collect data from an API', 'scrape a website', 'mine a codebase', 'fetch from multiple sources', 'normalize this response', 'query across sources with SQL', 'build a dataset', 'monitor something over time', 'schedule a recurring survey', 'cron this', 'run a survey', 'find papers', 'scan repos for patterns', 'track releases', 'audit dependencies', 'snapshot an API', 'diff API state over time', 'extract structured records', 'build a JSONL dataset', 'use Exa', 'use Firecrawl', 'use Tavily', 'use Serper', 'use Perplexity', 'call a search API', 'convert a PDF to markdown', 'convert a DOCX', 'ingest documents', 'aggregate structured data', 'write a recon config', 'capture MCP tool results', or needs to transform unstructured/semi-structured data from heterogeneous sources into queryable structured JSONL."
version: 0.9.0
---

# Recon

> **Prerequisite — the `recon` CLI is not installed by this plugin.** The plugin ships this
> skill; the runnable is a separate Python package. Every command below assumes `recon` is on
> PATH. Check with `recon --version`.
>
> ```
> uv tool install "recon @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/recon"
> ```
>
> recon is part of the gnx catalog and will be installed by `gnx add`; until that command
> lands, install it from the repository as above. If it is missing, say so rather than
> improvising an equivalent with curl and jq — the archive layout and the DuckDB query
> surface are what make the output reusable.


**Recon is the structured-data bridge.** Point it at any source — HTTP APIs, CLI tools, web pages, local filesystems — and it fetches responses, reshapes them into uniform JSONL, and makes the result queryable via DuckDB SQL. The reshape is declarative (normalize specs), not code. Claude writes the config, recon does the mechanical work, Claude reasons over the structured output.

## The Capability

Recon extends Claude's effective context through **structured indirection**. Instead of loading 1,000 records into Claude's prompt, recon stores them as queryable JSONL. Claude asks `SELECT title, year FROM papers WHERE citations > 100` and sees 10 rows — the other 990 never enter context. This scales Claude's effective reach by orders of magnitude, and keeps LLM inference focused on reasoning rather than on HTTP ceremony, response parsing, retry logic, or rate-limit handling.

Six capabilities it unlocks:

1. **Operate on datasets larger than the context window** — query, don't load
2. **Separate generative from mechanical work** — Claude thinks; recon fetches
3. **Reproducibility** — the config is a frozen, auditable artifact of what was collected
4. **Temporal awareness** — timestamped archives let Claude diff state over time
5. **Cross-source composition** — normalize heterogeneous sources to one schema, query across them
6. **Scheduled / offline execution** — cron it; Claude reads results later

## Domain Examples

Recon is general-purpose. The academic paper search is one instance. Other domains:

| Domain | What the config collects |
|---|---|
| Literature review | S2, arXiv, OpenAlex, Zenodo APIs |
| Code archaeology | `git log`, `gh api`, `rg` across multiple repos |
| Codebase audits | `rg` for patterns, `cargo tree`, unsafe census, TODOs |
| Release monitoring | GitHub releases, PyPI, crates.io, npm |
| Competitive analysis | Product pages, pricing, changelogs (web collector) |
| Content tracking | RSS feeds, blog indices, Substack archives |
| API state snapshots | Any REST endpoint captured periodically |
| Issue triage | `gh issue list` across repos with labels + metadata |
| Dependency audits | Package manifests, vulnerability feeds |
| Doc surveys | Scrape docs sites, extract structure |
| Benchmark collection | Tool outputs captured repeatedly over time |
| Market research | Company blogs, job boards, press releases |
| Infra inspection | `kubectl`, `docker inspect`, `terraform show` |

The common shape: *something out there has data in some response format; Claude needs to reason over it as structured records.* Recon bridges that.

---

## Contents

- [The Workflow](#the-workflow)
- [Probe Before Survey](#probe-before-survey)
- [Config Anatomy](#config-anatomy)
- [POST with JSON Body](#post-with-json-body)
- [Fan-Out](#fan-out)
- [CLI Commands](#cli-commands)
- [Writing Configs](#writing-configs)
- [Reading Results](#reading-results)
- [Sentinels and Error Signals](#sentinels-and-error-signals)
- [Preserving Raw Captures](#preserving-raw-captures)
- [Capturing MCP Tool Results](#capturing-mcp-tool-results)
- [Limitations](#limitations)

## The Workflow

```
Human intent
  ↓
Claude: PROBE — write minimal config, fetch 1 record, inspect raw shape
  ↓
Claude: write normalize spec from the actual response shape
  ↓
Claude: VALIDATE — re-run on the sample, confirm normalized fields
  ↓
Claude: PLAN — `recon survey <mission> --dry-run --json`: fix every problem it lists
  ↓
Claude: SURVEY — bump limits, run full collection
  ↓
Recon: collect → .recon/<mission>/archive/<timestamp>/*.jsonl + meta.yaml
  ↓
Claude: read JSONL directly, or `recon query <mission> "SQL" --json`
```

1. **Probe** — fetch a minimal sample first. Don't write normalize specs blind.
2. **Normalize** — write the reshape spec based on the actual response shape.
3. **Validate** — re-run on the sample to confirm the spec works.
4. **Plan** — `--dry-run` before any survey you have not run before: it lists every problem with its path, and every run with the URL or shell command it would execute, without fetching or running anything.
5. **Survey** — full collection with correct spec and production limits. Check the exit code (see [CLI Commands](#cli-commands)).
6. **Query** — read JSONL directly, or `recon query <mission> "SQL" --json`.

Pass `--json` whenever you will read the output: every command then prints one JSON document on stdout, and an error is one JSON line on stderr with a `kind` and a `fix`.

## Probe Before Survey

**Never write a normalize spec blind.** Normalize specs map source paths to output columns — but the actual source paths only become knowable after seeing a real response. Guessing wastes rate-limit budget on records that will need to be re-fetched.

The iteration cycle:

### Step 1 — Probe (minimal config, no normalize)

Write a collector with a tiny limit and **no `normalize:` block**. Recon emits raw records (after `extract:` navigation) as-is.

```yaml
catalog:
  - name: semantic-scholar
    type: api
    url: https://api.semanticscholar.org/graph/v1
    rate_limit: { rps: 0.33, burst: 1 }

collectors:
  - name: probe
    type: api
    source: semantic-scholar
    endpoint: /paper/search
    params:
      query: "transformer attention"
      limit: "1"                    # one record is enough
      fields: "title,abstract,authors,citationCount,openAccessPdf,venue"
    extract: "data"                 # navigate to the records array
    # no normalize — emit raw shape
```

Run: `recon survey my-mission`.

### Step 2 — Inspect the raw shape

Read `.recon/my-mission/archive/<latest>/probe.jsonl`. The raw JSONL reveals the actual field names, nesting, and types:

```json
{
  "paperId": "abc123",
  "title": "Attention Is All You Need",
  "abstract": "The dominant sequence...",
  "authors": [{"authorId": "1", "name": "Vaswani"}, {"authorId": "2", "name": "Shazeer"}],
  "citationCount": 94321,
  "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
  "venue": "NeurIPS"
}
```

Read off the shape: `authors` is a list of `{name}` dicts, `openAccessPdf` is nested, `citationCount` is at the top level.

### Step 3 — Write the normalize spec

Add the block based on what you saw:

```yaml
    normalize:
      title: title
      abstract: abstract
      authors: "authors.*.name"           # list-map
      citations: citationCount
      pdf_url: "openAccessPdf.url"        # nested
      venue: venue
```

### Step 4 — Validate on the sample

Re-run `recon survey my-mission`. Confirm the JSONL now has the uniform columns, populated correctly. Fix path errors here — they're cheap.

### Step 5 — Survey

Bump `limit` to production values (e.g. `"20"` or `"100"`). Run the full collection. The normalize spec is validated; no field-path surprises.

### Why this matters

Skipping probe means discovering normalize bugs after fetching 100 records with wrong field paths — wasted rate-limit budget and a forced second run. Probe cost: 1 request. Mistake cost: 100 requests + rate-limit cooldown + redo. Always probe first.

### CLI / web collectors

The same workflow applies:
- **CLI**: use a pattern matching a tiny sample (e.g. `--max-count 1` on ripgrep), no normalize, inspect, then write spec
- **Web**: the web collector emits a fixed shape `{url, title, content, status_code, content_type}` — no normalize needed, but probe one URL before scaling fan-out across dozens

## Config Anatomy

Every config has two sections: **catalog** (where to look) and **collectors** (what to ask).

```yaml
catalog:
  - name: semantic-scholar
    type: api                    # api | web | local
    url: https://api.semanticscholar.org/graph/v1
    auth: { header: x-api-key, env: S2_API_KEY }
    rate_limit: { rps: 0.33, burst: 1 }

collectors:
  - name: s2-search
    type: api                    # api | cli | web | capture (or an installed plugin type)
    source: semantic-scholar     # pin to specific source (omit for fan-out)
    type_url: acme.papers.v1.search-hit   # optional: what the records are (default recon.v1.records)
    endpoint: /paper/search
    params:
      query: "transformer attention"
      limit: "20"
      fields: "title,abstract,year,authors,citationCount"
    extract: "data"              # dotted path to record array in response
    normalize:                   # output column: source path [|$transform]
      title: title
      authors: "authors.*.name"
      year: year
      citations: citationCount
```

### Source Types

| Type | Purpose | `url` holds |
|------|---------|-------------|
| `api` | HTTP endpoints with auth, rate limits | Base URL |
| `web` | Web pages and documents, converted to markdown via markitdown | Site URL |
| `local` | Filesystem directories for CLI tools | Directory path |

### Collector Types

| Type | How it works | Key fields |
|------|-------------|------------|
| `api` | HTTP request → JSON/XML → normalize → JSONL | `endpoint`, `params`, `method`, `body`, `extract`, `normalize` |
| `cli` | Shell command → parse stdout → normalize → JSONL | `run`, `patterns`, `normalize` |
| `web` | HTTP GET → document to markdown → one record `{url, title, content, status_code, content_type}` | `endpoint` (optional), `normalize` (optional) |
| `capture` | Capture log → bodies → normalize → JSONL. Fetches nothing. | `path` (default `captures/captures.jsonl`), `match`, `extract`, `normalize` |

Collector types are extensions: `recon status --json` lists what is installed (`plugins.collectors`), and a type that is not installed is a config error naming the installed ones. Keys a model does not know are config errors too, so a typo like `normalise:` is reported with its path rather than ignored.

`type_url` declares what a table's records are, in the dotted grammar (`<namespace>.v<N>.<kebab-resource>`, never a slash). It is recorded per table in `meta.yaml`; the records themselves are written exactly as normalized.

Source auth: `auth.env` names the environment variable holding the secret; `auth.header` sends it as a header, `auth.param` as a query parameter (e.g. OpenAlex `api_key`), and `auth.prefix` is prepended in either place. An unset variable sends the request without credentials, and the plan warns about it.

For normalize spec syntax and built-in transforms: see [normalize-spec.md](references/normalize-spec.md).

## POST with JSON Body

Modern search and scraping APIs (Exa, Firecrawl, Serper, Tavily, Perplexity, GraphQL endpoints) take a **POST with a JSON body**, not query-string params. Recon supports this directly.

Three config knobs make it work:

1. **`body:` on the collector** — a dict sent as the JSON request body when `method: POST` (also works for `PUT`, `PATCH`, `DELETE`).
2. **`auth.prefix:` on the source** — prepended to the resolved env value. `"Bearer "` for `Authorization: Bearer $TOKEN`, `"token "` for GitHub-style, empty for plain header auth like `x-api-key`.
3. **`{placeholder}` substitution in body strings** — values from `params:` are substituted into string values in `body` recursively, same as the `endpoint` path interpolation. The config stays a template; changing the search term is a one-line edit to `params`, not a regeneration of the whole YAML. Non-string values (ints, bools, lists, nested dicts) pass through; only string values get substituted. **An unresolved `{foo}` is a plan problem (exit 3, path `collectors[i].body`) before any HTTP request fires** — recon refuses to send a body with missing placeholders, so you get a loud failure at plan time instead of silent 400s or empty results at production scale. Add every referenced key to `params:`.

### Exa — neural search

```yaml
catalog:
  - name: exa
    type: api
    url: https://api.exa.ai
    auth: { header: x-api-key, env: EXA_API_KEY }   # no prefix needed
    rate_limit: { rps: 1, burst: 2 }

collectors:
  - name: exa-search
    type: api
    source: exa
    endpoint: /search
    method: POST
    params:
      topic: "transformer attention mechanism"   # change me, don't regenerate the YAML
    body:
      query: "{topic}"                           # templated
      numResults: 10                             # literal int stays literal
      type: "neural"
      contents:
        text: true
    extract: "results"
    normalize:
      title: title
      url: url
      text: "text"
      score: score
```

### Firecrawl — scrape a URL to markdown

```yaml
catalog:
  - name: firecrawl
    type: api
    url: https://api.firecrawl.dev
    auth:
      header: Authorization
      env: FIRECRAWL_API_KEY
      prefix: "Bearer "           # ← the prefix is the important part
    rate_limit: { rps: 1, burst: 2 }

collectors:
  - name: scrape
    type: api
    source: firecrawl
    endpoint: /v1/scrape
    method: POST
    params:
      target_url: "https://example.com/article"
    body:
      url: "{target_url}"
      formats: ["markdown", "links"]
      onlyMainContent: true
    extract: "data"               # Firecrawl wraps the result in {success, data: {...}}
    normalize:
      markdown: markdown
      title: "metadata.title"
      source_url: "metadata.sourceURL"
```

### The pattern for any POST-body search API

Same shape works for Serper (`/search`), Tavily (`/search`), Perplexity (`/chat/completions`), Linkup (`/search`), You.com — swap the URL, auth, body fields, and `extract` path. See [config-patterns.md](references/config-patterns.md#modern-search-apis-post-body) for full examples including Tavily and Serper.

### Auth prefix cheatsheet

Store the raw credential in the env var; `auth.prefix` is prepended at request time.

| Service | `header` | `prefix` |
|---|---|---|
| Exa | `x-api-key` | `""` |
| Firecrawl, Tavily, Perplexity, OpenAI-compatible | `Authorization` | `"Bearer "` |
| Serper | `X-API-KEY` | `""` |
| GitHub (modern fine-grained) | `Authorization` | `"Bearer "` |
| GitHub (classic PAT) | `Authorization` | `"token "` |

## Fan-Out

Collector with no `source:` runs against **every catalog entry**. Specify `source:` to pin.

```yaml
catalog:
  - name: tokio
    type: local
    url: /Users/dev/oss/tokio
  - name: bytes
    type: local
    url: /Users/dev/oss/bytes

collectors:
  - name: scars
    type: cli
    # no source → fans out across tokio AND bytes
    run: "rg --json -C5 'note that|critical|must|never' --type rust"
```

Produces: `scars-tokio.jsonl`, `scars-bytes.jsonl`. For pinned collectors: `s2-search.jsonl`.

For CLI collectors, `source.url` is used as the working directory (`cwd`) when it's a valid local path.

## CLI Commands

```
recon init <name> -t <template>        # scaffold .recon/<name>/config.yaml (or --from <file>)
recon survey <name> --dry-run          # plan: problems with paths, runs with effects; no effects of its own
recon survey <name>                    # plan, then run; prints the archive path
recon survey <name> -c <file>          # run a config file; refuses to overwrite a different
                                       #   mission config unless --replace
recon status                           # missions, archives, installed collector types/transforms
recon query <name> "SQL"               # DuckDB query on latest archive (--run <ts> for another)
recon templates                        # built-in templates
recon capture <name>                   # append a PostToolUse hook payload (stdin) as a capture
```

Every command takes `--json`. Documents carry `schema`: `recon.v1.plan` (dry-run), `recon.v1.survey`, `recon.v1.status`, `recon.v1.query-rows` (`{columns, rows, count}`), `recon.v1.templates`, `recon.v1.mission`, `recon.v1.capture`.

**Exit codes** (shared with hardline and ix) — act on the code, then read the error's `fix`:

| Code | Kind | What to do |
|---|---|---|
| 0 | — | done |
| 1 | `collection` / `unknown` | read each failed table's `error.message` (survey `--json`), fix the config |
| 2 | usage | fix the flags |
| 3 | `config`, `conflict`, `query` | fix every listed problem (`problems: [{path, message}]`); for `conflict` decide on `--replace`; for `query` use the listed tables |
| 4 | `not_found` | the mission, archive or template does not exist; the `fix` names the command that creates it |
| 5 | `transient` | every failed table failed transiently (429, 5xx, network): retry later |
| 6 | `auth` | a table failed on credentials (401, 403): fix the key its source's `auth.env` names |

A survey with failed tables still writes the archive and reports every table; codes 1, 5 and 6 refer to the failed tables only.

Project root discovery: walks up from cwd looking for `.git`. Mission data lives at `<project>/.recon/<name>/` (the cwd when there is no repository).

## Writing Configs

Translate user intent into a complete config. Do not leave `{placeholder}` values — bake all query terms, limits, and paths directly into the YAML.

**Always probe first** (see [Probe Before Survey](#probe-before-survey)). Write a minimal config with `limit: "1"` (or equivalent) and no `normalize:` block. Inspect the raw JSONL. Then add the normalize spec based on what you actually saw. Then bump limits and run the full survey.

**Starting points:**
- Built-in templates: `recon templates` lists them. Each is a runnable starter for a distinct capability (code forensics, GitHub audit, docs mining, release tracking, factual grounding, RSS/Atom monitoring, MCP captures). Scaffold with `recon init <mission> --template <name>`. Plugin-owned catalogs (e.g. craft-research's academic catalog) load via `recon init <mission> --from <path>`.
- Pattern library for other domains — see [config-patterns.md](references/config-patterns.md) (code mining single/multi-repo, code-maat forensics, release monitoring, RSS, dependency audits, doc surveys, issue triage, content tracking)

For normalize spec syntax and built-in transforms: [normalize-spec.md](references/normalize-spec.md).

## Reading Results

### Direct Read

JSONL files are one JSON object per line. Read them directly:

```python
# Claude reads .recon/<mission>/archive/<latest>/*.jsonl
```

### DuckDB Query

DuckDB reads JSONL natively. Table names derive from filenames (hyphens → underscores):

```sql
-- After: recon query attention-mechanisms "SQL"
SELECT title, year, citations
FROM s2_search
WHERE citations > 100
ORDER BY citations DESC
```

### Meta.yaml

Each archive includes `meta.yaml` (`format_version: 2`): `recon_version`, `config_sha256` (of the config file that ran), timestamps, and `tables` — one entry per output with `name`, `file`, `collector`, `source`, `type_url`, `status`, `records`, `seconds`, and `error: {kind, message}` when it failed. `recon status --json` reads it for you, including archives written by older recon (`format_version: 1`).

## Sentinels and Error Signals

Recon marks these conditions explicitly so downstream tooling (and Claude reading an archive) never has to guess.

**`{"_empty": true}`** — a collector produced zero records. Recon writes this single-record sentinel to the JSONL so DuckDB `read_json_auto` has a valid file to read. Query for it with `WHERE NOT coalesce(_empty, false)` to exclude:

```sql
SELECT * FROM s2_search WHERE NOT coalesce(_empty, false)
```

**`.incomplete` file at archive root** — at least one table failed, or `meta.yaml` itself failed to write. The survey exits non-zero (1, 5 or 6 — see [CLI Commands](#cli-commands)); `recon status` shows the archive state as `incomplete`; `recon query` prints a warning before running the SQL. `survey --json` lists every table's status, and `meta.yaml` keeps it:

```yaml
tables:
  - name: arxiv-search
    type_url: recon.v1.records
    status: error
    error:
      kind: transient
      message: "[arxiv-search against 'arxiv'] HTTP request failed after retries: ..."
    seconds: 60.04
```

**Plan problems (nothing runs)** — every survey plans first, and these stop it with exit 3 before an archive exists, each reported with its path: an unknown collector type or transform, a misspelt key, a pinned source missing from the catalog, an output-name collision (a pinned `foo-arxiv` plus a fan-out `foo` over source `arxiv` both compute `foo-arxiv.jsonl`), and an unresolved `{placeholder}` in an `api` endpoint or `body:` after substitution from `params:`. `--dry-run` shows the same list without running anything.

## Preserving Raw Captures

Set `preserve_raw: true` at the top of the config to keep the raw fetched bytes (HTTP response body or CLI stdout) alongside the processed JSONL:

```yaml
preserve_raw: true
catalog:
  - name: ...
collectors:
  - ...
```

The archive then holds a capture log: `captures.jsonl` (one line per fetch, `schema: recon.v1.capture`, with `kind`, `request`, `status`, `content_type`, redacted `headers`, `sha256`, `captured_at`) and the bodies under `raw/<sha256>`. The format is documented in the package's `docs/reference/capture-format.md`.

Three reasons to turn this on:
- **Re-normalize without re-fetching** — a `capture` collector reads the log back and applies a new `normalize:` spec; no request is sent:

  ```yaml
  collectors:
    - name: renormalized
      type: capture
      path: "archive/2026-10-09-*/captures.jsonl"   # relative to the mission dir; globs work
      match: { collector: s2-search }               # fnmatch on source / collector / kind
      extract: data
      normalize: { title: title, year: year }
  ```
- **Audit** — `sha256` + timestamp + headers prove what the server said at capture time.
- **Experimentation** — try different normalize specs or extraction paths against a frozen input.

Trade-off: ~2× disk usage per mission. Off by default.

## Capturing MCP Tool Results

A Claude Code `PostToolUse` hook can feed MCP tool results into a mission, so results Claude already fetched become a queryable table:

```bash
recon init mcp --template mcp-captures     # a capture collector over the mission's hook-fed log
```

```json
{"hooks": {"PostToolUse": [{"matcher": "mcp__.*",
  "hooks": [{"type": "command", "command": "cd \"$CLAUDE_PROJECT_DIR\" && recon capture mcp"}]}]}}
```

Each MCP call appends one `external` capture (`collector` = tool name, `source` = MCP server, `request` = the payload's `tool_input` etc., body = `tool_response`) to `.recon/mcp/captures/captures.jsonl`. `recon survey mcp` normalizes them. In a capture collector's `normalize:`, `_capture.*` reaches the capture line (`_capture.collector`, `_capture.request.tool_input.query`). Probe the response shape before writing paths into it: it depends on the server. Full walkthrough: the package's `docs/how-to/capture-mcp-results.md`.

## Timeouts and Fallbacks

Sources accept a `timeout` field (seconds, default 60). Slow sources need longer:

```yaml
catalog:
  - name: slow-api
    type: api
    url: https://example.com/api
    timeout: 90
```

When a collector fails (timeout, 429, 5xx, non-zero exit), the error is recorded with its `kind` and remaining collectors continue; the survey then exits non-zero. Check the exit code (or `status` in `survey --json`) after every survey — recon does not crash the whole run on one failed collector, and it does not report success when one failed.

**Fallback pattern:** if an API fails or returns insufficient data, define a second collector using a different source type (e.g., `web` scraping when the `api` doesn't work). Recon doesn't chain fallbacks automatically — it's mechanical. Claude reads which tables failed, and why, and writes a second config to try the fallback.

## Limitations

- **No pagination.** API collectors make one request per collector entry. For paginated APIs, create multiple collector entries or use CLI tools that handle pagination.
- **Sequential execution.** Collectors run one at a time. Parallelism happens at the caller level (multiple `recon survey` calls).
- **No deduplication.** Each archive is independent. Cross-archive dedup is the caller's responsibility.
- **Shell injection surface.** CLI collectors execute commands via `shell=True`. Configs should be generated by Claude, not from untrusted user input.

## References

### Reference Files

Load on demand for deeper detail. Access from Claude with `recon --skill -r <name>`:

| Need | Reference |
|------|-----------|
| Config patterns across 9 domains (code mining, code-maat forensics, release monitoring, RSS, dependency audits, doc surveys, issue triage, content tracking) | [config-patterns.md](references/config-patterns.md) |
| Normalize spec syntax, path resolution, built-in transforms | [normalize-spec.md](references/normalize-spec.md) |

### Example Configs

Ready-to-run YAML configs in `examples/`. Copy, adapt the placeholders, write to `.recon/<mission>/config.yaml`:

| Example | Demonstrates |
|---------|--------------|
| [`probe-then-survey.yaml`](examples/probe-then-survey.yaml) | The three-stage iterative workflow with three annotated stages (probe → normalize → survey) for an OpenAlex search |
| [`code-mining-multi-repo.yaml`](examples/code-mining-multi-repo.yaml) | Fan-out across local repos with ripgrep patterns, git log, and unsafe census collectors |
| [`api-monitoring.yaml`](examples/api-monitoring.yaml) | Periodic state capture via cron — GitHub releases API with diff-across-archives pattern |
| [`exa-post-body.yaml`](examples/exa-post-body.yaml) | Exa neural search via POST with JSON body — `{placeholder}` templating + x-api-key auth |

### Built-in Templates

Bundled with the recon tool, loadable via `recon init <mission> --template <name>`. Each template demonstrates a distinct recon capability so the set collectively documents what the tool can do.

| Template | Demonstrates |
|----------|--------------|
| `code-forensics` | Local source + cli collectors, patterns fan-out, no HTTP/auth |
| `github-audit` | API source with optional header auth, path-level placeholders, multi-endpoint collection |
| `docs-mine` | Web source + web collectors, HTML → markdown with fixed output schema |
| `package-registries` | Multi-source API (PyPI / npm / crates.io) normalized to one common schema |
| `factual-ground` | Wikipedia MediaWiki API + Wikidata SPARQL with deeply-nested JSON unpacking |
| `rss-monitor` | `response_format: xml` with attribute extraction via `.@attr` — Atom feeds |
| `mcp-captures` | `capture` collector over MCP results a Claude Code hook captured; `_capture.*` paths; fetches nothing |

Domain-specific catalogs (e.g. academic literature for craft-research) live inside the consuming plugin, not inside recon. Scaffold from them via `recon init <mission> --from <path>`.
