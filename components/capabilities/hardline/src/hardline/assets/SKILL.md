---
name: hardline
description: Call any configured model — local MLX or ollama, Gemini, Claude — through one command, and get back the text plus which model family answered. Use when a task needs a second model's reading, a cheap local model for bulk extraction, or an out-of-family check on a judgment.
---

# hardline

One command for every model the operator has configured. Models are registry rows, not code.

## See what is configured

```bash
hardline models          # name, family, backend, local?, model id
hardline check           # validates every tier; names the file and key of any error
```

If `hardline models` lists nothing, no registry exists yet. Do not invent one — ask the operator
which models they run.

## Call a model

```bash
hardline complete qwen3-8b "Summarise in one line: ..."
hardline complete haiku --system "You are a strict reviewer." "Is this claim supported? ..."
echo "long input" | hardline complete qwen3-8b -          # prompt from stdin
hardline complete qwen3-8b --json "..."                   # full Completion as JSON
```

`--json` output carries `family`, `model`, `backend`, `local`, `usage`, `latency_ms`, `attempts`.
Report the `family` when a second model's answer is used as evidence: a check by the same family
as the author is not an independent check.

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
