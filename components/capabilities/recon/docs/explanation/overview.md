---
title: recon
summary: Collect from APIs, commands, files, web pages and MCP servers into tables, then ask them SQL.
kind: capability
---

## What it is

recon collects records from many kinds of source into JSONL tables and answers SQL over
them. A YAML file says what to collect; recon runs it with no model in the loop.

## The problem

An agent is asked which of a project's dependencies shipped a release this month, and who
owns each. The answer is spread over GitHub's releases API (JSON), an owners sheet (CSV)
and `git log`. Without recon the agent curls each one, reads the raw JSON into its context,
and reshapes it by eye: thirty releases fill the window before any reasoning starts.

Next week the question comes back. Nothing records what was fetched last time, from where,
or with which parameters, so the agent starts over, and a number in its answer cannot be
traced to the response it came from.

## How it works

A **mission** is one YAML file, kept at `.recon/<name>/config.yaml`. Its catalog lists
**sources**: an HTTP API's base URL, a directory on disk, an MCP server. Its
**collectors** say what to ask a source (an endpoint, a shell command, an MCP tool) and how
to reshape each answer into flat records (`normalize:`), a table per collector.

Before anything runs, recon builds the **plan**: every request with its URL and headers
(credentials shown as `REDACTED`), every command and the directory it runs in, every
setting and where its value came from. Nothing is fetched to make it. `recon survey` then
runs the plan and writes one **archive**: a timestamped snapshot holding every table, and
a record of what the run did, table by table.

`recon query` answers SQL over an archive, the latest by default. The agent asks for ten
rows and gets ten; the other thousands stay on disk.

## Try it

Offline: a mission from stdin whose one collector prints two JSON lines.

```console
$ recon init deps -c - <<'EOF'
collectors:
  - name: deps
    type: cli
    run: >-
      printf '%s\n' '{"name": "httpx2", "pin": ">=2.10,<3"}'
      '{"name": "duckdb", "pin": ">=1.0,<2"}'
EOF
Created: …/.recon/deps/config.yaml
$ recon survey deps --dry-run --json        # the plan; nothing runs
{
  "schema": "recon.v1.plan",
  "runs": [{"output": "deps", "type": "cli", "effects": ["subprocess"],
            "commands": ["printf '%s\\n' …"], "cwd": null}],
  …
  "provenance": [
    {"path": "collectors.0.retain", "value": "all", "origin": "recon",
     "note": "no archive is removed unless the mission says so"},
    …
$ recon survey deps
1 run(s) from 1 collector(s), 0 source(s)
  deps: 2 records
2026-11-07-211905-938253
$ recon query deps "SELECT line, name, pin FROM lines.deps"
archive 2026-11-07-211905-938253
┃ line ┃ name   ┃ pin       ┃
│ 1    │ httpx2 │ >=2.10,<3 │
│ 2    │ duckdb │ >=1.0,<2  │
```

Every row names its archive, and `lines.<table>` gives its line there: archive, table and
line together cite it. Files on disk, CSV, and what happens on a second run: the
[README](../../README.md).

## What it gives an agent

| What an agent can do | Why it can trust it | The command or call |
|---|---|---|
| Collect APIs, commands, files, web pages and MCP servers from one config | The plan names every request, command and MCP call, and each setting's value and origin, before anything runs; an unknown key or a typo is a problem at its path, never silently ignored | `recon survey <m> --dry-run --json` |
| Ask SQL of a result too big for its context | A query reads only the mission's own archives, runs SELECT only, and names the archive its rows came from | `recon query <m> "<SQL>" --json` |
| Say what each run collected, and cite a row | Each archive keeps the config's sha256 and, per table, its record count, status and error; a row is cited by archive, table and line | `recon status <m> --json`, `lines.<table>` |
| Run again without double-counting | A survey writes a new full snapshot and never appends to an earlier one; one survey of a mission runs at a time; what changed is a table of its own | `recon survey <m>`, `changes: {key: [...]}` |
| Act on a failure | An exit code by kind, and an error with a `fix` per problem; a failed table still leaves its archive, marked `incomplete` | the exit code, `error.fix` |
| Learn recon from the version it has | `--skill` prints the guide that ships with the installed recon | `recon --skill`, `recon --skill -r config-patterns` |

## What it does not do

- **It does not decide what to collect.** recon runs the mission someone wrote; choosing
  sources and reading the result stay with the agent and the person.
- **It does not parse CSV**, or a JSON document spread over several lines, from a command:
  each stdout line that is a JSON object is a record, any other line is
  `{line_number, line}`. Convert in the command (the README shows how).
- **It does not run JavaScript.** A page is one HTTP GET, converted to markdown.
- **It only reads.** It writes to no remote system.
- **It does not push.** Archives are read: followed as they are written (`recon watch`),
  or what changed is read by a subscription, or handed to a command after a survey
  (`recon deliver`). There is no broker and no daemon; a schedule runs on the operating
  system's own scheduler.
- **It does not sandbox commands.** A `cli` collector, an MCP server's command and a
  `deliver:` command run with your permissions: never take a mission from untrusted input
  ([SECURITY.md](../../SECURITY.md)).
- **It is not for one-off fetches.** One page read once is faster with `curl`.

## Next

- [README](../../README.md): install, a first run with files on disk, re-runs, the library
- `recon --skill`: the guide an agent reads; `-r <name>` for each reference
- [The command line](../reference/cli.md): every command, document, exit code, and the archive
- [Your first survey](../how-to/first-survey.md): an API mission, probe to query
