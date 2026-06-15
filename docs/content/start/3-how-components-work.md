---
title: How gnx components work
section: start
mode: explanation
status: planned
register: public
fidelity: tarmac
---

# How gnx components work

**A component tells you what it is before you run it.** That declaration — its manifest — is what lets an agent decide whether two components compose, from their declared surfaces alone, without executing either. Read the manifest, know whether the pieces fit. That is what the grammar is for.

This page is the model an agent reads to compose — how to tell what a component is, whether two fit, and what each one promises about itself, before running anything.

**What ships, what's designed.** slick v0.2.0 ships the five-field manifest, in memory. The on-disk format and the `kind` / `apiVersion` fields are gnx's to define — planned. The grammar below is the designed shape the catalog will enforce, and the gnx behavior described here — validation, search, registry computation — is designed too. Where the shipped manifest and the designed model differ, this page says so.

---

## Four kinds — and the kind decides how it's used

Every component in gnx declares one of four kinds: **Capability**, **Agent**, **Skill**, or **Flow**. Knowing which one tells an agent how to use it — a Capability you run, a Skill you read, a Flow you expand, an Agent you delegate to. The kind is the first thing an agent reads off a component.

Underneath, `kind` is an open string, not an enum. The constraint is not an exhaustive list — it's type-level consequences. Each kind implies which fields are valid, which are required, and which are forbidden. A violation is a registration error, not a lint warning.

| Kind | What it is |
|------|------------|
| `Capability` | A runnable capability. Declares how it's reached; takes typed input and emits typed output. Must expose `--skill`. |
| `Agent` | A named reasoning role with a distinct method. |
| `Skill` | A `provides`-only axiom. Read by agents; no transport, no topology. Activated by being read, not wired. |
| `Flow` | A declared composition of other components. |

Four is the settled set.

---

## The manifest declares a component in five fields

The manifest is the five-field declaration an agent reads to answer *what is this, and does it fit?* — without running the component. The shipped slick (v0.2.0) manifest:

| Field | Type | What it encodes |
|-------|------|-----------------|
| `type_url` | string | What type this component is — the schema key, `<namespace>.<version>.<Resource>` |
| `source` | string | Where the component comes from |
| `requires` | []string | Input ports — type_urls this component depends on |
| `provides` | []string | Output ports — type_urls this component offers |
| `relations` | map[string][]string | Named pointers to associated surfaces — skills, docs, schemas — each name mapping to one or more targets |

Shipped: JSON, in-memory only. No `kind`, no `apiVersion`, no `metadata.name` in slick's current code.

gnx defines the on-disk format. The lean is `manifest.yaml` for authoring ergonomics, JSON-aligned underneath — not decided. When `gnx component init` scaffolds a new component, it will emit this file. The shape gnx writes is the shape the ecosystem reads.

A sketch of what the on-disk manifest adds over the shipped Manifest:

```yaml
apiVersion: slick.dev/v1
kind: Skill
metadata:
  name: craft-rhetoric
spec:
  provides:
    - rhetoric
    - comprehension-transform
    - three-doors
  relations:
    content:
      - skills/rhetoric/SKILL.md
```

`apiVersion`, `kind`, and `metadata` are the gnx additions. `spec.*` maps to the in-memory fields.

---

## Discovery and wiring are different questions

Before composing, an agent asks two questions, and the manifest answers them with different surfaces.

**"What can do X?"** is discovery. A component's discovery tags — *episodic-memory*, *rust-mastery*, *rhetoric* — are what an agent and `gnx search` match against. Answered without running anything.

**"Does its output fit the next component's input?"** is wiring. Typed **ports** — what a component takes as input, what it emits as output — are matched by the runtime when it builds a pipeline. Types, not tags.

In the shipped slick manifest these ports are `requires` (inputs) and `provides` (outputs). The designed model splits the two roles apart — `provides` for discovery, `produces` / `consumes` for typed topology — but the final field names are not fixed; they arrive in a future slick release. What *is* settled is the distinction: discovery and wiring are different surfaces, matched by different consumers at different times.

| Question | Surface (shipped → designed) | Consumer | When |
|----------|------------------------------|----------|------|
| What can do X? | `provides` → discovery tags | agents, `gnx search` | before composition |
| What's the input? | `requires` → `consumes` | runtime (matrix) | at build, wiring the pipeline |
| What's the output? | `provides` → `produces` | runtime (matrix) | at build, matching upstream |

A component whose discovery tags match a query can still fail to wire if its ports don't align with the graph. Discovery is necessary, not sufficient. A component with neither tags nor ports is invisible to both, and should not exist in the catalog.

---

## Skills are provides-only — by design, not omission

Skills carry no topology ports and no transport. This is not an incomplete manifest. It is the definition of the kind.

A Skill is activated by being read. An agent reads it and incorporates it into reasoning. There is no port-level output for the runtime to route. Giving a Skill produce/consume ports would be a representable type error: the kind forbids them.

A Skill is a fact an agent picks up: it matches on what it offers, gets read in, and shapes the reasoning — no wiring, no execution slot.

The consequence: a Skill enters any composition without consuming runtime resources. Mastery encoded once in a Skill's discovery surface is available to every composition that searches for it.

---

## Every Capability must expose `--skill`

A Capability that cannot describe itself to an agent is not agent-operable. gnx will enforce this as a registration rule: every `kind: Capability` must ship an embedded Skill and expose `--skill`.

`memex --skill` emits a complete SKILL.md: activation frontmatter, an intent-to-command table, what memex offers and how to drive it. Claude Code loads the skill and uses the tool. The embedded skill is what makes the Capability composable.

`relations["skills"]` in the manifest points to this surface. The skill travels inside the artifact, not in a separate registry entry.

The pattern is already live in cix, recon, and memex. A Capability registered in gnx without an embedded skill will fail the curation bar — the hard catalog rule.

---

## `apiVersion` encodes vendor scope; portability is derived, not declared

The namespace follows the Kubernetes CRD pattern:

| Scope | apiVersion |
|-------|------------|
| Core (slick) | `slick.dev/v1` |
| Claude Code hooks | `hooks.claude.anthropic.com/v1` |
| Claude Code commands | `commands.claude.anthropic.com/v1` |

A component in `slick.dev/v1` is core — no vendor semantics. A component in `hooks.claude.anthropic.com/v1` is Claude Code-specific. The namespace *is* the vendor scope.

Portability class — Universal, Specialized, Vendor-Specific, Multi-Vendor — will be **computed by the registry from the namespaces present in the manifest**. A component cannot self-declare portability; gnx will derive it at `gnx validate` time. A component that asserts "Universal" while depending on a vendor-specific hook is making a false claim — registry-computation will make that claim impossible by construction.

The practical consequence: the `components/` and `extensions/` directories are the filesystem encoding of this split. Anything under `extensions/claude-code/` is vendor-scoped; anything under `components/` is core. Move a vendor-specific field into a core component and the invariant breaks — and portability computation breaks with it, for every component that depends on it.

---

## Composition needs legible surfaces, not proof

Two components can compose if a capable reasoner can decide, from their declared surfaces alone, that composing them makes sense.

Not from running them. Not from reading their source. From the manifest.

Composition doesn't require formal proof of correctness. It requires legible surfaces. The discovery tags and typed ports are what make a surface legible. A manifest that declares `provides: ["session-memory"]` and a typed output port with a schema in `api/` is a surface a reasoner can reason from.

A manifest that says `provides: ["does stuff"]` with no typed ports is not. It fails the composition test — not because the component is wrong, but because the surface doesn't carry enough information to decide.
