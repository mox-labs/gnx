# The public surface

The boundary: what gnx says publicly, and what it defers. This is the artifact that feeds
GTM work beginning the week of 2026-06-15.

---

## gnx ships standalone — before samsara, geist.sh, or Treya surface

The mox universe ships in phases. geist.sh is partial. samsara is designed but not public.
Treya is named. gnx is the first component of the catalog layer with enough surface to
ship standalone.

Standalone value is the positioning premise (§11, 2026-06-12). gnx is useful without the
rest of the ecosystem:

- A dev can install the catalog's plugins into Claude Code today, through Claude Code's own
  marketplace mechanism — the cix family ships now; the gnx marketplace projection is the
  planned entry point.
- `gnx init` (planned) sets up a project with a curated agent + skill set, through conversation.
- `gnx build` (planned) projects component sources into installable plugin dirs.
- `gnx search` and `gnx inspect` (planned) give Claude a legible discovery surface over the catalog.

None of these require geist.sh running, samsara inhabited, or Treya instantiated.

---

## Two readers, one bounded scope: component registry pragmatics

The public gnx docsite has two audiences (§11, §2, §3):

**User-facing (developer):** A dev who wants to use Claude Code more effectively. They
install plugins, initialize projects, author components, register work. Their mental model
is: package manager + project scaffolder, but for agent capabilities.

**Agent-facing (Claude Code):** Claude Code reads `gnx --skill` to understand what the
tool does and how to drive it. It calls `gnx search`, reads `inspect` output, and decides
whether to compose two components from their declared surfaces alone. The agent-facing docs
are not tutorial prose — they are the `--skill` surface and whatever the manifest's
`provides` field surfaces to Dagstra-style search.

Both audiences are bounded to the same domain: **component registry / marketplace
pragmatics**.

---

## Public docs cover the pragmatic loop and the legible boundary — nothing else

### User-facing docs are the pragmatic loop: install, init, compose, build

| Topic | Commands / artifacts | Notes |
|---|---|---|
| Add the marketplace | `~/.claude/settings.json` marketplace entry | Screenshot / config snippet |
| Install components | `gnx add <component>` | Also `gnx rm`, `gnx update` |
| Initialize a project | `gnx init` | SDK interview → scaffold `claude + dao + .gnx/` |
| Search and inspect | `gnx search <tag>`, `gnx inspect <component>` | What the catalog surface looks like |
| Compose components | selecting + wiring via `provides`/`requires` | Keep at usage level; no DAG topology internals |
| Build your own | `gnx component init [kind]` → manifest → `gnx validate` → `gnx build` → `gnx` registration | The authoring loop end-to-end |

The authoring loop deserves a worked example — not a hypothetical. Something like:

```
gnx component init capability    # scaffolds manifest.yaml + skill/SKILL.md + tests/
gnx validate                     # strict gate before registration
gnx build                        # writes plugin/ dir and marketplace.json entry
# submit to catalog: [open — registration flow not yet designed]
```

### Agent-facing docs are the legible boundary: what Claude reads at runtime

The agent-facing "docs" are partly not docs at all. They are the outputs Claude reads at
runtime:

- `gnx --skill` → emits complete SKILL.md (activation frontmatter + intent→command table).
  This is gnx's own front door to Claude Code. It must exist and be correct.
- `gnx search <tag>` → structured output over the `provides` index. Format TBD but must
  be machine-parseable, not prose.
- `gnx inspect <component>` → manifest surface + capability description. Must answer the
  question: can a capable reasoner decide to compose this component with another, from
  this output alone?

The public docsite documents what these outputs look like — their structure and
interpretation — so a developer authoring components knows what Claude will see.

---

## Cosmology is deferred — one load-bearing sentence maximum, then a link

These topics are load-bearing for the full architecture but are not user-actionable at
GTM. They get at most one sentence + a link out to design docs or the research corpus.

| Deferred topic | Why deferred | Treatment |
|---|---|---|
| samsara (the world-layer) | Not shipped; not required to use gnx | One sentence: gnx components are the vocabulary of a coordination layer; link to research |
| Treya (ilm · kalā · kriya) | Framework not yet public | Omit entirely until a Treya-bearing surface ships |
| geist.sh / matrix runtime | Partial; gnx is upstream of execution | One sentence: components execute inside geist.sh; link to geist docs when available |
| Accreditation mechanics | Design open (§10 Q1); not user-visible at GTM | Name that the catalog has a curation model; details deferred |
| DAG validation / SCC / leashes | Internal to Flow validation; authors need `gnx validate` not the theory | Surface the gate, not the mechanism |
| The recursion problem / exogenous anchor | Architectural doctrine; not actionable | [open] — candidate for one load-bearing sentence in a "why gnx" section |
| Produces/consumes vs provides semantics | Pending slick Mission C / S9 | Surface `provides` as the discovery field; don't expose the topology fields until stable |
| dao-corpus bindings | Expertise wing; not yet componentized | Reference as "domain expertise components" when describing the catalog's scope |
| Portability classes (Universal/Specialized/Vendor-Specific/Multi-Vendor) | Registry-computed, not user-declared | [open] — may be worth one sentence in the authoring guide |

The rule from §11: **"at most load-bearing sentences + links out."** A load-bearing
sentence is one the reader needs to avoid a wrong model, not one that opens a conceptual
door they have to walk through.

Example of a load-bearing sentence (draft):

> gnx components are the vocabulary of a broader coordination layer; the catalog is
> designed to be runtime-neutral so components work across execution environments.

Example of what is NOT a load-bearing sentence (do not publish):

> gnx is the vocabulary layer of the mox triad, whose execution organ is geist.sh, which
> governs agents stigmergically through samsara's append-only ledger of typed marks.

---

## Every public sentence must be actionable without ecosystem context

The failure mode is: a dev lands on the gnx site, reads about "the inhabited Construct" or
"Treya's ilm · kalā · kriya", and concludes that gnx is a research project, not a tool
they can install today.

The test for any candidate public sentence: can a developer who has never heard of mox
read this sentence and still know what to do next? If not, it belongs in the internal
design docs or the research corpus, not the public surface.

This constraint is symmetric for agent-facing content: `gnx --skill` output and `gnx
inspect` output must be actionable by Claude Code without context about samsara or the
world-ontology. The skill surface must be self-contained.

---

## The register split: what goes public, what stays internal

```
PUBLIC docsite (both user + agent facing)
  ├── Add marketplace
  ├── Install / rm / update components
  ├── gnx init (project genesis)
  ├── gnx search / inspect (discovery)
  ├── Compose: selecting and wiring components
  ├── Build your own: component init → validate → build → register
  ├── gnx --skill (agent entry point, documented as such)
  └── [load-bearing sentences + links] for: catalog curation model,
      runtime-neutral design intent, execution environment

DESIGN docs (this series, 01–10)
  ├── Full ecosystem triad (slick / gnx / geist.sh)
  ├── samsara world-model, stigmergic coordination
  ├── Treya (ilm · kalā · kriya)
  ├── The gnx loop (bodhi → dagstra → HADES → autopoietic closure)
  ├── Accreditation and produce-authority design
  ├── DAG/SCC/leash validation mechanics
  ├── Recursion problem and exogenous-anchor doctrine
  ├── Intent-hardening gradient (structural/behavioral/semantic tiers)
  └── All open questions in §10 of ground-truth.md

RESEARCH corpus (~/mox/research/, dao-corpus, system-model)
  └── Lineage, why decisions were made, REPs, formal models
```

---

## Open questions

The purpose of this doc is to be argued with. GTM content decisions should flow from
a resolved version of this list.

1. **The exact load-bearing sentences.** This doc drafts one example. The actual
   sentences need to be written and reviewed before any public page is finalized —
   they are the only cosmology-to-public bridge and they can go wrong in both
   directions (too much → confuses; too little → wrong model).

2. **The registration flow.** "Submit to catalog" in the authoring loop is marked
   `[open]`. The worked example for "build your own" cannot be complete without it.
   Is there a self-service registration path at GTM, or is it curator-gated?

3. **Agent-facing output formats.** `gnx search` and `gnx inspect` output format is
   TBD. The public docs need to document these formats; they can't be written until
   the formats are decided. Is this a GTM blocker or do the docs trail the implementation?

4. **Portability classes.** Registry-computed, not user-declared — but a dev authoring
   a component will want to know what class their component will land in. Is this
   surfaced in `gnx inspect` output at GTM? If yes, it belongs in the authoring guide.

5. **The "why gnx" section.** There may be value in one public-facing paragraph that
   names gnx's position without opening the cosmology. The recursion problem / exogenous
   anchor is a compelling "why" — but it is also the most cosmology-adjacent thing in the
   doctrine. Can it be stated at the level of "the catalog is human-curated so Claude can
   trust what it installs" without misleading?

6. **GTM sequencing.** Does the public site ship with all six user-facing topics, or
   does it launch with a subset (add marketplace + install + init) and add authoring
   docs as the registration flow hardens?

Invite comment on specific blocks — the in/out table and the deferred-topic table are the
load-bearing cells; those are where the line gets drawn.
