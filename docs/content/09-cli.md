# The gnx CLI

> **Design register.** gnx is pre-build (NAMED — doctrine shaped, build starting). Present tense below states the design, not shipped behavior. What runs today: [status](/docs/status).

The CLI is the third face of gnx — the agentic tool that initializes projects, manages the registry, and, by its own structure, demonstrates the pattern every Capability in the catalog must follow.

## Every Capability must self-describe via `--skill`

Every Capability ships its skill **inside the artifact**. `memex --skill` emits a complete SKILL.md — activation frontmatter plus intent→command table. Claude Code loads it to drive the tool. gnx will do the same: `gnx --skill` as its own front door.

The convention enforces a property: a Capability that cannot self-describe is not agent-operable. Claude Code can't read a man page; it reads a skill. If the skill isn't there, composition fails at discovery.

This is already the live pattern in cix, recon, and memex. gnx is the first catalog component that enforces it on everything else.

## Hard catalog rule

Every `kind: Capability` registered in gnx must:

1. Ship its embedded skill
2. Expose `--skill` to emit it
3. Point `relations["skills"]` at a surface that travels inside the artifact

A Capability without an embedded skill does not clear the curation bar. The rule was proposed in discourse (Claude) and accepted (yzavyas, 2026-06-12). It is enforced structurally — not by convention, by the registration gate.

`gnx --skill` is not a convenience. It is gnx eating its own requirement.

## Claude Code as outer agent

Claude Code drives the CLI as a tool. It runs `gnx init`, `gnx search`, `gnx validate` — the full command surface — the same way it runs any other shell command. This is the UC1 architecture (2026-04-01): Claude IS the orchestrator; gnx is something it reaches for.

## Agent SDK inside for agentic ops

`gnx init` runs a curated-selection interview. That interview is inherently conversational — it can't be reduced to a fixed menu or a flag surface. gnx uses the **Claude Agent SDK inside** to run it, which means:

- `gnx init` works from a bare terminal (before Claude Code is configured)
- `gnx init` works equally as a tool driven by Claude Code (from inside an existing project)

The Agent SDK handles the init interview, curation, and composition assistance. The CLI is the same binary in both modes. The caller changes; the behavior doesn't.

## Deterministic ledger — the anchor argument

Registry and ledger operations stay deterministic and auditable. SDK sessions **propose**; deterministic code **validates and writes**. The two never swap roles.

gnx is the nominated exogenous anchor that breaks the recursion problem in §9 — LLMs both compose and validate, and the only escape is an anchor the LLM composer can read but cannot mint. If SDK sessions could write to the ledger directly, gnx stops being an anchor. It becomes part of the loop it was designed to break.

The constraint in the codebase: "Dumb boundaries, smart interiors — intelligence never lives in the channel" (2026-06-10). The channel (registration, validation, the ledger write) is dumb by design.

## One source generates both projection manifests

```
gnx --skill                 # self-describe to agents
gnx init                    # SDK interview → scaffold claude+dao+.gnx/ → install curated set
gnx component init [kind]   # scaffold a lexicon entry (manifest + skill + tests, kind-aware)
gnx add / rm / update       # install components into .gnx/ (project) or user scope
gnx search / inspect        # agent-facing discovery (provides-index first)
gnx validate                # strict registration gate (manifest, ports, kind rules, namespace)
gnx build [--check]         # project Target surfaces (Claude Code plugins, marketplace.json)
```

`gnx build --check` in CI catches projection drift. The dual-manifest version problem that plagued cix is now structurally impossible: plugin.json and marketplace.json are both generated from one source.

## The language isn't locked — Python leads

**Python** — leaning, not hard-decided (§10.4).

The case for Python:

- Inherits cix's proven hexagonal core: domain/ports/application/adapters; TargetPort protocol; 40 tests already shaped
- Sits next to matrix (the execution tier) in the Python orchestration layer
- Distributes via `uv tool install`
- The `--skill` importlib.resources loader ports verbatim from cix

The TS alternative is real: npm-native distribution, adjacency to the Claude Code ecosystem. It was considered and not ruled out — only deferred.

Rust as CLI host retreats to "later, if evidence demands." The slickit precedent is kernel-tier, deterministic, long-lived. A CLI that runs interviews doesn't fit that shape.

### The slickit-crust landmine

slickit 0.2.0's Python crust is broken at source: ghost `Kind` and `TypedConfig` imports cause ImportError. gnx parses manifest JSON directly until this is fixed upstream. This is a known, bounded workaround — not a design decision. It resolves when the upstream fix lands.

## Open questions

- **§10.4 — CLI language**: Python (leaning, cix inheritance) vs TS (npm-native, CC-ecosystem adjacency). The decision gate: does distribution and ecosystem proximity outweigh the hexagonal core inheritance? [open]
- The `--skill` hard catalog rule applies to `kind: Capability` — does it extend to `kind: Agent`? Agents are also composed by Claude Code; a parallel agent-operability requirement may be warranted. [open, not in ground truth]
- `gnx init`'s dual-mode guarantee (bare terminal + CC tool) depends on the Agent SDK being callable in both contexts. Is there a deployment constraint (network, auth, SDK version) that breaks the bare-terminal case? [open, not in ground truth]
- The deterministic ledger boundary is stated as doctrine; the exact enforcement mechanism (where in the codebase the SDK→deterministic handoff lives) is not specified. Does this require a formal architectural seam, or is it a code-review convention? [open, not in ground truth]

---

*Comment on specific blocks by block anchor — particularly: the anchor argument, the hard catalog rule scope (Capability only vs Agent too), and the language decision gate.*

