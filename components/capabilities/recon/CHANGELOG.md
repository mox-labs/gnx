# Changelog — recon

## 0.10.0 — 2026-10-12

Fixes for cases where recon gave a wrong answer without saying so, and for three places where collected data or an installed plugin could reach further than the mission's config allowed.

### Changed (breaking)

- **An empty table reports `records: 0`.** It reported 1, because the `{"_empty": true}` line that keeps the file readable was counted, and `SELECT count(*)` on the table returned 1. The line is still written; it is no longer counted and the table's view has no rows. `meta.yaml` files written by 0.9.0 keep the number they recorded.
- **An unresolved `{placeholder}` in a `params:` value is a config error** at plan time, located as `collectors[i].params.<key>`. The parameter used to be left out of the request without a word. So was any value that merely contained a `{` (a SPARQL query, a JSON filter); those are now sent as written.
- **`recon query` is confined to the mission's archives.** SQL can read files under `.recon/<mission>/archive/` (earlier archives included) and nothing else: no other path, no `INSTALL` or `LOAD`, no URL. It used to run on an unrestricted DuckDB connection, where `read_text('/etc/hosts')` returned the file.
- **`$markitdown` reads only what the mission declares and never fetches.** The file a record names must be under the mission directory or a catalog source with `type: local`; a URL (`http:`, `https:`, `file:`, `data:`) is refused. Either is a config error with a fix, where it used to fetch the URL or read the file. Use a `web` collector to fetch a document.
- **A response body is bounded.** A source has `max_bytes` (default 50,000,000, counted after decompression). A larger body fails its table with kind `collection` instead of being buffered. Raise `max_bytes` on a source that serves larger documents.
- **The default User-Agent is `recon/<version> (+https://github.com/mox-labs/gnx)`** for `api` and `web` sources. An `api` source with no `user_agent:` used to send a desktop browser's string. A source's own `user_agent:` is sent as written; set one for a site that refuses the default.
- **For collector-type authors:** recon applies `normalize:` to the records of any type that does not declare `normalizes=True`, so a type that applies the spec itself must now say so. `ctx.env` holds only the `auth.env` variables of the sources that type's collectors use; reading any other name raises a config error. `build_collectors` takes the config.

### Added

- Top-level config keys beginning with `_` are ignored. A config can hold notes, or a YAML anchor that several collectors alias (`_normalize: &spec {...}`), without tripping the unknown-key check that 0.9.0 introduced. Every other unknown key is still an error, and so is a `_key` inside a source or collector.
- `max_bytes` on a catalog source.
- A failed table's `error` carries a `fix` when the error names its remedy, in `survey --json` and `meta.yaml`; an error that escapes a command keeps its own `fix` on the `--json` error line.

### Fixed

- A plugin collector type that did not apply `normalize:` wrote raw records for a config the plan had accepted. The spec is now applied for it.
- A collector type's factory was handed the whole process environment.
- The built-in `factual-ground` template's SPARQL `query` parameter was never sent (it contains braces).

## 0.9.0 — 2026-10-09

Agents are recon's main callers, so this release makes its boundary say what happened in a form a program can act on, and plans every survey before it touches anything.

### Changed (breaking)

- **Mission directories now live in `.recon/`** (`.recon/<name>/` at the git root, or the current directory outside a repository). The previous location is not read. To keep an existing mission, move its directory under `.recon/`.
- **A survey in which any table failed exits non-zero.** It used to exit 0 and leave only an `.incomplete` file. The archive is still written and every table's status is still reported; the exit code is 6 if any table failed on credentials, 5 if every failure was transient, 1 otherwise.
- **Errors exit with classified codes** shared with hardline and ix: 1 unclassified, 2 usage, 3 config (including a `conflict` and bad SQL), 4 not found, 5 transient, 6 auth. Errors that used to exit 1 now exit 3 or 4 where they are config or not-found.
- **`query --json` prints a document**, `{"schema": "recon.v1.query-rows", "columns", "rows", "count", ...}`, instead of a bare list.
- **An unknown transform is a config error.** `x|$typo` used to be skipped silently and leave the value untransformed.
- **Config keys are checked.** An unknown key (`normalise:`, `preserve-raw:`) is a config error with its path, instead of being ignored.
- **`survey -c FILE` no longer overwrites a mission's existing, different config.** It exits 3 (`conflict`) and suggests `--replace`.
- **`preserve_raw` writes a capture log** — `archive/<ts>/captures.jsonl` with bodies under `raw/<sha256>` — instead of `raw/<collector>/body` + `meta.yaml`.
- **`meta.yaml` is `format_version: 2`**: a `tables` list (was `collectors`) with each table's `type_url`, and `error: {kind, message}` (was a string), plus `recon_version` and `config_sha256`. `status` and `query` still read format-1 archives.
- **The `Collector` port** takes `captures: CaptureLog | None` instead of `raw_store: Any`, and `ApiCollector`, `CliCollector`, `WebCollector` take an optional transforms mapping. Transforms are registered by name without the `$`.
- The `$pdf2text` transform the docs described never existed; `$markitdown` (any document markitdown reads) is the one that does.

### Added

- `survey --dry-run` / `-n`: validates the config, resolves every collector × source run with its declared effects (network, subprocess, filesystem) and the URL or command it would use, and lists every problem with a path such as `collectors[2].normalize.title`. No network call, no subprocess, nothing written. Every survey runs the same plan first, so an invalid config never creates an archive. Unresolved `{placeholders}` in an API endpoint or body are caught here.
- `--json` on `survey`, `status`, `templates`, `init` and `capture`. Every document carries a dotted `schema` (`recon.v1.survey`, `recon.v1.plan`, `recon.v1.status`, ...). With `--json`, an error is one `{"error": {"kind", "message", "fix", ...}}` line on stderr.
- Collector types and transforms are extensions: entry-point groups `recon.collectors` and `recon.transforms`, with the built-ins registered the same way. A plugin that fails to load is reported by `status` and `--dry-run` and does not affect the others. See docs/reference/extending.md.
- A `capture` collector type that normalizes capture logs (a path or glob, filtered by source / collector / kind), and `recon capture <mission>`, which appends a Claude Code `PostToolUse` hook payload to the mission's capture log. The capture format is specified in docs/reference/capture-format.md; the hook setup in docs/how-to/capture-mcp-results.md. New template: `mcp-captures`.
- `type_url:` on a collector (dotted grammar; default `recon.v1.records`), recorded per table in `meta.yaml`.
- `auth.param` sends the credential as a query parameter (it was declared and ignored). Its value is redacted from captured URLs.
- `status` lists the installed collector types and transforms, and each mission's external capture count.

### Fixed

- HTTP failures carry a kind: 401/403 are `auth`, 429/5xx/transport errors `transient`, so a survey's exit code says whether to retry or re-key.
- Templates and docs pointed at the old repository; they now point at the gnx repository.
