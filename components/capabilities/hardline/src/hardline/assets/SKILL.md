---
name: hardline
description: Call any configured model — local MLX or ollama, Gemini, Claude — through one command, and get back the text plus which model family answered. Use when a task needs a second model's reading, a cheap local model for bulk extraction, or an out-of-family check on a judgment.
---

# hardline

One command for every model the operator has configured. Models are registry rows, not code.

## See what is configured

```bash
hardline models          # name, family, backend, local?, model id
hardline check           # validates every tier and resolves every api_key reference
hardline check --json    # {"ok", "tiers": [...], "models": [{"name", "ok", "problem", ...}]}
```

`check` exits 0 only when every row would get past composition and find its key. A key the
provider then rejects is only discovered by a call (exit 6).

If `hardline models` lists nothing, no registry exists yet. Do not invent one — ask the operator
which models they run.

## Call a model

```bash
hardline complete qwen3-8b "Summarise in one line: ..."
hardline complete haiku --system "You are a strict reviewer." "Is this claim supported? ..."
echo "long input" | hardline complete qwen3-8b -          # prompt from stdin
hardline complete qwen3-8b --json "..."                   # full Completion as JSON
```

`--json` output carries `family`, `model`, `backend`, `local`, `usage`, `latency_ms`, `attempts`,
`retries`, `fallback_from`. Report the `family` when a second model's answer is used as
evidence: a check by the same family as the author is not an independent check. When
`fallback_from` is set, the model that answered is not the one asked for — use the `family`
that actually answered, not the one in `fallback_from`.

## When a command fails

The exit code is the next move. Pass `--json` and stderr carries one JSON line,
`{"error": {"kind", "message", "retryable", "retry_after", "fix"}}`; `fix` is a step that can
succeed, or `null` when there is none.

| exit | kind | do this |
|---|---|---|
| 0 | — | use the output |
| 1 | `unknown` | report the message; do not retry blindly |
| 2 | — | usage: fix the flags (`hardline <command> --help`) |
| 3 | `config`, `bad_request` | change the input: the model name, the registry row, the secret |
| 4 | `not_found` | reserved; hardline raises none today |
| 5 | `rate_limit`, `timeout`, `unavailable` | retry later, after `retry_after` seconds if set |
| 6 | `auth` | stop and tell the operator the key was rejected |

The runtime has already spent the row's retries and fallbacks before exit 5, so an immediate
retry is unlikely to help.

## Choosing a model

- **Bulk extraction, triage, summarising** → a `local: true` row. No quota spent.
- **A verdict another model will be judged against** → a row whose `family` differs from the
  author's. Same-family checkers fail on the same hard items.
- **Nothing configured suits the task** → say so. Do not fall back to a model the registry does
  not name.

## Registry shape (`~/.hardline/config.yaml` or `./hardline.yaml`)

```yaml
default: qwen3-8b
models:
  qwen3-8b:
    backend: openai-compat              # any OpenAI-protocol server
    base_url: http://127.0.0.1:8080/v1  # mlx_lm.server
    model: mlx-community/Qwen3-8B-4bit
    family: qwen
    local: true
  haiku:
    backend: anthropic
    model: claude-haiku-4-5-20251001
    family: claude
    api_key: file:~/.secrets/claude-api  # a reference; literal keys are rejected
```
