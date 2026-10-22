# Security — matrix

matrix composes agents and runs components. It holds no secrets of its own and opens no
listening socket, but it is the package that **decides how much authority an agent session
gets**. That makes its defaults and its config validation, not its code paths, the
interesting surface.

Audience: anyone embedding matrix, and anyone reviewing a config that reaches it.

## Blast radius

A `claude-sdk` runtime launches a Claude Agent SDK session as a subprocess. Inside that
session the agent may read and write files, run commands, and reach the network — bounded by
the runtime's `permission_mode` and `cwd` and by the definition's `tools`.

A `model` runtime makes one model call through hardline and carries no local authority; it
refuses any definition that declares tools rather than run it without them. Where that call's
data goes is decided by the hardline registry; hardline's own security notes cover it.

## Trust boundaries

### 1. Config → agent authority

`compose` builds runtimes from `matrix.runtimes.<name>` and binds agents from
`matrix.agents.<name>`. Every authority-bearing value — `permission_mode`, `cwd`,
`setting_sources`, `plugins`, a definition's `tools` — arrives from config: a `matrix:`
section, an ix `experiment.yaml`, a markdown agent file.

**A matrix config file, and every agent file in a `definitions` directory, is a capability
grant. Review them like one.**

What constrains it: each runtime's options are validated through the config model it registered
with, with unknown keys rejected. `permission_mode` is a `Literal`, so an
unrecognised mode fails at composition naming the legal set. Selecting `bypassPermissions`
or `dontAsk` is a `permissive` warning in every document the `matrix` command prints, and is
logged at WARNING on `matrix.runtime` once per process, so a permissive run is visible in the
output, not only in the config. Relative plugin paths are resolved against the runtime's `cwd`, never the process's.

What does not constrain it: nothing restricts `cwd`. A config naming `cwd: /` gets `/`.

### 2. Ambient environment → session behaviour

The `claude-sdk` runtime does not touch `os.environ`. It used to pop `CLAUDECODE` for the
duration of a call (M-3); the installed SDK (`claude-agent-sdk >= 0.1.51`) now strips
`CLAUDECODE` from the child's own environment itself, so the runtime has nothing left to do
there — see M-3, fixed.

`setting_sources: []` is the hermetic setting — no ambient `~/.claude` or project plugin
config leaks into the subprocess. Any evaluation that claims reproducibility should set it.

### 3. Declared kinds

Each component receives `Inputs` holding only the rows of the kinds it requires, never the
run's Construct, and a row of a kind it does not provide, or one that fails its kind's schema,
raises `ContractError`. This is a correctness boundary, not a trust boundary: a component is
in-process Python and can reach anything the process can.

### 4. Events → an OpenTelemetry endpoint

When the `OTEL_*` variables ask for export, every run gets the `otel` observer, and its spans
go to whatever endpoint the environment names. So what a span carries leaves the machine.

By default a span carries no content: the GenAI conventions' attributes (agent, model,
provider, token counts, tool name and call id), matrix's statuses, reasons, error kinds,
counts, times and ids, and the numbers and booleans a component passes to `ctx.span`. It does
not carry an agent's task, any error message (as an attribute or as the span's status
description), or a component's string fields. `error.type` is always set on a failure.

Content leaves only when the config asks: `observers: [{type: otel, capture_content: true}]`.
That config is a grant to send prompts to the collector; review it like one. A `ctx.measure`'s
attributes are exported as the component gives them.

The local sinks are not filtered: the `jsonl` observer and a run's `events.jsonl` carry each
task and error message as emitted.

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

### M-3 — `CLAUDECODE` is mutated process-wide (fixed 2026-10-02)

The runtime used to pop `CLAUDECODE` from `os.environ` for the duration of a call, so
concurrent `claude-sdk` runs in one process raced on the variable. Closed: `claude-agent-sdk
>= 0.1.51` (the first release that strips `CLAUDECODE` from the child's own environment
itself, bisected against PyPI wheels) makes the mutation unnecessary, and the runtime no
longer touches `os.environ` at all. Concurrent `claude-sdk` runs in one process are safe
with that SDK version installed.

### M-4 — every event field was a span attribute (fixed 2026-10-22)

The `otel` observer copied every plain event field into span attributes: each agent's task as
`matrix.task`, error messages as `matrix.error` and as the span's status description, and
every field a component passed to `ctx.span`. Attached by the `OTEL_*` variables alone, it
sent prompts to the collector with nothing in the config saying so. Fixed: span attributes
come from an allowlist, and content needs `capture_content`. Regression tests:
`test_otel_observer.py::test_no_content_leaves_on_a_span_by_default` and
`::test_content_leaves_on_a_span_only_when_asked_for`.

## Not covered

- matrix does not sandbox agent sessions. Isolation is the caller's job — a container, a
  scratch `cwd`, or a definition with `tools: []`.
- matrix does not redact agent output. An agent that reads a secret and prints it puts that
  secret in an `AgentResponse`, which is recorded as a row and written to the run's store.
- Entry-point discovery (`matrix.extensions`) imports every installed extension at
  composition. Installing a package is trusting it.

## Reporting

Open an issue on the gnx repository, https://github.com/mox-labs/gnx/issues. There is no
separate embargo channel.
