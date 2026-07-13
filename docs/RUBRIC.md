---
title: "Evaluation rubric"
section: build
register: internal
mode: reference
status: shipped
fidelity: cobblestone
---

# GNX Genesis Dossier — Evaluation Rubric

This rubric is read before evaluating any doc in the gnx genesis dossier (`docs/content/`). The dossier is an internal design surface — full picture, for feedback, before public launch. Its register was established once (`_brief.md` and `ground-truth.md §11`). Evaluators apply this rubric per-doc; optimizers fix against the specific criteria that fail. Seven criteria. Hard gates as marked. One verdict.

**This rubric is a living instrument.** When a doc surfaces a failure mode the rubric doesn't catch, the rubric is wrong, not just the doc — add the criterion. Criterion G was added exactly this way: the line "grounded in Frames" in the landing abstract passed A–F clean, yet named an internal type where a value claim needed a property. That gap became G. The rubric is itself reviewable; comment on it.

---

## A. Voice Fidelity

**What it checks.** The PROTECT features from the yzavyas voice anchor: direct technical precision over hedging; short declarative sentences that carry load (staccato rhythm — short sentence, elaboration, back to short); architectural "X does this; you do that" framing, never apologetic or aspirational; concrete examples before abstractions, never generic placeholders; states rather than hedges; no "just," "really," "very," "powerful," "seamless," "robust." If it reads like a committee wrote it, it has been smoothed too much.

**Em-dash ruling.** Em-dashes are KEPT for gnx. The aces book-register and the existing docs (`03-grammar.md`, `05-dao.md`) use them deliberately for parenthetical precision and rhythm. Do NOT flag em-dashes as a tell or violation. Flag only em-dash overuse: three or more em-dash parentheticals in a single paragraph, or a case where the parenthetical phrase should stand as its own sentence and the em-dash is doing evasion work.

| Score | Meaning |
|---|---|
| 5 | Every sentence states; architectural framing throughout; staccato rhythm intact; no hedges; voice is unmistakably yzavyas |
| 3 | Mostly direct but occasional hedge or aspirational framing; rhythm softens in one or two blocks |
| 1 | Pervasive hedging, passive construction, or committee-smoothed prose; architectural register is gone |

**Hard gate: score must be >= 4 to ship.**

---

## B. No LLM Tells

**What it checks.** Patterns introduced by LLM generation that flatten or pad: hedging stacks ("it's worth noting that," "it's important to understand that"); symmetric constructions ("not only X but also Y"); trailing-significance clauses ("which is significant because...," "this matters because..."); empty topic sentences that restate what the next sentence will say; adjective inflation ("powerful," "seamless," "robust," "just," "really," "very"); throat-clearing openers ("In this section we will explore...," "Before diving in..."). These are distinct from voice hedges — they are generic filler no human writer with a point of view produces.

| Score | Meaning |
|---|---|
| 5 | Zero tells; every sentence earns its place; no filler openers, no symmetric constructions, no trailing significance |
| 3 | One or two isolated tells (a single "it's worth noting," one symmetric pair); does not accumulate |
| 1 | Tells cluster; multiple hedging stacks, repeated symmetric constructions, or pervasive adjective inflation |

---

## C. No Register-Announcing

**What it checks.** The doc must NOT announce its own register. Phrases like "this is internal context," "the public docs won't say this," "per §11 the public surface...," "since this is a design doc," or any variant that names the dossier's internality — these are violations. The dossier is internal by definition; `_brief.md` established the register once. Every subsequent doc re-establishing it is noise that breaks the reading contract.

**Critical distinction.** A doc's Open Questions section may discuss whether a specific idea should go public (e.g., "Should the Sabha verdict grammar live in the public charter or stay internal?"). That is a real design question about content routing — not register-announcing. Score that as clean. The violation is a doc using its register as framing or justification for what it covers, not a doc reasoning about where specific content belongs.

| Score | Meaning |
|---|---|
| 5 | No register-announcing; the doc is simply *in* the register, not describing itself as being in it |
| 3 | One borderline phrase that names internality in passing but does not use it as framing |
| 1 | The doc repeatedly names its own register, or opens or closes with register justification |

**Hard gate: score must be >= 4 to ship. A score of 1 or 2 is an automatic RETURN regardless of other scores.**

---

## D. Factual Fidelity

**What it checks.** Every claim traces to `ground-truth.md` or a named source. The established/decided/open registers are kept distinct — nothing open is stated as settled, nothing decided is stated as established, nothing established is hedged as open. Provenance markers are preserved exactly ("recovered 2026-06-12; in no draft," "[synthesis]," "untested"). Recency weighting is respected: June 2026 > May > April > Q1 > 2025. Stale doctrine is not rehabilitated — e.g., the 8-kind list is buried; it stays buried.

| Score | Meaning |
|---|---|
| 5 | Every claim traceable; all three registers kept distinct; provenance markers intact; no stale doctrine revived |
| 3 | One or two claims where register is softened (an open item stated as probable rather than flagged open); provenance markers present but one is loose |
| 1 | Claims without traceable source; open items stated as settled; stale doctrine reintroduced; provenance markers stripped |

**Hard gate: score must be >= 4 to ship.**

---

## E. Propagation / Clarity

**What it checks.** One idea per block. The principal could read this doc, set it down, and reconstruct the argument — not repeat it, reconstruct it. Headings carry decisions, not topics ("Skills are provides-only axioms" not "Skills"). A reader who forwards the key claim to a third person leaves the third person with understanding, not a summary. Tables and code blocks carry weight that prose would dilute; prose blocks do not carry what a table would clarify.

| Score | Meaning |
|---|---|
| 5 | Every block carries exactly one idea; headings are decisions; the principal can reconstruct and argue from this doc alone |
| 3 | Most blocks are clean but one or two run two ideas together; one heading is a topic not a claim; structure is findable but slowly |
| 1 | Dense undifferentiated blocks; headings are categories; the doc informs but a reader cannot reconstruct the argument |

---

## F. Tightness

**What it checks.** No padding. Show-don't-tell: diagrams and code carry weight prose would dilute; prose blocks do not narrate what the code already shows. No block that exists only to transition. No sentence that says what the next sentence will say. The doc is done when nothing further can be removed without losing a claim.

| Score | Meaning |
|---|---|
| 5 | Every block earns its place; diagrams and code do the heavy lifting; no transition padding; remove any sentence and something is lost |
| 3 | One or two transitional sentences that add no information; one block that could be a table but is prose |
| 1 | Padding accumulates; the doc narrates itself; code or table weight is duplicated in surrounding prose |

---

## G. Legibility to the Unfamiliar Reader

**What it checks.** A value claim or conviction-register sentence must not lean on an internal type or tool name (Frame, Construct, Dagstra, SMRT, TypedStruct, samsara…) that an outside reader can't parse and an inside reader gains nothing from. The test: **strike the internal noun — does the claim still land?** If the sentence names the *mechanism* where it should name the *property*, it fails. ("Mastery distilled once, grounded in Frames" fails — the reader needs *traceable to its sources*, not the container's type name. "Mastery distilled once, traceable to its sources" passes.)

**Scope.** Internal names are legitimate where the mechanism *is* the subject — a manifest sketch, an architecture walk, a reference table. Doc 04's `relations: { frames: ... }` is correct: the type is the thing being shown. The violation is an internal name carrying a value claim, an abstract, or conviction copy.

| Score | Meaning |
|---|---|
| 5 | Value claims name the property; internal type-names appear only where they are the subject |
| 3 | One internal name leaks into a value claim but is recoverable from nearby context |
| 1 | Value claims are unintelligible without corpus knowledge; the mechanism's name does the value's work |

**Hard gate (context-scoped): >= 4 on the cover, landing, abstract, and any value/conviction claim. Advisory in mechanism, reference, and architecture sections** — the dossier is internal, so a type-name is fine where the type is what's being explained.

---

## Verdict Grammar

Hard gates: A, C, D always; G on value/conviction claims (advisory elsewhere).

**SHIP** — all applicable hard gates >= 4 AND no criterion below 3.

**TIGHTEN** — all applicable hard gates >= 4 AND at least one non-gate criterion (B, E, F, or advisory-G) is exactly 3. Evaluator names the required fixes with the specific passage. Doc ships after fixes are confirmed; no re-score required if fixes are mechanical.

**RETURN** — any applicable hard gate scores below 4, OR any criterion scores 1 or 2. Evaluator names the failing criterion, the specific passage or passages, and the routing: voice failures (A, B) return to orwell; register-announcing (C) returns to orwell; factual fidelity failures (D) return to feynman with `ground-truth.md` open; propagation failures (E) return to sagan; tightness failures (F) return to orwell; legibility failures (G) return to sagan for the abstract or feynman for a doc body.

A RETURN on C is automatic for any score of 1 or 2. The principal flagged this criterion explicitly.
