# Guild Ratchet

## 2026-07-15: craft-rhetoric intake — grain and naming

### Deliberation
Focus panel: Karman, Burner, Ace, Chesterton (independent, Phase 1) → Lotfi (naming split, Phase 3) → Ixian (validation close). Proposal P: decompose into 9 Agent + 9 Skill + 1 Flow, bare type_urls.

### Verdicts
- Grain: converged APPROVE across all four seats, conditional on re-authoring amendments A1–A4 (no seat accepted file-splitting).
- Naming: three-way split (all-bare / split / family-scoped) → Lotfi Policy B at 0.83: personas + craft-rhetoric bare; 7 generic gerunds re-mint compound-descriptive before first serialization.

### Principles Extracted
> "Constitutive dependency is not identity-merger — an agent bound to a skill is a component with a declared relation, not a component that cannot exist alone." (Karman)

> "A mechanically-split unit whose interior still assumes the bundle is a component whose declared grain lies. Decomposition is re-authoring, not file-splitting." (Karman/Ace)

> "The type_url is the join key that travels everywhere without its manifest — that is precisely where the name must carry context." (Ace)

> "Prefixing encodes a packaging decision into permanent component identity — the dependency inversion the whole design bans. The naming axis is collision class, not bare-vs-scoped: proper nouns bare, generic words compound-or-justify." (Burner/Lotfi)

> "Serialization is the point of no return; everything upstream of it is reversible, nothing downstream is. No fidelity comparison without a measured noise floor first." (Chesterton/Ixian)

### Future Triggers
- Any cix-plugin intake: run the A1 relations-closure linter check and the V2 prediction test before mint; genericity gate applies to every bare candidate (`review`, `design`, `research` are known incoming landgrabs).
- Flow-as-projection is composition-conditional: a bundle with no pipeline meaning goes through the §8a distributions/ open edge — do not mint fake Flows.
- V11 pre-registered: compute cross-Flow skill reuse R at the next intake batch; R < 0.15 re-opens the naming weighting, R ≥ 0.30 vindicates Policy B.

## 2026-08-17: gen-0 intake — manifests deferred; slick's format has moved

### Deliberation
Gen-0 intake landed: matrix/ix/recon as capability packages, 34 agents + 34 skills as
per-unit components, 10 plugin projections, `just check` green. Manifests were held out
of the intake by the principal's original instruction. Mid-intake the question was
re-opened ("do we actually need manifests?") and settled against evidence rather than
doctrine.

### Verdicts
- **The marketplace does not need manifests.** 3 manifests against 69 payloads; `gnx build`
  runs off `components/bundles.yaml` + payload frontmatter. `load_components()` feeds only
  the `check_layout` cross-check. All 10 plugins project and pass `claude plugin validate`
  with the manifest layer essentially absent.
- **What manifests buy is Flow topology**, not projection. They stay as harness-local
  drafts in `harness/drafts/` so `flow-dao-init` and `flow-research` compile-check
  (GEP-0003 provisional) without minting anything.
- **DEFERRED by ruling (yzavyas):** leave manifests out, so gnx dogfoods slick's *real*
  format at the point it converts to a slick marketplace — rather than authoring 68
  manifests against a shape that has already been superseded.

### Principles Extracted
> "slick's shipped Manifest is SD-02 · the floor — seven fields: schema_version, name,
> summary, kind, spec, supersedes, attestations. `name` is non-referential — nothing may
> resolve by it. `kind` is derived from the Spec variant, so a kind/spec mismatch is
> unrepresentable. Ports live inside the kind-gated spec as a named map port-name →
> type_url (SD-05). `supersedes` is Digest content-addressing and is the only
> inter-revision relation (SD-03), replacing free-form `relations`." (verified against
> `~/mox/packages/slick/src/manifest.rs:726` + `DECISIONS.md`, 2026-08-17)

> "SD-04 — three kinds live, Agent RESERVED. `check_kind()` returns Err(ReservedKindError)
> for Spec::Agent; no spec law exists to shape it (parked P-01). No valid slick manifest
> can exist for an Agent component today — and 34 of gen-0's components are Agents."

> "GEP-0001/0002/0009 specify an overlay (slick-5 + kind + maturity + version) written
> against a slick that no longer exists. The GEPs, not slick, are the stale artifact."

> "slick enforces no-kind-in-type_url itself (`type-url-kind-segment`, unit-tested),
> independently confirming GEP-0001 §3. The `flow-` prefix is a filename convention only;
> it must never enter an identity." (this is what keeps a kind reclassification a two-way
> door — radix went Processor-kind → Capability-shape 2026-08-02 without a rename)

### Future Triggers
- **Before ANY manifest is minted:** re-read slick `src/manifest.rs` + `DECISIONS.md` and
  run GEP-0001/0002/0009 against the current seven-field kind-gated shape. The 2026-07-15
  entry above already binds this — serialization is the point of no return.
- **Any Agent component proposed for minting:** check SD-04 first. Until P-01 unparks and
  Agent spec law exists, an Agent manifest cannot validate; the payload is the whole
  component.
- **`research` bare remains a landgrab** (2026-07-15 gate): the research Flow minted its
  identity as `gnx.dev.v1.research-pipeline` — cix's `craft-` prefix dropped per GEP-0001
  (provenance never in identity), the genericity gate satisfied by the compound.
- **Namespacing over hyphen-compounds for a family:** `gnx.dev.v1.dao.init` does not parse
  (GEP-0001 §4 — resource is exactly one segment after the version). A family belongs in
  the namespace: `gnx.dao.v1.{init,figures,charter,projection,instance}`.

---

## 2026-08-18 — tarmac pass on the capabilities, and what running the code found

**Ixian.** Three defects reached this pass. Two were found by `mypy --strict`, one by running
the lab. All three were in the same place — the boundary between what a sensor reports and
what aggregation records — and all three produced a **silently wrong measurement** rather
than a crash.

> `aggregate_readings` derived `passed = score > 0.5`, discarding `Reading.passed`. Binary
> sensors agree; `FunctionTestSensor` does not. A submission failing 1 of 4 tests scored
> 0.75 and reported PASS while the sensor had already ruled it incorrect.

**In a measurement tool, a wrong verdict is worse than a crash, because a crash gets
noticed.** The eval harness is the thing every other quality claim rests on; a bug there
does not announce itself, it just makes every subsequent number a little bit false.

**Taleb.** `mypy --strict` found two of three. The lab found the third, on its first run,
in a case no reviewer had thought to look at. A type checker proves the shapes agree; only
execution proves the *semantics* do. The lesson is not "types are insufficient" — it is
that **the eval suite must exercise its own sharpest path**, and `sensor-integrity` now
includes code that fails its tests and code that raises, not only code that passes.

**Vector.** recon's raw capture wrote every response header into `meta.yaml` verbatim. A
`Set-Cookie` put a live credential into the one file whose entire purpose is to be kept,
committed, and handed to a colleague.

> The audit artefact was the leak. Redaction preserves the key and replaces the value —
> dropping the header would destroy the evidence the file exists to hold.

**Ace.** CI ran `docs-check` and a secret scan. It did not run the 407 tests, the five
strict typechecks, the grammar gate, the payload gate, or the projection check — all of
which `just ci` already declared. **A gate that only a local pre-commit hook runs is a gate
that protects the author and nobody else.**

### Future Triggers
- **Any new sensor, or any change to `analysis.py`:** the sensor grades, aggregation counts.
  Re-deriving a verdict from a score is the defect above; `Reading.passed` is authoritative.
- **Any new `Sensor` implementation from outside ix:** `Reading.score` is optional by
  contract. Aggregation must not assume it is populated.
- **Anything that persists a fetched response** (a new collector, a new store): headers are
  credential-bearing. Extend `SENSITIVE_RESPONSE_HEADERS` rather than trusting the source.
- **Before claiming a package is hardened:** run it. `matrix` and `ix` both execute things;
  `ix`'s `FunctionTestSensor` runs untrusted Python in-process with no sandbox, and on a
  platform without SIGALRM there is no timeout at all. Both facts are in `SECURITY.md`
  because an undocumented trust boundary is one nobody reviews.
- **Adding a gate to the justfile:** wire it into `.github/workflows/ci.yml` in the same
  commit, or it protects only the machine it was written on.

---

## The curation discipline (imported 2026-08-21 from `research/drafts/ratchet-curation`)

This file is append-only and has been growing since June without a curation rule. The
`ratchet-curation` mission settled one and it had never been folded back into the thing it was
written about — a ratchet about keeping memory net-positive that never reached the memory.

Its spine finding: **naive cumulative accumulation is a mis-specified quality signal.** An
append-only log that only grows reports health by volume while losing competence silently.
Organizational decay measured ~62% departure-without-transfer and ~38% aging-in-place, and
aggregate flow metrics understate both.

The six rules, as they apply here:

1. **Dedup at entry.** Admission is the cheap place to curate. Before appending, check whether
   an existing entry already carries the finding; if so, extend it rather than adding a
   near-duplicate. Storing everything is itself the failure mode (Finding 5).
2. **Staleness marks, not erasure.** When an entry's referent drifts — a file moves, a ruling
   is superseded — mark it stale in place. Never delete: the entry is evidence of what was
   believed when.
3. **Consolidation as appends.** Supersession is a new entry that names what it supersedes.
   Never rewrite history; the 2026-08-18 entry on the three ix defects stays exactly as
   written even though the code has moved on.
4. **Competence-weighted retention.** Value, not flow counts. An entry earns its place by the
   decision it would change, not by being recent.
5. **Diversity preservation.** Keep niche occupancy, not top-k. Entries covering rare failure
   modes are the ones a top-k pass would drop and the ones most worth keeping — top-k recovers
   short-term quality while collapsing diversity (Finding 3).
6. **Departing threads must transfer.** The turnover lever. When a line of work ends — a
   mission closes, a component is benched — its findings transfer into this file *before* the
   context is gone, or they are lost with it.

### Future trigger

- **Before appending here:** rule 1. Check for an existing entry on the same finding.
- **When a component moves to `incubator/` or a mission closes:** rule 6. The transfer is the
  point at which the knowledge either survives or does not.
- **This file has no eviction policy and does not need one yet.** Rule 2 forbids deletion, so
  growth is bounded by rules 1 and 4 at the entry point instead. If it ever stops being
  readable end-to-end, that is the signal to revisit — and the mission's PART 3 lists what
  would falsify each rule, which is where to start rather than improvising.

