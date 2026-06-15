# The component standard — the bar every component is held to

> Promoted from §7 (Hard ACE), §7a (the antifragile doctrine), and §8 (the degeneration
> watch). This is the standard each component page is written against: which decay channel its
> shape breaks, how it survives the Boundary Test, what would degenerate it. A component that
> cannot answer these is not a component — it is a feature waiting to rot.

---

## Why a standard, and not taste

Every platform decays through three endogenous channels. They are multiplicative, and Opacity
self-conceals — it hides the cost from the people who would fund the fix.

| Channel | Coupling | The diagnostic question |
|---|---|---|
| **Stasis** | system-to-terrain | Can you change the runtime without rewriting the core? |
| **Drag** | contributor-to-platform | Can a team add capability without the platform team mediating? |
| **Opacity** | behavior-to-integration | Can you predict composed behavior from the declared description? |

Three "no"s and the cycle is compounding: Opacity feeds Stasis feeds Drag. gnx is an **SDK
Frontier** instance — it enters through Stasis, because agent-SDK surfaces deprecate every two
to four quarters and switching cost accrues from day one, before any backlog exists to create
drag. The standard exists because the decay is structural; so the counter must be structural
too.

---

## The three properties, and the channel each one breaks

ACES is not three virtues. It is three mechanisms, each aimed at one channel.

| Property | The mechanism in gnx | Channel it breaks |
|---|---|---|
| **Adaptability** | Manifest is wire-equivalent; apiVersion namespaces; adapter capability negotiation | **Stasis** — a new vendor or runtime is absorbed as a namespace + adapter, never a rewrite |
| **Extensibility** | Typed Manifest schema; vendor surfaces extend by namespace, never by editing the core; O(1) validation at the catalog boundary | **Drag** — an extension does not queue behind a gnx maintainer |
| **Composability** | `requires`/`provides` ports; declared composition; skills travel via `relations` | **Opacity** — composed behavior is predictable from declarations, before execution |

A caveat the doctrine keeps honest (grammar-of-computation audit, June 2026): the
**composability** leg adds little over classical modularity. Lean its argument on
information-hiding, dependency-inversion, and antifragility — not on composability-as-novelty.
A component page that claims "composable" as if it were new is overselling; say what the
declared surface actually buys.

---

## The Boundary Test — the answer to the Inner Platform objection

The strongest objection to any ACES boundary is that it is an Inner Platform: a layer that adds
surface instead of removing it. The test is arithmetic, not rhetorical.

N components coupled directly to M runtimes = **N × M** integration points. Under gnx mediation:
**N + M + B**, where B is the boundary itself — the Manifest spec, the catalog, registration
validation.

```
direct:     N × M          (every component wired to every runtime)
mediated:   N + M + B       (each speaks to the boundary once)
```

For any non-trivial (N, M), `N + M + B < N × M`. A boundary that fails this test is not a
boundary — it is the Inner Platform the objection warns about. **Every component page states
which N and which M its boundary collapses.** A component that only ever sees one runtime and
one consumer has N = M = 1, where the test is indifferent — that component earns its boundary
on a different argument (isolation, identity, accreditation), and the page must make that
argument explicitly rather than invoke the network effect it does not have.

---

## The degeneration watch — what voids the standard

A boundary degenerates the moment its protocol surface stops being runtime-agnostic. Three
standing watch conditions (§8, 2026-05-25):

1. **The Manifest core accretes vendor semantics.** A `slick.dev/v1` component that references
   Claude Code hook vocabulary in its `provides` has already broken — that vocabulary enters
   the search index and the composition graph becomes CC-shaped for non-CC consumers.
2. **The CC Target adapter becomes the de-facto spec because no second Target ships.** This is
   the **live** risk: Claude Code is the only current surface, so the universal/vendor split is
   unproven under load until a second runtime binds the core.
3. **Accreditation encodes vendor trust policy** rather than staying vendor-neutral data that
   a trust layer reads.

The `components/` ↔ `extensions/` directory boundary is where watch #2 is enforced
structurally: a vendor-specific field on a core component breaks the namespace-as-vendor-scope
invariant and corrupts portability computation for everything downstream of it.

---

## What every component page must answer

The standard is not abstract. Each component page in the catalog answers, concretely:

- **Design** — which decay channel does this component's shape break, and through which
  mechanism? (If the answer is "none," the component is a feature, not a boundary-aligned
  component.)
- **Architecture** — how is it structured so the mechanism holds: its kind, its `provides`/
  `requires` surface, its namespace, its embedded skill?
- **Affordances** — what does it let an agent (or a developer) do, and how is it composed from
  its declared surface alone — without running it first?
- **The Boundary Test** — which N and M does it collapse, or, at N = M = 1, what other argument
  earns its boundary?
- **Maturity** — established / decided / open for its design; shipped / planned / proposed for
  its implementation. The two axes are independent: a design can be settled while the code is
  unbuilt.

A page that asserts value without naming the channel, or claims a boundary without surviving
the test, has not met the standard — regardless of how polished the prose is.

---

## How the standard is enforced, not just stated

The standard is held by the same three loci that hold Hard ACE (§8): the **catalog boundary**
(`gnx validate` rejects non-compliant shapes — kind consequences, namespace isolation, the
embedded-skill rule, the produce-authority wall), **scaffolds** (`gnx component init` generates
the compliant shape so the wrong shape is harder to produce than the right one), and the
**dao** (antifragile runs the Boundary Test on proposed abstractions at a defined trigger
point). Advice rots; gates and scaffolds do not. The standard is a gate, not a guideline.

---

## Open questions

- **Where the Boundary-Test gate runs.** Inside `gnx validate`, as a separate `gnx review`
  step, or as a CI job? The placement decides whether the standard is a hard gate or a softer
  signal (§8 doc, open #2).
- **The N = M = 1 exemption.** Many founding-set components currently see one runtime (Claude
  Code) and a narrow set of consumers. The standard says they earn their boundary on a
  non-network argument — but the catalog has no field that records *which* argument, so the
  claim lives only in prose. Should the registry compute or record it?
- **Composability's weight.** If the composable leg adds little over classical modularity, does
  the standard over-weight it by giving it equal billing with Adaptability and Extensibility?
  The honest framing is settled; the rubric weighting is not.

---

Comment on specific blocks — particularly the Boundary Test's N = M = 1 case, the live
watch #2 risk, and whether composability deserves equal billing.
