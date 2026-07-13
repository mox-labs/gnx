---
title: "The bootstrap plan"
section: ecosystem
status: planned
mode: explanation
fidelity: dirt-road
---

# The bootstrap plan

*Internal. What has to exist for gnx to boot as a real package manager — in dependency order, converged 2026-07-06. Phases 0 and the rulings inside it landed the same day (D13–D16); the rest is build work. geist.sh/geistr are deliberately out of scope here.*

## Definition of done

The never-run **bridge proof**: a catalog manifest → a CC-composed Flow manifest → `gnx validate` admission → a matrix run → the board written and dumped. No such path has ever existed end-to-end. When it runs, gnx is bootstrapped; everything after is population and polish.

## Phase 0 — rulings that gate everything *(landed 2026-07-06)*

- **Casing: kebab-case resources** (D13) — `gnx.dev.v1.intent-hardening`. Propagation to docs/doctests owed.
- **The shape rule ratified** (D14) — parses-as-type_url = port; dotted-lowercase = tag; Skills legitimately tags-only.
- **Expansion ratified** (D15) — generative noetic extensions.
- **Public naming boundary** (D16) — gnx + slick + Claude Code on the public wing.
- **Still open, decide-don't-drift:** the authorship credential (H2) — the wall-first ruling wants a non-self-issued credential + asserting-identity field *before the first registration*, or a conscious waiver accepting permanently unsigned provenance on the founding cohort. And the maturity value-space names (observed `shipped | design`, provisional).

## Phase 1 — slick (the long pole)

1. **`slick validate`** — doesn't exist; the crate validates essentially nothing (serde-structural, no unknown-field rejection). The kit's first real competency, encoding the Phase-0 rulings: identity grammar, port/tag rule, manifest structure, plus an overlay-schema slot so gnx registers its G-plane checks in the same engine (the extensible-validation lean — MSG boundary kept: gnx owns the overlay's content, slick owns the checking machinery).
2. **Fix the crusts** — the Python ImportError (ghost `Kind`/`TypedConfig` imports) and unpinned ABI block the entire Python side: matrix, gnx's CLI, config-driven composition. Foundation work now, not hygiene.
3. **CLI**: `validate`, `--skill`, likely `init`.
4. **Dogfood**: slick's own manifest — the first registration candidate; slick has never described itself.
5. **The CC plugin**: the authoring surface (manifest-model skills exist in embryo in the crate's `.claude/`). Bodhi/Dagstra/HADES operators stay PR6-pending — not bootstrap-blocking.
6. **Discipline shipped with it**: semver + a dependents-CI build. The cautionary tale is recorded: v0.2.0's rename silently compile-broke its only consumer (geist-edge still imports the ghost `TypedConfig` today), and nothing caught it.

*Deliberately deferred:* the Construct/Artifact hoist and the board's wire format — runtime-track (cobblestone), not needed for the catalog to boot. When runtime work starts, the wire format is a mint-once, GEP-grade decision (and it is what unblocks the generic board viewer — the view thread's "no wire to tail" dependency).

## Phase 2 — gnx

1. **Commit the working tree** — the entire authoritative layer (pipeline, GEPs, components, this wing) is untracked. First fix of all.
2. **`gnx validate` = slick validate + admission** — minting under `gnx.dev`, type_url uniqueness, overlay contract, maturity gate.
3. **Projector rebuilt to contract** — copy-tree for adopted trees; lockfile-grade identity file (type_url + version + source ref) per plugin dir; license/author passthrough (slick is Apache-2.0 — the current hardcoded MIT would mislabel it); overlay `version` → `plugin.json` (the CC install cache key — the hardcoded `0.1.0` means installed users can never receive an update *today*); Flow → N:1 plugin projection.
4. **Scaffolds** — `gnx init` / `gnx component init`: the generates-projects identity; the lab's experiment-as-directory pattern generalized (config entry point + source-referenced payloads + results ledger).
5. `--skill` on gnx itself.

## Phase 3 — population, and the loop closed once

1. **Re-mint the three existing components** under the ratified grammar; register slick (and gnx) as catalog citizens.
2. **cix intake at plugin grain** — 7 real plugins (the 8th registry entry, recon, is a bare tool with zero plugin components; it enters as a tool component). Landmines to preserve deliberately: ci-scaffolds' MCP is an external uvx package (`pythea`); guild-arch's hook reads the *host project's* `.claude/guild-ratchet.md`. Metadata re-stamped, never copied (the ancestor's three-way version drift is the empirical obituary for copying). Nothing grandfathered — per-component review at registration.
3. **The composer skill** — CC + catalog → Flow manifest → **run on matrix as-is**. Dirt-road call, made deliberately: shipped matrix works (119 consistent tests); its four prose docs teach a dead contract and are the hazard, so the composer skill teaches the *code's* contract, and the consumes/produces ↔ requires/provides mapping is the composer's job. Rebuilding matrix on slickit is the next milestone, not this one.
4. **Acceptance**: the bridge proof, above.

## The shape of the effort

Phase 0 is decision-hours (mostly done). Phase 1 is the real engineering — days, not weeks, *because* Phase 0 landed first: validation can't be written against unruled grammar. Phase 2 is a small tool rebuilt against a now-stable contract. Phase 3 is population plus one proof-run. The long pole is slick, exactly as assumed — and it is also the highest-leverage work, because every later consumer (both runtimes, the viewer, every component author, every future catalog) inherits it.
