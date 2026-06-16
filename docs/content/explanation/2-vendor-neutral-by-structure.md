---
title: Vendor-neutral by structure
section: explanation
mode: explanation
status: planned
register: public
fidelity: tarmac
---

# Vendor-neutral by structure

**Claude Code is the only runtime gnx targets today — so why describe components in a way no runtime owns?** Because the alternative has a known ending. Agent SDKs redefine how tools register, how memory works, how orchestration works — every few quarters. Couple your capabilities to one SDK's shape and you inherit its churn.

Vendor-neutral by structure is the hedge. A component declares what it is in a form no single runtime defines. A second runtime is then a new **adapter**, not a rewrite.

## The namespace is the vendor scope

The mechanism is plain: a component's namespace says who owns its semantics. Core components live in a vendor-neutral namespace (`slick.dev/v1`). Anything specific to a runtime — a Claude Code hook, a Claude Code command — lives in that vendor's namespace (`hooks.claude.anthropic.com/v1`). The core never learns vendor vocabulary; the vendor surface extends alongside it.

So "portable" is not a label a component claims. It's computed from the namespaces it actually uses. A component that depends on a vendor-specific hook cannot truthfully call itself universal — the computation won't let it. The claim and the structure can't drift apart.

## Why mediation reduces work instead of adding it

The fair objection: isn't a neutral boundary just another layer? Count the integration points.

N components wired directly to M runtimes is **N × M** connections — every component taught about every runtime. Put a boundary between them and it's **N + M + B**: each component speaks to the boundary once, each runtime adapts to the boundary once, plus the boundary itself.

```
direct:    N × M
mediated:  N + M + B
```

For any catalog worth having, `N + M + B` is far smaller than `N × M`. The boundary earns its place by collapsing the integration surface. A boundary that adds surface instead of removing it fails that test — gnx is built to fail it loudly.

## By structure, not by good intentions

The discipline is that neutrality is enforced, not promised. The core namespace is structurally unable to accrete one runtime's semantics — a vendor-specific field on a core component is rejected, not discouraged. The reason is blunt: if the neutral core quietly becomes "Claude Code with extra steps," the hedge is gone and nobody notices until the next SDK churn arrives. Vendor-neutral by structure means the structure refuses the drift, so editorial vigilance isn't the thing standing between you and lock-in.

## Where to go next

- **[How components work](/docs/how-components-work)** — the namespace and the manifest the neutrality rests on.
- **[The primary reader is an agent](/docs/the-primary-reader-is-an-agent)** — why legibility, not just neutrality, is the design center.
