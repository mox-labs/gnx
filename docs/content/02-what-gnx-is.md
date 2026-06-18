# What gnx is

> **Design register.** gnx is pre-build (NAMED — doctrine shaped, build starting). Present tense below states the design, not shipped behavior. What runs today: [status](/docs/status).

gnx is three things that share one catalog: a component **registry**, a **Claude Code marketplace**, and an **agentic CLI**. The three functions are access patterns on it.

## The registry answers one question: can these two compose without running them?

The registry holds components declared under slick manifests: validated at registration, accredited, indexed for search. Dagstra performs semantic proof-search over `provides` tags; Claude reads declared surfaces to decide whether two components compose.

Registration is strict, not Postel-liberal. A component without `provides` is invisible to composition search and doesn't belong in the catalog. Portability class (Universal / Specialized / Vendor-Specific / Multi-Vendor) is registry-computed from the `apiVersion` namespace. Components don't self-declare it.

## The marketplace is a Target-side projection of the registry

The Claude Code marketplace is a **Target-side projection** of the registry into installable plugin directories. Plugins are reads of the catalog, not first-class kinds. Nothing in the marketplace exists that doesn't exist in the registry first.

Projection happens at authoring time. `gnx build` writes plugin dirs and generates both `plugin.json` and the root `marketplace.json` entry from one source. `gnx build --check` keeps CI honest. This kills the dual-manifest version drift that plagued cix. Version lives in one place.

## The CLI initializes projects through discussion, not fixed menus

`gnx init` initializes a project: `claude` (agent + CLAUDE.md constitution) + `dao` (agentic organization) + `.gnx/` (installed catalog components). `gnx component init [kind]` scaffolds a new lexicon entry (manifest + skill + tests, kind-aware).

The keystone: `gnx init gnx`. gnx is the first project initialized by its own CLI, running its own core set.

## gnx is one node in the loop, not the whole loop

![The gnx loop](/diagrams/gnx-loop.svg)

gnx is the registry the system's self-extension runs through. Dagstra searches the registry on `provides`; HADES generates new components into it; whatever enters the catalog immediately extends what the next composition search reaches. The samsara audit named this autopoietic closure its strongest KEEP signal (2026-06-06): *"Bodhi sharpens → Dagstra searches → HADES generates into the registry → extends the next search."*

The loop is the design frame; the registry is gnx's contribution to it.

## The catalog keeps two registers distinct: a curated core set, an at-large marketplace

Two registers, kept distinct. The **core set** is what `gnx init` curates from — components serving a function in every project's life (the full function-tier table, with statuses, is doc 04's):

| Component | Function |
|---|---|
| ci-scaffolds | Discipline |
| guild-arch | Design challenge (panel + trust boundaries) |
| antifragile | Structural conscience (ACES boundary test) |
| craft-extensions | Lexicon growth |
| craft-evals → rubrix | Gates |
| aboot | Session continuity |
| dao-corpus bindings | Domain expertise (rust now; py/ts queued) |

The **at-large marketplace** carries those plus everything that enters as deposited: slick (grammar as plugin), radix (multi-modal comprehension), luminex (Flow — most design-complete manifest in the corpus; carries the §10.8 name collision, unresolved), memex and recon (memory and reconnaissance organs), post (publication surface), craft-research, craft-rhetoric.

**cortex** is roadmap-only. REP-015 is proposed with no research behind it. The program's ruling (2026-06-12): don't accept it to complete the picture.

## Claude and agents are the primary consumer; humans browse second

The primary consumer is Claude/agents (decided 2026-06-12, consistent with UC1 2026-04-01). Humans browse second.

Every catalog decision is evaluated as: *can a capable reasoner decide to compose two components it has never run, from their declared surfaces alone?* The Feb-2026 pragmatic turn: composition doesn't require formal proof; it requires legible surfaces.

The live risk: the CC Target adapter becoming de-facto spec because no second Target ships. Vendor-neutral-by-structure is the survival invariant precisely because the primary consumer is Claude.

## Each component is useful without the rest of the stack

The universe ships in phases; each component — including gnx itself — is useful without the rest of the stack.

Add the Claude Code marketplace without `gnx init`. Run `gnx init` without Dagstra in the loop. Register components without geist.sh running. GTM work starts the week of 2026-06-15.

## Open questions

- **§10.7 — gnx expansion-name**: "Generative noetic extensions" (README) vs "Generative Nootropic eXtensions" (May 15 scope doc). README rewrite deferred until first components + docsite exist. Which name lands when GTM starts?
- **§10.8 — the luminex name collision** (legibility surface vs the veritas-side gate): resolve before luminex registers — the catalog must not carry two luminexes.
- "Plugins are reads of the catalog, not first-class kinds" — is this stated explicitly in public-facing docs, or is it internal-only doctrine? The public boundary doc (doc 12) will decide, but it affects how the marketplace is introduced here.
- cortex's exclusion ("don't accept it to complete the picture") — is the ruling and reasoning surfaced publicly, or only the absence?

---

*Comment on specific blocks by block anchor — particularly: the loop description, the cortex ruling, and the standalone-value claim.*

---
