---
title: gnx CLI reference
section: start
mode: reference
status: planned
register: public
fidelity: tarmac
---

# gnx CLI reference

**Status: planned.** The gnx CLI is designed, not yet built. This page describes the designed command surface — what each command does, what it accepts, what it emits. Every entry is the intended contract, not current behavior. Nothing here runs today.

---

## `gnx --skill`

Self-describe to agents.

**What it emits.** A complete `SKILL.md` — activation frontmatter plus an intent→command table. Claude Code reads this file to know what `gnx` does and how to drive it. Every command in this reference surfaces in that table. The designed shape:

```markdown
---
name: gnx
description: Discover, compose, and author cognitive extensions
---

| Intent | Command |
|--------|---------|
| set up a project | gnx init |
| find a capability | gnx search <tag> |
| inspect a component | gnx inspect <component> |
| scaffold a new component | gnx component init [kind] |
| validate before registering | gnx validate |
| project Target surfaces | gnx build |
```

**Why it exists.** The house pattern: every `kind: Capability` registered in gnx must ship its embedded skill and expose `--skill`. Claude Code cannot read a man page. It reads a skill. A Capability that cannot self-describe is not agent-operable and does not clear the curation bar. gnx enforces this rule on every Capability it registers — and satisfies it on itself.

`gnx --skill` is gnx's own front door to Claude Code. It must exist and be correct before any other command is agent-operable.

**Output.** Printed to stdout. No side effects.

---

## `gnx init`

Genesis: set up a project through conversation.

**What it does.** Runs a curated-selection interview via the Claude Agent SDK, then scaffolds three things:

- `claude/` — the agent layer: a `CLAUDE.md` constitution seeded with project context
- `dao/` — the agentic organization: charter, guild roles, ratchet
- `.gnx/` — the tooling layer: installed components, projection state

It installs a curated component set — chosen through conversation, not from a fixed menu.

**Two modes, same binary.** `gnx init` works from a bare terminal before Claude Code is configured. It works equally as a tool driven by Claude Code from inside an existing project. The caller changes; the behavior doesn't. The Agent SDK handles the interview in both modes.

**What it does not do.** It does not write to the gnx registry. The init session proposes a component selection; deterministic code installs what was agreed. The agentic phase ends at the proposal.

**Inputs.** None required. The SDK interview elicits everything.

**Outputs.**
- `claude/CLAUDE.md` (constitution)
- `dao/` (org structure)
- `.gnx/` (installed components and projection state)

---

## `gnx component init [kind]`

Scaffold a new component.

**What it does.** Creates the directory structure and starter files for a new component of the given `kind`. Kind-aware: a `Capability` scaffold includes an `impl/` stub and a `--skill` entrypoint; a `Skill` scaffold omits them (Skills have no transport; a Skill declaring one is a representable type error).

**Kinds.** `capability` | `agent` | `skill` | `flow`. Omitting `kind` enters a brief prompt.

**What it writes.**

| File | Purpose |
|------|---------|
| `manifest.<yaml\|json>` | Slick manifest stub — `type_url`, `source`, `requires`, `provides`, `relations`. The on-disk format is not yet fixed (manifest.yaml leaning, JSON-aligned underneath). |
| `skill/SKILL.md` | Embedded skill stub — activation frontmatter + intent→command table |
| `tests/` | Test stubs, kind-appropriate |

**What it does not do.** It does not register the component. Registration happens after `gnx validate` and `gnx build` confirm the component is well-formed.

**Inputs.** `[kind]` — optional positional argument. One of the four kinds.

**Outputs.** A directory tree rooted at the component name, in the current working directory.

---

## `gnx add` / `gnx rm` / `gnx update`

Install, remove, and update components.

**Scope.** Two install scopes:

| Flag | Scope | Where it writes |
|------|-------|----------------|
| (default) | project | `.gnx/` in the current project |
| `--user` | user | user-scoped gnx data dir |

**`gnx add <component>`**

Installs a component from the catalog into the current scope. Resolves `requires`/`provides` ports to confirm the component is compatible with the current installation. Writes component files into `.gnx/` (project) or the user data dir.

**`gnx rm <component>`**

Removes a component from the current scope. Does not touch the registry.

**`gnx update [component]`**

Updates a component to the latest catalog version. Without an argument, updates all installed components.

**What none of these do.** They do not modify the gnx registry. They install from it.

---

## `gnx search` / `gnx inspect`

Agent-facing discovery.

**`gnx search <tag>`**

Queries the `provides` discovery surface. Returns components whose declared `provides` match the query. Output is structured and machine-parseable — not prose. Claude Code reads this output to decide what to compose. Discovery is how an agent finds what's available before deciding to compose.

**`gnx inspect <component>`**

Returns the full manifest surface for a named component: `type_url`, `source`, `requires`, `provides`, `relations`, and the registry-computed portability class (Universal / Specialized / Vendor-Specific / Multi-Vendor — the last still future). Structured output.

The test for `inspect` output: can a capable reasoner decide to compose this component with another, from this output alone? If not, the output is insufficient.

**Output format.** TBD; decided before the CLI ships. Machine-parseable is the hard requirement.

---

## `gnx validate`

Strict registration gate.

**What it checks.** Given a component directory (or the current directory), validates:

| Check | What it enforces |
|-------|-----------------|
| Manifest well-formedness | Required fields present; valid `type_url` structure |
| Port satisfaction | `requires` entries have a matching `provides` in the catalog |
| Kind rules | Kind carries type consequences — a `Skill` with a transport fails; a `Capability` without `--skill` fails |
| Namespace scope | `slick.dev/v1` for core; vendor-namespaced extensions under their own `apiVersion` |
| Embedded skill | Every `kind: Capability` must expose `--skill` |

**Strict, not forgiving.** Validate does not accept an ambiguous manifest and infer intent — it rejects it. Strictness at the boundary is the property that makes the catalog trustworthy as a composition surface.

**Inputs.** Path to a component directory. Defaults to current directory.

**Outputs.** Pass/fail with a specific message per failed check. Zero output on pass.

**When to run.** Before `gnx build`. In CI, via `gnx build --check`.

---

## `gnx build [--check]`

Generate installable plugins from one source — write each plugin directory and its manifests from your authored component.

**What it does.**

For each component in the project's authored sources (`components/`, `extensions/`), `gnx build`:

1. Resolves dependencies
2. Writes a self-contained plugin directory under `plugins/`
3. Generates `plugin.json` inside that directory
4. Generates (or updates) the root `marketplace.json` entry

One source. Two generated manifests. The dual-manifest version drift that plagued cix is structurally impossible: version lives in one place; both manifests derive from it.

**`--check`**

Dry-run for CI. Verifies that committed generated files match what `gnx build` would produce. Exits non-zero on any drift. CI placement: after every authored change.

**Committed output.** Plugin directories are generated and committed — not generated at install time. Claude Code copies the plugin subdirectory in isolation, bans `..` path traversal, and has no install-time build hook. Self-contained at authoring time is the requirement.

**Inputs.** None required. Reads authored sources from the project root.

**Outputs.**
- `plugins/<name>/` — self-contained plugin directory, one per component
- `plugins/<name>/plugin.json` — generated
- `.claude-plugin/marketplace.json` — generated root marketplace manifest

---

## The two invariants behind the surface

**1. `--skill` is not a convention.** It is the operability gate. A `kind: Capability` without an embedded skill cannot be driven by Claude Code and does not clear the curation bar. `gnx validate` enforces it. `gnx --skill` demonstrates it on gnx itself.

**2. The ledger is deterministic.** SDK sessions — the `gnx init` interview, curation assistance — propose. Deterministic code validates and writes. These roles never swap. If the agent could write to the registry directly, gnx would become part of the loop it exists to break — it could no longer be the fixed outside reference point the system checks itself against. Dumb boundaries, smart interiors; intelligence never lives in the channel.
