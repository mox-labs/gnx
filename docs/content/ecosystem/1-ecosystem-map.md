---
title: "The ecosystem map"
section: ecosystem
status: mixed
mode: explanation
fidelity: cobblestone
---

# The ecosystem map

*Internal. The product decomposition as ruled in discourse 2026-07-06. Each product is one component; each ships `--skill` beside `--help`. Maturity marks are load-bearing — most of this map is direction, not shipped code.*

## The products

**slick** — the semantic LLM-interpretable component kit. Three faces of one thing: the **library** (the slickit crate — Rust core, Python/TypeScript crusts — carrying the five primitives as types), the **CLI** (`slick validate`, `--skill`), and the **CC plugin** (the authoring surface an agent uses to produce new components). Apache-2.0, deliberately catalog-independent: gnx is the home catalog that speaks slick, not the owner of slick. *Maturity: crate shipped (v0.2.0, five-field Manifest + TypedStruct + TypedRegistry); the CLI, real validation, and the plugin are design — and the Python crust is broken (ImportError) with an unpinned ABI.*

**gnx** — generative noetic extensions: the package manager, registry, and marketplace. Scaffolds projects (`gnx init`), validates and admits components (the wall), projects the catalog into installable form per Target, and carries governance (overlay, maturity gates, accreditation). *Maturity: three components and a directory marketplace ship; the CLI is a prototype that cannot yet execute the population; the pipeline is being rebuilt against the ratified contract.*

**geist.sh** — the distribution. Packages three parts into a Tauri shell and/or a deployable container (HUD optional):
- **geistr** — the runtime for agents. Defining property: it reaches the world *only* through geist-edge — capability-based, no ambient authority. *Maturity: direction ruled 2026-07-06; the shipped geist-run chat CLI is the pre-geistr prototype it replaces (that prototype holds ambient file authority, violating the thesis its own docs state).*
- **geist-edge** — the capability edge, both directions: ingress (intercept and evaluate an agent's actions — the shipped processor pipeline) and egress (dispatch to upstream capabilities over HTTP/gRPC/MCP — declared in the proto, unbuilt). A boundary component for anything, not geistr's private organ. *Maturity: Rust skeleton ships (axum-only, no upstream forwarding, one deny-first ACL processor) — and it is currently compile-broken against current slick (imports the ghost `TypedConfig`).*
- **mox.hud** — the view layer: the spatial workspace whose atom is the Panel ("a view of a resource"). *Maturity: design-heavy, code-thin — the Rust kernel doesn't compile, the SvelteKit shell is lost from disk; the panel doctrine and steering ladder (Ambient → Inspect → Steer → Shape) are the durable content.*

**matrix** — the in-process runtime. Python; you write configuration against it (the ix pattern) and it compiles the DAG from declared ports and runs it — components are code you brought, no external mediation needed. *Maturity: the shipped code (in the archived cix repo) is the validated ancestor — 119 consistent tests, but stale docs teaching a dead contract and a standing ruling now superseded. The continuing matrix is a rebuild on slickit; its new home is an open pick (lean: its own package — it is the runtime you don't need the geist distribution for).*

## The two trust postures

The load-bearing distinction between the runtimes is **where the trust boundary sits**, and it maps exactly onto the correctness≠trust doctrine:

- **matrix is the correctness-domain runtime.** Trusted code, in-process. The grammar's contracts — typed ports, compile-time rejection, the single mediated writer — are the entire enforcement story. CAN suffices.
- **geistr is the trust-domain runtime.** The acting principal is an LLM — untrusted by architectural commitment — so every effect transits the mediation plane and **ALLOWED is enforced on top of CAN**. That is *why* "only via geist-edge" is geistr's defining constraint rather than an implementation detail.

One grammar, two enforcement postures. The 2026-04-01 deliberation's guard ("don't build a separate runtime until matrix proves insufficient") is consciously retired: its premise — matrix is THE runtime — was superseded 2026-07-06, and the trust-posture split is a principled reason for two runtimes, not scope creep.

## Claude Code's place: the composer, and the first Target

CC plays two roles:

1. **The first runtime Target.** The catalog projects into CC plugins; an agent installs and runs them. The agent is the runtime adapter.
2. **The composer.** An agent in CC, wearing the slick plugin, reads the catalog and **generates the Flow manifest** — `kind: Flow` plus members `(type_url, config)` — then runs it. The manifest it writes is not a side-file: it is the same artifact gnx validates/registers/projects and matrix compiles and executes (GEP-0003's "one artifact, two consumers"). Composition is authoring a catalog citizen. Agentic flows — those with agent components among their members — execute on matrix via its agent adapters.

The lineage runs straight through the lab: ix's `experiment.yaml` was the proto-version of exactly this — thin config selecting and parameterizing members, a registry resolving `(type_url, config)`, a fixed board underneath.

Runtimes generalize the **Target** concept: CC (via projection), matrix (via config), geistr (via capability session) are three Targets the same catalog serves — the same manifest, three consumers. That is the vendor-neutral-by-structure claim made concrete.

## The boundary map, in one line each

- slick defines **what a component is** (CAN's definition — semantic identity, typed surface).
- gnx registers, governs, and distributes **what exists** (CAN's registry — the wall, discovery, projection).
- geist-edge and policy decide **what is permitted** (ALLOWED — the gap between what a component can do and what it may do; the shell enforces the gap, the HUD makes it visible).
- Runtimes make it **happen**; surfaces make it **legible**; the human steers from outside the DAG.
