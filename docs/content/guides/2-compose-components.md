---
title: Compose components
section: guides
mode: explanation
status: planned
register: public
---

# Compose components

Composing is how the suit assembles its parts. An agent reads declared surfaces, matches ports, and knows what runs together before anything executes. Each component declares what it takes and what it emits; from those declarations alone, a pipeline either compiles or it does not. And every part added to the catalog widens what the agent can reach for — composed, the parts assemble into working sets that none of them reaches alone. That's the move [what gnx is](/docs/what-is-gnx) names.

The interactive composer below runs in your browser — no catalog, no CLI, nothing installed. It's the same kind of check `gnx add` is designed to run at install time. If you've read how ports and tags work, or you've already installed `intent-hardening` from [install a plugin](/docs/install-a-plugin), this is where you watch the same rule reject a bad edge instead of taking it on faith.

---

## Try it

Each card is a component with declared ports. Edit a port, toggle a card off, add one, or stack a new sensor — the pipeline recompiles instantly from the declarations. Break an edge and it rejects, with the reason.

```composer
```

Two things fall out:

- **Parallel batches** — components with no dependency edge between them land in the same batch and run concurrently. The topology computed that; you did not declare it.
- **Compile-time rejection** — a required port with no provider, or two components claiming the same output, is rejected before anything runs. The composer decides *fit* and projects the plan; it never runs anything. Execution belongs to whatever runtime loads the composition — and the design guarantees a runtime never receives an invalid graph. (Composition is proposed, not shipped: no runtime executes a Flow today; Claude Code is the designed first target.)

---

## Why the check requires no execution

Both port roles ride the shipped `requires` and `provides` fields, and the composer's `produces` / `consumes` labels are just readable names for them. Composing is graph work over manifests — matching what one component provides against what another requires, checking the graph is valid — with the components themselves never opened.

(Every cycle is rejected today. A rule that would admit a loop with a *declared bound* — how long it may run, how it converges — is proposed, not settled. Until it lands, first compositions are acyclic by choice.)

The check decides *fit*, not quality — it will wire two things that can talk and say nothing about whether they should. A component's tests and evals ground the behavioural claims — [why a catalog](/docs/why-a-catalog) covers how the catalog keeps structure, behaviour, and meaning on separate axes. The composer does by hand what an agent does from the registry: read the ports, compile the graph, catch what is broken. The legibility bar a surface must clear is [the primary reader's test](/docs/the-primary-reader-is-an-agent).

---

## An agent runs the same three moves

The agent's loop mirrors the composer's three steps:

```
gnx search <tag>       # find components whose provides match the need
gnx inspect <name>     # read the declared ports + the computed portability
gnx add <name>         # install into .gnx/, resolving requires against provides
```

`gnx add` resolves ports the way the composer resolves edges — it refuses a component that `requires` something nothing in the installation `provides`. Same compile-time check, run at install instead of in a diagram. These three commands are designed; today's runnable install path is [a plugin through Claude Code's marketplace](/docs/install-a-plugin).

---

## Where to go next

- **[How components work](/docs/how-components-work)** — the manifest model behind each card: the kinds, and ports vs tags.
- **[Authoring a component](/docs/author-a-component)** — give your own component a legible surface so it composes like these do.
