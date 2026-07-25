---
title: gnx CLI reference
section: start
mode: reference
status: planned
register: public
fidelity: tarmac
---

# gnx CLI reference

The commands an agent uses to work the catalog — find capability, compose components, scaffold new ones, validate before registering, project before shipping. The whole surface here is **designed**: a prototype exists, but it does not yet run the catalog. This page is the contract the tool holds to. Per-capability maturity lives in [what's real vs planned](/docs/status).

---

## `gnx --skill`

Emit a complete `SKILL.md` to stdout so Claude Code knows what `gnx` does and how to drive it.

```markdown
---
name: gnx
description: Discover, compose, and author components
---

| Intent | Command |
|--------|---------|
| set up a project | gnx init |
| find a capability | gnx search <tag> --json |
| inspect a component | gnx inspect <component> --json |
| install a component | gnx add <component> |
| remove / update a component | gnx rm / gnx update |
| scaffold a new component | gnx component init [kind] |
| validate before registering | gnx validate |
| project target surfaces | gnx build |
```

The intent table is generated from the command tree, so the skill cannot drift from the surface it describes — the same discipline a generated `plugin.json` follows. Claude Code reads this file to drive the tool; it cannot read a man page. Every `kind: Capability` in gnx must expose `--skill`, and `gnx validate` enforces that rule — `gnx --skill` is gnx meeting its own requirement on itself.

**Output.** Printed to stdout. No side effects.

---

## `gnx init`

Run a curated-selection interview via the Claude Agent SDK, then scaffold a project:

| Directory | What lands there |
|-----------|-----------------|
| `claude/` | `CLAUDE.md` — the agent constitution, seeded with project context |
| `dao/` | The project's agentic organization — charter and the agent roles that review the work |
| `.gnx/` | Installed components and projection state |

It works from a bare terminal before Claude Code is configured, and equally as a tool Claude Code drives inside an existing project — the caller changes, the behaviour does not.

The agentic phase ends at a **proposal**. Deterministic code installs what the interview agreed on; the SDK session never writes to the registry directly, and these roles never swap. That split keeps the catalog auditable: no non-deterministic step sits on the registration path.

**Inputs.** The interview elicits project context; the SDK session itself needs Anthropic credentials and network, and their absence is a declared failure (a named exit code and hint), not a mystery. `gnx init --defaults` is the deterministic, interview-free path — the interview is the human courtesy, the flags are the agent contract.

---

## `gnx component init [kind]`

Scaffold a new component. Kind-aware: the files written depend on what the component is.

**Kinds.** `capability` | `agent` | `skill` | `flow`. Omitting `kind` prompts interactively.

| File | Purpose |
|------|---------|
| `manifest.yaml` | Manifest stub — the five slick fields plus the gnx overlay (`kind`, `maturity`) |
| `SKILL.md` | Embedded skill stub — activation frontmatter + intent→command table |
| `tests/` | Test stubs, kind-appropriate |

A `Capability` scaffold includes an implementation stub and a `--skill` entrypoint; a `Skill` scaffold omits both, because a Skill carries no transport. Registration happens later, after `gnx validate` and `gnx build`. This command only scaffolds.

**Output.** A directory tree rooted at the component name, in the current working directory.

---

## `gnx add` / `gnx rm` / `gnx update`

Install, remove, and update components from the catalog. None of these modify the registry — they install from it.

| Flag | Scope | Writes to |
|------|-------|-----------|
| (default) | project | `.gnx/` in the current project |
| `--user` | user | the user-scoped gnx data dir |

**`gnx add <component>`** — installs into the current scope. Resolves `requires` / `provides` ports to confirm the component fits the current installation before it lands. How a component in `.gnx/` becomes active in Claude Code is unspecified; today activation runs through the marketplace + `enabledPlugins` path.

**`gnx rm <component>`** — removes from the current scope. Does not touch the registry.

**`gnx update [component]`** — updates a **floating** install to the latest catalog version. A **pinned** install (set by `add --pin`) is held at its commit and reports so; overriding a pin is explicit. The installed commit is always recorded separately, as provenance.

---

## `gnx search` / `gnx inspect`

Agent-facing discovery — output is structured and machine-parseable, not prose.

**`gnx search <tag>`** — queries the discovery surface, returning components whose `provides` tags match. Claude Code reads this to decide what to compose.

**`gnx inspect <component>`** — returns the full manifest surface for a named component: `type_url`, `source`, `requires`, `provides`, `relations`, and a portability signal the registry computes from the namespaces the component touches — never a value the author declares.

The test for `inspect` output: can a capable reasoner decide whether to compose this component with another, from this output alone? If not, the output is insufficient.

**Output format.** JSON is the contract — one document on stdout, diagnostics on stderr, no ANSI. Every command supports `--json` and emits a versioned envelope carrying `ok`, `data`, and structured `errors` (each with a `code`, `message`, `path`, and `hint`). On a TTY without `--json`, a human-readable table renders instead; the JSON path is the one agents are taught.

---

## `gnx validate`

The strict registration gate. Ambiguous manifests are rejected, not inferred.

| Check | What it enforces |
|-------|-----------------|
| Manifest well-formedness | Required fields present; a valid `type_url` under the identity grammar |
| Shape rule | `provides` entries resolve as ports (typed) or tags (dotted-lowercase) with no third case |
| Port satisfaction | `requires` entries have a matching provider in the catalog |
| Kind rules | A `Skill` declaring a transport fails; a `Capability` without `--skill` fails |
| Namespace scope | Core stays in `gnx.dev`; runtime-specific components under their own namespace |
| Embedded skill | Every `kind: Capability` exposes `--skill` |

Validation is **static** — it parses; it never imports or executes the candidate. Checks needing only the component and local roots run offline; catalog-dependent checks (port satisfaction against a registry) report **skipped, with the reason** when no catalog is reachable — never a silent pass.

**Inputs.** Path to a component directory; defaults to the current directory. **Outputs.** On pass, a one-line summary or `{"ok": true, ...}` under `--json`. On fail, a non-zero exit with one structured error per failed check — `code` names the rule, `hint` says how to fix it.

**When to run.** Before `gnx build` — and `gnx build` itself validates every source component before projecting, which is why `gnx build --check` covers both in CI.

---

## `gnx build [--check]`

Project installable plugin directories from one source. One source, two generated manifests — version drift between them is structurally impossible. A **target** is a runtime surface the catalog projects into; Claude Code is the only target that ships today.

For each plugin definition, `gnx build`:

1. Resolves dependencies
2. Writes a self-contained plugin directory under `plugins/`
3. Generates `plugin.json` inside that directory
4. Generates (or updates) the root marketplace entry

**`--check`** — a dry-run for CI. Verifies that committed generated files match what `gnx build` would produce, exiting non-zero on drift. Run after every authored change.

Plugin directories are generated and committed — not built at install time. Claude Code copies the plugin subdirectory in isolation, bans `..` traversal, and runs no install-time build hook, so the directory must be self-contained before it is ever installed.

| Path | What it is |
|------|-----------|
| `plugins/<name>/` | Self-contained plugin directory, one per plugin |
| `plugins/<name>/.claude-plugin/plugin.json` | Generated plugin manifest |
| `.claude-plugin/marketplace.json` | Generated root marketplace manifest |

---

## Where to go next

- **[Authoring a component](/docs/author-a-component)** — the workflow these commands serve, end to end.
- **[Grammar reference](/docs/grammar-reference)** — the manifest fields and kind rules `gnx validate` enforces.
