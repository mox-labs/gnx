# hardline

One port for every model family. Messages in; text or a validated object out; tagged with
the family that answered, what it cost in tokens, and how long it took.

## Install

hardline is not on a package index, and `pip install hardline` installs an unrelated project.
Install it from this repository:

```bash
# run it once, at the release's tag
uvx --from "git+https://github.com/mox-labs/gnx@hardline-v0.3.1#subdirectory=components/capabilities/hardline" hardline models
# or install the command
uv tool install "git+https://github.com/mox-labs/gnx@hardline-v0.3.1#subdirectory=components/capabilities/hardline"
```

From a clone:

```bash
git clone https://github.com/mox-labs/gnx && cd gnx
uv tool install ./components/capabilities/hardline
```

hardline depends on no provider SDK: it speaks the OpenAI-compatible and Anthropic wire
protocols over HTTP itself.

## What it is

A **model runtime**: one call to one model, with a registry that makes a model a row of
configuration rather than a class. It is not an **agent runtime**: no tool loop, no
permissions, no trajectory. A call may offer the model tools and return the calls it asks
for, but hardline runs none of them. Agents run one layer up, in
[matrix](../matrix/README.md), which reaches hardline through
[matrix-hardline](../matrix-hardline/README.md).

## A first call, offline

A model is a registry row in `hardline.yaml`. The `mock` backend replies from the row's own
options, so this runs with no server and no key; the second row is a local model on ollama.

```yaml
# hardline.yaml
default: offline
models:
  offline:                  # no server, no key: replies from config
    backend: mock
    model: echo
    family: mock
    options: {default: '{"supported": true}'}
  qwen:                     # ollama, serving qwen3:8b on its default port
    backend: openai-compat
    base_url: http://127.0.0.1:11434/v1
    model: qwen3:8b
    family: qwen            # required; never inferred
    provider: ollama
    local: true
```

```bash
hardline models
hardline check
hardline complete offline "Is the sky green?"
```

```
NAME       FAMILY  BACKEND        LOCAL  MODEL
offline *  mock    mock                  echo
qwen       qwen    openai-compat  yes    qwen3:8b

* default
…
ok: 2 model(s) valid against installed backends; every secret reference resolves
{"supported": true}
```

`hardline complete qwen "..."` calls ollama. With nothing listening there, it says where to
look:

```
hardline: error (transient): model 'qwen': transient failures exhausted retries. Last: model 'qwen' (openai-compat → http://127.0.0.1:11434/v1): ConnectError: All connection attempts failed
hardline: fix: check that a server is listening at http://127.0.0.1:11434/v1 (start it, for a local model server), or point models.qwen.base_url at one that is
```

hardline reads `hardline.yaml` from the working directory, `~/.hardline/config.yaml`, and the
file `$HARDLINE_CONFIG` names: its three **tiers**, merged field by field.
[Configure](docs/guide.md#configure) has every field, mlx_lm.server and hosted rows, and
`api_key` references.

## From Python

```python
import asyncio

from pydantic import BaseModel

from hardline import build_runtime


class Verdict(BaseModel):
    supported: bool


async def main() -> None:
    rt = build_runtime()  # reads ./hardline.yaml, among its tiers
    async with rt:
        c = await rt.complete("offline", "Is the sky green?")
        print(c.text, c.family, c.answered)  # {"supported": true} mock True
        s = await rt.extract("offline", "Is the sky green?", output=Verdict)
        print(s.value)  # supported=True


asyncio.run(main())
```

`complete` returns a `Completion`: the text, the row and family that answered, why the reply
ended, usage, latency, and hardline's record of how it made the call. `extract` validates the
reply against the model you name, repairing it as many times as the row allows.

## Next

| | |
|---|---|
| [Overview](docs/explanation/overview.md) | what hardline is for, and what it gives an agent |
| [Using hardline](docs/guide.md) | configure, calls, what a call returns, tools, telemetry, the command's output and exit codes, writing a backend |
| [CONTRACT.md](CONTRACT.md) | what each surface hides, its defaults and failures, how far it is promised |
| [SECURITY.md](SECURITY.md) | where prompts go, how keys are read, what is recorded |
| [CHANGELOG.md](CHANGELOG.md) | what changed in each release |
| `hardline --skill` | the guide an agent reads, for the installed version |
