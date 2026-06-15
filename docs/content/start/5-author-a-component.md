---
title: Authoring a component
section: start
mode: explanation
status: planned
register: public
fidelity: tarmac
---

# Authoring a component

**Build a capability once; make it composable by any agent that speaks the grammar — Claude Code today — in any project that declares it needs it.** That is what authoring a gnx component buys you — distilled work that travels, instead of capability trapped in the project that first built it.

**This loop needs the gnx CLI — planned, not yet shipped.** The shape below is the designed authoring path, so you know what building a component will look like when the CLI arrives. Nothing here is runnable today.

The loop: `gnx component init` → fill the manifest and skill → `gnx validate` → `gnx build` → registration.

---

## A component must stand on its own

A component earns its place in the catalog by being useful on its own. That is a design requirement, not a nicety.

Before authoring, hold the question: if someone installs only this component — not the others it relates to, not the full catalog — does it still do something? A Skill that needs context from five other Skills to say anything is not standalone. A Capability that only makes sense inside one Flow is not standalone either — it's a fragment of that Flow.

The discovery surface is where this shows. Discovery tags are what an agent searches. If the tags only make sense alongside other components in the same system, the value isn't standalone — it's relational. Author for the case where this is the only component the agent has.

---

## The scaffold gives you a manifest, a skill, and tests

`gnx component init [kind]` will scaffold a new component directory with three things:

- `manifest.yaml` — the component's declaration; this is what the registry reads
- `skill/SKILL.md` — the embedded skill; this is what an agent reads to drive the component
- `tests/` — a test harness seeded for the component's kind

The scaffold is kind-aware. A `Capability` template differs from a `Skill` template — different required fields, different port shapes, different curation requirements. The kind you pass to `component init` determines what you get.

The on-disk manifest format is `manifest.yaml` (leaning, not yet decided — the final field names resolve with slick's next grammar pass). The structure underneath is JSON-aligned.

---

## The manifest declares identity, ports, and relations

The shipped slick manifest has five stringly-typed fields. The gnx on-disk format adds `kind` and `apiVersion`:

| Field | What it carries |
|-------|-----------------|
| `type_url` | The globally unique identity — dot-delimited `<namespace>.<version>.<Resource>`, e.g. `slick.dev.v1.Aboot` |
| `source` | Where the component's implementation lives |
| `provides` | What the component offers as output — the typed surfaces it exposes; under the designed refinement these double as the discovery surface an agent searches |
| `requires` | The component's input ports — the typed surfaces it depends on |
| `relations` | Typed pointers to related surfaces, each a named list. For a Capability, `relations["skills"]` must point at the embedded skill that travels inside the artifact. |
| `kind` *(gnx on-disk)* | One of `Capability / Agent / Skill / Flow` — carries type-level consequences, not a label |
| `apiVersion` *(gnx on-disk)* | The vendor scope: `slick.dev/v1` for core; `hooks.claude.anthropic.com/v1` for Claude Code-specific kinds |

The shipped five fields are `type_url`, `source`, `requires`, `provides`, `relations`. `kind` and `apiVersion` are gnx's on-disk additions. The split that turns the topology ports into typed `produces` / `consumes` lands with slick's next grammar pass — the authoring loop holds regardless of the final names. Skills leave the topology ports empty; they only declare what they provide.

The namespace split is the portability split. `slick.dev/v1` is the vendor-neutral core — components here compose across any agent runtime that speaks the grammar. A vendor-specific kind, like a Claude Code hook, goes under its own apiVersion such as `hooks.claude.anthropic.com/v1`. Portability class — Universal, Specialized, Vendor-Specific — is computed by the registry from the namespaces in play; you don't declare it.

A component with no discovery tags is invisible to composition, and has no reason to exist in the catalog.

---

## gnx validate — the strict gate

`gnx validate` will run before registration, checking:

- **Manifest well-formed** — all required fields present, no unrecognized fields, `type_url` globally unique
- **Kind-appropriate ports** — a component declares typed ports, and the rules follow from its kind: a Skill declares only what it provides and carries no topology ports
- **Kind carries type consequences** — a `Skill` declaring a transport is a representable type error, not a warning; a `Capability` without `--skill` exposure does not pass
- **Namespace scope** — a `slick.dev/v1` component must carry no vendor-specific fields; vendor semantics belong in a vendor apiVersion

Validate is strict, not forgiving. The grammar boundary is where correctness is enforced — not at runtime, not by convention, not by code review. What passes validate is what the registry will accept.

---

## Every Capability must embed and expose a skill

Every `kind: Capability` registered in gnx must:

1. Ship its embedded skill inside the artifact
2. Expose `--skill` to emit it
3. Point `relations["skills"]` at a surface that travels inside the artifact — not a URL, not a path out of the plugin dir

A Capability without an embedded skill is not agent-operable. Claude Code reads a skill to drive a tool; it cannot read a man page. If the skill isn't there, composition fails at discovery. The curation bar enforces this — not optional for Capabilities, not waivable post-registration.

`gnx validate` will check for the `--skill` surface before the registration gate.

---

## gnx build — projection at authoring time

`gnx build` will take the authored component source and generate:

- a self-contained **plugin directory** under `plugins/` — the Target-side projection Claude Code installs
- a **plugin.json** inside it — the install manifest; the version here is the CC cache key
- an updated **marketplace.json** entry under `.claude-plugin/` — the discovery surface CC reads to enumerate available plugins

Both generated manifests come from one source. Dual-manifest version drift — where plugin.json and marketplace.json diverge over time — is structurally impossible when one write generates both.

Projection happens at authoring time because Claude Code's install mechanics leave no alternative. When CC installs a plugin, it copies only the plugin subdirectory into a versioned cache. No install-time build hook exists — there is no lifecycle event where gnx could generate files after install. What lands in the plugin subdir at clone time is what CC sees. The plugin dir must be self-contained; path traversal out of it is banned.

`gnx build --check` will re-generate and diff. If the committed output doesn't match what build would produce, CI fails. Single-source-of-truth becomes a structural property, not a convention.

---

## Registration is human-curated, not self-serve

The catalog is human-curated. Submitting a component is not a self-serve write to the registry — it goes through a curation review before the ledger is updated.

The registration flow is open design: how a component moves from a validated, built artifact to a registered catalog entry is not yet specified. What *is* specified is what the submission must contain — a passing `gnx validate`, a committed `gnx build` output, and for Capabilities a verified `--skill` surface — and what registration produces: an accreditation record on the append-only ledger, human-mintable only.

---

## Format, registration, and portability remain open

- **On-disk manifest format**: `manifest.yaml` is the lean; the final field names resolve with slick's next grammar pass. The authoring loop here holds regardless of the final filename.
- **Registration flow mechanics**: how a validated component moves to the registry ledger is not yet specified. The curation-bar requirements (validate passing, build output committed, `--skill` verified for Capabilities) are specified; the submission and review mechanics are open.
- **Portability classes** are registry-computed, not declared — the exact computation rules are not yet specified.
