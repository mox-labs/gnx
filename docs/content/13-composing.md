# Composing components

The catalog is not a shelf of tools. It is a lego system — primitives, configurations, and compositions promoted to primitives, all declared through one grammar. The earlier docs listed the blocks by function. This one shows them stacking.

The model has three layers. Build it in that order, because each one depends on the last.

## The socket: one manifest, any internals

Every component clicks into the catalog through one declarative contract — the slick Manifest. Not a base class, not a runtime interface. A small data record. Five fields (`packages/slick/src/manifest.rs`):

```rust
pub struct Manifest {
    pub type_url: String,                        // identity — and the bridge to the runtime
    pub source: String,                          // where it lives (git URL, path)
    pub requires: Vec<String>,                   // input ports: type_urls it needs
    pub provides: Vec<String>,                   // output ports: type_urls it offers
    pub relations: HashMap<String, Vec<String>>, // typed edges (skills, replaces, tested_with)
}
```

Composition is **type_url matching**, declared and checked before anything runs: a component that `provides: ["cix.v1.ReconReport"]` connects to one that `requires` it, by string identity. The gnx target wraps these fields in `apiVersion` / `kind` / `metadata` (§3); the five are the load-bearing core, and `type_url` is the join key that also bridges to the runtime (`Manifest.type_url == TypedStruct.type_url`).

Because the contract is a *data record*, a Rust parser that computes a syntax graph in milliseconds and a Python agent that reasons for minutes are indistinguishable through it. The manifest is the common interface; language is a tier decision (kernel / orchestration / surface, §8). That is the stud every brick clicks into.

A runtime then *realizes* a manifest as executable code. cix's `matrix` is one such runtime — one iteration — and its Python `Component` protocol (`consumes` / `produces` / `run`) is how *that* engine binds a manifest to a running node. It is not the grammar: matrix's `produces`/`consumes` are the topology refinement of the manifest's ports (§3, "provides is discovery; produces/consumes is topology"), realized in one language for one engine. The socket is the manifest. The protocol is a runtime's grip on it.

## The ledger: bricks connect without touching

Components never call each other. They compose through a shared, typed, **append-only** ledger (the comprehension store the runtime carries). A producer writes a frozen, attributed artifact; a consumer reads it by type.

Append-only is the load-bearing constraint. Because nothing can mutate another component's output, two properties fall out for free: anything without a dependency edge runs in parallel, and adding a component that consumes existing types extends the system *with zero change upstream*. This is composability as information-hiding — dependency flows outward to the contract, not sideways to another component's state (§7). Sideways-to-state is the coupling that rots; declared-state is the inversion.

## The combinator: a component that takes components

A runtime plays the higher-order component — the one that takes manifests and yields a runnable. In cix that runtime is `matrix`. It reads only the declarations, **compiles** a dependency graph, validates the topology (a missing producer, a cycle, or two components producing the same type are all rejected before any work runs), sorts into parallel batches, and executes. It is a compiler, not a coordinating agent: the orchestration path is encoded, not inferred. The grammar (the manifest) names the ports; the runtime (matrix here, the geist.sh execution layer in the gnx stack) compiles them.

This is the curation test (§2) made mechanical — *can a capable reasoner decide to compose two components it has never run, from their declared surfaces alone?* The compiler answers it from declarations, before execution.

## Compose one yourself

Below is that compiler, client-side. Each card is a component declaring its ports. Edit a `consumes` or `produces` field and the pipeline recompiles from the declarations alone — nothing runs. Components in the same batch have no dependency edge between them, so they parallelize. Break the topology (consume a type nothing produces, or make two components produce the same type) and the compiler rejects it, the way the real one does.

The seed pipeline is an `ix` evaluation: a probe and a subject (no dependencies, so they're parallel), a trial that runs the subject on the probe, and a sensor that grades the result.

```composer
```

**Now stack rubrix over ix.** Press *stack rubrix over ix* — it adds a grader that `consumes: trial.observation` and `produces: rubric.score`. It slots onto the trial's output with no rewiring, because it declares the type it reads. That is the whole of "rubrix is a composition over ix": a grader is just another component on the same socket; the eval harness is the engine with grader components loaded.

## The three moves, named

The principal's examples are not metaphors. Each is a real mechanic:

- **"matrix composed over for agent judges"** — proven in cix's code today (`tools/ix/src/ix/eval/sensors_deepeval.py`). `ix`'s grader builds its judge from the same registry that builds the subject: `registry.create("matrix.agent.claude", {...})`. The LLM-judge runs through the identical Agent protocol as the thing it judges, so its tokens and cost are observable. The judge is a component resolved by name, loaded into the engine.
- **"when we use ix, we provide the lab and the experiments"** — the configurability thesis. A *lab* is a directory of experiments; an *experiment* is `experiment.yaml` + `tasks/*.md` (probes) + `subjects/*.md` (subjects). The file format is the interface; no Python to add a probe or a subject. From the ACES journal: the *same DAG engine* ran code-intelligence and the eval harness — "because a grader follows the same Component protocol as an encoder." Different subject, different components loaded, same engine.
- **"rubrix is a composition over ix"** — the worked exercise above. The pattern is fully present in cix (the sensor registry, the injectable scoring module, the judge-agent build); the `rubrix` tool itself is not built. It is one new sensor type registered in one map.

## Protocols: absorbing transport diversity

Components don't all speak the same wire. One is an MCP server, another an HTTP API, another a CLI binary, another an in-process function. The grammar above has to compose them identically anyway. It does, by keeping transport heterogeneity off the core grammar entirely and splitting it onto **three separate axes**:

- **Vendor scope → the apiVersion namespace.** `slick.dev/v1` is core; `hooks.claude.anthropic.com/v1` is vendor. The namespace string *is* the vendor scope — there's no vendor field. A Claude Code hook is a vendor *kind* (`ClaudeCodeHook`), not a "protocol."
- **Transport → the Protocol** — the M of MSG, "how to reach a component." A component declares the transport(s) it speaks (MCP, HTTP, CLI, gRPC, function, WASM), and it may declare **several at once**. The brownfield payoff: wrap any existing API with a Manifest plus a Protocol and it's composable. (Where the Protocol *lives* — a Manifest field, per the April proto draft, or inside the implementation config, per the later gnx settlement — is genuinely open; see below.)
- **Dispatch uniformity → the adapter contract.** Four adapters — HTTP, CLI, gRPC, function — normalize each transport's I/O into typed enrichments on the Construct. "One Component trait works across all four because the protocol specifics live in the adapter, not the component." HTTP is the degenerate single-shot case; agentic streaming is the general case. This is the Envoy `ext_proc` pattern (its lineage migrated 59 plugins with zero rewrites) — and it lives in **geist-edge, the runtime**, not gnx. gnx supplies the typed schemas the runtime reads; it does not dispatch.

Skills are the exception: no protocol. They're provides-only axioms, read not invoked — "a Skill that carries protocols is a representable type error."

On top of the three axes: **portability is computed, not declared** (Universal / Specialized / Vendor-Specific / Multi-Vendor, derived from the namespaces a component's closure requires); each **Target adapter declares which namespaces it supports**, and the registry projects the same catalog, namespace-filtered, per Target; and a **Universal Facade** — one vendor-neutral interface in `components/` fronting N vendor-specific implementations in `extensions/<target>/` — lets a consumer ask for "pdf" or "web-search" without knowing the vendor, the runtime picking a concrete implementation by capability.

The fork worth pinning — where the Protocol declaration lives:

```decision
{
  "question": "Where does a component's Protocol (its transport: MCP / HTTP / CLI / gRPC) live?",
  "context": "Settled: a Skill has no protocol; vendor scope rides the apiVersion namespace; runtime dispatch is the adapter contract in geist-edge. Open: how a component declares the transport(s) it speaks. The April proto draft and the May gnx settlement diverge.",
  "alternatives": [
    { "id": "field", "name": "A field on the Manifest" },
    { "id": "impl", "name": "Inside Capability.spec.implementation", "recommended": true }
  ],
  "criteria": [
    { "id": "neutral", "name": "Keeps the core Manifest runtime-agnostic" },
    { "id": "legible", "name": "Legible to the composer up front" },
    { "id": "kindsafe", "name": "Can't put a protocol on a Skill" },
    { "id": "vendor", "name": "Vendor scope stays on the namespace, not the protocol" }
  ],
  "matrix": {
    "field": {
      "neutral": ["partial", "A repeated Protocol field is generic, but invites a vendor-specific protocol type to creep onto the core schema — degeneration-watch #1."],
      "legible": ["pass", "The composer reads transports straight from the Manifest without opening the implementation."],
      "kindsafe": ["fail", "A flat `protocols` list on every kind makes 'a Skill with protocols' representable — Dijkstra's type error (April)."],
      "vendor": ["partial", "Risks conflating transport (MCP) with vendor scope (ClaudeCodeHook); the April draft pushed vendor-specificity toward the Protocol type before it was rejected as the wrong axis."]
    },
    "impl": {
      "neutral": ["pass", "Transport is an implementation detail; the core Manifest carries only identity, ports, relations. Vendor scope stays on the namespace, full stop."],
      "legible": ["partial", "The composer needs the component's generated surface (its --skill / generated manifest) to see transports — not the bare 5-field Manifest."],
      "kindsafe": ["pass", "Only invocable kinds have an implementation to carry a protocol; a Skill (axiom, no implementation) structurally can't."],
      "vendor": ["pass", "Vendor-specificity routes to the apiVersion namespace; 'no new Protocol types needed for ClaudeCodeHook' (historical-context:146)."]
    }
  }
}
```

## Adapters: the swap knob

The same declarations make the runtime swappable. The domain core depends on *ports* (an agent runtime, a config source, a store); concrete runtimes are *adapters*. In the ACES build, the Claude Agent SDK and Google ADK + Ollama run side by side through one runtime port — "swapping Claude SDK for ADK means changing the composition configuration. One adapter replaced, zero component changes, zero orchestrator changes." In the catalog this generalizes to per-Target projection: one catalog, a namespace-filtered adapter per runtime, the NM coupling collapsed to N + M + B (§7).

## The gap this surfaces

Here is the design decision the chapter forces, and it is gnx's, not the docsite's.

A component already declares its ports in its own code — matrix's `consumes`/`produces` are attributes on the component, and the compiler reads them. So the recipe (which components, what config) plus those declarations already form the DAG. The open question is narrower than "add a composition field to the manifest": it is *how gnx learns a component's ports to validate the graph before running it.*

Three ways, and the right one shifts with context — toggle the scenarios:

```decision
{
  "question": "Where do a component's composition edges (consumes / produces) live?",
  "context": "The recipe — which components, what config — is already declarative (an experiment.yaml, a Flow manifest). The fork is the ports: how does gnx know what each component consumes and produces, to compile and validate the DAG before running it?",
  "alternatives": [
    { "id": "declared", "name": "Hand-declared in the manifest" },
    { "id": "runtime", "name": "Read by loading the component" },
    { "id": "generated", "name": "Generated from the component", "recommended": true }
  ],
  "criteria": [
    { "id": "drift", "name": "No drift (one source of truth)" },
    { "id": "polyglot", "name": "Works for py / rust / ts" },
    { "id": "norun", "name": "Compose without running" },
    { "id": "trust", "name": "Safe for untrusted components" },
    { "id": "cost", "name": "Low authoring cost" }
  ],
  "matrix": {
    "declared": {
      "drift": ["fail", "Ports live in both the code and the manifest, kept in sync by hand — they drift. This is the cix version-drift lesson, now on ports."],
      "polyglot": ["pass", "The manifest is language-agnostic; every tier reads it the same way."],
      "norun": ["pass", "Dagstra reads the manifest; nothing loads or executes."],
      "trust": ["pass", "A manifest is data, not code."],
      "cost": ["fail", "The author maintains ports in a second place, forever in sync with the code."]
    },
    "runtime": {
      "drift": ["pass", "The code is the only source; there is nothing to keep in sync."],
      "polyglot": ["fail", "Import-and-introspect works for Python; a Rust crate or a TS bundle can't be loaded and reflected the same way."],
      "norun": ["fail", "Importing a module executes its top-level code. That is running it."],
      "trust": ["fail", "Loading an untrusted component to validate means executing it at plan / registration time."],
      "cost": ["pass", "Nothing to author; the ports are already in the code."]
    },
    "generated": {
      "drift": ["pass", "The code stays the single source; the manifest is emitted from it, never hand-edited."],
      "polyglot": ["partial", "Needs one uniform emit per language — the --skill pattern generalized to ports, where gnx build calls each component's describe step."],
      "norun": ["pass", "Dagstra reads the generated manifest; the component runs only at execution, gated by geist-edge."],
      "trust": ["pass", "The published artifact is data; consumers never execute a component to plan a composition."],
      "cost": ["pass", "gnx build emits it; the author writes the ports once, in the code."]
    }
  },
  "scenarios": [
    { "id": "trusted", "name": "Trusted same-language project", "matters": ["drift", "cost"], "winner": "runtime", "why": "inside one Python project, matrix already loads the components and reads their ports. Load-to-validate is simplest, and it is exactly what cix does today." },
    { "id": "marketplace", "name": "Untrusted cross-language marketplace", "matters": ["polyglot", "norun", "trust"], "winner": "generated", "why": "components are py / rust / ts and submitted by others; you must validate without running their code, so the ports have to be emitted into a manifest at build time." }
  ]
}
```

The synthesis: ports are never hand-declared (that is the drift trap). The code is the source of truth. For a trusted local project, gnx can read them by loading — that is matrix today. For the marketplace, `gnx build` **will generate** the manifest's topology from the component, the same seam that emits `--skill`. One mechanism, two projections: a skill for agents, ports for the compiler. "Compose without running" survives because what Dagstra reads is the generated artifact, not the live module.

## Open questions

- **Generated topology — ratify the seam.** The explorer above settles the shape: ports are generated from the component (the `--skill` pattern), not hand-declared. What is unratified is the seam itself — the uniform "describe your ports" call every component exposes so `gnx build` can emit them across py / rust / ts. That call is the polyglot crux (the one `partial` in the matrix). (Surfaced by the 2026-06-13 composition dive.)
- **Should `rubrix` be scaffolded as a real `ix.sensor.rubric`?** The pattern is proven but the tool is not built. It is the cleanest first "add-a-component" worked example for the marketplace — but presenting it as shippable would overstate it.
- **Where does this chapter sit?** It is placed last for now to avoid renumbering. Pedagogically it belongs right after the grammar (§3), where `provides`/`produces` are introduced — the grammar gives the fields, this shows them composing. Move it there in the next reorganization.
- **Does the composer become a catalog-driven tool?** Right now it runs on a hand-seeded set. If composition edges land in the manifest, the composer could read the real catalog and let a reader compose actual registered components — the docsite chapter becoming a thin client over the registry.

Comment on any block — particularly whether composition edges belong in the manifest, and whether the live composer teaches the model or just decorates it.
