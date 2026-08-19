---
title: "The feedback loop"
section: build
register: internal
mode: explanation
status: shipped
---

# The feedback loop

> The comment mechanism on this site is not a docs feature bolted on. It is the first running
> instance of the coordination pattern the whole ecosystem is built on: parties leave marks on a
> shared surface, and the next party reads the marks and acts. Here the parties are a human and
> an agent, and the surface is this doc set.

---

## A mark anchors to a block

Select any text and a comment floats up. It anchors to the block by its content hash, plus a quote and a little prefix/suffix context — so the mark survives small edits elsewhere and detaches only when *its* block changes. Block-level commenting works too. A mark renders inline: open marks in the accent color, resolved ones in green. Click one to open its thread.

Events append to `docs/feedback/<doc>.jsonl` — comment, reply, resolve, reopen. The thread you see is a projection derived from that event log, so the full discourse history lives in the file (and in git), not in a database. The inbox at `/feedback` collects open marks across the site.

---

## The loop closes when understanding converges

The cycle is plain:

```
human reads → comments on a block → agent reads the marks → revises → resolves
```

The comment is the human's interpretant exposed; the revision is the agent's response to it; the resolve is the convergence. This is why the content-hash anchor matters: when a revision rewrites a block, the mark on it detaches, and that detachment is the signal that the thing commented on is gone. A surviving mark means the agent left that block alone — also signal.

---

## Why this shape, and not a review tool

A pull-request review optimizes for approve/request-changes on a diff. This optimizes for **shared understanding** on a living surface. The doc set is the design surface; the comment loop is how two minds reconcile their models of it. That a human and an agent can coordinate this way — stigmergically, by reading each other's marks rather than messaging — is the thing the ecosystem is built to scale. The docsite proves it on the smallest possible instance first; whoever builds the next mark-reading surface — a board, a ledger — starts from a pattern that already ran.

---

## See also

- **[How this site works](/dossier/appendix/how-this-site-works)** — the content-hash anchors the marks ride on.
- **[The collaboration method](/dossier/appendix/the-collaboration-method)** — the loop as one move in a larger discipline.
