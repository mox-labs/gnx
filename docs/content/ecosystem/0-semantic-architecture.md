---
title: "The semantic architecture"
section: ecosystem
status: mixed
mode: explanation
fidelity: cobblestone
---

# The semantic architecture

*Internal. This is the alignment document — what the whole ecosystem is made of, semantically, and who owns which piece. Converged in discourse 2026-07-06 (yzavyas × Fable) after five deep-read verifications of the shipped artifacts. Where a claim is one party's synthesis rather than corpus doctrine, it is marked.*

## The sprawl, and what it actually is

The ecosystem's history looks scattered: matrix has components-with-contracts over a board; ix has type_url→factory registries and a fixed DAG; recon declares catalog+collectors bound by name; geist-edge runs processors from a typed registry; the hud designed panels-with-manifests; luminex sketched a Flow. Three things carry the name "slick," five carry "geist," two carry "Construct."

That is not scattered ideas. It is **one idea, independently implemented about five times, each in a local dialect** — because no shared layer existed to depend on, every subsystem re-derived the same machinery and minted local vocabulary doing it. Convergent evolution is evidence, not mess: five independent derivations of the same primitives is the strongest argument available that the primitives are right. *(The "convergent evolution as evidence" framing is an **[extension]** — ours, built on GEP-0003's triple-derivation argument, not a written-down corpus claim. The [argued]/[extension] marking discipline is defined in [the reliability ledger](/dossier/ecosystem/reliability-and-honesty).)*

The clean-room job is therefore **extraction and canonization, not invention**.

## The five primitives

Everything in the ecosystem reduces to five semantic atoms. *(The set-of-five framing is an **[extension]**; each primitive individually is **[argued]** — corpus doctrine with shipped instances.)*

| Primitive | What it is | Already lived as |
|---|---|---|
| **the mark** | `TypedStruct` — a `type_url` plus an opaque value; the atom of data | slick `TypedStruct`, matrix component outputs, xDS `Any`/`TypedStruct` |
| **the contract** | `Manifest` — identity, source, typed ports (`requires`/`provides`), relations; the atom of description | slick's five fields, matrix's component protocol, hud panel manifests |
| **the member** | `(type_url, config)` — the atom of composition | matrix `build_orchestrator` specs, ix `registry.create`, recon collectors, xDS `TypedExtensionConfig`, GEP-0003 Flow members |
| **the board** | an append-only ledger of provenance-wrapped marks with a single mediated writer; the atom of coordination | matrix `Construct`, recon's timestamped run archives, luminex's radix face, the hud's "the board mediates collaboration" |
| **the registry** | `type_url → factory/payload` resolution; the atom of instantiation | matrix `ComponentRegistry`, ix's "one registry, one pattern," slick `TypedRegistry`, Envoy's extension registry |

## The semantic rules

1. **One identity joins everything.** `type_url`, matched by string equality — the only coupling permitted between planes. `Manifest.type_url == TypedStruct.type_url` is the bridge, and there is no second shared symbol.
2. **Coordination by vocabulary, never co-location.** Components share the grammar, not each other's interiors. A producer emits a mark of a known type_url; any consumer that understands it can act, with zero access to the producer's inside.
3. **Declared contracts, derived topology.** Members are declared; wiring is computed from ports; nothing hand-wires edges. Selection among N providers of one interface is explicit config, made per composition (recon's `source:` binding is the shipped precedent).
4. **Config is data, never code — defined or opaque.** A member's config validates against a schema when one exists and rides as an opaque mark until then (ix's Pydantic-at-the-factory is the shipped proof of the defined half).
5. **M/S/G separation.** Structure lives in the struct (M); meaning lives in payloads — SKILL.md, `--skill` (S); governance lives off the manifest entirely (G) — on the catalog's overlay and the runtime's policy, never in slick core.
6. **Governance is structurally external.** No agent-internal mechanism can govern composition (the Rice's-theorem derivation in the geist research cluster). The catalog walls admission; the runtime enforces policy; neither delegates to the acting agent's self-judgment.
7. **Humans are exogenous.** Steering is appends to the board through the single mediated writer — epochs, not compiled cycles. The human is in the oversight loop, never a DAG node. No source in the corpus models the human as a scheduled member.
8. **The ceiling is calibration, not correctness.** The grammar makes composition legible and checkable before execution; it never makes generated or described content true. Any surface that promises more is overclaiming (see [the reliability ledger](/dossier/ecosystem/reliability-and-honesty)).
9. **Mint once, alias forever.** Serialized names are permanent; lineage and supersession live in `relations`, never in identities. Applies to type_urls, GEP numbers, enum values, and project names alike.

## The four planes, and who owns what

**slick — the language.** The five primitives as types, plus their validity: `slick validate` is the grammar's own conformance check (shape, identity grammar, port/tag rule, structural composition invariants). slick is simultaneously the semantic surface an LLM *reads* (the founding SLICK thesis) and the kit it *authors with* (lib + CLI + CC plugin). The contract is **wire-normative** — a component that never imports the kit but emits conformant shapes is a first-class citizen — and the kit is **blessed**: the reference implementation everyone actually links. This is the xDS split exactly: the protos are the contract; go-control-plane is the SDK. slick owns the *nouns* of composition and never the verbs — no scheduling, no execution, no policy.

**gnx — the economy.** The package manager and marketplace: minting (namespace authority), admission (the wall — what only a catalog can judge: uniqueness, the overlay contract, maturity gates, accreditation records, intake conventions), discovery (the provides index a composer searches), projection (catalog → Target), and scaffolding (`gnx init` — projects). cargo plus crates.io. gnx calls `slick validate` as its first gate and adds catalog judgment on top; it owns the *registry of CAN*, not the definition of CAN.

**Runtimes — plural, adapters over the grammar.** The verbs: schedule, execute, transport, mediate, enforce. Two trust postures exist by design (see [the ecosystem map](/dossier/ecosystem/ecosystem-map)): the correctness-domain runtime (matrix — in-process, config-composed, contracts are the whole enforcement story) and the trust-domain runtime (geistr — the acting principal is an untrusted LLM; every effect transits geist-edge, and ALLOWED is enforced on top of CAN). Claude Code is the first shipped runtime adapter, reached by projection; the agent is the adapter.

**Surfaces — human-operability.** Views derived or declared from the same manifests; the board's serialized form is what a viewer tails; panels are catalog citizens like any component. The symmetry that governs this plane: *skill is to agent-operability as view is to human-operability* — both travel with the artifact, both attach through `relations`.

Cross-cutting both: the governance axis (the wall, policy, accreditation — CAN vs ALLOWED, with policy as the deliberate gap between them) and the honesty axis (maturity marks, the calibration ceiling, argued-vs-extended discipline).

## What the sprawl resolves to

Under this architecture every existing artifact has exactly one status:

- **Canonical home** — slick (primitives + validity), gnx (the economy).
- **Validated ancestor** — matrix-as-shipped (its compile-time rejections, single mediated writer, double-entry produce-check survive as *inherited semantics*, not code), the ACES blueprints (n=29, +21pp — comprehension, not generation), cix's plugins (intake payload).
- **Plane instance** — geist-edge (enforcement), Claude Code (first runtime Target), mox.hud (surface plane, tarmac grade).
- **Pattern to inherit** — ix's experiment-as-directory (the proto-project `gnx init` generalizes), recon's template/instance config split (a preset is itself a catalog citizen), the timestamped archive-as-ledger discipline.

The architecture survives the two questions it deliberately leaves open — where the component run-interface lives (kit vs runtime package vs structural), and the kind count (four blessed literals + an open string, per GEP-0009) — which is the test of its shape: no answer to either forces a rename or a re-plumb.
