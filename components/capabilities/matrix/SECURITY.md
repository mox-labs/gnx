# Security — matrix

matrix composes agent runtimes and runs component DAGs. It holds no secrets of its own and
opens no listening socket, but it is the package that **decides how much authority an agent
session gets**. That makes its defaults, not its code paths, the interesting surface.

Audience: anyone embedding matrix, and anyone reviewing a config that reaches it.

## Blast radius

A `ClaudeAgent` run launches a Claude Agent SDK session as a subprocess. Inside that
session the agent may read and write files, run commands, and reach the network — bounded
only by `permission_mode`, `allowed_tools`, and `cwd`. An `AnthropicAgent` run is a plain
API call and carries no local authority.

## Trust boundaries

### 1. Component config → agent authority

`ComponentRegistry.create(type_url, config)` splats a config dict into a factory, and the
factory into `ClaudeAgent(**kwargs)`. The registry performs **no validation**, so every
authority-bearing keyword — `permission_mode`, `allowed_tools`, `cwd`, `setting_sources` —
arrives from whatever produced that dict: an `experiment.yaml`, a CLI flag, a caller.

**A matrix config file is a capability grant. Treat it like one.**

What constrains it: `permission_mode` is a `Literal` enumerated at runtime with
`get_args`, so an unrecognised mode raises at construction naming the legal set rather
than reaching the SDK as an unknown string
(`adapters/_out/runtime/claude.py`). Selecting `bypassPermissions` or `dontAsk`
logs at WARNING, so a permissive run is visible in the output and not only in the config.

What does not constrain it: nothing validates `cwd` or `allowed_tools`. A config naming
`cwd: /` gets `/`.

### 2. Ambient environment → session behaviour

`ClaudeAgent.run` pops `CLAUDECODE` from `os.environ` for the duration of the call and
restores it afterwards (`claude.py`). This is process-global mutation: **concurrent
callers in the same process share it**. Runs are serialised per agent, but an unrelated
thread reading `CLAUDECODE` during a run sees it absent.

`setting_sources=[]` is the hermetic setting — no ambient `~/.claude` or project plugin
config leaks into the subprocess. Any eval that claims reproducibility should set it.

### 3. API credentials

`anthropic_agent.py` resolves a key from `~/.secrets/claude-api`, falling back to
`ANTHROPIC_API_KEY`. Keys are never logged and never enter an `Artifact`. Nothing in
matrix writes a credential to disk.

## Findings

### M-1 — `permission_mode` defaulted to `bypassPermissions` (fixed 2026-08-17)

`ClaudeAgent.__init__` took `permission_mode: str = "bypassPermissions"`. A tree-wide
search confirmed **no caller overrode it**, so every agent-backed component in the stack
ran with permissions bypassed — a decision made by a constant in one file rather than by
anyone's configuration.

Fixed: `DEFAULT_PERMISSION_MODE = "default"`. An unattended harness may legitimately need
bypass, but it now has to ask, which puts the choice in the config and in the run record.

Impact if you depended on the old behaviour: a harness that previously ran unattended will
now block on confirmation. Set `permission_mode: bypassPermissions` explicitly and accept
the WARNING line.

### M-2 — unvalidated mode strings reached the SDK (fixed 2026-08-17)

`permission_mode` was typed `str`, so a typo in a YAML config (`bypass_permissions`)
passed straight through to the SDK, which may not recognise it. Now a `Literal` checked
against `get_args` at construction.

## Not covered

- matrix does not sandbox agent sessions. Isolation is the caller's job — a container, a
  scratch `cwd`, or a restricted `allowed_tools` list.
- matrix does not redact agent output. An agent that reads a secret and prints it puts
  that secret in an `Artifact`.

## Reporting

Open an issue on the gnx repository. There is no separate embargo channel; this is a
pre-gen-0 component with no external users.
