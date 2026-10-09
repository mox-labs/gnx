# Capture log format — `recon.v1.capture`

A capture is one fetched response kept verbatim: an HTTP body, a command's stdout, or a result another process obtained. recon appends captures to a **capture log**. A `capture` collector reads capture logs back and normalizes their bodies, so any tool that writes this format can hand data to recon without recon fetching it.

This page is the interface. It is versioned by the `schema` field; anything not written here is not part of it.

## Layout

A capture log is a directory:

```
<log>/
  captures.jsonl        append-only, one capture per line, UTF-8 JSON
  raw/<sha256>          each body, named by the lowercase hex sha256 of its bytes
```

recon writes two kinds of log:

| Log | Written by | Where |
|---|---|---|
| Archive log | `recon survey` with `preserve_raw: true` | `.recon/<mission>/archive/<timestamp>/` |
| External log | `recon capture <mission>` (e.g. from a Claude Code hook) | `.recon/<mission>/captures/` |

Another tool may write either kind, or a log anywhere else that a `capture` collector's `path:` points at.

## A line

```json
{
  "schema": "recon.v1.capture",
  "id": "5c1f0e0b8c3a4f53a5e1d2a9c7b4e8f1",
  "source": "exa",
  "collector": "mcp__exa__web_search_exa",
  "kind": "external",
  "request": {"tool_name": "mcp__exa__web_search_exa", "tool_input": {"query": "state space models"}},
  "status": null,
  "content_type": "application/json",
  "headers": {},
  "body": "raw/9f2c…e41a",
  "sha256": "9f2c…e41a",
  "bytes": 1832,
  "captured_at": "2026-10-09T06:12:44.120931+00:00"
}
```

| Field | Type | Required to read | Meaning |
|---|---|---|---|
| `schema` | string | yes | `recon.v1.capture`. A reader rejects another major version (`recon.v2.capture`). |
| `id` | string | yes | Unique within the log. recon writes a uuid4 hex. |
| `source` | string or null | no | Where it came from: a catalog source name, or an MCP server name. |
| `collector` | string | yes | What produced it: the output name of a recon collector, or a tool name. |
| `kind` | string | yes | `http`, `cli`, `web` or `external`. |
| `request` | object | no | What was asked. Kind-specific (below). |
| `status` | integer or null | no | HTTP status for `http`/`web`, exit code for `cli`, null when there is none. |
| `content_type` | string | no | MIME type of the body, without parameters. Decides how the body is parsed. |
| `headers` | object | no | Response headers, credential values redacted (below). |
| `body` | string | yes | Path of the body relative to the log directory. Must be exactly `raw/<sha256>`. |
| `sha256` | string | yes | Lowercase hex sha256 of the body bytes. Must equal the name in `body`. |
| `bytes` | integer | no | Body size. |
| `captured_at` | string | no | ISO 8601 timestamp with offset. |

**Readers ignore fields they do not know.** A writer may add fields; it may not change the meaning of the ones above without a new major version.

### `request` by kind

| `kind` | `request` holds |
|---|---|
| `http` | `method`, `url` (with an `auth.param` query value redacted), and `body` for a JSON request body |
| `web` | `method`, `url` |
| `cli` | `commands` (each substituted command, in order), `cwd`, `exit_codes` |
| `external` | the Claude Code hook payload minus `tool_response`: `tool_name`, `tool_input`, `tool_use_id`, `session_id`, `cwd`, `hook_event_name`, `mcp_server`, … |

## Writing rules

- **Append only.** Never rewrite or reorder `captures.jsonl`. Write each line with one `write` call, ending in `\n`. recon also takes an exclusive `flock` on the file while appending, so concurrent writers (parallel tool calls firing hooks) never interleave lines; another writer appending to the same file should do the same.
- **Bodies are content-addressed.** Write the bytes to `raw/<sha256>` before appending the line that names them. Identical bodies share one file. Write to a temporary name and rename, so a reader never sees a partial body.
- **Redact credentials before writing.** recon replaces the values (not the keys) of `set-cookie`, `set-cookie2`, `authorization`, `proxy-authorization`, `x-api-key`, `x-auth-token` and `x-amz-security-token`, matched case-insensitively, with `«redacted by recon»`. Request headers are never written.

## Reading rules (what recon enforces)

A `capture` collector refuses — with an error naming the file and line — any line that:

- is not a JSON object, or has a `schema` other than `recon.v1.capture`;
- has a `body` that is not `raw/<64 hex>` (no other path, absolute or relative, is ever opened);
- names a body that is missing, or whose bytes do not hash to `sha256`.

## How a body becomes records

The capture collector parses each matching body by its `content_type`, unless the collector sets `response_format` explicitly:

| `content_type` | Parsed as | Then |
|---|---|---|
| `application/json`, `*+json` | JSON | `extract` path, then each dict is a record |
| `application/xml`, `text/xml`, `*+xml` | XML (as the `api` collector parses it) | `extract` path, then each dict is a record |
| anything else | text lines | each line is a record: a JSON object as-is, else `{line_number, line}` |

A `normalize` spec then applies as usual, and may also read the capture line itself as `_capture` (`_capture.collector`, `_capture.request.tool_input.query`, `_capture.captured_at`). Without a normalize spec, records are written exactly as parsed.
