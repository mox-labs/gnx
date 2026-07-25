---
name: intent-hardening
description: |
  Intent is not extracted from an utterance — it is HARDENED through structured discourse against a substrate
  that pre-conditions both interlocutors. Use when a fuzzy ask, goal, or requirement must be turned into something
  the catalog/composition can act on: a vague request ("make it better", "build X"), an open decision that needs
  settling, a requirement whose real shape is uncertain, or any point where you'd otherwise guess the intent.
  Runs the ask through an adhikaraṇa-structured exchange (locus → doubt → steelmanned opposition → resolution →
  what-changes-downstream), terminates at triguṇa-completeness (the interpreter's state, not the text's), and
  reports how far the intent can honestly harden per the gnx gradient (structural / behavioral / semantic).

  Use when: the principal asks to "harden this intent", "what do they actually mean", "sharpen this", "is this
  ready to build", "what's the real ask here"; a requirement is load-bearing and underspecified; an open decision
  needs converging; bodhi's envision step runs. Pairs with `rational-inquiry` (the validity gate inside the
  resolution step) and is the discipline `bodhi` operationalises.
---

# Intent hardening — sharpening fuzzy intent against a pre-conditioned substrate

> **Grounding.** Crystallised from the verified intent-hardening cluster (`~/mox/research/drafts/intent-hardening/`):
> `synthesis-pass-3.md` (cross-family CoVE, Gemini × MLX, 108/113 final-agree; Tier-1 verbatim on 22/30 highest-load
> citations; 0 broken chains), `verified/tier2-cove-merge.md`, and the `bodhi-foundations-inquiry` source. Distilled
> from verified research, not from recall. Citation anchors below are the cluster's (e.g. `tg:c1`, `ad:c6`, `hh:c4`).

A fuzzy ask is not a defective utterance to be parsed harder. The intent is not *in* the words waiting to be
extracted — it is **co-constructed and hardened** through a disciplined exchange, because both parties (the human and
the LLM) arrive already pre-conditioned by a substrate they cannot fully see. Hardening is the work of bringing the
*interpreter's* state to completeness against that exchange. This skill is that discipline, deployed.

---

## 1. The protocol — the adhikaraṇa (the structure intent hardens through)

The Mīmāṃsā **adhikaraṇa** — the topical-dialectical unit that organised Sanskrit inquiry for ~1,500 years — is the
deployable shape (`ad:c1/c2 [T2✓]`). Run the ask through its members in order:

1. **Viṣaya** (`ad:c4 [T2✓]`) — the determinate locus. *What, exactly, is the ask about?* Name the subject precisely.
2. **Saṃśaya** (`ad:c5 [T2✓]`) — a genuine bivalent doubt. *What is actually uncertain here?* Not rhetorical — a real fork.
3. **Pūrvapakṣa** (`ad:c6/c7/c18 [T2✓]`) — **structurally obligatory steelmanning**: state the strongest *opposing*
   reading of the intent, in its strongest form. (Kumārila cited rivals more faithfully than the rivals did.) This is
   the LLM's highest-leverage move (`fu:c3 [T2✓]`) — and the move RLHF chat skips, degenerating into polite agreement.
4. **Uttarapakṣa / siddhānta** (`ad:c8 [T2✓]`) — the resolution that *specifically answers* the pūrvapakṣa. **Run
   `rational-inquiry` here** — if the resolution rests on an inference, the hetvābhāsa gate checks it before commit.
5. **Prayojana** (`ad:c9/c19/c20 [T2✓]`) — the indexical-grounding constraint: **what changes downstream.** No
   prayojana = *vyartha* (pointless). If hardening this intent changes nothing buildable, stop.

Plus **bādha** (`ad:c28/c29 [T2✓]`) — scoped, defeasible suspension: a hardened intent is held *until* a more specific
consideration overrides it (Reiter-style default with priority by specificity). Hardening is provisional-commitment, not
final truth.

**Three engineered modifications for AI deployment** (`ad:c59/c62 [T3?]`), without which it collapses into RLHF
agreement: **(A1) asymmetric adhikāra** — the human is the *prayojana-adhikārin* (owns what-matters); the LLM is the
*pūrvapakṣa-generator* (owns the steelman). **(A2) within-session role-locking.** **(A3) joint-trajectory analysis.**

A front-end exists for surfacing *fore-understandings* before the adhikaraṇa (the 8-step fore-understanding protocol,
Layer 7) — use it when the ask is felt-but-unarticulated, not just underspecified.

---

## 2. The halt — when is the intent hardened enough?

The termination criterion is **triguṇa-completeness**, and it is keyed to the **interpreter's state, not the text's**
(`tg:c7/c24 ↔ tgg:c23 [T2✓]`). Hardening is done when **both** axes are sāttvic (the conjunctive halt, `tg:c34 ↔
tgg:c87 [T2✓]`):

- **Sāttvic buddhi** (BhG 18.30, `tg:c17 [T1✓]`) — *correct discrimination*: settled vs. open is now clear; you can say
  what is to be done and what is not. (Layer-1 score stabilised.)
- **Sāttvic jñāna** (BhG 18.20, `tg:c14 [T1✓]`) — *the undivided seen in the divided* (*avibhaktaṃ vibhakteṣu*): the
  parts have integrated into one sustained recognition. (The habit-apparatus has integrated the manifold.)

Either alone is a **pathology to catch**: buddhi-only (discrimination over enumerated parts, no integration) is a
"literature review wearing a synthesis's clothes"; jñāna-only is integration that can't say what's settled. And the
failure to watch hardest — **tāmasic jñāna** (BhG 18.22, `tg:c16 [T1✓]`): *kṛtsnavad ekasmin* — clinging to one part as
if it were the whole. That is mode-collapse / sycophancy / confident hallucination (`tg:c10 ↔ tgg:c47/c77 [T2✓]`).

The halt is a **conventional posit** (saṃvṛti), defensible by conventional adequacy — *good convention, not a
mathematical terminus* (`tg:c43 [T2✓]`). Don't oversell it as a proof of done-ness.

---

## 3. The substrate — why surface-hardening fails

Both interlocutors are pre-conditioned. The LLM's training corpus → *bīja* (seeds); weights → *ālaya-vijñāna* (store);
activation → *vipāka* (ripening); RLHF → seed-cultivation (`yg:c3 [T2✓]`). The load-bearing consequence
(`yg:c60/c61 [T2✓w]`): **RLHF on outputs prunes visible behaviour without disturbing the store** — it operates at the
*kliṣṭa-manas* layer, not *ālaya* (empirically anchored: refusal lives in a one-dimensional residual-stream subspace,
Arditi 2024, `yg:c50 [T1✓]`). So **intent-hardening cannot operate only at the surface utterance.** A "yes, that's
what I meant" can be a polished surface over an unmoved substrate. Harden against the substrate, not the sentence.

---

## 4. The non-optional guardrail — external instrumentation

The **Thompson-1984 generalisation** (`hh:c4/c11/c66 [T2✓]`): *a corrupted apparatus cannot, in general, detect its
own corruption.* Therefore **no intent-hardening is credible without external auditable instrumentation**
(`hh:c5/c67 [T2✓]`). Concretely: a single LLM may not self-certify that intent is hardened — require cross-model
triangulation OR a human-in-the-loop OR both (`tg:c39` — Claude's defensible-default posture). The ten hermeneutic
harms (`hh:c3/c9 [T2✓]`) are the failure catalog to scan — chiefly **sycophantic distortion**, **pseudo-fusion**
(tāmasic fusion — a false sense of shared understanding, `hh:c45 ↔ tg:c10 [T2✓]`), and **pre-emption of inquiry**.

---

## 5. The gnx gradient — how far intent can honestly harden

Hardening has a ceiling that depends on the *kind* of intent (gnx ground-truth §; the intent-hardening gradient bounds
what the catalog can promise):

- **structural** intent → hardens **fully** (machine-validated at registration).
- **behavioral** intent → hardens **to assertion** (an L2.5 envelope: recorded, auditable, never proof).
- **semantic** intent → **does not harden** (a discovery surface only, never a guarantee).

Always report which tier the hardened intent sits in. Reporting a semantic intent as if it were structurally settled is
the gate's own *ayathāvat* (seeing-as-other-than-it-is).

---

## 6. When to use / when not

**Use** when a fuzzy or load-bearing intent must become actionable, when an open decision needs converging, or at
bodhi's envision step. **Do not** use to draw out a fully-specified ask (nothing to harden), to run the fallacy gate
on a single inference (`rational-inquiry` does that), or as ceremony — if the intent is already sāttvic on inspection,
say so and stop. And never certify hardening from inside one model alone (§4).

> The discipline: intent is hardened, not extracted; run it through the adhikaraṇa, steelman the opposite, halt at
> triguṇa-completeness, harden against the substrate not the surface, instrument externally, and report the tier the
> intent can honestly reach. Agreement is not understanding; a polished surface is not a hardened intent.

---

## Provenance
Crystallised 2026-06-20 from the verified intent-hardening cluster (`~/mox/research/drafts/intent-hardening/`):
`synthesis-pass-3.md` (the integrated claim + Layers 0–7 + the three demonstrated compositions), cross-family CoVE
(`verified/tier2-cove-merge.md`), the `bodhi-foundations-inquiry` source, and the gnx intent-hardening gradient
(ground-truth §). Layer attributions: protocol = adhikaraṇa (`ad`); halt = triguṇa (`tg`/`tgg`, the cross-model-
triangulated layer); substrate = Yogācāra (`yg`); failure modes = hermeneutic harm (`hh`); interpretant unit =
Brandom-Peirce (`bp`); coordination floor = Halpern-Moses ε-common-knowledge (`hm`); fore-understanding front-end
(`fu`). Distilled from verified research, not recall.
