# Security — matrix

matrix composes agents and runs component DAGs. It holds no secrets of its own and opens no
listening socket, but it is the package that **decides how much authority an agent session
gets**. That makes its defaults and its config validation, not its code paths, the
interesting surface.

Audience: anyone embedding matrix, and anyone reviewing a config that reaches it.

## Blast radius

A `claude-sdk` runtime launches a Claude Agent SDK session as a subprocess. Inside that
session the agent may read and write files, run commands, and reach the network — bounded by
the runtime's `permission_mode` and `cwd` and by the definition's `tools`.

A `model` runtime makes one model call through modelrt and carries no local authority; it
refuses any definition that declares tools rather than run it without them. Where that call's
data goes is decided by the modelrt registry — see modelrt/SECURITY.md.

## Trust boundaries

### 1. Config → agent authority

`compose` builds runtimes from `matrix.runtimes.<name>` and binds agents from
`matrix.agents.<name>`. Every authority-bearing value — `permission_mode`, `cwd`,
`setting_sources`, `plugins`, a definition's `tools` — arrives from config: a `matrix:`
section, an ix `experiment.yaml`, a markdown agent file.

**A matrix config file, and every agent file in a `definitions` directory, is a capability
grant. Review them like one.**

What constrains it: each runtime's options are validated through its own typed config
(`register_typed`), with unknown keys rejected. `permission_mode` is a `Literal`, so an
unrecognised mode fails at composition naming the legal set. Selecting `bypassPermissions`
or `dontAsk` logs at WARNING, so a permissive run is visible in the output, not only in the
config. Relative plugin paths are resolved against the runtime's `cwd`, never the process's.

What does not constrain it: nothing restricts `cwd`. A config naming `cwd: /` gets `/`.

### 2. Ambient environment → session behaviour

The `claude-sdk` runtime pops `CLAUDECODE` from `os.environ` for the duration of a call and
restores it afterwards. This is process-global mutation (M-3).

`setting_sources: []` is the hermetic setting — no ambient `~/.claude` or project plugin
config leaks into the subprocess. Any evaluation that claims reproducibility should set it.

### 3. Declared reads

Each DAG component is handed a view of the ledger restricted to its `requires`. A component
that reads an undeclared kind raises `ContractError`. This is a correctness boundary, not a
trust boundary: a component is in-process Python and can reach anything the process can.

## Findings

### M-1 — `permission_mode` defaulted to `bypassPermissions` (fixed 2026-08-17)

The Claude adapter took `permission_mode: str = "bypassPermissions"` and no caller overrode
it, so every agent-backed component ran with permissions bypassed — a decision made by a
constant rather than by configuration. Now `"default"`; bypass must be requested explicitly
and is logged at WARNING.

### M-2 — an empty tool list granted the full toolset (fixed 2026-09-24)

The Claude adapter passed `tools=self._allowed_tools or None`. `[]` — the value meaning *no
tools* — is falsy, so it collapsed to `None`, which the SDK reads as *the default toolset*.
An agent configured with no tools was given all of them.

Fixed: `AgentDefinition.tools` distinguishes `None` (runtime default) from `()` (no tools),
and the runtime passes `()` as `[]`, which the installed SDK (0.2.139) documents as "disable
all built-in tools". Regression tests: `test_claude_runtime.py::TestToolsNeverFailOpen`.

### M-3 — `CLAUDECODE` is mutated process-wide (open)

Concurrent `claude-sdk` runs in one process race on the variable. Serialise SDK runs within
a process. ix's Inspect engine defaults `max_samples: 1` for this reason.

## Not covered

- matrix does not sandbox agent sessions. Isolation is the caller's job — a container, a
  scratch `cwd`, or a definition with `tools: []`.
- matrix does not redact agent output. An agent that reads a secret and prints it puts that
  secret in an `Artifact` and an `AgentResponse`.
- Entry-point discovery (`matrix.components`) imports every installed extension at
  composition. Installing a package is trusting it.

## Reporting

Open an issue on the gnx repository. There is no separate embargo channel.
