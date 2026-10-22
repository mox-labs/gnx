# Collect from an MCP server

An MCP server offers tools and resources to agents. When the calls you need are known in advance, recon can make them itself on every survey, and keep each answer as a table you query with SQL: the same issues search every morning, a directory tree, a database's schema. This walkthrough writes a mission with an `mcp` source and `mcp` collectors, plans it, and surveys it.

A server recon cannot reach by itself (one behind an OAuth sign-in in a browser) is reached the other way: the agent calls it, and a hook captures what it returned ([capture MCP tool results](capture-mcp-results.md)).

## 1. Name the server

An `mcp` source is one server, of one of two shapes.

**A stdio server** is a program recon starts:

```yaml
catalog:
  - name: files
    type: mcp
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem@2026.8.31", "."]
    timeout: 120
```

It is started on the first table that uses it, serves every other table of the survey, and is stopped when the survey ends, however it ends. It runs in the project root (the directory holding `.recon/`), wherever you run recon from, with your user's rights, and with little of your environment: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM` and `USER`, and what `env:` gives it. A credential it needs is named, never written:

```yaml
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: env:GITHUB_TOKEN   # recon's GITHUB_TOKEN, under this name
      LOG_LEVEL: warn                                  # a value as written
```

`env:NAME` reads `NAME` from recon's environment; `file:PATH` reads a file (a relative path is relative to the mission's directory). An unset variable is left out, and the plan warns (`env_unset`).

**A remote server** is reached over Streamable HTTP at its `url`:

```yaml
  - name: tracker
    type: mcp
    url: https://mcp.example.com/mcp
    token: env:TRACKER_TOKEN       # sent as Authorization: Bearer <token>; or file:PATH
```

A server that wants a header other than a bearer token gets it from a command instead, `headers_helper:`, run once per survey, whose stdout is a JSON object of header names and values:

```yaml
    headers_helper: ./bin/tracker-headers    # prints {"X-Api-Key": "..."}
```

The command runs in a shell, as a `cli` collector's does; a failure, or output that is not such an object, fails the table before anything is sent.

The credential goes to the server's origin and nowhere else: a redirect to another host is not followed. A 401 or 403 fails the table as `auth` (exit 6).

`timeout` bounds each call, the first one's start of the server included (`npx -y` downloads the package on first use: give it time). `rate_limit` holds for every call, a listing included.

## 2. Name the calls

Each `mcp` collector calls one tool, or reads one resource:

```yaml
collectors:
  - name: tree
    type: mcp
    source: files
    tool: directory_tree
    arguments: {path: ".", excludePatterns: [".git", "node_modules"]}
    result: text

  - name: issue
    type: mcp
    source: tracker
    resource: "tracker://issues/{id}"
    vars: {id: "1234"}
```

`vars:` fill the `{placeholders}` in the string values of `arguments` and in `resource`; one that nothing fills is a plan problem. A collector with no `source:` runs against every catalog source, as any collector does.

A tool's records come from its structured content when it declares an output schema, and from the JSON in its text content when it does not. `result:` says which explicitly. Some servers declare an output schema of a single string and put their JSON in it and in their text, as the filesystem server does; for those, `result: text` reads the JSON. `extract:` then names where the records are in it, as for an `api` collector, and `normalize:` shapes them.

## 3. Plan

```
recon survey files --dry-run
```

The plan shows each run's call (`tools/call directory_tree`, its arguments, and the server). For an `mcp` run the dry run does more than read the file, and says so: it starts each stdio server (and connects to each remote one), lists its tools and resources, checks each run against them, and stops it. What it found is listed under the table, each with whether it holds; had `tree` left out `path`, it would read:

```
yes  the stdio server of source 'files' starts and answers (tree)
yes  the server of source 'files' lists the tool 'directory_tree' (tree)
NO   the arguments given to 'directory_tree' (source 'files') match its input schema (tree); arguments: 'path' is a required property; correct arguments: as the tool's input schema says
Runnable: no: a survey fails each table whose requirement is NO
```

Under `--json` these are the plan's `requirements` (each with `need`, `holds`, `why`, `fix` and the runs that need it), `runnable`, and `preflight`. The plan itself is still valid: a survey runs, and fails the table whose requirement does not hold as `config`, with the same fix, before calling the tool.

## 4. Survey, probe, normalize

Leave `normalize:` out for the first survey, so the records are what the server returned, and look at their shape:

```
recon survey files
recon query files "SELECT * FROM tree LIMIT 3"
```

Then write `normalize:` from what you saw, plan again, and survey. What a table never says is `ok, 0 records` for an answer recon could not use:

| The server answered | The table |
|---|---|
| a tool error | fails (`run_failed`), quoting the error |
| no content at all | is kept empty, with a `result_empty` warning |
| text that is not JSON | fails, saying where the JSON broke |
| JSON with nothing at `extract:` | is kept empty, with an `extract_unresolved` warning |
| an empty list | is empty: the server had nothing |

With `preserve_raw: true`, each answer is kept whole in the archive's capture log as an `mcp` capture, every credential the source resolved replaced by `«redacted by recon»`: its token, the variables a stdio server is given by reference, and each header a `headers_helper` prints whose name says it carries a credential (`Authorization`, `X-Api-Key`, a name holding `key`, `token`, `auth`, `secret`, `cookie`, `session`, `signature`, `password` or `credential`). Name a custom credential header so; any other header's value is kept, since it is evidence.

## Before you run someone else's mission

A stdio source's `command` is a program recon runs, at the dry run as at the survey, with `npx -y` or `uvx` one it downloads first. Read it as you would a `cli` collector's `run:`. And a tool reads whatever its server can reach: its answers land in the archive. [SECURITY.md](../../SECURITY.md), boundary 8, says what recon does and does not hold.

`recon init <name> -t mcp-server` starts from a mission that reads a directory tree this way.
