# Capture MCP tool results and query them with SQL

An MCP tool call in Claude Code returns its result into the conversation, and then it is gone. A search through an MCP server, a list of issues, a page of query results: none of it can be queried later. This walkthrough keeps every MCP result as a capture and turns the captures into a table, with no second fetch.

Three parts do the work:

1. A Claude Code **`PostToolUse` hook** fires after each MCP tool call and pipes the call's payload to `recon capture`.
2. **`recon capture <mission>`** appends the payload to the mission's capture log, `.recon/<mission>/captures/captures.jsonl`, as an `external` capture (format: [capture-format.md](../reference/capture-format.md)).
3. A **`capture` collector** in the mission's config reads that log on `recon survey`, normalizes the bodies, and writes JSONL that `recon query` reads.

## 1. Create the mission

```bash
recon init mcp --template mcp-captures
```

The template has one collector:

```yaml
collectors:
  - name: mcp-results
    type: capture
    match:
      kind: external
    normalize:
      tool: _capture.collector
      server: _capture.source
      input: _capture.request.tool_input
      captured_at: _capture.captured_at
      text: text
```

`_capture` is the capture line itself (see [capture-format.md](../reference/capture-format.md)), so the table records which tool ran, with what input, and when.

## 2. Wire the hook

In `.claude/settings.json` at the project root:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "mcp__.*",
        "hooks": [
          {
            "type": "command",
            "command": "cd \"$CLAUDE_PROJECT_DIR\" && recon capture mcp"
          }
        ]
      }
    ]
  }
}
```

- MCP tools are named `mcp__<server>__<tool>`, and a matcher is a regex: `mcp__.*` matches every MCP tool, `mcp__exa__.*` one server's. A matcher without the `.*`, such as `mcp__exa`, matches no tool at all.
- `cd "$CLAUDE_PROJECT_DIR"` makes recon resolve `.recon/` at the project root, wherever the session's working directory has moved.
- `recon capture` exits 0 when it has appended the capture. If the mission does not exist (4) or the payload is malformed (3), Claude Code shows a hook error in the transcript instead of losing the capture silently.

What recon reads from the payload (field names from the Claude Code hooks reference, `PostToolUse` input):

| Payload field | Becomes |
|---|---|
| `tool_name` | the capture's `collector` |
| `mcp_server.name`, else the `<server>` in `mcp__<server>__<tool>` | the capture's `source` |
| `tool_response` | the body: JSON-encoded, or stored as text when it is a string |
| everything else (`tool_input`, `tool_use_id`, `session_id`, …) | the capture's `request` |

## 3. Use MCP tools, then survey

Work as usual. Each MCP tool call adds one line to `.recon/mcp/captures/captures.jsonl`. Then:

```bash
recon survey mcp --dry-run     # plan: one capture run, effects: filesystem; nothing fetched
recon survey mcp
recon query mcp "SELECT tool, captured_at, input FROM mcp_results ORDER BY captured_at DESC"
```

## 4. Probe the response shape, then normalize it

The shape of `tool_response` depends on the MCP server (and on how Claude Code hands it to the hook), so write the normalize paths after looking at a real one. Find a capture and read its body:

```bash
tail -1 .recon/mcp/captures/captures.jsonl | jq -r .body      # raw/<sha256>
jq . .recon/mcp/captures/raw/<sha256>
```

Suppose an Exa search body looks like `{"results": [{"title": ..., "url": ..., "publishedDate": ...}, ...]}`. Add a collector for it, filtered to that server's search tool:

```yaml
  - name: exa-hits
    type: capture
    type_url: acme.research.v1.search-hit      # optional: declare what the records are
    match:
      collector: "mcp__exa__web_search*"
    extract: results
    normalize:
      title: title
      url: url
      published: publishedDate
      query: _capture.request.tool_input.query
```

```bash
recon survey mcp
recon query mcp "SELECT query, title, url FROM exa_hits ORDER BY published DESC LIMIT 20"
```

Every survey re-reads the whole log, so the table always holds every result captured so far, and changing the normalize spec never costs another call to the server.

## Try it without Claude Code

`recon capture` reads one payload on stdin, so you can feed it by hand:

```bash
echo '{"tool_name": "mcp__exa__web_search_exa",
       "tool_input": {"query": "state space models"},
       "tool_response": {"results": [{"title": "Mamba", "url": "https://arxiv.org/abs/2312.00752"}]}}' \
  | recon capture mcp --json
```

`--json` prints the capture line it appended.

## Limits

- The capture holds what the tool returned to Claude Code, not what the server sent on the wire.
- `PostToolUse` fires only after a tool succeeds, so failed calls are not captured.
- A capture log grows without bound. Archive or delete `.recon/<mission>/captures/` when it has served its purpose; the archives that surveys produced from it stay readable.
- A captured response can contain anything the tool saw, including private data. Treat `.recon/` as you would the conversation transcript.
