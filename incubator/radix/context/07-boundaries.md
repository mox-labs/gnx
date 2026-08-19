# 07 — Boundaries: where radix stops

Status legend: **[DECIDED]** ratified · **[LEANING]** principal lean, not closed · **[OPEN]** unlegislated · **[BUILT]** shipped in a prototype · **[UNBUILT]** designed, no code.

---

## The line, in one sentence

radix comprehends and **stops**. Its job is total and bounded: **Artifact → collect + compose Frames → ground each → judge each → asserted comprehension, STOP.** Ratified as **D-JUDGE**. [DECIDED]

Relevance/ranking and decay/eviction are **downstream**. radix introduces no downstream vocabulary — but the Construct interface must not **preclude** the downstream reads. That is the whole boundary discipline: *do not name, do not preclude.*

Why it is load-bearing: naming downstream machinery inside radix has, in prior cycles, "spawned fake forks" — a `ResonanceRanker` or a decay clock sketched inside radix drags the whole comprehension core toward concerns it should not own. The discipline exists because the failure has already happened.

---

## Three things downstream of radix [DECIDED]

| Concern | Owner | radix must NOT |
|---|---|---|
| **Relevance / ranking** — 4D resonance score, `ResonanceRanker` | **ilm** (read-side, query-time) | not rank, not compute relevance; naming `ResonanceRanker` inside radix violates D-JUDGE |
| **Decay / eviction** — hot→cold tier, scheduled sleep-cron + write-admission | **REP-021** (RQ4) | no decay, no eviction; **no deletion ever** — the ledger is immutable; eviction only moves tiers |
| **Recency-as-ranking** — `last-recall` clock, fold over a `recall_event` ledger | **ilm** | not compute or store a recency scalar |

The recency/decay pairing is a known trap. Recency (the high tail — recently recalled, rank up) and decay (the low tail — long unrecalled, evict) are **two scalars over ONE shared `recall_event` ledger**. Collapsing them into a single number is the error — MemoryBank's mistake. radix owns neither scalar; it must not even store the ledger's recency fold.

Source: `drafts/ilm/relevance-synthesis.md`; REP-021 (RQ4); `meta/decisions.md` (D-JUDGE).

---

## Verification-audit tiering is NOT downstream decay [DECIDED]

One easy confusion to head off: radix **does** cost-tier its Records (`Record.tier ∈ {cheap, expensive, pinned}`, cost-weighted eviction of heavy payload sidecars). That is a **verification-audit** economy — how much it costs to recompute a grounding witness — **not** the downstream hot→cold memory decay REP-021 owns. Same word ("eviction"), different concern:

- radix Record tiering: evicts heavy *recompute payloads* to save space; the Frame's `record_hash` and grounding survive; nothing about relevance or memory age.
- REP-021 decay: evicts *comprehension* from working memory by age/recall; scheduled; relevance-driven.

radix moves Record tiers; it never deletes a Frame and never evicts by relevance. See `context/04-construct.md` §storage.

---

## Interface constraint — do not preclude, do not name [DECIDED]

radix introduces no downstream vocabulary — no `ilm`, no `SMRT`, no integration-phase, no reduction, no `Grounded` status (that status is written *downstream*, see `context/04-construct.md` §Status and OD-2(b)). **But** the Construct interface must leave three doors open:

1. **Shared-Construct reading** — the `ConstructStore` interface must be readable by a downstream consumer.
2. **A ranking Component downstream of asserted Frames** — asserted Frames must be enumerable/queryable so ilm can rank them.
3. **The `recall_events` ledger** that both ilm-ranking and REP-021-eviction will read.

"Do not preclude" is a design test, not a feature. radix builds none of the three; it must not make any of them impossible.

---

## What is NOT there today (honest BUILT-state gap) [BUILT-state]

radix-text as-built does **not** yet preserve the hooks the downstream will need. Recording this so the gap is not mistaken for a decision:

- Source valid-time survives **only** inside `artifact.meta` JSON — not a typed, queryable column.
- `frames.created_at` = **comprehension** time (episode `started_at`), not source valid-time.
- No `recall_events` table.
- `edges.kind` has no typed-signed `CITES / REFUTES / EXTENDS / SYNTHESIZES`.
- No P/A/D (proposed/accepted/superseded) modal enum.

Net: "there is nothing to rank by yet." This is a gap to *not preclude closing*, not a gap radix closes itself. Source: `tools/radix-text/` schema; `drafts/ilm/relevance-synthesis.md`.

---

## D-ilm-1 — an ilm recommendation ABOUT radix, not a radix task [OPEN]

**D-ilm-1** proposes schema additions to *enable* downstream ranking: a bi-temporal split (transaction time vs valid time), a `recall_events` table, typed-signed edges, a modality enum, and a `ResonanceRanker`.

Its status is precise and must be respected:

- It is an **ilm-side recommendation**, **OPEN**, gated behind a **principal scope amendment** (= **OD-6**).
- It **cannot lean on D-B3** — that is a different seam (vaani parse entry, `context/06-modality-registry.md`). One resolved seam does not authorize another.
- radix **must not name or build it**, but **must not preclude it**.

Surfacing source valid-time as a typed queryable column is the concrete first item D-ilm-1 would touch — and it is explicitly **gated behind OD-6 / D-ilm-1**, not a free radix schema decision. Source: `meta/decisions.md` (D-ilm-1); `drafts/ilm/relevance-synthesis.md`.

---

## Keep the two temporal concerns distinct [DECIDED]

There are two clocks in play and they are not the same clock:

- **radix's HISTORY/SNAPSHOT axis** (see `context/02-temporal-axis.md`) is a **comprehension** concern — it produces HISTORY *frames* and SNAPSHOT *frames* about how an artifact became what it is vs what it is now.
- **ilm's bi-temporal recency** (transaction time, valid time, last-recall) is a **ranking** modulator, downstream, a query-time fold over `recall_events`.

radix produces the frames; ilm folds the recall ledger. The one legitimate touchpoint is **source timestamps** — and even that is gated behind OD-6 (above). Do not let the history-frame axis smuggle in a recency scalar.

---

## Boundary discipline in the manifest

The same rule binds `manifest.yaml`: downstream consumers (ilm ranking, REP-021 decay) must **not** appear in `relations`. Naming them there violates D-JUDGE. A future `relations.consumed-by` could point *inward* from ilm's own manifest — but radix's manifest stays silent on what is downstream of it. See the component `manifest.yaml` (parent directory) and its OD-10 note.
