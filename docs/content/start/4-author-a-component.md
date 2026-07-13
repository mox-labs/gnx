---
title: Authoring a component
section: start
mode: explanation
status: planned
register: public
fidelity: tarmac
---

# Authoring a component

Authoring is how the suit gets extended — the concrete end of the loop [what gnx is](/docs/what-is-gnx) opens with. When the catalog has nothing that fits, an agent builds the missing piece, and what it builds folds back into the catalog **through you**. The *through you* is the honest limit: the loop runs through human governance; it does not close on its own. The steps are scaffold → declare → validate → build → register, and what clears them becomes part of the catalog — discoverable by every agent that searches after.

The tooling in this loop (`gnx component init`, `gnx validate`, `gnx build`) is **designed**; this page describes the contract it will hold, not a path you can run today. The runnable path today is [installing a plugin](/docs/install-a-plugin).

---

## What makes a component a component

The catalog exists so agents can compose capability from declared surfaces alone — without running a component to learn what it does. That only works if every component's manifest tells the whole story from the outside. Two properties are load-bearing:

**Standalone value.** A component has to do real work installed on its own, not only alongside the system it was born in — the premise the catalog rests on ([why a catalog](/docs/why-a-catalog)). Its discovery tags are what an agent searches when composing; if those tags only make sense inside one specific system, the value is relational, not standalone.

**Agent-operability.** Every `kind: Capability` must ship its embedded skill inside the artifact, expose `--skill` to emit it, and point `relations["skills"]` at a surface that travels inside the artifact. A Capability without an embedded skill is opaque to an agent, and will not clear the curation bar.

These are not editorial preferences — the designed `gnx validate` checks both before the registration gate.

---

## The scaffold

`gnx component init [kind]` produces a kind-aware tree:

- `manifest.yaml` — the component's declaration; what the registry reads
- `SKILL.md` — the embedded skill; what an agent reads to drive the component
- `tests/` — a test harness seeded for the component's kind

The scaffold is kind-aware because the kinds differ structurally. A `Capability` template carries an implementation stub and a `--skill` entrypoint; a `Skill` template omits both, because a Skill carries no transport — a Skill with a transport declared is a type error, not a lint.

---

## The manifest

A component's manifest is slick's five fields plus gnx's small overlay, written as flat YAML:

| Field | What it carries |
|-------|-----------------|
| `type_url` | Global identity — dotted `<namespace>.<version>.<resource>`, kebab-case resource, e.g. `gnx.dev.v1.review-panel` |
| `source` | Where the component's implementation lives |
| `provides` | What the component offers — typed ports *and* discovery tags, told apart by shape (a `type_url` is a port; a dotted-lowercase phrase is a tag) |
| `requires` | Input ports — the typed surfaces this component depends on |
| `relations` | Named pointers to related surfaces; for a Capability, `relations["skills"]` points at the embedded skill inside the artifact |
| `kind` *(gnx overlay)* | `Capability / Agent / Skill / Flow` — or an open literal when the behaviour genuinely differs; carries type-level consequence, not just a label |
| `maturity` *(gnx overlay)* | `shipped` gates projection to an installable plugin; a lower value keeps the component honest about not being ready |

A few things follow structurally from `kind`:

- A `Skill` is `provides`-only — it declares tags and no ports, because it is read into reasoning, not wired into a graph.
- A `Capability` that does not expose `--skill` does not pass `gnx validate`.
- A core (`gnx.dev`) component must carry no runtime-specific fields — runtime-specific semantics belong under that runtime's namespace.

A component with no `provides` at all is invisible to composition, and has no reason to be in the catalog.

Do not reach for an `apiVersion` / `metadata` / `spec` envelope — the manifest is flat. The identity grammar (the dotted, kebab-case `type_url`) and the shape rule (ports vs tags) are settled; whether topology ports ever get their own separate fields is deferred to the first component the shape rule cannot serve. The authoring loop holds regardless.

---

## Validation

`gnx validate` runs before registration and enforces the structural rules above: manifest well-formedness, a valid `type_url`, port satisfaction, the kind rules, the embedded-skill requirement for Capabilities. It is **static** — it parses and checks; it never imports or executes the candidate's code. What passes `validate` is what the registry accepts. Correctness of *structure* is enforced at this boundary — not at runtime, and not by convention. The [CLI reference](/docs/cli-reference) is the contract.

---

## Build

`gnx build` takes authored source and projects an installable plugin: a self-contained plugin directory, a `plugin.json` inside it (whose version is Claude Code's install cache key), and the marketplace entry Claude Code reads to enumerate what is available. Both generated files come from one source, so drift between them is structurally impossible — one write generates both.

Projection happens at authoring time, not install time, because of how Claude Code installs: it copies the plugin subdirectory into a versioned cache, bans path traversal out of that directory, and runs no install-time build hook. Whatever sits in the plugin subdirectory at clone time is what Claude Code sees — so the directory must be self-contained before it is ever installed, and the built output is committed. `gnx build --check` re-generates and diffs; if committed output drifts from what build would produce, CI fails. Single-source-of-truth becomes a structural property, not a promise.

The two plugins installable today are exactly this: committed projections of two catalog components. The `gnx build` command that would regenerate them is the designed tool.

---

## Registration

The catalog is human-curated. A submission goes through curation review before the registry is updated. The bar is a passing `gnx validate`, a committed `gnx build` output, and — for Capabilities — a verified `--skill` surface. The submission mechanics beyond that bar are not yet specified.

**Registration and accreditation are separate axes.** Passing validation gets a component into the catalog as a structurally sound entry. **Accreditation** is a trust status minted on top — by a person, never self-serve, never automatic with registration. A component can be registered and unaccredited; what it cannot be is accredited by the agent that authored it. Why structure and trust stay on separate axes — machine-checked versus human-minted — is [why a catalog](/docs/why-a-catalog)'s argument.

---

## What remains open

- **The overlay contract** — the legal `kind` and `maturity` values, the projection gate, and the `version` field are proposed, not yet enforced.
- **Identity minting** — the catalog mints a new component's `type_url` under its own namespace; the registration mechanics that assign it are not yet built.
- **Registration flow** — how a validated component moves from submission to the registry, including where the authoring loop runs relative to the catalog repository, is unspecified.
- **Curation refusal and accreditation** — what a refusal looks like, and how accreditation is later requested, is not yet specified.

What you register, the next search reaches. The catalog grows through this loop — one component at a time, each one narrowing the gap an agent has to fill by itself.

## Where to go next

- **[gnx CLI reference](/docs/cli-reference)** — the canonical contract for every command this loop uses.
- **[Grammar reference](/docs/grammar-reference)** — the manifest fields, kind rules, and identity grammar your component declares against.
