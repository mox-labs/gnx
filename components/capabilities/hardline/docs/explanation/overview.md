---
title: hardline
summary: One port for every model family; a model is a row of config, and every reply says which row and family answered.
kind: capability
---

## What it is

hardline makes one call to one model, whichever family it belongs to: a local model on
ollama or mlx_lm.server, a hosted one behind a key. A model is a row of configuration, and
every reply says which row and which family answered, and why the reply ended.

## The problem

An agent wants a second reading of its own judgment from another model family, or a cheap
local model for bulk extraction. Each provider has its own client, its own reply shape and
its own word for "cut off", so a reply truncated at `max_tokens` reads like an answer. An
environment variable can move where a key is sent without any config saying so. After a
retry lands on a fallback model, nothing records that a different model answered.

## How it works

A **registry row** is a model: a name, the backend that speaks to it, the provider's model
id, and its family (`qwen`, `claude`, `gemini`), which is required and never inferred.
Everything a call sends, and where, is in the row.

A **backend** speaks one wire protocol: `openai-compat` (ollama, mlx_lm.server, vLLM,
llama.cpp, Gemini, OpenAI), `anthropic`, or the offline `mock`. A backend is an installed
entry point, so a new one needs no edit to hardline.

The **tiers** are the files the rows come from, merged field by field, lowest first:
`~/.hardline/config.yaml`, `./hardline.yaml`, `$HARDLINE_CONFIG`. A host such as matrix can
hand hardline its own registry instead.

A **fallback** is another row, asked once a row's retries for transient failures are spent.

A **Completion** is what a call returns, as two documents: the reply (the text or tool calls,
the row and family that answered, why it ended, usage, latency) and hardline's record of how
it made the call (attempts, retries, fallback, what it sent, where each value of the row came
from).

## Try it

Offline and free, with the `mock` backend, which can plant failures. Install hardline as the
[README](../../README.md#install) says, then in an empty directory:

```yaml
# hardline.yaml
models:
  flaky:                    # fails once, as an unreachable server would
    backend: mock
    model: m
    family: qwen
    retries: 0
    fallbacks: [steady]
    options: {script: [{error: unavailable}]}
  steady:
    backend: mock
    model: m
    family: mock
    options: {default: "answered by the fallback"}
```

```bash
hardline check
hardline complete flaky "hi" --json | jq -c '{name, family, text, fallback_from: .record.fallback_from, attempts: [.record.attempt_log[] | {name, reason}]}'
```

```
…
ok: 2 model(s) valid against installed backends; every secret reference resolves
{"name":"steady","family":"mock","text":"answered by the fallback","fallback_from":"flaky","attempts":[{"name":"flaky","reason":"unavailable"},{"name":"steady","reason":null}]}
```

`flaky` was asked, `steady` answered, and the reply says so: the row, its family, the row it
fell back from, and every attempt with why it failed.

## What it gives an agent

| what an agent can do | why it can trust it | the command or call |
|---|---|---|
| see which models are configured, and check them before a call | every row is validated against its backend and every key reference resolved, naming the file and key of each problem; each row's worst case of backend calls is shown | `hardline models`, `hardline check --json` |
| call any family with one command | one reply shape for every provider: `text`, `tool_calls`, `finish_reason`, `usage`, `latency_ms` | `hardline complete <row> "<prompt>" --json` |
| tell a whole answer from a cut one | `answered` is false for a reply cut at `max_tokens`, refused, withheld or empty; `finish_reason` is one vocabulary across providers | `Completion.answered`, `.finish_reason` |
| know which model answered | the reply names the row, family and provider that answered, and the record the row it fell back from | `.name`, `.family`, `.record.fallback_from` |
| get structured output it can rely on | the reply is validated with pydantic and repaired as many times as the row allows, or `SchemaError` | `rt.extract(row, prompt, output=Model)` |
| know where each setting came from | the record names, for every field of the row, the tier that set it or `default` | `.record.row`, `hardline check --json` (`rungs`) |
| bound a call | `limits` caps a whole call's backend calls, seconds and tokens, every retry and fallback included | a row's `limits` |
| act on a failure | each failure has a kind, an exit code and a fix that can succeed from there | `--json` error envelope |
| learn hardline for the version installed | the guide is printed by the installed package | `hardline --skill` |

## What it does not do

- **No agent loop.** A call may offer tools and return the calls the model asks for; hardline
  runs none of them. Agents are matrix's.
- **No streaming.** A call returns once the reply is whole.
- **Text only.** A turn's content is text.
- **No money.** It reports tokens, never a price; `limits.tokens` bounds tokens, not spend.
- **No content in telemetry**, and no option to record it: no prompt, reply or error message
  in a span or metric.
- **`base_url` is not constrained.** A row can send a hosted model's key to any http(s) URL;
  review registry changes ([SECURITY.md](../../SECURITY.md), M-1).
- **A backend runs in-process.** Installing a backend package is trusting it.

## Next

- [README](../../README.md): install, a first call, from Python.
- [Using hardline](../guide.md): every field, calls, tools, telemetry, exit codes, writing a
  backend.
- [CONTRACT.md](../../CONTRACT.md): how far each surface is promised.
- `hardline --skill`: the guide an agent reads.
