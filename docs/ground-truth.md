# gnx — ground truth

Deposited 2026-06-12 from the design discourse between yzavyas and Claude. This is the
source document for the design docsite content. It records what is *established*, what
is *decided*, and what is *open* — with recency-weighted grounding in the mox research
corpus. Content drafted from this document must preserve the established/decided/open
distinctions; nothing here is marketing.

Weighting rule (yzavyas, explicit): more recent discussions and concept-hardening
outrank older artifacts. June 2026 > May > April > Q1 > 2025. Older docs are lineage
(*why*), not doctrine (*what*).

---

## 1. The ecosystem (established)

The platform is a strict triad (settled 2026-06-09/10, `mox-system-model/system-model.md`):

| Layer | Repo | Role | Status |
|---|---|---|---|
| Grammar | slick (`~/mox/packages/slick`, crate `slickit`) | What components look like: Manifest, TypedStruct, TypedRegistry. 4 kinds. | SHIPPED v0.2.0 |
| **Vocabulary** | **gnx** (this repo) | The catalog: component registry, marketplace, scaffolds, CLI. *Where components come from.* | NAMED — doctrine shaped, build starting |
| Execution | geist.sh (geist-edge + matrix + mox.hud) | Governed runtime that hosts components and mediates agent tool calls | PARTIAL |

Above the triad, the world-ontology (settled 2026-06-04/05): **samsara** is the world —
the inhabited Construct, an append-only ledger of typed marks (slick TypedStructs) plus
per-domain private projections, where humans and agents coordinate *stigmergically* by
reading each other's marks. **Treya** is the framework that acts (ilm · kalā · kriya).
A **Locus** is a bounded view. geist.sh is the world's execution organ, not the world.

A **project** = `claude` (agent + CLAUDE.md constitution) + `dao` (the agentic
organization) + `.gnx/` tooling (the plugin-style projection of installed catalog
components). (2026-06-10.)

The pipeline through a project: comprehend (radix) → envision (bodhi → [roadmap GAP] →
dagstra) → do (matrix → rubrix) → show (luminex → mox.hud) → human re-grounds.

**The gnx loop** — the self-extension joint (samsara audit's strongest KEEP, 2026-06-06):
*Bodhi sharpens → Dagstra searches → HADES generates into the registry → whatever enters
extends the next search.* gnx is the registry the system's self-extension runs through:
Dagstra performs semantic proof-search over the registry (matching on `provides`); HADES
writes generated components into it; whatever enters the catalog immediately extends what
the next composition search reaches.

**On "autopoietic" — the honest ceiling (H1, reframed 2026-07-06).** The audit called this
loop *"autopoietic closure in the precise sense."* That overclaims, and REP-020 already
refuted the strong form: measured against the Maturana-Varela definition, two of three
criteria fail — the boundary is not self-produced (a trust boundary's accrediting authority
must be *disjoint* from what it bounds, the logical negation of self-production), and
components are human-catalyzed, not self-produced. The precise statement: the loop is
**sympoietic — made-with**, not autopoietic — made-of-itself. Deontically, it is
*operationally closing on the correctness axis* (deterministic validation is the system's
own perception; it self-originates reliability-entitlement) but *anchored on the trust axis*
(no detector for adversarial / out-of-distribution value can exist — Rice's theorem — so
trust-entitlement must originate exogenously, in a human **avowal** and a Sybil-resistant
authority disjoint from the marketplace it bounds). The one frame under which "autopoietic"
holds: draw the unity as the **collective** — human + primary agent + catalog — in which the
human is *constitutive, not external*. That is exactly what *generative noetic extensions
**of the collective*** names: gnx is the organ through which a human-and-agent collective
extends its own cognition. So — the *collective* is the autopoietic unity; gnx is where it
self-extends. Keep the loop; drop the solo-machine autopoiesis claim. (Supersedes the
2026-06-12 §1 wording that enshrined "autopoietic closure in the precise sense.")

**Primary consumer: Claude/agents** (decided by yzavyas 2026-06-12; consistent with UC1,
2026-04-01: "Claude Code → gnx (discover) → slickit (types) → matrix (execute). Claude IS
the orchestrator."). Humans browse second. Every catalog decision is evaluated as: *can a
capable reasoner decide to compose two components it has never run, from their declared
surfaces alone?* (The Feb-2026 pragmatic turn: composition doesn't require formal proof;
it requires legible surfaces.)

## 2. What gnx is (decided 2026-06-12; positioning sharpened 2026-06-14)

**Mission (the product framing, public):** gnx provides **cognitive extensions** —
reusable, composable components any agent can use — plus the **slick plugin** agents use
to compose them. As a product/package, **gnx is a marketplace + registry**, full stop.
The ecosystem connection (the registry the collective's sympoietic self-extension loop runs
through; feeding geist.sh / samsara; Bodhi-Dagstra-HADES) is **internal detail — the *why*,
not the product** — and lives only in the internal Design register. "For any agent" is the
*structural* promise (vendor-neutral by namespace + adapter negotiation); **Claude Code
is the only Target that ships today** — frame it "designed for any agent; Claude Code
today."

gnx is **the open-source component**: the component **registry**, the **Claude Code
marketplace**, and an **agentic CLI** (`gnx`) that initializes projects. Three functions,
one catalog:

- **Registry** — components registered under slick manifests; validation-at-registration;
  accreditation; the surface Dagstra/Claude search.
- **Marketplace** — the Claude Code plugin face: a Target-side *projection* of
  vendor-namespaced components into installable plugin directories. Plugins are reads of
  the catalog, not first-class kinds.
- **Tool** — `gnx init` sets up a project (claude + dao + .gnx/) with curated agents and
  skills, **selected through discussion with the user**, not from fixed menus. `gnx
  component init` scaffolds new lexicon entries.

Planned marketplace plugins include: slick (plugin), the cix plugin family (ci-scaffolds,
guild-arch, antifragile, craft-extensions, craft-evals, craft-research, craft-rhetoric),
radix (multi-modal), luminex, aboot, memex, recon, post — and cortex (roadmap only:
REP-015 is proposed with no research behind it; the programme's ruling is "don't accept
it to complete the picture").

## 3. The CLI architecture (decided 2026-06-12)

The house pattern, already live in cix/recon/memex: **every capability ships its skill
inside the artifact**, exposed via `--skill` (e.g. `memex --skill` emits a complete
SKILL.md: activation frontmatter + intent→command table). Claude Code loads the skill and
drives the tool.

- Claude Code is the **outer agent**: it runs `gnx init`, `gnx component init`,
  `gnx search`, etc.
- gnx uses the **Claude Agent SDK inside** for operations that are themselves agentic
  (the init interview, curation, composition assistance) — so `gnx init` works from a
  bare terminal and equally as a tool driven by Claude Code.
- **Registry/ledger operations stay deterministic and auditable.** SDK sessions
  *propose*; deterministic code validates and writes. This preserves gnx's
  exogenous-anchor property (see §9): if gnx's own verdicts were LLM-minted, the anchor
  would dissolve into the loop it exists to break. ("Dumb boundaries, smart interiors —
  intelligence never lives in the channel," 2026-06-10.)

**Hard catalog rule** (proposed by Claude, accepted in discourse): every `kind:
Capability` registered in gnx must ship its embedded skill and expose `--skill`;
`relations["skills"]` points at a surface that travels inside the artifact. A capability
without an embedded skill is not agent-operable and does not clear the curation bar.
`gnx --skill` is gnx's own front door.

Command surface (sketch, confirmed in discourse):

```
gnx --skill                 # self-describe to agents
gnx init                    # genesis: SDK interview → scaffold claude+dao+.gnx/ → install curated set
gnx component init [kind]   # scaffold a lexicon entry (manifest + skill + tests, kind-aware)
gnx add / rm / update       # install components into .gnx/ (project) or user scope
gnx search / inspect        # agent-facing discovery (provides-index first)
gnx validate                # strict registration gate (manifest, ports, kind rules, namespace)
gnx build [--check]         # project Target surfaces (Claude Code plugins, marketplace.json)
```

Language: **Python** (leaning, not hard-decided): inherits cix's proven hexagonal core
(domain/ports/application/adapters; TargetPort protocol; 40 tests' shape), sits next to
matrix, distributes via `uv tool install`, and the `--skill` importlib.resources loader
ports verbatim. The TS alternative (npm-native, next to the CC ecosystem) was considered.
Rust kernel retreats to "later, if evidence demands" (the slickr precedent). Landmine:
slickit 0.2.0's Python crust is broken at source (ghost `Kind`/`TypedConfig` imports →
ImportError) — gnx parses manifest JSON directly until that's fixed upstream.

## 4. The grammar gnx inherits (established; slick's call, gnx does not reopen)

- **4 kinds** (April 2026 compression; reaffirmed June 10): **Capability / Agent / Skill
  / Flow**. Open string, not enum. The 7-kind (2025-10) and 8-kind (early 2026)
  taxonomies did not survive; note the 8-kind list *resurfaced* in a June 5 doc against
  the "don't reopen" ruling — stale sourcing, flagged for explicit correction.
- **Vendor extension via apiVersion namespaces** (Kubernetes-CRD style): core in
  `slick.dev/v1`; vendor surfaces in e.g. `hooks.claude.anthropic.com/v1`,
  `commands.claude.anthropic.com/v1`. Portability classes (Universal / Specialized /
  Vendor-Specific / Multi-Vendor) are registry-*computed*, not declared.
- **provides vs produces/consumes** (settled semantically 2026-04-21; proto lags):
  `provides` = semantic discovery tags, the Dagstra/agent search surface; `produces`/
  `consumes` = DAG topology, matched by matrix at build time. **Skills are
  provides-only** — axioms in the proof-search space, invisible to DAG validation by
  design. A component with neither is invisible to composition and should not exist.
- **Kind must carry type-level consequences** (2026-06-02): a Skill declaring protocols
  is a representable type error. Label-only kinds re-open the refuted k8s homonym.
- **Cycles**: DAG-only validation is stale doctrine (2026-06-02 coinduction + condensation
  design); Flow validation classifies SCCs and requires declared leashes
  (fuel+convergence vs guardedness). Exact rule pending Mission C / S9.
- **Strict, not Postel-liberal, at the grammar boundary** (2026-06-02). Composes with
  "calibration not correctness": gnx validates structure strictly while never promising
  semantic correctness.
- Shipped slickit Manifest = 5 stringly fields (type_url, source, requires, provides,
  relations), JSON, in-memory only. **No kind/apiVersion/metadata.name in shipped code; no
  on-disk file convention exists — gnx defines the on-disk format** (manifest.yaml
  leaning, JSON-aligned underneath; not hard-decided).
- The intent-hardening gradient bounds what the catalog can promise per tier:
  **structural** intent hardens fully (machine-validated at registration); **behavioral**
  hardens to assertion (L2.5 envelope, recorded and auditable, never proof);
  **semantic** does not harden (discovery surface only, never a guarantee). Catalog
  surfaces should show which tier each claim sits in.

## 5. The core set (decided 2026-06-12, shape; membership per-project by discussion)

`gnx init` curates per project through conversation. The catalog's founding set, by
function in a project's life:

| Function | Component(s) | Status |
|---|---|---|
| Constitution | CLAUDE.md seeding + dao charter + ratchet hook | pattern proven, needs componentizing |
| Discipline | ci-scaffolds | working (cix), port as-is |
| Challenge | guild-arch (panel + trust-boundaries) | working |
| Structural conscience | antifragile (ACES review) | working |
| Lexicon growth | craft-extensions | working |
| Gates | craft-evals → rubrix | working / named |
| Continuity | **aboot** | raw material exists, needs componentizing |
| Expertise | dao-corpus bindings (rust now; py/ts/rust-py-ts queued) | rust corpus CLOSED |
| Stack scaffolds | hexagonal skeletons + justfile + .githooks + CI per py/rust/ts | conventions surveyed |

Organ tier enters as deposited: memex, recon, radix (text built research-side;
multimodal designed), luminex (Flow; most design-complete manifest in the corpus), post,
slick-as-plugin.

**Keystone move: `gnx init gnx`** — gnx is the first project initialized by its own CLI,
running its own core set. (The Engelbart dual-role condition: the bootstrap claim only
holds under developer-as-primary-user practice; 2026-05-15 verdict "conditionally
satisfied — requires deliberate institutional practice".)

**dao-corpus** (`~/mox/packages/dao-corpus`, public): the expertise wing. Data only;
tooling lives elsewhere (its own principle #5). corpora/rust is CLOSED (12/12 milestones,
~150 Frames, 11 patterns); py, ts, rust-py-ts (FFI/dual-publish), experience are named
next. A corpus enters the catalog as a **Skill component whose relations point into the
corpus** — provides-only, an axiom in proof-search. Mastery distilled once, grounded in
Frames, composed into any project by reading its mark.

## 6. aboot (corrected definition, yzavyas 2026-06-12)

aboot is **not** init/genesis. aboot is the **session-continuity capability**: manages
session lifecycle events and continuity. Raw material already live as personal tooling:
the `write-resume.sh` SessionStart hook (generates `.claude/resume.sh` per repo),
guild-arch's ratchet loader, the research repo's continuity discipline
(next-session-brief.md, handoff-on-a-branch, continuity dumps), the scratch
session-context pattern.

Grammatically the sharpest first test of the vendor-namespace split:

- **Universal core** (`slick.dev/v1`): continuity artifacts — briefs, handoffs, resume
  state, context dumps — their formats, when written/read. Any runtime with sessions
  needs this.
- **Vendor binding** (`hooks.claude.anthropic.com/v1`): SessionStart / SessionEnd /
  PreCompact / Stop are Claude Code's event vocabulary. A future harness binds the same
  core to different events.

Projected onto Claude Code: plugin with `hooks/hooks.json` + embedded skill +
`bin/aboot` (shipping `--skill`). Directly exercises degeneration-watch #1 (don't let CC
hook vocabulary leak into the core manifest).

## 6a. The dao (established doctrine + 2026-06-12 deep dive)

**Definition (June, current):** the project's agentic organization — "a thin bench of
method-distinct agent practitioners + consciences that stewards a project"
(dao-coordination, 2026-06-09). DAO = Diverse Agentic Organization (sabha.md;
disambiguation enforced 2026-06-07, research PR #75). The dao leg of project anatomy =
charter, guild, ratchet. Homonym guard: dao ≠ dao-corpus (the public expertise data
repo). Lineage uses (Treya's old leg name, the IEX triad, samsara's "dao Domain"
verifier label) are superseded.

**Settled structural anatomy** (May 2026; agent-organizations synthesis, 93 verified
claims + agent-ceremonies 2026-06-04): thin router + method-distinct bench (the DMAD
test — a seat is earned only by a distinct reasoning method; medium is a skill
parameter, never a seat) + **structurally separate conscience** (sycophancy is
training-origin, so the conscience must be a separate seat, not a prompt);
single-agent-first with multi-agent on affirmative justification (~15x token cost);
writes single-threaded; **the one law** (a ritual only helps if it injects signal the
agent could not have fabricated — Huang et al. ICLR 2024); **the second law**
(coordination scales with agent count, consolidation does not — 17x vs 4x error
amplification, Cemri).

**Sabha governance grammar** (built, ziran.ink/ideas/sabha.md): peers-in-inquiry /
asymmetric-in-accountability; decision logs (DECISION / ACTOR / SCOPE / WHY /
WHAT-WOULD-CHANGE-IT / HUMAN-NOTIFIED); a human-amended escalation table — agents
propose amendments, cannot enact them; anti-sycophancy first-consulted; default-to-hold.
The April 2026 live-run verdict grammar exists in NO draft (recovered by the 2026-06-12
memex dive): SHIP / SHIP-WITH-TIGHTENING / REVISE-AND-SHIP; G10 hold-by-default on split
verdicts; Round-0 author self-review treated as suspicious (rubric F7); per-cell
misgrounding scans; CANON status versioning.

**The June 9 Ostrom layer** (every application flagged [synthesis], untested — "no
source studies Ostrom-for-agent-daos directly"): chartered named membership for writers
(ephemeral writers are a tragedy-vector); amendable constitution with protected dissent;
insider monitoring; graduated sanctions as a 3-rung drift ladder (gate flag → RATCHET
entry → human; never skip to 3); a **conjunctive ship-gate** (any single conscience's
refusal-to-ship holds; the human is the defined escape valve); the **inspector-general
invariant** (a maker may not mark its own work shipped; overrides only from the human,
logged). Three accountability axes: vertical (human), horizontal (same-family
consciences), diagonal (out-of-family evaluator — non-negotiable). The prescribed
dirt-road landings ("three lines in dao.md") were never written anywhere (§10.13).

**The artisans dao is the one live, proven instance** (~/mox/assets/artisans — its own
repo, and already a Claude Code plugin, `artisans` v0.1.0): 8 practitioners in three
phases — design trio (davinci/director/roark), makers (generator/projector/
craftsperson), consciences (veritas/labcoat); a **two-family eval panel** (gemini +
mlx-lm Qwen; claude excluded as same-family-as-makers) that caught a fabricated quote
same-model self-review passed; RATCHET.md (18 entries); bimodal source/commission lanes;
the inbox ticket flow (the asset-request skill as front door); real deliveries
("delivered means reviewed, not just built"). Operational hazards its RATCHET recorded:
**registry-is-not-the-loader** (a skill change isn't done until skill dir + dao.md +
dao.html + agent frontmatter all agree); the **altitude rule** (a skill is broad
reusable grammar; project-specific intel travels with the ticket, never a standing
skill); the **factory/library split** (decided 2026-06-09/10 — the wing graduates onto
the gnx stack; personal library vs LLC factory entity hygiene — not yet executed).

**dao ↔ gnx:** `gnx init` scaffolds the dao leg (no genesis spec exists — §10.13); the
dao is Hard ACE's process locus — its equipment IS catalog components at defined
trigger points; dao membership doctrine and catalog governance are one design at two
loci (Ostrom 1A chartered writers ≡ the produce-authority wall ≡ §9's chartered
membership); geist-edge enforces what the dao constitutes; and the gnx loop is the
April dao compounding loop carried forward — with one step worth keeping explicit:
artifacts promote into the registry only after surviving the governance stack.

**The constitution's why** (2026-04-19 conversation, recovered by the dive):
"Constitution is pre-generative; rubric is post-hoc" — encode properties and
orientations, not procedural rules (REP-002: motivation/why outperforms mandate/how).

## 7. Hard ACE (decided 2026-06-12: enforced by code and gates, never by advice)

Three loci:

1. **Catalog boundary** (gnx's own ACE): strict validation at registration; kind carries
   type consequences; new component = lexicon entry, never a grammar edit; core namespace
   structurally unable to accrete Claude Code semantics; **owner/authority identity on
   every registration** (the produce-authority wall — hardest to retrofit); accreditation
   as append-only ledger, human-mintable only.
2. **Scaffolds** (projects born ACE): hexagonal skeletons per stack — Python in the
   cix/matrix shape (uv, ruff, mypy strict), Rust in the vaani/x.uma shape (leaf crates,
   clippy -D warnings), TS with the same ports discipline (ziran/SvelteKit precedent).
   justfile as single source of truth for gates; fast/slow .githooks **actually wired by
   init** (cix shipped hooks that were never wired — fix structurally); CI mirrors hooks.
3. **Process** (the dao): guild review on designs; antifragile boundary test on proposed
   abstractions; evals on flows; ratchet across sessions.

Stack mapping (py/rust/ts — "the new stack", yzavyas): Rust = kernel tier (deterministic,
leaf-crate, long-lived: slickit; future gnx kernel only if evidence demands); Python =
orchestration tier (matrix, agentic pipelines, the gnx CLI); TS = surface tier (this
docsite, the public catalog site, CC-ecosystem adjacency).

## 7a. The antifragile doctrine (established; the *why* under §7)

Source: the trilogy at `~/yzavyas/public/blueprints/content/journal/the-antifragile-imperative/`
(01-structural-fragility, 02-the-87x, 03-conways-ratchet) + the 2026-05-25 gnx settlement
(`scratch/historical-context.md`). This is the doctrine that explains gnx's shape — not
taste, channel-specific inversion.

**The decay cycle.** Three endogenous channels, multiplicative, with Opacity
self-concealing (it hides cost from the people who would fund the fix):

| Channel | Coupling | Diagnostic question |
|---|---|---|
| Stasis | system-to-terrain | Can you change the runtime without rewriting the core? |
| Drag | contributor-to-platform | Can a team contribute capability without platform-team mediation? |
| Opacity | behavior-to-integration | Can you predict composed behavior from declared description? |

Three "no"s = the cycle is compounding. Opacity feeds stasis feeds drag.

**gnx is an SDK Frontier instance** — the third archetype (after Coupled Monolith, which
enters through Drag, and Fragmented Estate, through Opacity). SDK Frontier enters through
Stasis: "Frameworks define incompatible surfaces for tool registration, memory,
orchestration, and inter-agent communication. A team that couples to any one SDK today
finds it deprecated or re-architected within two to four quarters." Switching cost
accumulates from day one, before any backlog exists to create drag.

**ACES mapping for gnx** (each property breaks one channel through one mechanism):

| Property | gnx mechanism | Channel broken |
|---|---|---|
| Adaptability | Manifest as wire-equivalent; apiVersion namespaces; adapter capability negotiation | Stasis — new vendors/runtimes absorbed as namespaces + adapters, not rewrites |
| Extensibility | Typed Manifest schema; vendor extensions via namespace, never core changes; O(1) validation at the catalog boundary | Drag — extension doesn't queue behind a gnx maintainer |
| Composability | requires/provides ports; declared composition; skills travel via relations | Opacity — composed behavior predictable from declarations before execution |

June 2026 caution (grammar-of-computation, audited): the "composable" leg adds little
over classical modularity — lean argumentation on info-hiding, dependency-inversion, and
antifragility rather than composability-as-novelty.

**The Boundary Test** (the answer to the Inner Platform objection — the strongest
objection to any ACES boundary): does the boundary reduce total integration surface?
N components × M runtimes coupled directly = NM integration points; under gnx mediation,
N + M + B (B = the boundary: Manifest spec, catalog, registry validation). NM → N+M+B
passes for any non-trivial (N, M). Degeneration = the protocol surface stops being
runtime-agnostic (the three watch conditions, §8).

**Conway's Ratchet.** Organizations resist the fix because the fragility is load-bearing
for careers built on it (the trilogy: 37:1 returns lost to incentive gradients; "I don't
have a solution for this"). gnx cannot dissolve the ratchet but must refuse to encode
it: vendor-neutral **by structure**, not by editorial choice. If `slick.dev/v1` becomes
"Claude Code with extra steps," the ratchet has captured the project.

**Two payoff shapes, both structurally installed:** network-effect (each
boundary-aligned component becomes available to every Target adapter — value
superadditive in component count) and antifragile convexity (∂²V/∂σ² > 0 — volatility in
the agent-SDK ecosystem arrives as adapter work, becoming capability expansion).
Affordances to observe, not engineer: hormesis (new SDK → new namespace + adapter) and
Lindy (MCP, the Skills format, Agent SDK primitives are Lindy-shaped; gnx bets on
adjacency to them).

**The through-line** (Dijkstra 1972): the purpose of abstraction is precision. The cycle
is what happens when the right semantic levels do not exist; ACES is what those levels
look like when they do.

## 8. Claude Code projection mechanics (established 2026-06-12, verified)

Verified against official docs + live ~/.claude inspection:

- Marketplace add clones the whole repo; **plugin install copies only the plugin subdir**
  to a versioned cache. Cached plugins must be self-contained. No install-time build hook
  exists. Path traversal out of a plugin dir is banned.
- Symlinks within a marketplace dereference into copies at install — but behavior for
  `directory`-source marketplaces (how yzavyas consumes his own) is ambiguous, and
  Windows-hostile. **Therefore: projection happens at authoring time; output is
  committed.** `gnx build` writes plugin dirs; `gnx build --check` keeps CI honest.
- **One source, two generated manifests**: plugin.json AND the root marketplace.json
  entry are both generated from the component/distribution source — killing cix's chronic
  dual-manifest version drift structurally. Version lives in one place (plugin.json wins
  silently in CC's resolution; version is the cache key — explicit bump = release).
- marketplace.json must live at repo root (`.claude-plugin/`); plugin dirs can live in
  any subdir (no `..`). Commands are legacy per CC docs ("use skills for new plugins").
- cix lessons inherited: publish state lives in data, not directory renames; validation
  is first-class in the CLI, not outsourced bash; stale CI paths from renames are the
  tax of churn — name things once.

Degeneration watch (standing, 2026-05-25): (1) Manifest core accreting vendor semantics;
(2) **the CC Target adapter becoming de-facto spec because no second Target ships — the
live risk, since CC is the only current surface**; (3) accreditation encoding vendor
trust policy. Vendor-neutral-by-structure is the survival invariant *because* the primary
consumer is Claude.

## 8a. The repo layout (proposed 2026-06-14, design discourse)

The marketplace repo is the catalog (authored) and the Claude Code marketplace (generated
projection) at once. The directory tree mirrors the apiVersion namespace, so "where a
component lives on disk" = "its vendor scope" = "its portability input."

```
gnx/
├── api/                       # type_url → schema. ALL declared types: data types,
│   └── slick.dev/v1/…         #   component config schemas, AND protocol config schemas
├── components/                # vendor-neutral vocabulary — the 4 core kinds (slick.dev/v1)
│   ├── capabilities/ agents/ skills/ flows/
├── extensions/                # vendor-namespaced kinds + concrete vendor facade impls
│   └── claude-code/           #   hooks.claude.anthropic.com/v1, commands.…/v1
├── .claude-plugin/marketplace.json   # GENERATED (root — CC requires it here)
└── plugins/                   # GENERATED, COMMITTED — Target-side projections (a read, never a source)
```

- **Authored** = `api/` + `components/` + `extensions/`. **Generated, committed** =
  `marketplace.json` + `plugins/`.
- **Plugins are projections that pull, not owners.** A plugin references components by
  type_url; `gnx build` resolves them into a **self-contained, committed** plugin dir
  (CC copies the subdir in isolation, bans `..` traversal, has no install-time build —
  §8). Author once, reference, generate self-contained. This kills cix's #1 pain
  (every plugin owned its own copies → version drift).
- **The `components/` ↔ `extensions/` directory boundary IS the degeneration-watch #2
  enforcement surface.** A vendor-specific field on a core schema or core component
  breaks the namespace-as-vendor-scope invariant and corrupts portability computation.
- Open edge: whether plugin *definitions* (which components each bundles) live in
  `marketplace.json` entries or a thin `distributions/` layer. Lean: distributions, so
  a plugin's composition is itself a reviewable authored artifact. Not settled.

## 8b. Protocol diversity (established doctrine + 2026-06-14 dive)

Components are invoked through heterogeneous transports (MCP, HTTP, CLI, gRPC,
in-process function, WASM) yet compose through one grammar. The design handles this by
**separating three axes** so heterogeneity never touches the core grammar:

1. **Vendor scope → the apiVersion namespace** (no field). `slick.dev/v1` (core) vs
   `hooks.claude.anthropic.com/v1` (vendor) vs `<org>.com`. A Claude Code hook is a
   vendor *kind* (`ClaudeCodeHook`), **not** a "protocol." New vendors declare
   themselves at the boundary — Adaptability against Stasis.
2. **Transport → the Protocol (the M of MSG: "how to reach a component").** The April
   proto draft (NOT shipped — shipped Manifest is 5 fields) modeled it as
   `repeated Protocol protocols` on the Manifest, each `Protocol = { type_url
   ("slick.protocol.v1.mcp"), config: TypedStruct }`; a component may declare **multiple
   protocols** (`ProtocolServer` = what it exposes, `ProtocolClient` = what the runtime
   speaks). The later gnx settlement (historical-context:146) moved this *inside*
   `Capability.spec.implementation` and routed vendor-specificity to the apiVersion
   namespace instead — "no new Protocol types needed." **Where Protocol lives —
   Manifest field vs implementation config — is genuinely open** (Burner vs K, April).
   Process isolation rides the protocol: user components run out-of-process, reached via
   their transport (Taleb blocked embedded PyO3 — a segfault kills the runtime). The
   brownfield story: "wrap an existing API with Manifest + Protocol → now composable."
3. **Dispatch uniformity → the adapter contract (runtime, NOT gnx).** Four adapters —
   HTTP / CLI / gRPC / function — normalize protocol-specific I/O into typed enrichments
   on the Construct. "One Component trait works across all four because the protocol
   specifics live in the adapter, not the component." HTTP is the degenerate `Mono`
   case; agentic streaming the general `Flux` case. This is the Envoy `ext_proc` pattern
   (lineage: 59 plugins migrated with zero rewrites). It lives in **geist-edge**, not
   gnx — the design-time/runtime split.

**Skills are the one kind with no protocol** — `protocols` is empty for Skills; they're
provides-only axioms, *read* not *invoked*. "KIND_SKILL + protocols is a representable
type error" (Dijkstra).

**Atop the three axes:**
- **Portability is registry-computed**, never declared: Universal (core only) /
  Specialized (core + runtime feature) / Vendor-Specific (one vendor namespace) /
  Multi-Vendor (several; Future). Lives in gnx's validate/inspect tooling, not a field
  or directory.
- **Adapter capability negotiation:** each Target adapter declares the namespaces it
  supports (Claude Code: `slick.dev/*` + `*.claude.anthropic.com/*`; a future Cursor
  adapter: `slick.dev/*` + `*.cursor.sh/*`). Same catalog, namespace-filtered projection
  per Target.
- **The Universal Facade** is a two-location construct: a vendor-neutral interface in
  `components/` fronting N vendor-specific implementations in `extensions/<target>/`,
  bound by matching `provides`/`requires` ports (type_urls in `api/`). The runtime
  selects a concrete implementation by adapter capability. No single file is "the
  facade."

**Layout consequence:** protocol *config schemas* (MCP config, HTTP config, …) are
type_url'd schemas → they live in `api/` alongside data-type and component-config
schemas (one join key, type_url). Defined-vs-opaque applies to protocol configs too: a
*defined* protocol config gets a schema in `api/` (gnx can validate it); an *opaque* one
travels as an unvalidated blob. Two notes: "slick.protocol.v1.mcp" appears in the April
deliberation but the May settlement leans away from Protocol-as-type toward
implementation-config — unresolved; and MCP may *ride* the HTTP/CLI adapters
(session-based JSON-RPC over stdio/SSE/HTTP) rather than being a fifth adapter.

## 9. Accreditation & produce-authority (established doctrine; most consequential open design)

- **Correctness vs trust split** (load-bearing, 2026-05-25): gnx ships correctness
  (structure, port satisfaction, namespace scope); x.uma + geist-edge ship trust (FIS,
  autonomy ceilings, mediation). gnx accreditation is *data* x.uma's FIS reads —
  accredited compositions get higher autonomy ceilings. "A Manifest that declares
  requires:[PII] and provides:[Logs] composes cleanly and leaks cleanly unless something
  above gnx enforces info-flow policy."
- **The recursion problem** (fuzzy-governance REC1–3): LLMs both compose and validate;
  escape requires an exogenous anchor. gnx accreditation is the nominated anchor:
  **human-ratified status the LLM composer can read but cannot mint.** (Open position:
  formally ratify gnx taking this role. Everything in the June corpus leans yes.)
- **Produce-authority is the make-or-break** (2026-06-05): TypedRegistry has no owner
  concept; geist keys on self-declared agent_id; "until minting a warrant is structurally
  restricted to an authority disjoint from the mark's author, every warrant is forgeable.
  Build that wall first." gnx owns the registry → authorship identity is one of its
  earliest schema decisions.
- Verdicts are **two-axis** (grounding ⊥ warrant), never a single bool — the
  Frame.asserted:bool incident was prosecuted as a constitutional violation (fixed
  2026-06-07, PR #74).
- The catalog is a **governed commons** whose existential failure mode is pollution
  (2026-05-29): confident, schema-shaped, low-grounding agent traces. Cures already in
  doctrine: chartered membership for writers; validation-at-registration; decay/relevance
  dynamics (REP-022's staged ranking — cheap recall → resonance rerank; recency separate
  from eviction — is the programme's only settled ranking discipline, chartered for the
  Construct, transferable to catalog discovery by explicit decision); curation budget
  from day one. Ranking by raw adoption counts is "wrong-signed" (rewards echo, favors
  stale).

## 10. Open questions (for the docsite's feedback loop)

1. Accreditation record shape: relation on the Flow's Manifest vs `kind:
   AccreditationRecord` vs side-table. (Partly slick's call.)
2. Does gnx formally take the exogenous-anchor role? (Leaning yes; unratified.)
3. On-disk manifest format: manifest.yaml (leaning) vs manifest.json.
4. CLI language: Python (leaning, cix inheritance) vs TS (npm/CC adjacency).
5. The proto seam: provides/produces vs requires/provides — blocked on slick's next proto
   pass (Mission C / S9 unrun). gnx treats provides as semantic, produces/consumes as
   topology, regardless of final field names.
6. Init profiles: plain curation data (leaning) vs a new kind. Init is
   conversation-driven either way.
7. gnx expansion-name: "Generative noetic extensions" (README) vs "Generative Nootropic
   eXtensions" (May 15 scope doc). README rewrite deferred until first components + doc
   site exist.
8. The luminex name collision (legibility surface vs veritas-side gate) — resolve before
   the catalog carries two luminexes.
9. Roadmap-planner: new kind, new agent, or Kalā formalization? The kind taxonomy may be
   incomplete for the temporal leg. (Explicitly not gnx's to settle alone.)
10. gnx has no chartered research home (no REP); doctrine lives in scratch + an unaudited
    system-model. Does the catalog layer need its own settlement pass?
11. Flat vs provenance-aware catalog: should a registry entry record that a component is
    a *promoted composition* (depth, constituent lineage) rather than a primitive — and
    does a promoted composition inherit accreditation or require re-accreditation?
    (Raised 2026-04-28 in conversation; absent from every draft; surfaced by the
    2026-06-12 memex dive.)
12. Which dao sense does `gnx init` scaffold? The corpus carries three senses with
    supersessions never recorded: Q1's Diverse Agentic Organizations (Trinity Council,
    orchestration-forbidden), April's task-scoped instantiated cooperative (lifecycle:
    instantiated → runs → escalates → terminates → promotes artifacts into the
    registry), June's standing thin bench + consciences. "Whether dao is the right unit"
    was explicitly left open 2026-04-28 and never closed.
13. No dao genesis spec exists: the scaffolded `dao/` directory's contents are
    undocumented. The artisans wing's anatomy (dao.md registry + dao.html reader +
    RATCHET.md + agents/ + skills/ + managing CLAUDE.md) is the only proven template.
    The June 9 governance dirt-road items (3-rung drift ladder, conjunctive gate,
    inspector-general invariant) were prescribed but never landed anywhere.
14. Registry alias/lineage ledger: the corpus carries ~15 renames and collisions
    (cix→gnx, Dijkstra→dagstra, luminex ×2, morpheus ×2, press vs post, semex/shastra
    dead…). HADES writes into the registry — it must not re-mint dead names. Does
    superseded-by/alias become a manifest-level concern?
15. press vs post: the designed-but-unbuilt publishing pipeline (Pandoc/Typst/Vale/
    PaperQA2, packages/press) overlaps post's comms role — fold or compose before
    either registers.
16. Catalog candidates surfaced 2026-06-12, not yet in §2/§5 lists: **niti** (live v0.1
    governance-gate CC plugin in chanakya; hook-layer sibling of geist-edge; x.uma rumi
    as planned evaluator), **veritas** and **labcoat** (the two proven consciences),
    mudge's council + forensic skill (explicitly open-source-candidate), mashq
    (memory says shipped, **no repo found** — locate before reserving the name).

## 11. Two documentation registers + the public boundary (decided 2026-06-12)

**The universe ships in phases, and each component — especially gnx — is useful on its
own.** Standalone value is the positioning premise. GTM work starts the week of
2026-06-15.

**These design docs are not the public docs.** The public gnx docsite — *both*
user-facing and agent-facing — covers the **pragmatics of usage with Claude Code**, with
the domain bounded to **component registry / marketplace**: add the marketplace, install
plugins, `gnx init`, compose components, build and register your own. Do not confuse or
overload devs: no samsara, no Treya, no mox cosmology. The universe gets at most
load-bearing sentences + links out to research/blueprints. (Consistent with 2026-05-25:
"the catalog IS the demonstration"; ethos = sigil + load-bearing sentences, not essays.)

The 10 design docs below are the *internal* register: full picture, for feedback, on the
design surface. Doc 10 (public surface) defines the boundary itself — what gnx says
publicly, what it defers to later phases — as a reviewable artifact feeding GTM.

## 12. The docsite itself (decided 2026-06-12)

Document-driven design: this docsite is the design surface for gnx, built before the
tool. SvelteKit + TS + bun (`docs/experience/`), markdown content (`docs/content/`),
**file-backed feedback**: block-anchored comments written to `docs/feedback/*.jsonl` via
dev-server routes; an inbox page; comment state (open/resolved) in the files; the whole
discourse history in git. yzavyas comments; Claude reads the marks, revises, responds.
The feedback loop is the first running instance of the coordination pattern the ecosystem
is built on. Voice anchor for content: `~/mox/research/drafts/aces/voice.md`. Craft
direction: show-don't-tell; diagrams carry weight prose would dilute; each doc ends with
its open questions.
