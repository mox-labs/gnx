---
name: rational-inquiry
description: |
  Inference-validity discipline — the fallacy / defeat gate. Given an inference, argument, or causal claim
  ("X, therefore Y"), check whether it is VALID by testing it against the named defeat conditions: the Nyāya
  hetvābhāsa catalog (the five fallacious-reason types) plus the defeater apparatus (upādhi undercutting + tarka
  reductio). Built to catch the moves a fluent-but-invalid argument makes: a reason that doesn't discriminate, that
  proves the opposite, that is counterbalanced, that is itself unestablished, or that a stronger source defeats.

  Use when: a claim rests on an inference and you need to test the inference rather than the conclusion; a "therefore"
  is load-bearing; a causal or universal claim ("all X are Y", "X causes Y") needs its pervasion checked; an argument
  feels persuasive but the reason seems weak; the principal asks to "check this inference", "is this valid", "what's
  the fallacy", "run the hetvābhāsa gate".

  In gnx: this is the validity gate run INSIDE `intent-hardening`'s uttarapakṣa (resolution) step, and is assigned to
  the agent `bodhi`. It is element III (named defeat conditions) of the five functional necessities of valid inquiry.
---

# Rational inquiry — the inference-validity gate

> **Grounding.** Crystallised from the REP-017 cross-tradition corpus, cross-family verified (CoVE + Gemini,
> `~/mox/research/drafts/inquiry-frameworks/sources/nyaya-verified.md`, 23/25 [T2✓]) and the CF1-resolved synthesis.
> Not authored from memory. Claim anchors below are NY-xx from the verified corpus. Ported into the gnx catalog 2026-06-20.

A fluent argument is not a valid one. Fluency produces a *reason*, a *conclusion*, and a *therefore*; validity requires
that the reason actually force the conclusion. Nyāya built the most developed catalog of the ways a reason *fails* to
force its conclusion — the **hetvābhāsa** (pseudo-reasons). This skill is that catalog, deployed: given an inference,
run it through the gate and report which defeat condition (if any) fires.

Not parochial: REP-017 (CF1) established a **named defeat-catalog as a functional necessity** of any defeasible,
warrant-seeking inquiry — independently invented by Nyāya (*hetvābhāsa*), Buddhist logic (Dignāga's *hetucakra*), and
modern formal argumentation (Pollock's defeaters, Dung's attacks). Nyāya's is the most fine-grained, so it is the
working catalog; the cross-tradition mapping is noted so the gate isn't read as merely Indic.

---

## 1. The structure of a valid inference (what the gate presupposes)

An inference (*anumāna*) has a subject (*pakṣa*), a reason (*hetu*), and a target (*sādhya*) — *"the mountain has fire,
because of smoke."* It is valid when **two conditions jointly hold** (NY-08 [T2✓]):

1. **Pakṣadharmatā** — the reason is genuinely present in the subject. (Smoke is actually on the mountain.)
2. **Vyāpti** — *pervasion*: the reason is invariably accompanied by the target (*"wherever smoke, there fire"*). The
   load-bearing, hard condition.

**The gate's job:** given a candidate inference, check pakṣadharmatā and vyāpti, then test for the five named ways the
reason can be a *pseudo*-reason.

---

## 2. The hetvābhāsa gate — the five named defeat conditions

For "P, because of H, establishing S" check each (NY-15 [T2✓] — Navya-Nyāya fixes exactly **five**):

| # | Pseudo-reason | The defeat condition | Modern analog |
|---|---|---|---|
| 1 | **Savyabhicāra** — *inconclusive / deviating* (NY-16) | H occurs in BOTH where S holds and where S is absent — it doesn't discriminate, so it can't force S. *("Audible, therefore eternal" — audibility is in both.)* | a non-discriminating ground; the warrant doesn't track the conclusion |
| 2 | **Viruddha** — *contradictory* (NY-17) | H is concomitant with the **absence** of S — it proves the *opposite*. | a self-undermining warrant; the evidence cuts the other way |
| 3 | **Satpratipakṣa** — *counterbalanced* (NY-19) | An equally strong counter-inference establishes ¬S; they cancel. | a rebutting defeater of equal force (Dung: mutual attack → neither grounded) |
| 4 | **Asiddha** — *unestablished* (NY-18) | Pakṣadharmatā fails — the **reason itself isn't established** (or is as doubtful as the conclusion). | an unestablished premise; assumes what it should show |
| 5 | **Bādhita** — *defeated* (NY-20) | The conclusion is **overturned by a stronger pramāṇa** — typically perception. *("Fire is cold, because a substance.")* | a rebutting defeater from a stronger source (Pollock) |

**If any fires, the inference does not earn its conclusion.** Report which, with the locus/evidence that triggers it.

---

## 3. The defeater apparatus — pervasion is where inferences actually die

The hetvābhāsa catch gross failures; subtle ones live in **vyāpti**:

- **Upādhi** — the *conditioning factor* (NY-22): a hidden property pervading the target but **not** the reason. Its
  presence means the pervasion is *conditional* — **Pollock's *undercutting* defeater**, attacking the inference-link
  itself. *Always ask: is there a hidden condition C such that "H → S" holds only when C is present?* If yes, the
  pervasion is spurious — a disguised savyabhicāra. (This is the same move as the remove-constituent / co-absence gate.)
- **Tarka** — *suppositional reductio* (NY-23): test a pervasion by assuming the contrary — *suppose H without S; what
  absurdity follows?* If a genuine absurdity follows, the pervasion holds; if the reductio smuggles in the pervasion it
  was meant to prove (*cakraka*, circularity), the support fails.

---

## 4. The honest hard problem — vyāpti and induction (do not paper over it)

How is a universal pervasion ("wherever smoke, fire") ever established? Perception grasps only particulars; grounding
it in further inference regresses — so (the Cārvāka charge) pervasion may be mere habit, not certainty (NY-21; the
Indian problem of induction). **Operational consequence:** when an inference rests on a universal/causal pervasion, do
not certify it merely because co-occurrence is frequent. State the pervasion's *grounding*: causal/constitutive
necessity (strong), no-counterexample-plus-no-upādhi (medium), or repeated co-observation only (weak — defeasible).
**Frequency of co-occurrence is not pervasion.** A "valid" verdict on a habit-grounded pervasion is the gate's own
savyabhicāra.

---

## 5. Composition — where this sits

rational-inquiry is **element III** (named defeat conditions) of the five functional necessities of valid inquiry
(REP-017): V generation (`intent-analysis`) · I–II engage opposition + defeasibility (`mimamsa` / `dialectic`) ·
**III named defeat conditions (this skill)** · IV external grounding (the anchoring discipline). In gnx it runs inside
`intent-hardening`'s **uttarapakṣa** step — when the resolution rests on an inference, this gate checks it before the
intent is committed — and its **upādhi** check is the same operation as the remove-constituent gate elsewhere in the
stack: find the hidden condition the apparent link depends on.

---

## 6. When to use / when not

**Use** when an inference is load-bearing and you must test the *reasoning*, not the *conclusion*. **Do not** use for
drawing out fuzzy intent (`intent-hardening` / bodhi), establishing a contested conclusion through full dialectic
(`mimamsa`), or a question with no inferential structure (no hetu to test). And do not run it as ceremony — if the
inference is sound on inspection, say which condition it *passes* and stop.

> The discipline: a reason earns its conclusion only by surviving the named ways a reason fails. Check the five; check
> the hidden condition; state the pervasion's real warrant. Frequency is not pervasion; fluency is not validity.

---

## Provenance
Crystallised from REP-017 (`~/mox/research/drafts/inquiry-frameworks/`): the cross-family-verified Nyāya corpus
(`sources/nyaya-verified.md`, 23/25 [T2✓], CoVE + Gemini; NY-08/10/15–22), the cross-tradition synthesis (§1.2 element
III), and the CF1 adjudication establishing the defeat-catalog as a *functional necessity*. Cross-validated against
Pollock (undercutting/rebutting) and Dung (attack semantics). Authored 2026-06-04 from verified research (not recall);
ported into gnx 2026-06-20.
