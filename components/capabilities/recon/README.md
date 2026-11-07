# recon

recon collects from many kinds of source (HTTP APIs, shell commands, files, web pages, MCP
servers) into JSONL tables, and answers SQL over them with DuckDB. One YAML file, a
**mission**, says what to collect; recon shows the plan, runs it with no model in the loop,
and keeps each run as an archive.

Why it exists, and what it gives an agent: [docs/explanation/overview.md](docs/explanation/overview.md).

## Install

recon is installed from the gnx repository at its release tag; it is not on a package index:

```bash
uv tool install "git+https://github.com/mox-labs/gnx@recon-v0.19.1#subdirectory=components/capabilities/recon"
recon --version
```

From a clone, with nothing installed: uv runs recon from the clone's workspace, the
matrix beside it (recon's one gnx dependency) included. Run it from the clone's root.

```bash
git clone https://github.com/mox-labs/gnx && cd gnx
uv --project components/capabilities run recon --version
```

Or install your working copy as the `recon` command:
`uv tool install --editable components/capabilities/recon`.

An agent loads the guide for the installed version with `recon --skill`; the gnx `recon`
Claude Code plugin ships the same text.

## A first run, offline

Two files on disk, one JSON lines and one CSV, joined with SQL. Run it outside a git
repository, or at a repository's root: missions live in `.recon/` at the git root (the
current directory outside one), and a `local` source's `url` is relative to that root.

```bash
mkdir -p demo/data && cd demo
printf '%s\n' '{"id": 1, "name": "Ada", "team": "core"}' \
  '{"id": 2, "name": "Grace", "team": "compilers"}' \
  '{"id": 3, "name": "Linus", "team": "core"}' > data/people.jsonl
printf 'team,lead\ncore,Ada\ncompilers,Grace\n' > data/teams.csv
```

`team.yaml`:

```yaml
catalog:
  - name: data
    type: local        # a directory; a cli collector's commands run in it
    url: data          # relative to the project root

collectors:
  - name: people       # JSON lines: each line is a record
    type: cli
    source: data
    run: cat people.jsonl
  - name: teams        # CSV: recon does not parse it, so the command does
    type: cli
    source: data
    run: >-
      python3 -c "import csv, json, sys;
      [print(json.dumps(row)) for row in csv.DictReader(open('teams.csv'))]"
```

```console
$ recon init team -c team.yaml
Created: …/demo/.recon/team/config.yaml
Next: recon survey team --dry-run && recon survey team
$ recon survey team --dry-run          # every setting and its origin, then each run; nothing runs
…
│ people │ cli  │ data   │ subprocess │ cat people.jsonl (in …/demo/data)
│ teams  │ cli  │ data   │ subprocess │ python3 -c "import csv, json, sys; …
2 run(s); nothing was fetched, executed or written.
$ recon survey team
2 run(s) from 2 collector(s), 1 source(s)
  people: 3 records
  teams: 2 records
2026-11-07-211651-187428
$ recon query team "SELECT p.name, p.team, t.lead FROM people p JOIN teams t USING (team) ORDER BY p.id"
archive 2026-11-07-211651-187428
┃ name  ┃ team      ┃ lead  ┃
│ Ada   │ core      │ Ada   │
│ Grace │ compilers │ Grace │
│ Linus │ core      │ Ada   │
```

`recon init` makes the mission's directory readable by you alone. Make it by hand
(`mkdir -p .recon/team`) and `recon status` warns `mission_exposed`, with the
`chmod -R go-rwx` that closes it.

## Reading files

A `local` source is a directory, not a file reader: a `cli` collector runs its command
there, and recon parses the command's stdout line by line.

| The file holds | `run:` | Each record |
|---|---|---|
| JSON lines | `cat f.jsonl` | the line's object |
| CSV | `python3 -c "import csv, json, sys; [print(json.dumps(r)) for r in csv.DictReader(open('f.csv'))]"` | a row, keyed by the header |
| one JSON document (an array) | `python3 -c "import json; [print(json.dumps(r)) for r in json.load(open('f.json'))]"` | an item of the array |
| other text | `cat f.txt` | `{line_number, line}`, every line, a header row included |

recon has no CSV parser, and no converter for one ships. A PDF, DOCX or XLSX file becomes
markdown through the `$markitdown` transform (`recon --skill -r normalize-spec`). A `file`
source is for following a growing log with the `tail` type (`recon --skill -r streaming`).

## Running it again

Every survey writes a new archive holding everything it collected: a full snapshot, never
appended to an earlier one. `recon query` reads the latest, so a second run gives no
duplicate rows; `--archive <id>` reads an earlier one, and `recon status team` lists them
all. Every archive is kept unless a collector's `retain:` says otherwise.

For what changed between runs, record by record, give a collector `changes:` with the
fields that name a record:

```yaml
  - name: people
    type: cli
    source: data
    run: cat people.jsonl
    changes: {key: [id]}
```

recon then writes a `people_changes` table into each archive, one row per `insert`,
`update` or `delete` (deletes only with `deletes: true`). Added to a mission that already
has archives, the next survey writes one `rebaselined` row and nothing else; what changed
appears from the survey after it:

```console
$ recon init team -c team.yaml --replace && recon survey team
$ recon query team "SELECT op, key FROM people_changes"
│ rebaselined │ None │
$ echo '{"id": 4, "name": "Barbara", "team": "core"}' >> data/people.jsonl
$ recon survey team
$ recon query team "SELECT op, key, record FROM people_changes"
│ insert │ {'id': 4} │ {'id': 4, 'name': 'Barbara', 'team': 'core'} │
```

A mission that has `changes:` from its first survey tells every record of that survey as
an `insert`.

A change table covers one survey: what changed since the survey before it. A survey that
changed nothing has an empty one (`No rows.`). An `update` row carries the record as it is
now, not what it was; the previous value is in the archive before, which `--baseline`
reads beside the latest:

```console
$ recon query team "SELECT c.op, b.team AS was, c.record.team AS now
    FROM people_changes c JOIN baseline.people b ON b.id = c.key.id" --baseline 2026-11-07-211711-509832
archive 2026-11-07-214825-719794, against 2026-11-07-211711-509832
┃ op     ┃ was  ┃ now ┃
│ update │ core │ ops │
```

The history across every survey, oldest first, is `recon.changes_since("team", "people")`
in Python, or `recon subscribe team <name>` for a subscription the mission declares
(`subscriptions: [{name: history, output: people}]`). The keys, the ops and every refusal:
[docs/reference/changes.md](docs/reference/changes.md); subscriptions:
[docs/reference/subscriptions.md](docs/reference/subscriptions.md).

## Files and an API in one mission

A mission's catalog can mix kinds: here a directory and GitHub's public API. A public API
needs no `auth:` block at all; leave it out.

```yaml
catalog:
  - name: data
    type: local
    url: data
  - name: github                  # a public API: no auth: block at all
    type: api
    url: https://api.github.com

collectors:
  - name: people
    type: cli
    source: data
    run: cat people.jsonl
  - name: releases
    type: api
    source: github
    endpoint: /repos/duckdb/duckdb/releases
    params: {per_page: "3"}
    normalize: {tag: tag_name, published: published_at, author: author.login}
```

```console
$ recon init mixed -c mixed.yaml && recon survey mixed
2 run(s) from 2 collector(s), 2 source(s)
  people: 3 records
  releases: 3 records
2026-11-07-214932-242656
$ recon query mixed "SELECT tag, published, author FROM releases ORDER BY published DESC"
│ v2.0.0 │ 2026-10-21 12:49:12 │ lnkuiper │
│ v1.5.6 │ 2026-09-28 13:35:11 │ Maxxen   │
…
```

A survey of local files takes about a second. One that reaches a network source waits for
it: each send is bounded by the source's `timeout:` (60 seconds by default) and retried
twice, so a connection that stalls can hold a survey for a minute before the retry
succeeds. Set a shorter `timeout:` on the source to fail over sooner.

## From Python

`import recon` is the same surface: every command is a function that returns the document
`--json` prints, or raises an error whose `kind` is the CLI's.

```python
import recon

survey = recon.survey("team")  # the missions of the current project, as the CLI finds them
for table in survey.tables:
    print(table.name, table.status, table.records)

rows = recon.query("team", "SELECT name, team FROM people ORDER BY id")
print(rows.archive, rows.rows)
```

```text
people ok 4
teams ok 2
2026-11-07-211711-509832 [{'name': 'Ada', 'team': 'core'}, {'name': 'Grace', 'team': 'compilers'}, …]
```

Each function takes `root=`, the project directory, so a program need not change into it.
`help(recon.survey)` and its siblings document each one.

## What a mission can collect

`recon templates` lists the built-in starters; `recon init <name> -t <template>` copies one.

| Source | Collector | Starter |
|---|---|---|
| Files on disk, a repository's `git log` or `rg` | `cli` with a `local` source | `code-forensics` |
| A REST API (GitHub, PyPI, Wikipedia) | `api` | `github-audit`, `package-registries`, `factual-ground` |
| RSS or Atom feeds | `api` with `response_format: xml` | `rss-monitor` |
| Web pages and documents, as markdown | `api` with `response_format: document` | `docs-mine` |
| An MCP server's tools and resources | `mcp` | `mcp-server` |
| MCP tool results a Claude Code hook captured | `capture` | `mcp-captures` |
| A growing log file, server-sent events | `tail`, `sse` | `recon --skill -r streaming` |

Complete missions by use: `recon --skill -r config-patterns`.

## Next

- [docs/how-to/first-survey.md](docs/how-to/first-survey.md): an API mission, probe to query
- [docs/reference/cli.md](docs/reference/cli.md): every command, `--json` document, exit
  code and the archive
- [docs/explanation/overview.md](docs/explanation/overview.md): what recon is for, and its limits
- [docs/explanation/design.md](docs/explanation/design.md): the layers, ports and extension points
- `recon --skill`: the guide an agent reads, with `-r <name>` for each reference
- How-tos: [POST-body search APIs](docs/how-to/search-api-post-body.md),
  [an MCP server](docs/how-to/collect-from-an-mcp-server.md),
  [MCP results from a hook](docs/how-to/capture-mcp-results.md),
  [a survey as it runs](docs/how-to/watch-a-survey.md)
- Reference: [errors](docs/reference/errors.md), [changes](docs/reference/changes.md),
  [pagination](docs/reference/pagination.md), [ranges](docs/reference/ranges.md),
  [streaming](docs/reference/streaming.md), [live reading](docs/reference/live-reading.md),
  [subscriptions](docs/reference/subscriptions.md), [schedules](docs/reference/schedules.md),
  [telemetry](docs/reference/telemetry.md), [capture format](docs/reference/capture-format.md),
  [extending](docs/reference/extending.md), [CONTRACT.md](CONTRACT.md)
- [SECURITY.md](SECURITY.md): the trust boundaries; [CHANGELOG.md](CHANGELOG.md)

## License

MIT
