# The core set and gnx init

`gnx init` asks before it installs. That's the design decision with the longest reach.

## Why discussion, not menus

A fixed menu presupposes that the project type determines the right set. In practice the right set depends on details the form can't capture: what's already wired, what stack, whether the project has a dao yet, how much structure the team can absorb. The Claude Agent SDK interview reads those details and reasons from the catalog's `provides` surfaces to what to install. The output is a curated `.gnx/` — same conversation logic whether `gnx init` runs from a bare terminal or as a Claude Code tool call.

This also means the interview is not a one-time UX decision. It's the curation budget from day one (§9): the agent selects against the catalog's declared surfaces, not against a hardcoded list. If the catalog grows, future `gnx init` runs reach further.

## The function tier names the founding members

The Status column encodes each member's readiness class — where it sits between proven pattern and registered component.

| Function | Component(s) | Status |
|---|---|---|
| Constitution | CLAUDE.md seeding + dao charter + ratchet hook | pattern proven, needs componentizing |
| Discipline | ci-scaffolds | working (cix), port as-is |
| Challenge | guild-arch (panel + trust-boundaries) | working |
| Structural conscience | antifragile (ACES review) | working |
| Lexicon growth | craft-extensions | working |
| Gates | craft-evals → rubrix | working / named |
| Continuity | aboot | raw material exists, needs componentizing |
| Expertise | dao-corpus bindings (rust; py/ts/rust-py-ts queued) | rust corpus CLOSED |
| Stack scaffolds | hexagonal skeletons + justfile + .githooks + CI per py/rust/ts | conventions surveyed |

Status distinguishes three readiness classes: **port as-is** (already componentized elsewhere, requires only manifest + registration); **needs componentizing** (the pattern exists in practice but hasn't been cut into a manifest + skill + tests); **conventions surveyed** (the shape is known but the artifact doesn't exist yet). The table is a build sequence as much as a catalog sketch.

## The organ tier: enters as deposited

A second tier enters as deposited — already designed, enough manifest surface to register:

```
memex      recon      radix (text built; multimodal designed)
luminex    post       slick-as-plugin
```

"Deposited" has a specific meaning here: these artifacts don't need componentizing from raw patterns. They need the gnx on-disk format to settle (§4, §10.3) and then registration. luminex is the most design-complete manifest in the corpus; it enters first — with one blocker: the luminex name collision (§10.8 — the legibility surface and the veritas-side gate share the name) must resolve before the catalog carries two luminexes.

Function-tier entries require a componentizing pass (manifest + embedded skill + tests, kind-aware — `gnx component init` is the scaffold for that). Organ-tier entries require a registration pass once gnx's manifest.yaml format is finalized.

## dao-corpus: the public expertise wing

`~/mox/packages/dao-corpus` is public. Its governing principle is separation of concerns: data only, tooling lives elsewhere (principle #5 of the dao-corpus itself). The corpus doesn't run — it's read.

**corpora/rust** is CLOSED: 12/12 milestones, ~150 Frames, 11 patterns. Named next: py, ts, rust-py-ts (FFI/dual-publish), experience.

A corpus enters the catalog as a **Skill component** — `kind: Skill` in slick's 4-kind taxonomy. Skills are provides-only: Dagstra matches on them but they carry no DAG topology (no produces/consumes). They are axioms — the base layer the composition search reasons from. A corpus Skill declares its expertise in `provides`; any project composition that needs that expertise reaches it.

```yaml
# sketch — manifest.yaml for a corpus binding (format not hard-decided)
kind: Skill
provides:
  - rust/ownership-patterns
  - rust/lifetime-elision
  - rust/ffi-dual-publish
relations:
  corpus: dao-corpus/corpora/rust
  frames: dao-corpus/corpora/rust/frames/
```

"Mastery distilled once, traceable to its sources, composed into any project by reading its mark." The mark is the provides entry; the source Frame is the grounding artifact behind it.

## gnx init gnx: the keystone dual-role move

`gnx init gnx` is the first project gnx initializes — the tool, run on itself.

This is the Engelbart dual-role condition: using the tool you're building to build it. The bootstrap claim — "gnx works" — only holds under active developer-as-primary-user practice. The 2026-05-15 verdict was "conditionally satisfied — requires deliberate institutional practice." Conditionally, not automatically.

What the move proves concretely:
- The interview runs against the function-tier table and selects the right subset for a Python CLI project
- The installed core set (ci-scaffolds, guild-arch, antifragile, craft-extensions, dao-corpus rust binding) governs gnx's own development
- Every subsequent `gnx add`, `gnx validate`, `gnx build` runs inside a project that gnx initialized

If gnx's own development drifts from running through its CLI, the bootstrap claim dissolves. The institutional practice is the enforcement mechanism — there is no structural one.

---

## Open questions

**§10.6 — Init profiles: curation data vs a new kind.**
The leaning is curation data (a structured record of what a conversation produced — component IDs, reasoning, project context). The alternative is a first-class `kind: Profile` that can be registered in the catalog, versioned, provides-indexed, and composed. The difference: a catalog Profile is shareable and searchable by future `gnx init` runs ("other Python CLI projects initialized with these components"); plain data is opaque config local to the project. If profiles become a catalog kind, they also need a curation policy to prevent the catalog pollution failure mode (§9).

**Does the function-tier "Expertise" row cover only dao-corpus, or does it have a broader binding?**
The table lists "dao-corpus bindings" but the provides-only / axiom pattern would apply to any curated knowledge corpus registered as a Skill. Is dao-corpus the sole public expertise Skill, or is this row a pattern that other knowledge sources can fill? The ground truth scopes it to dao-corpus but doesn't close the pattern.

**"Needs componentizing" work: who does it and when?**
Constitution (CLAUDE.md seeding + dao charter + ratchet hook) and aboot both have "needs componentizing" status. `gnx init gnx` requires at least Constitution to be componentized to run its own core set. Is there a sequenced dependency here, and does it gate the first `gnx init gnx` run?

**§10.8 — the luminex name collision.**
luminex enters the catalog first among the deposited tier, but the name currently denotes two things in the corpus (the legibility surface and the veritas-side gate). Resolve before registration — the catalog must not carry two luminexes.

---

Comment on any block — particularly the function-tier status distinctions, the corpus Skill manifest sketch, and whether the Engelbart condition reads as strong enough a constraint.

