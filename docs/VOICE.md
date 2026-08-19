---
title: "How gnx writes"
section: build
register: internal
mode: reference
status: shipped
fidelity: cobblestone
---

# How gnx writes — the voice, and the anti-LLM-speak gate

This is the voice instrument for the gnx documentation surface. It does two jobs: Part 1 defines the voice the documentation agent writes *in*; Parts 2–3 define the gate that catches what the agent writes *instead* when it drifts. It operationalizes RUBRIC criteria A (voice fidelity) and B (no LLM tells) at the depth those criteria gesture at.

Like the RUBRIC, this is a living instrument — when a page surfaces a failure this file doesn't catch, the file is wrong; add the pattern. Two extra maintenance rules apply here that the RUBRIC doesn't need: the lexical lists **decay** (vendors patch famous tells, humans adopt them — *delve* rose ~51% in human academic speech after ChatGPT) and must be re-verified by era; and the protected-patterns list in Part 3 is **load-bearing** — a gate without it reproduces the misfires we have already measured.

---

## Part 1 — The voice

### The voice in one sentence

**An engineer explaining their own system to a peer** — someone who built the thing, states what it does, marks what doesn't exist yet, and doesn't perform.

That is a compression. Adjectives don't transfer — an agent told to be "clear and direct" produces what every model calls clear and direct, which is the committee voice. What transfers is behavior. Five layers, each with its setting:

| Layer | gnx setting | The behavior |
|---|---|---|
| **Diction** | everyday words carrying technical precision | `wire`, `bolt on`, `folds back`, `the wall` — not `facilitate`, `integrate`, `enable`. The right jargon (`type_url`, `provides`, kebab-case) used exactly; no jargon as decoration. |
| **Cadence** | staccato, then elaboration, back to short | Short declarative carries the claim. A longer sentence develops it. Fragment lands it. Sentence-length variation is the single most measurable human marker — uniform mid-length sentences read as generated. |
| **Stance** | architectural — "X does this; you do that" | The system and the reader both get verbs. Never apologetic, never aspirational, never selling. The reader is a peer with a task, not an audience. |
| **Commitment** | states what it knows; marks what it doesn't | Maturity honesty *is* voice: `shipped` said plainly, `designed` said plainly, and no hedging in between. A hedge appears only where the uncertainty is real — and then it commits to the uncertainty. |
| **Calibration** | dry; never performs | No enthusiasm inflation, no cleverness that costs a beat, no narrating its own rhetorical moves. The word works and vanishes. |

The triguna rule from the doc-revision canon is the compact form: **cut rajas** (prose performing itself), **lift tamas** (prose that is fog), **leave sattva** (the reader sees the thing, not the words). A passage is done when the reader sees the thing — keyed to the reader's state, not the text's polish.

### The anchor corpus

Description doesn't transfer; exemplars do. These passages are the regression anchor — new prose is compared against them, the way orwell compares against a voice anchor. The first set is the human anchor (yzavyas, Steersman, book register):

> "Deploying the edge proxy took about an hour per region. Build the AMI. Create the CloudFormation stack. Wait for the ELB. Cut over traffic. Verify. Per region."

> "The problem wasn't the architecture. The proxy worked. The problem was that deploying it took long enough to discourage deploying it."

> "I built the replacement with an intern."

The second set is the site's own gate-passed prose — the voice already running on this surface:

> "The suit does not replace you. It hands you parts — inspectable, swappable, checked before anything bolts on — and you decide which ones it wears." — *what gnx is*

> "The ceiling: **this makes structure checkable; it does not make generated content correct.**" — *why a catalog*

> "The check decides *fit*, not quality — it will wire two things that can talk and say nothing about whether they should." — *compose components*

> "Ports do topology; tags do findability; nothing ever joins on a tag." — *how components work*

> "Opaque is the onboarding ramp; defined is the destination." — *the api/ schema index*

What these share, mechanically: every sentence commits; concrete nouns do the arguing; the rhythm breaks where the idea breaks; nothing introduces itself.

### Register bracketing

A register is a band, not a point. The voice sits between two failure registers:

| Too loose | The voice | Too smooth |
|---|---|---|
| "So basically gnx is like a app store for your AI — pretty neat right?" | "gnx is a mech suit for your agent. It wears a governed catalog of extensions: skills, capabilities, agents, whole workflows." | "gnx is a comprehensive, vendor-neutral component ecosystem designed to seamlessly empower agentic workflows at scale." |
| "Just yeet the manifest into the repo and you're good." | "Write the manifest, run `gnx validate` (designed), and what passes is what the registry accepts." | "Users may subsequently submit their component manifest for validation, whereupon it will be processed by the registration pipeline." |

The left column fails commitment (slang spends precision). The right column is the committee voice — every banned behavior in Part 2 at once, and the one this model family drifts toward under polish pressure.

### One voice, five tones

Voice is constant; tone flexes by Diátaxis mode. What never flexes: commitment, maturity honesty, concreteness, the ban on performing. What flexes:

| Mode | Tone dial | Example of the flex |
|---|---|---|
| Tutorial / how-to (`guides/`) | warmest; imperative; the reader mid-task | "Open `~/.claude/settings.json` and add an entry." Direct address, no theory beyond what the step needs. |
| Reference (`reference/`, CLI pages) | austere; describe and only describe | Tables carry the weight; prose only where a table can't. Terse is the courtesy. No weaving, no thread. |
| Explanation (`explanation/`, `start/`) | argued; the voice at full range | Claims, mechanisms, and the one concrete that makes the argument land. This is where cadence matters most. |
| Landing (`start/0`) | compressed; every sentence load-bearing | The most-read page earns the most cuts. |
| Internal (`ecosystem/`, `build/`) | densest; evidence-marked | Internal names allowed; provenance markers sacred; still sattvic — density is not license for fog. |

### Giving the agent the voice — the loading stack

Persona adjectives decay over a session; measured drift is worse in larger models. What holds, in order of durability:

1. **This file, read before writing.** The documentation agent (whichever model, whichever session) reads VOICE.md before drafting or revising any page — the same way evaluators read RUBRIC.md before scoring. Traits carry their reasons because reasons generalize; a rule without its why gets pattern-matched into mush.
2. **The anchor corpus as few-shot.** The exemplars above ride in the writing context. Models mirror pattern far better than they follow description.
3. **Mechanical rules enforced by grep** (Part 2's pre-checks). Deterministic, model-independent, survive every session.
4. **Re-anchoring per task, not per session.** Long revision runs drift; each new page or wing re-reads the anchor. The craft-pass pipeline already does this structurally — orwell runs after every prose-transforming stage, not once at the end.
5. **The gate** (Part 2) as the failure surface. A voice becomes real at the moment it can fail a review.

---

## Part 2 — The anti-LLM-speak rubric

### Doctrine

Three rules govern scoring, all evidence-forced:

1. **Density and co-occurrence, never single hits.** One `crucial` is coincidence; `crucial` + a tricolon + a trailing significance clause in one paragraph is a signature. This is Wikipedia's field doctrine (thousands of editors, WP:AISIGNS) and the ratio methodology detection tools converged on.
2. **Weight by decay rate.** Vocabulary tells decay fast — the famous words get patched by vendors and adopted by humans. Structural tells decay slowly. Epistemic tells barely decay, because they reflect what post-training rewards. So: V1 outweighs V2 outweighs V3 outweighs V4.
3. **The protected list is part of the rubric.** Detectors flagged 61–98% of human-written TOEFL essays as AI; plain prose, technical enumeration, and em-dashes are how good human writers work. Part 3 binds every scorer.

Triguna mapping: V1–V4 are rajas detectors (what the machine adds — performance); V5 is the tamas detector (what the machine fails to supply — specificity). The RUBRIC's criterion A covers drift (what polish removes); this rubric covers tells (what generation adds).

### V1. Epistemic integrity — slowest decay, weighted hardest

**What it checks.** Significance asserted instead of shown: trailing clauses that inflate ("...underscoring its importance," "...which is significant because"); analysis-shaped `-ing` participles carrying no analysis ("highlighting," "showcasing," "reflecting broader trends"); symmetric hedging that never reaches a verdict ("While X offers advantages, it also presents challenges" — and then no call); hedge stacks decoupled from real uncertainty ("could potentially possibly"); praise inflation ("commendable" ran 9.8×, "meticulous" 34.7× in LLM-written peer reviews); the bimodal register — fluent overconfidence on facts, ritual hedging on judgments. The deep tell: human hedges track belief; generated hedges track register.

| Score | Meaning |
|---|---|
| 5 | Every claim commits or marks its real uncertainty; significance is carried by content; no verdict is ducked |
| 3 | One ducked verdict or one inflated significance clause; the rest commits |
| 1 | Symmetric hedging or significance inflation is the page's default register |

**Hard gate everywhere.** This is also where maturity honesty and voice meet: a designed capability described with shipped confidence is simultaneously a D-failure and a V1-failure.

### V2. Structural tells — slow decay

**What it checks.** Negative parallelism as a tic ("not just X — it's Y", "It's not X. It's Y." when both are true — the false-dichotomy test: cover the first half; if the second stands alone, the denial is performing); rule-of-three abuse (triads faking comprehensiveness); uniform rhythm (sentence-length coefficient of variation below ~0.25 reads generated; human prose sits ≥0.4); heading templates (every heading the same grammatical form — read them as a list); summary conclusions that restate; bolded-list-itis (**Bold header:** sentence, repeated); section-level monotone (every section the same beat — two is coincidence, three is a pattern, four is a machine); copula avoidance ("serves as," "stands as," "represents" where *is* would do).

| Score | Meaning |
|---|---|
| 5 | Structure is shaped by content; rhythm varies; headings vary; contrasts are real contrasts |
| 3 | One template pattern (a heading series, one performed contrast, one uniform-bullet block) |
| 1 | The page's skeleton is generated: templated sections, flat rhythm, contrast-as-connective |

### V3. Syntactic tells — medium decay

**What it checks.** The measured overuse constructions: present participial clauses (2–5× — "Leveraging X, we..."), nominalizations (1.5–2× — "the implementation of"), "that"-clause subjects (2.6×), trailing elaboration doing an em-dash's job badly, synonym cycling (three words for one thing because repetition penalties fear the repeat — pick one word, repeat it; repetition is precision), empty topic sentences that restate the heading, filler transitions ("Moving on to...").

| Score | Meaning |
|---|---|
| 5 | Finite verbs, direct subjects, one name per thing, every sentence adds information |
| 3 | A couple of participial openers or one synonym cycle; isolated, not systemic |
| 1 | The constructions cluster; the prose is syntactically correct and rhythmically dead |

### V4. Lexical tells — fast decay, era-versioned

**What it checks.** The measured word lists, as *density* signals. Seed lists (2024–2025 era, from Kobak et al.'s 379 excess words, Juzek & Ward's 21 focal words, Liang et al.'s adjective lists):

- **Delete on sight** (no legitimate docs use): `delve` `tapestry` `testament to` `rich tapestry` `it's worth noting` `in today's [x]` `in the rapidly evolving` `at its core` `treasure trove` `game-changer` `embark`
- **Density-scored** (fine alone, signature in clusters): `crucial` `pivotal` `comprehensive` `robust` `seamless(ly)` `intricate` `meticulous(ly)` `leverage` (verb) `utilize` `showcase` `underscore` `boasts` `landscape` (metaphorical) `realm` `navigate` (metaphorical) `foster` `vibrant` `holistic` `multifaceted` `furthermore` `moreover` `additionally` (as paragraph openers)

| Score | Meaning |
|---|---|
| 5 | No delete-list hits; density-list words rare and each earning its place |
| 3 | One delete-list hit or a small cluster of density words on one page |
| 1 | The vocabulary is the 2024 excess-word list wearing a page |

**Maintenance rule:** this criterion is dated **2026-07**. The lists must be re-verified against the current model era before being enforced on new content; a word that has entered normal human usage moves off the list. The other criteria don't need this — that asymmetry is the point of the tiering.

### V5. Authenticity — the tamas floor

**What it checks.** The inverse of the others: not what generation adds, but what it fails to supply. The Specific Name Test (does the page name real tools, versions, numbers, dates — `slickit v0.2.0`, `plugin.json is the cache key` — or traffic in generalities?). The Could-Anyone-Say-This test (strip the context: could any model write this paragraph about any project?). Implied context (the voice references shared knowledge without explaining it — explaining what needs no explaining is its own tell).

| Score | Meaning |
|---|---|
| 5 | The page could only be about this system; specifics do the arguing |
| 3 | One passage goes generic where a concrete exists to be named |
| 1 | Fails Could-Anyone-Say-This; the prose is about "components" the way a brochure is |

### Mechanical pre-checks

Run before scoring; greps produce counts, the scorer judges. These feed V2–V4 — they are never verdicts on their own.

| Check | Grep / method | Feeds |
|---|---|---|
| Delete-list | grep the V4 delete list | V4 (any hit = flag) |
| Density-list | grep the V4 density list, count per page | V4 (cluster = flag) |
| Negative parallelism | `not just\|isn't .*—it's\|not only .* but` | V2 |
| Participial openers | first words of sentences: `-ing` clause count | V3 |
| Trailing significance | `, underscoring\|, highlighting\|which is (particularly\|especially)` | V1 |
| Sentence-length CV | compute per section; < 0.25 = flag | V2 |
| Heading grammar | list all headings; same form ×3+ = flag | V2 |
| Em-dash pile-up | 3+ parenthetical pairs in one paragraph | V2 (house threshold — see Part 3) |

### Verdict grammar and register profile

Same three-valued grammar as the RUBRIC: **SHIP** (all applicable gates ≥ 4, nothing below 3) / **TIGHTEN** (gates clear, one non-gate at exactly 3, fixes named) / **RETURN** (any gate below 4 or anything at 1–2).

| Criterion | Public wings | GEPs | Internal wings | Reference pages |
|---|---|---|---|---|
| V1 epistemic | gate | gate | gate | gate |
| V2 structural | gate | ~ | ~ | ~ |
| V3 syntactic | ~ | ~ | ~ | ~ |
| V4 lexical | ~ (pre-check) | ~ | ~ | ~ |
| V5 authenticity | gate | ~ | ~ | exempt |

(`~` = scored, advisory.) This upgrades the RUBRIC's criterion B — advisory everywhere in the register-profile table — into something that can actually fail a public page.

---

## Part 3 — Protected patterns: never flag these

Every scorer — human, in-family agent, out-of-family judge — is bound by this list. Each entry carries its evidence, because a protected list without reasons gets eroded by the next zealous reviewer.

| Protected pattern | Why it's protected |
|---|---|
| **Em-dashes** | House style, ruled in the RUBRIC. No measured evidence LLMs out-dash skilled human writers; the panic is social contagion. The house threshold stands: flag only 3+ parenthetical pairs in one paragraph, or a dash doing evasion work. |
| **Sentence fragments and staccato** | The anchor corpus is built on them ("Per region." / "It wasn't."). Flattening them is voice drift, the thing the gate exists to prevent. |
| **Plain, predictable prose** | Perplexity-punishing heuristics flagged 61–98% of human non-native-English essays as AI. Simple prose is a virtue here, not a signature. |
| **Technical enumeration and content-bearing triads** | "type_url, source, requires, provides, relations" is five items because the manifest has five fields. A list earns suspicion only when its length is rhetorical rather than factual. The out-of-family judge misflagged exactly this, twice, on 2026-07-13. |
| **Repetition of the right word** | Anti-synonym-cycling cuts the other way: `manifest` fifteen times on a manifest page is precision. |
| **Real names** | `slickit`, `Claude Agent SDK`, `mox-labs` in a URL — flagged as violations by the out-of-family judge, all misfires. A name's obscurity is not evidence of anything. |
| **Corpus vocabulary** | Terms like *epochs*, *avowal*, *stigmergically* carry precise doctrinal referents and grep-linkage across the corpus. Plain-wording them severs the link — it happened once this pass and was reverted. |
| **"In conclusion"-class phrases** | Non-discriminative — humans use them as much as models. (The house style avoids summary conclusions anyway, but under V2, not as a lexical ban.) |
| **Hedges that track real uncertainty** | "Not hard-decided," "leaning Python," "unratified" are the honesty system working. V1 punishes hedging decoupled from belief, never hedging that expresses it. |

---

## Provenance

Measured sources: Kobak et al., *Science Advances* 2025 (379 excess vocabulary words, ≥13.5% of 2024 abstracts LLM-processed); Liang et al., ICML 2024 (adjective fold-increases in peer reviews); Juzek & Ward, COLING 2025 (21 focal words; RLHF as likely cause); Yakura et al. 2024 (LLM vocabulary entering human speech); Liang et al., *Patterns* 2023 (detector bias against non-native writers — the false-positive anchor); arXiv 2412.00804 (persona drift, larger models worse). Field-tested: Wikipedia WP:AISIGNS / WikiProject AI Cleanup (era-versioned catalogs, density doctrine, false-positive doctrine). Craft: Orwell 1946; Zinsser; Graham (*Write Like You Talk*; *Good Writing* 2025). Voice operationalization: GOV.UK words-to-avoid; Google developer style guide's register bracketing; Monzo's tone dials; Diátaxis register-per-mode; Mailchimp/Kiefer Lee (one voice, many tones); Anthropic character training (traits with reasons over persona masks). In-house: the craft-rhetoric voicing skill and its anti-patterns reference (the four-pass review this rubric compresses and re-weights); `docs/RUBRIC.md` A/B; the 2026-07-13 out-of-family misfire log.
