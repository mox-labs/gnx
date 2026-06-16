# Hard ACE — Enforcement by Code and Gates

> §7 decided 2026-06-12. Three loci: catalog boundary, scaffolds, process. None of these are
> guidelines. Each is a gate that rejects or a scaffold that makes the wrong shape impossible to
> produce.

---

## 1. Enforcement must be structural, not advisory

ACE (Adaptable, Composable, Extensible) decays unless enforcement is structural. Advice
documents rot; gates don't. The doctrine is explicit: every enforcement point in gnx ships
as code that rejects at a boundary, or as a scaffold that generates the compliant shape — never
as documentation that requests compliance.

---

## 2. Locus 1 — The catalog boundary rejects non-compliant shapes

The catalog is the primary enforcement surface. Everything registered in gnx must pass
`gnx validate` before it is visible to Dagstra, Claude, or any downstream consumer.

### 2.1 gnx validate is strict and non-advisory

`gnx validate` is not advisory. It rejects on:

- Missing or malformed `apiVersion` / `metadata.name`
- `kind` that carries no type-level consequences (label-only kinds reopen the refuted k8s
  homonym; gnx refuses them at registration)
- Namespace scope violations (a component declaring core `slick.dev/v1` semantics it does
  not own is rejected; vendor surfaces land in their own apiVersion, e.g.
  `hooks.claude.anthropic.com/v1`)
- Missing `provides` on a non-Skill component (invisible to composition; should not exist)
- Missing embedded skill on a `kind: Capability` (hard catalog rule, decided 2026-06-12:
  a Capability without `--skill` is not agent-operable and does not clear the curation bar)

The rule on Skills:
```yaml
# VALID — Skill is provides-only, an axiom in proof-search
kind: Skill
provides: [rust-patterns, ownership-model]
# no produces/consumes — by design; DAG validation skips Skills

# REJECTED — Skill declaring port topology is a type error
kind: Skill
produces: [analysis-report]   # gnx validate: kind:Skill may not declare produces/consumes
```

### 2.2 Owner identity is a first-class schema field, not a convention

The registry schema treats authorship identity as a first-class field — not a convention, not
a comment. This is §9's "make-or-break": until minting a registration is structurally
restricted to an authority disjoint from the mark's author, every accreditation record is
forgeable.

`gnx validate` must verify the registrant's identity against the component's declared
namespace. The exact schema field is [open] (§10 item 1 — relation on Manifest vs
`kind: AccreditationRecord` vs side-table), but the constraint is not open: the wall ships
before accreditation is used as an autonomy signal by x.uma.

### 2.3 Accreditation is append-only and human-mintable only

Accreditation records are written by `gnx accredit` and are human-initiated. The SDK
session inside `gnx init` may *propose* a curation set; deterministic code validates and
writes. This is the exogenous-anchor property (§3, §9): if gnx's own verdicts were
LLM-minted, the anchor would dissolve into the loop it exists to break.

```
SDK session proposes → gnx validate (deterministic) → registry write
                                    ↑
                          rejects here on any violation
                          no partial writes
```

### 2.4 The core namespace is structurally closed to vendor semantics

The `slick.dev/v1` namespace is structurally closed to Claude Code semantics. Vendor
surfaces (`hooks.claude.anthropic.com/v1`, `commands.claude.anthropic.com/v1`) are separate
apiVersions with their own validation paths. The degeneration watch (§8) names this as risk
#1 and #3; the catalog boundary is its structural counter.

A component in `slick.dev/v1` that references Claude hook vocabulary fails `gnx validate`.
Not a warning — a rejection.

---

## 3. Locus 2 — Scaffolds make the non-ACE shape unproducible

`gnx init` generates the project structure. The generated structure is the enforcement
mechanism — not the documentation that comes with it.

### 3.1 cix shipped unwired hooks; gnx init wires them in code

cix shipped `.githooks/pre-commit` and `.githooks/commit-msg`. Neither was ever wired.
The hooks exist at `/Users/yza.vyas/mox/products/cix/.githooks/` but `.git/config` carries
no `hooksPath` entry. Git ignores them entirely.

gnx init fixes this as a required step, not a recommendation:

```bash
# gnx init runs this — not in docs, in the init code
git config core.hooksPath .githooks
chmod +x .githooks/*
```

CI mirrors hooks. If the hook runs `uv run ruff check`, the CI job runs `uv run ruff check`
with the same invocation. The justfile is the single source of truth for both:

```just
lint:
    uv run ruff check
    uv run ruff format --check

check: lint test   # mirrors CI; hook calls `just lint`
```

The hook calls `just lint`. The CI job calls `just lint`. They cannot drift.

### 3.2 Each tier ships one skeleton with pre-wired gates

Three tier shapes, one discipline:

| Tier | Shape | Gates |
|------|-------|-------|
| **Python** (orchestration) | cix/matrix hexagonal core: `domain/`, `ports/`, `application/`, `adapters/`; `uv` for deps; `ruff` + `mypy --strict` | pre-commit: `just lint`; CI: `just check` |
| **Rust** (kernel) | vaani/x.uma leaf-crate shape; workspace `Cargo.toml`; `clippy -D warnings` | pre-commit: `cargo clippy -- -D warnings`; CI: same |
| **TS** (surface) | ziran/SvelteKit ports discipline; `bun`; `svelte-check` + `tsc --noEmit` | pre-commit: `just check`; CI: same |

"Leaf crate" for Rust means: no binary-crate logic in a lib crate, no circular workspace
dependencies, each crate compiles independently. This is the vaani/x.uma precedent.

### 3.3 Python tier scaffold file tree

```
my-project/
├── .githooks/
│   ├── pre-commit          # calls: just lint
│   └── commit-msg          # conventional commit check
├── .github/workflows/
│   └── ci.yml              # calls: just check
├── justfile                # single source of truth for all gate invocations
├── src/
│   └── my_project/
│       ├── domain/         # pure types, no I/O
│       ├── ports/          # abstract interfaces (Protocol classes)
│       ├── application/    # use-case orchestration
│       └── adapters/       # concrete I/O (CLI, HTTP, filesystem)
├── tests/
├── pyproject.toml
└── .gnx/                   # installed component projections
```

`.gnx/` is the plugin-style projection of installed catalog components. `gnx init` writes
this structure; `gnx add` installs into it.

### 3.4 `gnx component init` scaffolds lexicon entries

```bash
gnx component init capability    # generates: manifest.yaml + skill/SKILL.md + tests/
gnx component init skill          # generates: manifest.yaml (provides-only) + SKILL.md
gnx component init flow           # generates: manifest.yaml + ports declaration
```

The generated manifest.yaml for a Capability includes a `--skill` stub that the scaffold
makes non-optional: the file exists, the `relations["skills"]` field points to it. The
developer fills in content; the structure is pre-wired.

---

## 4. Locus 3 — The dao runs process enforcement at defined trigger points

Process enforcement is the softest locus but still code-and-gates, not advice.

### 4.1 Guild review fires on file-pattern triggers, not discretion

guild-arch runs on design proposals, not on implementation PRs. The trigger is a design
doc or manifest commit to `docs/` or `manifests/`. The pre-commit hook can fire
guild-arch's trust-boundaries scanner; the CI job gates merge.

### 4.2 Antifragile reviews proposed abstractions

antifragile runs an ACES review on proposed abstractions. It is triggered by `gnx validate`
when a new `kind: Capability` is registered: the registration check includes a structural
review gate, not just manifest validation. [open] — exact trigger point and whether this is
a gnx validate step or a separate `gnx review` command is undecided.

### 4.3 Evals gate accreditation, not registration

craft-evals gates Flows before they enter the catalog as accredited. A Flow that has not
passed evals can be registered but not accredited. Accreditation is the signal x.uma reads
for autonomy ceiling decisions. The gate has downstream consequence.

### 4.4 Session hooks wire the architectural ratchet

guild-arch's ratchet loader fires on SessionStart via aboot. The ratchet is an
append-only record of decisions. aboot's `write-resume.sh` pattern — generating
`.claude/resume.sh` per repo — becomes a registered component whose SessionStart hook
wires this automatically. The hook is wired by `gnx init`, not installed by docs.

---

## 5. Each tier maps to one language by §7 doctrine

The tier decision is §7 doctrine, not a preference:

| Tier | Language | Criterion | Current examples |
|------|----------|-----------|------------------|
| Kernel | Rust | Deterministic, long-lived, leaf-crate, no LLM in the loop | slickit; gnx registry core only if evidence demands |
| Orchestration | Python | Agentic pipelines, SDK sessions, hex core, uv distribution | matrix, gnx CLI, agentic init interview |
| Surface | TS | CC-ecosystem adjacency, browser-native, SvelteKit | this docsite, public catalog site |

The Rust kernel retreats to "later, if evidence demands" (the slickr precedent). The
current gnx CLI is Python (leaning, not hard-decided; §3 §10 item 4). TS is not an
orchestration language in this stack: it lives at the surface, where CC adjacency matters
and npm is native.

---

## 6. The hard catalog rule: three conditions, no exceptions

Every `kind: Capability` in gnx:

1. Ships an embedded skill at the path declared in `relations["skills"]`
2. Responds to `--skill` by emitting a complete SKILL.md (activation frontmatter + intent→command table)
3. Passes `gnx validate` (checks items 1 and 2 at registration)

A Capability that fails any of these three is not registered. Not warned. Rejected.

`gnx --skill` is gnx's own front door. gnx is a Capability component. It must satisfy its
own rule.

---

## Open questions

**Priority call needed — which boundary checks land in v1?**

The catalog boundary has at least five distinct checks (kind consequences, namespace
isolation, embedded-skill requirement, produce-authority identity, accreditation
append-only). They cannot all ship simultaneously on day one. The question is which checks
are hard-gates at v1 registration vs. later additions. The answer determines the
schema decisions hardest to retrofit (produce-authority is explicitly called out
as "hardest to retrofit" in §9).

1. **Produce-authority schema** (§9, §2.2 above): field name, format, verification
   mechanism; whether it blocks registration or only accreditation at v1.
   This is the hardest to retrofit; it should be decided before any registration schema
   is frozen, even if enforcement is partial at first.

2. **Antifragile trigger point** (§4.2 above): does the ACES boundary test run inside
   `gnx validate`, as a separate `gnx review` step, or as a CI job? The placement
   determines whether it's a hard gate or a softer signal.

3. **Accreditation record shape** (§10 item 1 in ground-truth): relation on the Flow's
   Manifest vs `kind: AccreditationRecord` vs side-table. Partly slick's call, but gnx
   needs to know before it writes any accreditation plumbing.

4. **Init hook-wiring on non-git repos**: `git config core.hooksPath` requires a git repo.
   Does `gnx init` fail on a non-repo, create one, or skip hook wiring with an explicit
   warning? The cix lesson is about hooks that silently do nothing — the fix must not
   create a new silent failure mode.

---

Comment on specific blocks — particularly §2.2 (produce-authority), §3.1 (hook-wiring
implementation), and the v1 priority question in Open questions.
