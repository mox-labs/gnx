---
title: How gnx components work
section: start
mode: explanation
status: mixed
register: public
---

# How gnx components work

**An agent reads a manifest before it runs anything.** The manifest is how the agent decides whether two components compose, from their declared surfaces alone, without executing either. That is what the grammar is for.

The shipped grammar is **slick** (the `slickit` crate, v0.2.0): a five-field manifest, in memory. gnx adds a small on-disk layer on top of it. Where the shipped grammar and the fuller gnx model differ, this page says so per claim.

---

## The manifest declares a component in five fields

The five-field slick manifest is the whole declaration an agent reads to answer *what is this, and does it fit?* — without running the component. **shipped:**

| Field | Type | What it encodes |
|-------|------|-----------------|
| `type_url` | string | The component's identity — the single join key, `<namespace>.<version>.<resource>` |
| `source` | string | Where the component's implementation lives — a path or repository URL |
| `requires` | []string | Input ports — the typed surfaces this component depends on |
| `provides` | []string | What this component offers — typed output ports *and* discovery tags, told apart by shape (below) |
| `relations` | map[string][]string | Named pointers to associated surfaces — skills, docs, lineage — each name mapping to one or more targets |

That is the entire shipped type: JSON, in memory, five fields. There is no `kind`, no `apiVersion`, no `metadata`, and no transport field in slick's own code. The crate stores the manifest; it does not yet validate much beyond structure — a real validator is designed.

---

## gnx adds a thin on-disk overlay

slick ships no on-disk file convention. gnx defines one: the same five slick fields, written as flat YAML, plus a short overlay it owns. Two overlay fields are written in every real component today — `kind` and `maturity` — and a third, `version`, is proposed. This is the file you write by hand when you author a component: not the in-memory shape an agent parses, the shape you commit. Here is a real component as it sits on disk (comment header and two sibling tags trimmed):

```yaml
type_url: gnx.dev.v1.intent-hardening   # kebab-case resource
kind: Skill                             # gnx overlay
source: ./SKILL.md
provides:
  - intent.hardening                    # dotted-lowercase → a discovery TAG
  - intent.triguna-halt
requires: []
relations:
  uses:
    - gnx.dev.v1.rational-inquiry
maturity: shipped                       # gnx overlay
```

- **`kind`** names what the component is (below). It is written on disk today.
- **`maturity`** is the honesty field: `shipped` means it installs and does real work now; a lower value means the component is not ready to project as a plugin. It is written on disk today.
- **`version`** — a proposed third overlay field — would carry the content revision that flows into the installable plugin. It is not yet on disk.

The overlay is a gnx-side contract: the slick crate cannot see it, so gnx's own validator carries the whole burden of checking it. The exact legal values, the projection gate, and the `version` field are **proposed**, not yet enforced by any tool.

---

## The kinds — and the kind decides how it is used

Every component declares one kind. The kind tells an agent what to do with it — a Capability you run, a Skill you read, an Agent you delegate to, a Flow you expand.

| Kind | What it is | Ports | Skill |
|------|------------|-------|-------|
| `Capability` | A runnable capability — takes typed input, emits typed output | input + output ports | must expose `--skill` |
| `Agent` | A named reasoning role with a distinct method | not yet specified | — |
| `Skill` | A `provides`-only axiom — read into reasoning, not wired | none | is the skill |
| `Flow` | Composes other components as its members | derived from its members | — |

`Capability`, `Agent`, `Skill`, `Flow` are the current set. But `kind` is an **open literal, not a closed enum**: a component may declare a kind outside the four when its behaviour genuinely differs (one of the three real components declares `kind: Processor` — a comprehension processor whose typed topology is design-intent; its shipped manifest declares discovery tags only, `requires: []`). The constraint is not an exhaustive list; it is behavioural consequence. Each kind implies which fields are valid, required, or forbidden. A Skill that declared a transport would be a category error, the same as a verb in a noun slot — not a lint warning.

Because `kind` never appears inside the `type_url`, a component can be reclassified without breaking its identity. That is deliberate: the kind count is still open, and keeping it out of the join key keeps reclassification a cheap, reversible move.

---

## Discovery and wiring are different questions — told apart by shape

Before composing two components, an agent faces two distinct questions, answered by two different surfaces that share the `provides` field:

- **"What can do X?"** is **discovery**. Answered by tags — `intent.hardening`, `recon.search` — short dotted-lowercase phrases an agent and `gnx search` match against. No execution.
- **"Does its output fit the next component's input?"** is **wiring**. Answered by typed ports — matched by type, not by tag.

One rule tells them apart, and it is settled: an entry in `provides` that **parses as a `type_url`** (namespace + version + resource) is a **typed port** — it joins the composition. An entry that is **dotted-lowercase and has no version segment** is a **discovery tag** — it feeds search and never joins anything.

```yaml
provides:
  - gnx.dev.v1.recon-report   # parses as a type_url → a typed PORT (joins topology)
  - recon.search              # dotted-lowercase, no version → a TAG (search only)
```

This is why one field can carry both populations without confusion: the shape decides the role. Ports do topology; tags do findability; nothing ever joins on a tag.

| Question | Surface | Consumer | When |
|----------|---------|----------|------|
| What can do X? | `provides` tags | agents, `gnx search` | before composition |
| What's the input? | `requires` ports | a runtime | at build, wiring the graph |
| What's the output? | `provides` ports | a runtime | at build, matching upstream |

Discovery is necessary, not sufficient. A component whose tags match a query can still fail to wire if its ports do not align. A component with neither tags nor ports is invisible to both — and has no reason to be in the catalog.

---

## Skills are provides-only — by design, not omission

A Skill carries no ports and no transport. It is activated by being *read*: an agent reads it and folds it into its reasoning; there is nothing to route. Giving a Skill produce/consume ports would be the type error the kind forbids.

That is exactly why the two components that ship today — both Skills — carry tags alone and no ports. The shape rule makes that legible instead of looking like something is missing: for the skill class, ports genuinely do no work. A Skill is the only kind that enters a composition without consuming any runtime slot — encoded once, available everywhere its discovery surface matches.

---

## Every Capability must expose `--skill`

A Capability that cannot describe itself to an agent is not agent-operable. So gnx makes it a registration rule: every `kind: Capability` ships an embedded skill and exposes `--skill`, which emits a complete `SKILL.md` — activation frontmatter, an intent-to-command table, what it offers and how to drive it. Claude Code loads that skill and drives the tool from it.

`relations["skills"]` points at that surface, and the skill travels *inside* the artifact — not a URL, not a path out of the plugin directory. A Capability registered without an embedded skill fails the curation bar. This is the hard catalog rule, and gnx holds itself to it: `gnx --skill` is its own front door.

---

## A composition is itself a component

When an agent wires existing components together, what it produces is a **Flow** — and Flow is one of the kinds. So a composition is registrable on the same terms as any other component: it carries its own `type_url`, is declared through the same manifest, and can be curated back into the catalog and composed again. This is the structural sense in which what an agent composes *becomes* catalog — the loop closes through registration, not on its own.

How a Flow declares its members is **proposed**: a `members` list of `(type_url, optional config)` pairs, with the wiring **derived** from the members' ports rather than hand-declared.

```yaml
type_url: gnx.dev.v1.review-bench
kind: Flow
source: ./manifest.yaml
provides:
  - review.bench                        # a tag — findability
requires: []
members:                                # proposed — the composition root
  - type_url: gnx.dev.v1.rational-inquiry
  - type_url: gnx.dev.v1.intent-hardening
    config: { gradient: strict }
maturity: design
```

Members are declared; topology is computed. Treat this shape as designed, not shipped.

---

## Portability is computed, not declared

A component's namespace says who owns its semantics. Core components live in the vendor-neutral `gnx.dev` namespace; anything specific to one runtime lives under that runtime's namespace instead. How far a component travels is then **computed** from the namespaces present in its manifest — never a label the author claims. Move a runtime-specific dependency into a core component and the computation refuses to call it portable. The full classification is not yet specified; the principle — derived from structure, not asserted — is what [vendor-neutral by structure](/docs/vendor-neutral-by-structure) argues for.

---

## Composition needs legible surfaces, not proof

Two components compose if a capable reasoner can decide fit from their declared surfaces alone — the test [the primary reader is an agent](/docs/the-primary-reader-is-an-agent) owns. The manifest tells you what to compose, not that it is good; [why a catalog](/docs/why-a-catalog) draws that line.

## Where to go next

- **[Compose components](/docs/compose-components)** — the port model in use: wiring installed components together.
- **[Authoring a component](/docs/author-a-component)** — declare your own surface and take it through the registration loop.
- **[Grammar reference](/docs/grammar-reference)** — the same grammar as a lookup table, with each entry's maturity mark.
