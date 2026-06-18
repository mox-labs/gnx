---
title: Compose components
section: guides
mode: how-to
status: planned
register: public
fidelity: tarmac
---

# Compose components

**Composing means assembling pieces from what they declare — before running any of them.** Each component states what it consumes and what it produces. From those declarations alone, a pipeline either compiles or it doesn't. No execution, no trial and error.

The interactive model below runs in your browser today. The `gnx` CLI that performs this for an agent — `gnx add`, `gnx search` — is planned.

---

## Try it

Each card is a component with declared ports. Edit a port, toggle a card off, add one, or stack a new grader — the pipeline recompiles instantly from the declarations. Break an edge and it rejects, with the reason.

```composer
```

**Parallel batches fall out for free**: components with no dependency edge between them land in the same batch and run at once — the topology computed it, you didn't declare it. **A broken wire is caught at compile time**: a consumed port with no producer, or two components claiming the same output, is rejected before anything runs. That rejection is the point — the orchestrator never starts an invalid graph.

---

## Why this works without running anything

A component's `produces` and `consumes` are typed declarations. Composition is matching outputs to inputs across those declarations and checking the result is a valid DAG — pure graph work over the manifests. The components stay unopened.

A capable reasoner — an agent — can decide whether two components fit from their declared surfaces alone. The composer above does by hand what an agent does from the registry: read the ports, compile the graph, see what's parallel, catch what's broken.

A surface has to be legible for this to hold. `produces: [trial.observation]` against a typed schema is something to reason from. `produces: ["does stuff"]` is not — it compiles to nothing an agent can wire. That's the bar a component meets to be composable at all.

---

## An agent runs the same three moves

When the CLI ships, an agent's loop is the same three moves the composer makes:

```
gnx search <tag>       # find components whose provides match the need   (planned)
gnx inspect <name>     # read the declared ports + portability           (planned)
gnx add <name>         # install into .gnx/, resolving requires/provides (planned)
```

`gnx add` resolves the ports the way the composer resolves edges — it refuses a component that `consumes` something nothing in the installation `produces`. Same compile-time check, run at install instead of in a diagram. Until the CLI ships, [install a plugin](/docs/install-a-plugin) through Claude Code's marketplace.

---

## Where to go next

- **[How components work](/docs/how-components-work)** — the manifest model behind each card: four kinds, `provides` vs the typed ports.
- **[Authoring a component](/docs/author-a-component)** — give your own component a legible surface so it composes like these do.
