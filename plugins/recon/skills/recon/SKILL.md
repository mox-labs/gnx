---
name: recon
description: "Writes, plans and runs recon missions: one YAML file that names sources (HTTP APIs, shell commands, web pages, MCP servers, capture logs) and what to ask them; recon collects the answers into timestamped JSONL archives and answers SQL over them with DuckDB. This skill should be used when the user asks to \"collect data from an API\", \"build a dataset from several sources\", \"mine a codebase with ripgrep or git log\", \"snapshot an API over time\", \"normalize API responses\", \"query collected records with SQL\", \"collect from an MCP server\", \"capture MCP tool results\", \"write a recon mission\", \"watch a survey as it runs\", or runs recon survey, recon query, recon watch or recon init."
metadata:
  version: "0.15.0"
---

# recon 0.15.0: collect from many sources into tables SQL can query

recon runs a mission: one YAML file that lists sources (the catalog) and what to ask each of them (the collectors). recon checks the file and shows every request, command and MCP call it would make, then fetches, runs or calls them, reshapes each answer into records with a declarative `normalize:` spec, and writes one JSONL table per collector and source into a timestamped archive. `recon query` answers SQL over an archive, so a large result is read a few rows at a time instead of loaded whole. recon uses no model: it runs exactly the file you wrote.

## When to use it, and when not

Use recon when records from APIs, shell commands, web pages or MCP servers must be collected repeatably, kept, and queried: a literature search across several APIs, a census of patterns across repositories, a release or API snapshot taken on a schedule and compared with the last one.

Do not use recon:

- to read one page or one API response once: fetch it directly.
- for a page that renders its content with JavaScript: the `web` collector does one HTTP GET and converts what comes back.
- to write to a remote system: recon only reads.

## Prerequisites

The plugin carries this text; the `recon` command is a separate Python package. Check with `recon --version`. Install with:

```
uv tool install "git+https://github.com/mox-labs/gnx@recon-v0.15.0#subdirectory=components/capabilities/recon"
```

If `recon` is missing, say so; do not improvise it with curl and jq, because the archive layout and the query surface are what make the output reusable. A `cli` collector runs whatever program its command names (`rg`, `git`, `gh`), which must be installed too.

## Minimal example

`mission.yaml`, a mission that needs no network:

```yaml
catalog:
  - name: repo
    type: local
    url: .
collectors:
  - name: commits
    type: cli
    source: repo
    run: "git log --oneline -n 20"
```

```
recon init commits -c mission.yaml --json
recon survey commits --dry-run --json
recon survey commits --json
recon query commits "SELECT line FROM commits LIMIT 5" --json
```

`init` copies the file to the mission's config and prints its path as `config`, and says what it did as `outcome`: `created`, `unchanged` (run again with the same file, it changes nothing and succeeds) or `replaced` (with `--replace`, over a different config). The dry run prints the plan: the command each run would execute, in which directory, and its effects. The survey prints the archive and each table's status and record count. Each stdout line that is not a JSON object becomes the record `{line_number, line}`.

## The loop

Do these in order. Every command takes `--json`: it then prints one JSON document on stdout (`recon watch --json`: one a line, a read each), and an error is one JSON line on stderr.

1. **Create the mission.** `recon init <name> -c <file> --json` (`-c -` reads the file from stdin), or `recon init <name> -t <template> --json` from a built-in template (`recon templates` lists them). Edit the file the document names as `config`.
2. **Plan.** `recon survey <name> --dry-run --json`. Exit 3 lists every problem with its path and fix; fix them all and plan again. When the error says `complete: false`, part of the file could not be checked (an entry of the wrong shape), so more problems may follow the fixes. Exit 0 prints `recon.v1.plan`: read `effects`, each run's `commands` and the `cwd` they run in, or its request (`method`, the `url` with every query parameter, `headers`, `body`; a credential shows as `REDACTED`), or its MCP call (`server`, `method`, `tool` and `arguments`, or `resource`), the `warnings` and the sources `skipped`, before going on. Nothing is written, and nothing is fetched or run, except an `mcp` run's preflight, which `preflight` names: the dry run starts each stdio server (and connects to each remote one) to list its tools, then stops it. `requirements` lists what it found, each with `holds`, `why` and `fix` (the server answers, the tool is listed, its input schema is valid JSON Schema, the arguments match it), and `runnable` is false when one does not hold: the plan is still valid, and the survey fails that table as `config`.
3. **Probe.** Keep the first survey small (a `limit` or `per_page` of 3, `-n 3` on a command) and leave `normalize:` out, so records are written as the source returned them. Ask for more than one: a list of one record looks like a field, and the survey recognizes a response's list only when it holds two or more. Run `recon survey <name> --json`, then look at the shape: `recon query <name> "SELECT * FROM <table> LIMIT 3" --json`. Two shapes to watch for:
   - **One record holding the whole response.** An API whose records sit under a key needs `extract:` naming it; without it the table is one record. When a collector has no `extract:` and its table is one record holding a list of two or more objects, the survey warns (`extract_missing`) and names the list's path. A collector with `extract:` is not warned about lists inside its one record: that record is a record, and a list inside it (a paper's authors) is its own. It is warned instead when its table is empty for a reason recon can see: `extract:` resolves to nothing in a response that is not empty, often an error the source sent with status 200 (`extract_unresolved`, naming the keys the response has), or what it names holds no object, such as a list of ids (`records_not_objects`). An empty list at the path is a source that had nothing, and is not warned. arXiv answers in Atom XML with its papers under `feed.entry`:

     ```yaml
     catalog:
       - name: arxiv
         type: api
         url: https://export.arxiv.org/api
     collectors:
       - name: papers
         type: api
         source: arxiv
         endpoint: /query
         params: {search_query: "abs:state space model", max_results: "2"}
         response_format: xml
         extract: feed.entry
     ```

   - **An object keyed by data, not by field names**, such as OpenAlex's `abstract_inverted_index` (keyed by word). SQL cannot make columns of it when two keys differ only in case, so its table cannot be queried: the survey warns (`table_unreadable`), a query of another table warns again, and one of that table fails with exit 10. Normalize the field (`abstract: "abstract_inverted_index|$inverted_index"`) or leave it out, then survey again; until then `SELECT json FROM read_ndjson_objects('<file>')` reads the raw lines.
4. **Normalize.** Write `normalize:` from the shape you saw, raise the limits, plan again (step 2), then survey.
5. **Query.** `recon query <name> "<SQL>" --json`. Each table is a view named after its output, with every character but a letter, a digit or `_` turned into `_` (`oa-works` is `oa_works`, `v1.2` is `v1_2`); SQL matches the name without regard to case. Two outputs that would be one view (`a-b` and `a_b`, `Foo` and `foo`) are a plan problem, as is an output name holding `/`, `\` or `..`, or not starting with a letter, a digit or `_`. The document names the archive the rows came from by its id, `archive` (what `--archive` takes), and its directory, `path`. Each row is an object keyed by column, so a name the SQL gives twice (`SELECT a.id, b.id`) is made unique in `columns` and in every row: `id`, `id_1`. The SQL is DuckDB's; a refused query exits 10 with DuckDB's message in the `fix`. To count the items of a list column, unnest it in a subquery and group outside it (DuckDB does not unnest beside `count(*)`): `SELECT author, count(*) AS n FROM (SELECT unnest(authors) AS author FROM papers) GROUP BY author ORDER BY n DESC`.

A survey with failed tables still writes its archive, marked incomplete, and lists every table's status; its exit code says what to do about the failures. `recon status --json` lists missions, their latest archive, and the installed collector types and transforms; `recon status <name> --json` lists every archive of that mission, oldest first, as `missions[0].history`: each archive's `id`, `path`, `started_at`, `finished_at`, `state` and `tables`. `state`: `complete`, `incomplete` or `writing`; for an incomplete one, `ended` says whether its survey `finished` (a table failed), was `stopped` on request, or `crashed`.

**Watch.** `recon watch <name> --json` follows an archive as a survey writes it, a `recon.v1.records-since` line per read, and returns when the survey ends: `--stream` of a `--forever` stream never returns. To follow, poll: `--once` reads once and returns, and the next call passes the `cursor` it printed as `--cursor` (with `--archive`, or `--stream`). Records of a table later `retracted` are void. Flags, defaults, refusals, `state`, `ended` and the library calls: `recon --skill -r live-reading`.

**Streams.** A source that never ends (`tail` a file, `sse` events) is a stream, sealed in windows: `recon stream <name> --stream <c>` runs one window and blocks until it ends, 300 s by default, which can outlast a tool call: run it with `--dry-run --json` first and read `stream.returns` (seconds), then set a shorter `window:` or run it in the background. `--windows N`, `--until`, `--forever` run more. A survey runs one window of each stream, in turn, after its tables: its dry run says `survey.returns`. All: `recon --skill -r streaming`. A long crawl is one too (`paginate:`, `window: {pages: N}`): a run goes on where the last left off, the last page ends it: `-r pagination`.

## Errors and exit codes

Act on the exit code, then on the error's `fix`:

| Exit | Kind | Meaning | What to do next |
|---|---|---|---|
| 0 | `ok` | done | go on: plan, survey, query, in that order |
| 1 | `unknown` | a failure recon did not classify | report it as a bug in recon, with the command that raised it |
| 2 | `usage` | a bad command, flag or argument | correct the command; `recon <command> --help` lists its arguments |
| 3 | `config` | the mission config or another input is invalid | fix each problem at its `path` as its `fix` says, then plan again; when `complete` is false, more problems may follow the fix |
| 4 | `not_found` | no such mission, archive or template | run the command the error's `fix` names |
| 5 | `transient` | every failed table failed transiently (HTTP 429, 5xx, network, a deadline); the archive is kept, marked incomplete | if a table's error names a `deadline:` or `timeout:`, raise it (a retry fails too); else survey again later |
| 6 | `auth` | a table failed on credentials (HTTP 401, 403); the archive is kept, marked incomplete | give the failing source's auth.token (or headers_helper) a valid key, then survey again |
| 7 | `conflict` | init, or survey -c, would overwrite a different mission config | add --replace to overwrite it, or choose another mission name |
| 8 | `run_failed` | a table failed, and not only on credentials or transiently; the archive is kept, marked incomplete | read each failed table's error and fix its cause, then plan and survey again |
| 9 | `store` | the survey could not finish its archive; it is kept, marked incomplete | free space where the archive is, or make it writable, then survey again |
| 10 | `query` | the SQL failed, or reached outside the mission's archives | correct the SQL as the error's `fix` says; for a missing table, it names the tables there are |
| 11 | `busy` | another recon is writing the stream, surveying the mission, or reading the subscription | let it run, or stop it as the `fix` says (`kill -TERM <pid>`, the pid in `details`), then run the command again |
| 12 | `stale` | the config changed since its schedule was installed; ticks are refused | read what changed, then `recon schedule install <mission>` |
| 130 | `interrupted` | Ctrl-C or SIGTERM stopped the command; a survey's archive reads `ended: stopped` (a stopped `recon stream` exits 0) | run the command again |

A survey whose archive could not be finished (a full disk) exits 9 (`store`) whatever its tables did, an `archive_unfinished` warning saying why. The error is this envelope, on one line:

```json
{"schema": "recon.v1.error", "error": {"kind": "config", "message": "<file>: 1 problem(s)", "fix": "fix each problem, then: recon survey m --dry-run", "complete": true, "problems": [{"path": "collectors.0.type", "kind": "name_unknown", "message": "unknown collector type 'ftp'; installed: api, capture, cli, mcp, sse, tail, web", "fix": "use one of: api, capture, cli, mcp, sse, tail, web", "allowed": ["api", "capture", "cli", "mcp", "sse", "tail", "web"], "got": "ftp", "source": ""}], "details": {}}}
```

A problem's `path` is dotted from the top of the file, list positions included (`collectors.0.type`); `allowed` lists the legal values where the set is closed; `got` is the value written, when there is one. `details` carries structured values: a failed survey's `archive` and `failed` tables, a wrong `value` and the `allowed` ones. A warning is a problem record in the document's `warnings` list (`recon.v1.plan`, `recon.v1.survey`, `recon.v1.query-rows`): it does not stop the command. `error.kind` is the table's Kind; every problem and warning `kind` is listed by `recon --skill -r errors`.

Documents name their shape in `schema`: `recon.v1.plan`, `recon.v1.survey`, `recon.v1.status`, `recon.v1.query-rows`, `recon.v1.templates`, `recon.v1.mission`, `recon.v1.capture`, `recon.v1.records-since`, `recon.v1.error`, and `recon.v1.event` for the lines of an archive's event log. A reader ignores a field it does not know, and handles an error or problem kind it does not know by the exit code; both sets grow. A document whose schema has another major version (`recon.v2.*`) is not a `recon.v1` document.

## The mission file

```yaml
# Every key a mission file can hold, with its default; | separates the values allowed.
preserve_raw: false            # true: keep every fetched body in the archive's capture log
deadline: 600                  # optional: seconds the whole survey may take (> 0)
schedule: {every: 600, missed: once}   # optional: the OS scheduler runs it
subscriptions: [{name, output, match, deliver}]  # who is told what changed; -r subscriptions
catalog:                       # where to look; optional
  - name: <source>             # required, unique
    type: api                  # api | web | local | mcp, or a kind an installed type adds
    url: <base URL, or a directory for local>   # required, but by an mcp stdio source
    auth: {token: env:<VAR>, header: <Header-Name>, param: <query param>, prefix: "Bearer "}
    # or auth: {headers_helper: <command>}
    rate_limit: {rps: 10.0, burst: 3}   # tokens a second (> 0), most held (>= 1)
    timeout: 60                # seconds a send may take, first byte to last (> 0)
    max_bytes: 50000000        # largest response body; a larger one fails the table
    user_agent: <string>       # default: recon/<version> (+https://github.com/mox-labs/gnx)
    # mcp: command, args, env (as is, or env:NAME) for stdio; or url, token, headers_helper
collectors:                    # what to ask; required, at least one
  - name: <output>             # required; names the table
    type: api                  # api | cli | web | capture | mcp, or an installed plugin type
    source: <source>           # pin one source; omit to run against every one it can use
    type_url: recon.v1.records # what the records are: <namespace>.v<N>.<kebab-name>
    normalize: {<column>: "<path>|$<transform>"}   # every type; omit to keep records whole
    extract: <path>            # api, capture and mcp: where the records are in a body
    paginate: {type: link}     # api: page by page; -r pagination
    changes: {key: [<field>]}  # tell what changed; -r changes
    deadline: 120              # optional: seconds each of its tables may take (> 0)
    retain: all                # or {archives, seconds, bytes}: archives kept; -r schedules
    # Every other key is the type's own:
    # api:     endpoint, params (the query), vars (fill {placeholders}),
    #          method (GET | POST | PUT | PATCH | DELETE), body, response_format (json | xml)
    # cli:     run, patterns
    # web:     endpoint
    # capture: path, match {source, collector, kind}, response_format
    # mcp:     tool and arguments, or resource; vars (fill {placeholders});
    #          result (structured | text)
```

These are all the keys there are: an unknown key at the top level, in a source, a collector, `auth`, `rate_limit` or `match` is a plan problem with its path, so a typo like `normalise:` never passes silently. A collector's keys beyond the shared ones are its type's own, checked by that type (`run:` is unknown on an `api` collector), and an installed plugin type may take keys of its own; its source kinds, too. A top-level key beginning with `_` is ignored: a note, or a YAML anchor collectors alias. `params:` values are strings: quote a number (`limit: "20"`).

**Fan-out.** A collector with `source:` runs once and writes the table `<name>`. Without `source:` it runs against every catalog source of a kind its type uses (`cli`: `local`; `api`, `web`: `api` or `web`; `mcp`: `mcp`; `capture`: any) and writes `<name>-<source>` for each; with no catalog it runs once. The plan's `skipped` names each source it passed over, with its kind; a catalog with none it can use is a plan problem, and so is a pinned source of another kind.

**Placeholders.** A placeholder is `{name}`, the name a letter or `_` and then letters, digits or `_`, so a regular expression's `\w{3}` or `{2,5}` is not one; `{{` and `}}` are a literal brace (a GraphQL body's `{viewer{{login}}}`), except in `run:`. In an `api` collector, `{key}` in `endpoint` and in the string values of `body` is filled from `vars:`, which are sent nowhere else; `params:` are the query string, each value sent as written. A placeholder nothing fills is a plan problem (`unresolved`), and so is one in a `params:` value. In an `mcp` collector, `vars:` fill `{key}` in the string values of `arguments` and in `resource`. A `web` collector fills none, so a placeholder in its `endpoint` is a plan problem. In a `cli` collector recon fills `{url}`, the source's `url`, and `{pattern}`, each entry of `patterns:` in turn: `{pattern}` without `patterns:` is a plan problem, and so is `{url}` in a run with no source. Any other `{...}` in `run:` is the command's own (`cargo tree --format '{p}'`, `'{{.Name}}'`) and is passed as written.

**Credentials.** A secret is never in the file. `auth.token` is `env:NAME` (a variable) or `file:PATH` (relative to the mission; refused, `config`, if its group or others can read or write it, or it is a link), sent as `auth.header` and/or `auth.param`, `auth.prefix` in front; or `auth.headers_helper`, a command printing JSON headers, run once a survey. An unset variable sends the request without it, and the plan warns. A scheduled tick sees no shell variable: use `file:` or a helper there (the plan warns `env_unscheduled`). An `mcp` remote's `token:` (as `Authorization: Bearer`) and `headers_helper:` are the same; a stdio server's `env: {GITHUB_TOKEN: env:GITHUB_TOKEN}` gives it that variable, with `HOME`, `PATH`, `SHELL`, `TERM`, `USER` and `LOGNAME` and nothing else of recon's environment. A collector type sees only the variables the mission names this way.

## Collector types

| Type | Does | Each record is |
|---|---|---|
| `api` | one HTTP request per source to `url` + `endpoint`; retries 429, 5xx and network errors twice, waiting out a `Retry-After` of up to 60 s (a longer one fails the table at once, `transient`, naming the wait); every send, a retry included, draws from the source's `rate_limit` | each object at `extract` in the JSON or XML body |
| `cli` | runs `run` in a shell, in its `local` source's directory (a relative `url` is relative to the project root; a directory that does not exist is a plan problem), else, with no source, in the working directory; the plan shows it as each run's `cwd` (null: the working directory); exit 0 succeeds, and so does exit 1 with nothing on stderr (grep's and rg's no match) | each stdout line: a JSON object as is, other text as `{line_number, line}` |
| `web` | one HTTP GET, converted to markdown by content type (HTML, PDF, DOCX, ...) | one `{url, title, content, status_code, content_type}`; `content` stops at 100,000 characters |
| `mcp` | calls `tool` with `arguments`, or reads `resource`, on its `mcp` source's server. A stdio server is started on its first table and serves every other table of the survey, then is stopped; a remote is reached over Streamable HTTP. Before each call the tool must be listed and the arguments must match its input schema (else the table fails, `config`); every call, a listing included, draws from the source's `rate_limit`; an HTTP 401 or 403 is `auth` | each object at `extract` in the tool's structured content when it declares an output schema, else in the JSON of its text content (`result: structured` or `text` says which); a resource's JSON text. A tool error fails the table; an answer with no content keeps it empty, warned (`result_empty`); text that is not JSON fails it |
| `capture` | reads capture logs (default `captures/captures.jsonl` in the mission; a glob works); fetches nothing; fanned out over a catalog, each run keeps the captures whose `source` is that source's name; a run that read captures and kept none is warned (`captures_unmatched`) | each object in each matching body, parsed by its content type: JSON, XML, or text lines as `cli` parses them |

A `cli` command without a source times out after 300 seconds, otherwise after the source's `timeout`; the command and everything it started are then killed.

**Bounds in time.** A source's `timeout` bounds each send of a request in wall time, from connecting to the body's last byte, redirects included, so a slow server cannot hold a table for longer; a send past it is retried like any timeout. Each redirect draws a `rate_limit` token. `deadline:` at the top bounds the whole survey and `deadline:` on a collector each of its tables: a request, a wait for the rate limit or a retry, and a command are all cut to what is left, a table cut short fails as `transient` (exit 5) with a fix naming the `deadline:` to raise, every table not started by the survey's deadline fails the same way, and the archive is still finished, marked incomplete. A failed command's error ends with the last of its stderr. The command's stdin is empty, so a tool that reads stdin when it has no path argument gets nothing from it: give it an explicit path (`rg --json TODO .`). With `patterns:`, each record carries the `_pattern` that produced it.

## Normalize

A spec maps each output column to a path into the record, optionally piped through a transform: `title: title`, `pdf: "openAccessPdf.url"`, `first: "authors.0.name"`, `authors: "authors.*.name"` (the rest of the path from every item), `abstract: "abstract_inverted_index|$inverted_index"`. A path that does not resolve gives `null`. Built-in transforms: `$html2text`, `$inverted_index`, `$join`, `$first`, `$markitdown` (a local file under the mission or a `local` source, to markdown; a file it cannot read or convert fails the table). An unknown transform is a plan problem. The full syntax: `recon --skill -r normalize-spec`.

## Reading results

```
.recon/<mission>/                  under the git root, or the working directory outside a repository
  config.yaml
  captures/captures.jsonl          the log recon capture appends to
  archive/<archive id>/            one per survey; the id is its start time, so ids sort; a
                                   directory not named so is not an archive
    <table>.jsonl                  one record per line
    meta.yaml                      recon version, config sha256, times, table_layout (1:
                                   this layout of the tables), and per table: type_url,
                                   status, records, seconds, error {kind, message, fix}
    telemetry.jsonl                the survey's recon.v1.event lines: a start and an end per
                                   span (survey, table, HTTP request); not a table
    .incomplete                    present unless the survey finished with every table: one
                                   failed, it was stopped or crashed, or meta.yaml failed
    captures.jsonl, raw/<sha256>   the capture log, with preserve_raw: true
```

A table that collected nothing holds the one line `{"_empty": true}`; it is not a record, its view has no rows, and it counts as 0 records. When its collector has `normalize:`, the line keeps the spec's columns (`"_columns": [...]`) and the view has them, so a query of them runs on an empty table as it does on a full one; without `normalize:` the view has only `_empty`. `query` reads the latest archive unless `--archive <archive id>` names another (`recon status <mission>` lists the ids), and reports `state: incomplete` for an incomplete archive, whose rows may be partial, or `state: writing`: the table being written is not a view yet. SQL reads only files under the mission's `archive/` directory (an earlier archive's too; for what changed since, `changes:`), loads no extension and reaches no network. The session is in UTC (`now()`, `current_date`, `date_trunc`). A query runs SELECT statements only (`WITH`, `FROM`, `DESCRIBE` and `SUMMARIZE` among them); `COPY`, `ATTACH`, `CREATE` and the rest exit 10.

To cite a row, cite its line in one archive's file: each record is one line. Take the archive's `path` from `recon status <name> --json` (`missions[0].latest.path`, or an older archive's in `missions[0].history`; a query's document names its own as `path`), then `SELECT ordinality AS line, title FROM read_json_auto('<path>/<table>.jsonl') WITH ORDINALITY` gives `<archive id>/<table>.jsonl` line `line` for each row. The number is the line in the file, counted before any `WHERE` or `ORDER BY`. Read one file, not a glob across archives (`archive/*/<table>.jsonl`): a glob numbers the lines of all its files as one, and archives surveyed with different `normalize:` specs (one raw, one normalized) have different columns. A table's view leaves the number out, so `SELECT *` shows only the record.

The same mission and the same answers from its sources give the same records: recon adds nothing that depends on the time.

## Telemetry

Every survey writes its event log, `telemetry.jsonl`, into its archive, a `recon.v1.event` line per span start and end; no secret or body is logged. A failed span's `error.type`, the spans, their attributes, exporting through OpenTelemetry: `recon --skill -r telemetry`.

## Captures

`preserve_raw: true` keeps every fetched body (an HTTP response, a command's stdout, an MCP tool's or resource's result) in the archive's capture log, content-addressed, with every credential recon sends or receives redacted. A `capture` collector reads such logs back with a new `normalize:`, so an archive can be reshaped without fetching again.

Two ways to make an MCP server's answers a table. An `mcp` collector calls the server itself, the same way every survey (`recon init files -t mcp-server` shows one): use it for a stdio server, or a remote one that takes a token or a header. A server behind OAuth that signs in through a browser is reached the second way: the agent calls its tools, and a `PostToolUse` hook running `recon capture <mission>` captures the results; the hook and the mission: `recon --skill -r config-patterns`.

The line format, for a tool that writes captures itself: `recon --skill -r capture-format`.

## Effects and safety

A plan names each run's effects: `network`, `subprocess`, `filesystem`, or `unknown` for a plugin type that declared none. A `cli` collector runs its command through a shell with the user's permissions, and an `mcp` source's `command` (with `npx -y`, a package it downloads) runs with them too, at the dry run as well as the survey: write missions yourself, and never copy a `run:`, a `command:` or a mission file from untrusted input. What an MCP tool returns may be private (a repository, a mailbox); it lands in the archive. Nothing prompts.

## References

Each prints with `recon --skill -r <name>`:

| Name | What it holds |
|---|---|
| `errors` | every `error.kind`, problem `kind` and warning `kind` |
| `normalize-spec` | paths, the pipe, every built-in transform, XML attributes, `extract` |
| `config-patterns` | complete missions, by use; MCP results captured by a hook |
| `capture-format` | the `recon.v1.capture` line format and how a `capture` collector reads it |
| `telemetry` | the event log, every span and attribute, exporting through OpenTelemetry |
| `streaming` | stream keys, defaults, refusals; `tail`, `sse`; `--stream` |
| `pagination` | `paginate:` keys, defaults, stops, refusals; crawls that resume |
| `changes` | `changes:` keys, defaults, ops, refusals |
| `live-reading` | `recon watch` and `recon.read_since`: archive states, sealing, the cursor, every flag, default and refusal |
| `schedules` | `schedule:`, `recon schedule`, ticks |
