# 08 — Decision Ledger

The decisions that govern the radix build, with honest status on each. This is a **staging view** of the authoritative ledger, not the ledger itself. Source of truth: `/Users/yza.vyas/mox/research/meta/decisions.md` and `/Users/yza.vyas/mox/research/meta/missions/radix-od-queue.md`. On any conflict, those files win over this staging copy.

Status legend: **[DECIDED]** ratified · **[LEANING]** principal lean, not closed · **[OPEN]** unlegislated · **[BUILT]** shipped · **[UNBUILT]** designed, no code.

---

## Handle conventions

- **`OD-n`** names an **open act** — a decision the build must make and must not silently resolve.
- **`D-*`** names a **prior ratified decision**.
- Where an `OD` acts on a `D`, the alias appears in the row (e.g. OD-4 confirms D-B2).

**Principal gating order** (the sequence the build must respect):

> **OD-12 → OD-1 → OD-4 → OD-2 (+OD-3)**, then **OD-7 / OD-5** in the build/research lanes; **OD-6 / 8 / 9 / 10 / 11 deferred** (they gate nothing in the current plan).

Authority order on any conflict: **this-session principal input > `meta/decisions.md` OD rulings > 2026-06-09 proposal > 2026-06-02 common model > prototypes.**

---

## RULED (2026-06-14) [DECIDED]

| ID | Ruling | What it settles |
|---|---|---|
| **OD-12** | ratify | Extract the ixian close verbatim into `meta/designs/` as canonical; pin the latency procedure. Unblocks Phase 1.0 harness. |
| **OD-2** | **(b)** amendment stands | radix introduces neither `Grounded` nor a 4th state; the **3-state Status union is retained**; `Grounded` is written **downstream**. CM §4 amended + owed a superseded-by pointer. States `Avowed`'s scope. Gates the Status migration → Phase 1.1. |
| **LEX-1** | (c) | "reads" = sibling grounding deep-reads; "dossier" = decision-staging form. The execution (renaming sibling "grounded dossiers" → "reads") is a downstream follow-up, **NOT done**. |

---

## OPEN — the next gates, in order [OPEN]

| ID | Decision | Gates |
|---|---|---|
| **OD-1** | Third-frame name (**leaning KEEP `Models`**, not closed) + deposit-by-version + exhaustive identifier inventory | Phase 0 deposit PR — **all vocabulary** |
| **OD-4** | Confirm **D-B2** (content-hash terminus for the grounding chain / Anchor; D-B2 is "pending confirmation") | Anchor migration root |
| **OD-3** | Claim→Frame rejection aggregation + Episode verdict enum + halt semantics + the 9-state→union migration map (5 illegal states) | validator/runner + Phase 3b |
| **OD-7** | radix-vis corpus: regenerate vs retire (every existing visual chain is circular by the decided grounding standard) | Phase 4 build |
| **OD-5** | Thematic-role boundary (structural-vaani vs interpretive-consumer) | vaani track |

**OD-1 is the headline open item.** It is *not* closed. The 2026-06-09 rename to `Interpretations` was **reversed this session**; the principal now **leans KEEP `Models`**. See `context/10-open-questions.md` and `context/05-vocabulary-unification.md` §OD-1 for the full divergence (the comprehension-ontology dossier recommends `Construal`; principal leans `Models`). Do not write `Models` as settled — write it as the lean, note OD-1 open, note `Construal` / `Interpretant` were the alternatives and `Interpretations` was proposed-and-reverted.

---

## OPEN — deferred register (gates nothing in this plan) [OPEN]

| ID | Decision | Note for the gnx target |
|---|---|---|
| **OD-10** | Ecosystem shape — **L2 processor vs L3 component**; manifest convention; mint `type_urls`; **gnx placement** | **Directly bears on `gnx/components/radix/` — and UNRULED.** Do not presume the L2/L3 shape. The `manifest.yaml` `kind` field is provisional until this rules. |
| **OD-11** | Canonical radix for distribution (v0.4 fork vs v0.3.4 plugin) | external naming only |
| **OD-9** | Encode Gate 2 in `framework.md`, or amend the framework-wins rule | citation base |
| **OD-8** | D6 — LLM external-API | LLM-fidelity path only; never bundled |
| **OD-6** | D-ilm-1 scope amendment | ilm's domain — out of radix scope (see `context/07-boundaries.md`) |

**OD-10 is the one deferred item that touches this deliverable directly.** The `kind:` literal in `manifest.yaml` (`Processor` vs `Component`) is **PROVISIONAL** and must stay flagged until OD-10 rules. Do not harden it.

---

## Prior ratified `D-*` decisions [DECIDED unless noted]

| Handle | Decision | Status |
|---|---|---|
| **D-JUDGE** | BOTH gates live in radix; radix comprehends and **STOPS**; no downstream terminology | DECIDED |
| **D-B3** | vaani enters as a **parse-stage Component on the board** → RecordStore, swappable behind `NlpProvider`; the substrate-library alternative was **REJECTED** | RESOLVED 2026-06-08 |
| **D-B2** | content-hash terminus for the grounding chain / Anchor | **pending confirmation — OD-4 confirms it** |
| **D-ilm-1** | ilm-side schema recommendation (bi-temporal + `recall_events` + typed-signed edges + modality enum) | **OPEN — needs a principal scope amendment; = OD-6** |

Related settled decisions carried in other context files (not re-litigated here):

- **OD-2(b)** — 3-state `Status` union `{Unjudged, Asserted, Dissolved}`; `Grounded` is downstream. → `context/04-construct.md`.
- **The two gates** are a Russellian type distinction, neither covers the other; three disciplines, not two. → `context/03-gates.md`.
- **The Kintsch retirement** — SURFACE becomes substrate-under-Base; BASE becomes claim-extraction. → `context/01-ontology.md` and the framework §1 scrub.

---

## What "DECIDED" does and does not license

- A **DECIDED** row may be stated flatly in framework prose.
- A **LEANING** row (OD-1's `Models`) must be written as a lean, with the alternative recorded — never as closed.
- An **OPEN** row must **not** be silently resolved by the build. If the build hits one, it stops and surfaces it. The complete list of "must not silently resolve" items is `context/10-open-questions.md`.

Source: `meta/decisions.md`, `meta/missions/radix-od-queue.md`, and the consolidated spec §8.
