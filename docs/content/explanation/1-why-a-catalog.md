---
title: Why a catalog
section: explanation
status: mixed
register: public
---

# Why a catalog

**Capability gets trapped in the project that first built it.** A review panel you tuned, a research scaffold you refined, a skill you distilled — they live in one repo, and the next project rebuilds them from scratch. The waste is not the first build. It is the rebuild, every time, because there was nowhere to put the thing so it could travel. And when the runtime that housed it is deprecated, the capability goes with it — because it was described in terms that runtime owned, not in terms another one can read.

A catalog is the place to put it: distill a capability once, register it in a form no single runtime owns, compose it into any project that declares it needs it.

## Why a registry, specifically

Storing capability is easy. The hard part is **composition before execution** — an agent deciding two components fit without ever having run either, because it is choosing among candidates, not testing one, and running each to find out would cost more than the task is worth.

That is what a registry buys that a folder of plugins does not. Each entry declares its surface — what it provides, what it requires, how it is reached — in a form legible enough to reason over. An agent reads those declarations and decides what composes, with no trial-and-error execution. The registry exists to keep those declared surfaces legible and trustworthy enough to build on.

This is the structural bet: composition from declaration, not from execution. An agent that must run a component to learn what it does cannot safely compose it with anything else. An agent reading a well-formed manifest can.

## What the structure actually buys — and where it stops

The reliability gain from this is real. Because every component declares a typed surface — what it takes, what it emits — a composition is **checkable before anything executes**: a mis-wire fails when the graph is compiled, not at runtime, not in production. And a component authored *into* that grammar is **compose-by-construction** — a structurally non-conformant artifact fails validation at the boundary instead of composing wrong and failing later. That is what makes the self-extension loop survivable: when an agent authors a missing part, the part either declares a legible surface or it does not clear the gate.

The ceiling: **this makes structure checkable; it does not make generated content correct.** A manifest that declares clean ports still says nothing about whether the component's output is *true*, or its judgment sound. Structural conformance is calibration — it makes fit cheap to check and failure early — not correctness. A component that declares it handles sensitive data and one that declares it writes logs compose cleanly at the structural level and can still leak: whether that composition is *safe* is a separate question, and the catalog does not pretend to answer it by checking shape.

## Structure is machine-checked; trust is human-minted

gnx certifies **structure** — that a component is well-formed, that its declared inputs and outputs match, that its namespace is in scope. **Trust** is a separate axis, signed by a person. The two stay separate by design, and collapsing them into a single verdict is the one thing gnx is built to refuse.

- **Structure** is machine-checked. `gnx validate` — designed — certifies the manifest is well-formed, the ports match, the namespace is in scope. A structural claim in the catalog is a checked claim.
- **Behaviour** is tested and asserted, never proven. A component ships its tests, and evals record how it actually behaves. Those are auditable assertions a composing agent can read — not guarantees.
- **Meaning** is described, never certified. A discovery tag is a claim to search on, not a promise the catalog enforces. And tag polish can never *be* the trust signal: if a nicer description minted trust, polishing would mint trust. Whether a composition is fit for purpose is the judgment a person signs.

## Why curated, not open-write

The catalog is human-curated, and that is a feature, not friction. An agent composes from what the catalog says — so what the catalog says has to be trustworthy. **Accreditation** — designed with the registry — is human-mintable: a status an agent can read and build on, but cannot mint for itself. The catalog grows through agents; it stays trustworthy because accreditation is structurally external to the agent doing the composing.

Accreditation is advisory, not a gate. An unaccredited component still installs and still composes — nothing blocks it. What accreditation changes is the agent's *decision*: a human-signed status it weighs when choosing what to build on. This separation is also what lets the loop stay safe — because trust is external, an agent can author and submit a new part without ever vouching for its own. A person does that. That generative loop is where [what gnx is](/docs/what-is-gnx) begins.

## Standalone value is the premise

A component earns the catalog by being useful on its own — installable and worth installing without the rest of the stack. The catalog's two shipped components — intent-hardening and rational-inquiry — install into Claude Code today and do real work with nothing else present. Each piece stands alone; the catalog is how they find each other, and how the next project picks them up without rebuilding them. A person decided each one was worth keeping before it ever composed with anything else; every later entry enters the same way.

The boundary cuts the other way too: a capability that never leaves its project and composes with nothing does not need the catalog — a project-local skill file is fine. gnx starts paying the moment a second project would rebuild it, or a second component must reason about it from its declaration.

## Where to go next

- **[What gnx is](/docs/what-is-gnx)** — the catalog's three functions and where the shipped/designed line sits.
- **[Vendor-neutral by structure](/docs/vendor-neutral-by-structure)** — why a component is described in a way no single runtime owns.
- **[The primary reader is an agent](/docs/the-primary-reader-is-an-agent)** — the legibility bar every surface has to clear.
