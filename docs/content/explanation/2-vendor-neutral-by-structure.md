---
title: Vendor-neutral by structure
section: explanation
mode: explanation
status: planned
register: public
fidelity: tarmac
---

# Vendor-neutral by structure

**An agent composing from the catalog today runs on Claude Code — so why describe components in a way no runtime owns?** Because the alternative has a known ending. Agent runtimes redefine how tools register, how memory works, how orchestration works — every few quarters. Couple your capabilities to one runtime's shape and you inherit its churn.

Vendor-neutral by structure is the hedge. A component declares what it is in a form no single runtime defines. A second runtime is then a new **adapter**, not a rewrite.

## The namespace is the vendor scope

The mechanism is plain: a component's namespace says who owns its semantics. Core components live in the vendor-neutral `gnx.dev` namespace; anything specific to one runtime — a hook, a command, a serving surface that only that runtime understands — lives under that runtime's namespace instead. The core never learns a runtime's vocabulary; the runtime-specific surface extends alongside it.

Portability is not a label a component claims. It is **computed** from the namespaces a component actually uses. An author cannot call a component portable if it depends on a runtime-specific surface — the computation will not allow it. The claim and the structure cannot drift apart, because the claim is not made by the author at all.

A catalog whose portability labels are self-declared is only as honest as its worst contributor. A catalog that computes portability from namespace usage is honest by construction.

## Why mediation reduces work instead of adding it

The fair objection: is a neutral boundary not just another layer? Count the integration points.

N components wired directly to M runtimes is **N × M** connections — every component taught about every runtime. Put a boundary between them and it becomes **N + M + B**: each component speaks to the boundary once, each runtime adapts to the boundary once, plus the boundary itself.

```
direct:    N × M
mediated:  N + M + B
```

For any catalog worth having, `N + M + B` is far smaller than `N × M`. The boundary earns its place by collapsing the integration surface. A boundary that adds surface instead of removing it fails that test — which is the test to hold any such layer to.

## By structure, not by good intentions

Neutrality is enforced, not promised. The core namespace is structurally unable to accrete one runtime's semantics — a runtime-specific field on a core component is rejected, not merely discouraged. The reason is blunt: if the neutral core quietly becomes "Claude Code with extra steps," the hedge is gone and nobody notices until the next churn arrives.

This is the live risk, stated honestly. Claude Code is the only runtime target that ships today, so its adapter accumulates all the practice. Vendor-neutral by structure means the structure refuses the drift — not editorial vigilance that has to hold forever.

## Where to go next

- **[How components work](/docs/how-components-work)** — the namespace and the manifest the neutrality rests on.
- **[The primary reader is an agent](/docs/the-primary-reader-is-an-agent)** — why legibility, not just neutrality, is the design center.
