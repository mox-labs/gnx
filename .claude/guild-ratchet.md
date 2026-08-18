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
