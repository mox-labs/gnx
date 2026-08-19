# Bring the gnx docsite into register — a craft-rhetoric documentation pass

Work at `~/mox/products/gnx`. This is a **register pass** over the documentation content,
run through **craft-rhetoric**, calibrated against **external exemplars**, gated by **gemini**.
Read the whole brief before touching a file. Comprehension is the goal — yours first, then
the reader's.

---

## 0. Mission — two registers, one goal

- **PUBLIC** (Start / Guides / Reference / Explanation) — **pragmatics**. Every page answers
  **what, why, and how from the usage lens**: what it lets you (or your agent) *do*, why that
  matters, how you do it. Value and affordance before structure; name the property, not the
  mechanism; architecture only as support; every capability claim maturity-marked.
- **INTERNAL** (Design = the `01–13` dossier, The Build) — **technical depth, clarity,
  completeness**. The mechanism *is* the subject: architecture, design rationale, what was
  rejected, provenance. Keep the established / decided / open distinctions. Do **not** dilute
  these to "pragmatic" — their value is the depth.
- **THROUGHOUT** — **comprehension**: understanding that propagates. A reader can *reconstruct*
  the argument, not just repeat it. A property of each whole section cohering — not one doc.

---

## 1. Source context — build understanding thoroughly, first

Read these before writing a line. Do not introduce a claim that does not trace to them.

1. `docs/ground-truth.md` — **the factual authority.** Read it whole. Anchor sections: §1
   (ecosystem), §2 (what gnx is — the product framing), §3 (the CLI surface), §4 (the grammar:
   4 kinds, the 5-field manifest), §8/§8a/§8b (Claude Code projection, repo layout, protocol
   diversity), §9 (accreditation), §11 (the public/internal boundary).
2. `~/.claude/CLAUDE.md` (constitution) + the **ci-scaffolds** skills
   (`~/.claude/plugins/cache/cix/ci-scaffolds/0.6.0/skills/`).
3. `docs/RUBRIC.md` + `docs/_handoff/01-RUBRIC-EXTENSION.md` (the gate; register profiles; H/I/J).
4. The exemplar pages in §3 below — **go read them**; they are the external bar.
5. `docs/decisions/log.md` (doors closed this build) + `docs/_handoff/02-PLAN.md` (the milestones).
6. Run it: `cd docs/experience && bun dev --port 5183`; `bun run check`.

**Test of understanding before drafting:** you can state, cold, what gnx *is* (a registry +
marketplace + CLI of composable cognitive extensions), *why this shape* (the antifragile/ACES
rationale), and *what is shipped vs designed* (slick v0.2.0 + the cix plugin family ship; the
gnx CLI/registry/marketplace are designed). If you cannot, re-read §1.

---

## 2. Style / content guidelines — the two registers in practice

**Public — pragmatics.** Open every page with the affordance and the stakes, then the
mechanism as support. The exemplar is **already written: `docs/content/start/1-what-is-gnx.md`**
— it leads with "a catalog your agent builds from" → *what you can do* → *why it matters*,
with structure demoted to a "How it's organized" section. **Match its register; do not rewrite
it.** A public page that opens by describing structure ("gnx is three things…") is in the
wrong register — flip it to lead with the affordance.

**Internal — depth.** Open with the design question or the rationale; the mechanism is the
subject. The exemplars are the existing `docs/content/01-*.md … 13-*.md` dossier. Hold the
KEP discipline (§3): an explicit *Non-Goals*, *Drawbacks/Alternatives* (what would make this
wrong, what was rejected), and *Version Skew* (how namespaces/versions coexist) belong in a
design doc — required, not optional.

**Voice (both registers), the `RUBRIC.md` criterion-A anchor:** direct technical precision;
short declaratives that carry load (staccato — short sentence, elaboration, back to short);
architectural "X does this; you do that"; concrete before abstract. Em-dashes are **kept** (a
deliberate gnx ruling). No marketing.

**Comprehension is the gate on both:** if a reader cannot reconstruct the argument from the
page, it is not done — regardless of register.

---

## 3. Exemplars per section — external signal, go read and check against

Real, established OSS docs, each re-verified live (2026-06-15). Read the page, then check the
gnx doc against the one concrete thing named. Two are gnx's **actual design lineage** — its own
DNA written down well.

### Public register (pragmatics)

- **Astral uv — Projects guide** · https://docs.astral.sh/uv/guides/projects/ — *highest
  scope-match: a Rust CLI that consumes a registry.* **Check:** every runnable command is
  followed by the **literal output it produces** (the file tree, the real manifest, the printed
  result) so success is self-verifying. When a gnx page says install/compose an extension, does
  it show the resulting state — what the registry returns, what the composed manifest looks like
  — or stop at the command? Also: the one-line value opener.
- **Stripe API Reference** · https://docs.stripe.com/api — *the prose-to-runnable bar, itself
  LLM-aware.* **Check:** each what/why claim has its exact composable artifact (the invocation,
  the registry payload) **co-located and copy-runnable beside it**, not described and left to
  reconstruct. Note Stripe addresses the LLM reader in-band — gnx's primary-reader thesis.
- **Bun — Quickstart** · https://bun.com/docs/quickstart — *the marketplace-of-capabilities
  entry.* **Check:** an agent/dev goes from "I want capability X" to the literal discover/compose
  command in **one hop**, value stated in one line beside the invocation — no "overview-first"
  tax. Each quickstart step adds exactly **one** idea and shows its output. *(Anti-pattern, do
  NOT emulate: Astro's getting-started — https://docs.astro.build/en/getting-started/ — a
  resource-hub splash that presumes intent before stating what/why/first-command.)*
- **llms.txt + MCP adoption** · https://llmstxt.org/ — *gnx's load-bearing primary-reader-is-an-
  agent axis; nothing else covers it.* **Check:** gnx ships an actual machine-readable index an
  agent fetches **first** to enumerate the whole doc surface (each entry annotated with what it
  is and when to read it), plus a clean `.md`/structured form per page — not the agent scraping
  rendered SvelteKit. Test: a cold agent enumerates the entire surface from one index before
  reading any page.

### Internal register (depth)

- **Kubernetes KEP template** · https://github.com/kubernetes/enhancements/blob/master/keps/NNNN-kep-template/README.md
  — **gnx's DESIGN LINEAGE** (the apiVersion-namespace + kind/spec model). **Check:** every gnx
  design doc carries an explicit **Non-Goals**, **Drawbacks/Alternatives** (what was rejected),
  and **Version Skew** (how namespaces/versions coexist). KEP makes "what we rejected and how
  versions coexist" required.
- **Envoy ext_proc filter** · https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_proc_filter
  — **gnx's DESIGN LINEAGE** (the adapter contract; "59 plugins, zero rewrites"). **Check:** the
  adapter/protocol doc separates the **prose model from the typed contract** — points to the
  `api/` `type_url` schema as authoritative (same `type.googleapis.com` convention gnx uses),
  no field-level prose that will drift — and enumerates failure modes + the degenerate-vs-general
  phases (HTTP = Mono, streaming = Flux).
- **MCP Registry** · https://modelcontextprotocol.io/registry — *closest full structural analog;
  gnx's namespace-trust precedent.* **Check:** gnx can state in **one sentence** what the
  registry is deliberately unopinionated about and what the marketplace layer adds — not blurring
  "registry" and "marketplace" — and identity/accreditation is namespace-verified (reverse-DNS /
  apiVersion).
- **The Cargo Book — Publishing** · https://doc.rust-lang.org/cargo/reference/publishing.html —
  *closest analog for author→register; CLI + registry + manifest grammar = gnx's shape.* **Check:**
  the author→register doc specifies the **exact required manifest fields** plus the registry's
  **permanence/versioning/yank rationale** — not just a command sequence. Its Guide-vs-Reference
  split is itself the model for gnx's public-vs-internal cut.
  - *Secondaries (for the per-component description bar and discovery-time quality signal):*
    Anthropic "Define tools" · https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
    (the description is "by far the most important factor in tool performance" — same reader,
    Claude Code); JSR scoring · https://jsr.io/docs/scoring (mechanical doc-quality at discovery
    — the accreditation-marker analog; open in a browser, it 403s a plain fetch).

---

## 4. LLM-speak anti-patterns — must not appear

Generic generated filler no human with a point of view produces. Grep for these after each
stage; if any survive, the page is not done.

- **Hedging stacks:** "it's worth noting that," "it's important to understand," "it should be
  noted," "keep in mind that."
- **Symmetric constructions:** "not only X but also Y," "it's not just X, it's Y," "X isn't
  merely Y."
- **Trailing-significance clauses:** "which is significant because," "this matters because,"
  "underscoring the importance of," "highlighting the fact that."
- **Empty topic sentences** that restate what the next sentence says.
- **Adjective / verb inflation:** powerful, seamless, robust, comprehensive, rich, vast,
  cutting-edge, game-changing; leverage, utilize, unlock, empower, supercharge, delve, dive into;
  realm, landscape, tapestry, journey. (The `RUBRIC.md` criterion-A hard bans: *just, really,
  very, powerful, seamless, robust.*)
- **Throat-clearing openers:** "In this section we will…," "Before we dive in…," "Let's
  explore…," "It's worth taking a moment to…."
- **Rule-of-three padding** ("fast, simple, and reliable") and **conclusion restatements** ("In
  summary," "To sum up," "At the end of the day").
- **Register-announcing:** "this is the public doc," "as a design doc," "per §11…".

**Em-dashes are KEPT** — do not flag them as a tell. Flag only em-dash *overuse*: three or more
parenthetical em-dashes in one paragraph, or a dash doing evasion work where a full stop belongs.

---

## 5. The rubric + the gemini gate

Evaluate every changed doc with the **out-of-family gemini eval** against `docs/RUBRIC.md`,
told the doc's **register** so it loads the right profile (public pages get H/I + pragmatics;
internal pages get J + depth; all get D factual fidelity). claude is excluded as the maker
family — it optimizes, gemini judges.

```
gemini -p "<rubric + doc + register>" --approval-mode plan --skip-trust   # strip the "Ripgrep" warning line
```

Loop: gemini eval → optimize the flagged passages → gemini re-eval. **RETURN** on any hard-gate
< 4 or any criterion ≤ 2; **TIGHTEN** on a single non-gate at 3; **SHIP** when clean.
Regenerate `docs/evaluation.json` (with the H/I/J columns) so the in-app scorecard reflects the
new state. The LLM-speak grep in §4 is part of the gate, not a nicety.

---

## 6. Pipeline, constraints, done

**craft-rhetoric pipeline:** vyasa (arrangement) → feynman (comprehension/explanation) → sagan
(make it land) → orwell (voice) → tufte (figures where they earn it) → ebert (the gate).

**THE RULE (learned the hard way this build):** craft-rhetoric agents must **write to the
content files directly** — Read / Edit / Write in place. They must **not** return narrated prose,
rubric reviews, or process traces as output. Chained and asked to "return the markdown," orwell
returns its review commentary instead and contaminates the result. The artifact is the file on
disk. After each stage, grep the files for leaked `Voice review summary`, `ORWELL REVIEW`,
`**A. Voice Fidelity**`, `Entry: Door 2`, `Socrates loops` — and strip any that appear.

**Hard constraints:**
- **Maturity honesty** (hard gate): shipped / planned / proposed on every capability claim;
  shipped → imperative how-to ok; planned/proposed → explanation/reference register only, never
  command an unbuilt action. slick v0.2.0 + the cix plugin family are SHIPPED; the gnx
  CLI/registry/marketplace are PLANNED.
- **Public/internal boundary:** no cosmology (the loop, geist.sh, samsara, Treya,
  Bodhi/Dagstra/HADES) on the public surface — internal Design only.
- **Substrate:** keep the frontmatter convention (`section / mode / status / register /
  fidelity`), the ` ```composer ` / ` ```decision ` island fences, the content-hash block
  anchors (a large rewrite moves a block's anchor — that is signal in the feedback loop, but be
  deliberate). `bun run check` after edits.

**Scope note:** the existing public Start pages to re-register are `0-overview` (light),
`3-how-components-work`, `4-cli-reference`, `5-author-a-component` (leave `1-what-is-gnx` — the
exemplar). Writing the still-unwritten public sections (Guides / Reference / Explanation) is the
broader build; if you want only the re-register pass first, do that and stop before new sections.

**Definition of done:** the public section reads as coherent **pragmatics** (what/why/how from
the usage lens), the internal section as coherent **technical depth**; comprehension propagates
across each section; no LLM-speak survives the §4 grep; every claim maturity-honest; the gemini
gate green; the site renders (routes 200, islands compile, a comment anchors, badges show).
**Do not commit** — the git workflow is owed first (Phase 6 of `docs/_handoff/02-PLAN.md`).
