---
title: intent-hardening
summary: A skill that turns a vague ask into something buildable through a structured exchange, and says how far the ask can honestly sharpen.
kind: skill
---

# intent-hardening

## What it is

A skill for Claude Code. It takes a loose ask ("make the onboarding better") through a fixed
exchange until it is clear what is to be built, and reports how settled the result really is.

## The problem

"We should probably make the onboarding better. Is this ready to build?" A model's easy answer
is yes, and a plan. The plan answers one reading of "better", chosen without saying so. A week
later the work is done and it answers the wrong question, and both sides had agreed it was clear.

## How it works

The ask runs through five steps, in order, from the Mīmāṃsā tradition of inquiry: name exactly
what the ask is about; state the real doubt; state the strongest **opposing reading** of the
intent; resolve it, answering that reading; and say **what changes downstream**. An ask that
changes nothing buildable stops there.

The opposing reading is the step that matters most. It is the one a model skips when it agrees
politely, so the skill makes it compulsory.

The exchange **halts** when two things hold together: it is clear what is settled and what is
open, and the parts fit into one picture. One without the other is a failure the skill names.

At the end it reports a **tier**: structural intent (a shape, a schema) can harden fully;
behavioral intent (what something does) only to an assertion that can be checked; semantic
intent (what something means) does not harden at all. A model may not certify the result alone:
a person or a second model checks it.

## Try it

Install it in Claude Code:

```
/plugin marketplace add mox-labs/gnx
/plugin install intent-hardening@gnx
```

Then ask, in a session: *"We should probably make the onboarding better. Is this ready to
build?"* An answer from a Claude session with the skill loaded, trimmed:

```
Locus: the first session of a new user, from install to a first working run.
Doubt: is "better" fewer people giving up, or the same people succeeding faster?
Strongest opposing reading: onboarding is fine, and the real complaint is one failing
  install step. Then the work is a fix, not a redesign.
Resolution: needs your answer, not mine. Which complaint started this?
Downstream: decides whether this is one bug or a docs rewrite.
Tier: behavioral at best: "a new user reaches a first run in under ten minutes" can be
  asserted and checked. Not ready to build until the doubt is answered.
```

The answer is a model's, so its wording varies; the steps and the halt do not.

## What it gives an agent

| What an agent can do | Why it can trust it | The call |
|---|---|---|
| Turn a vague ask into a buildable one | a fixed sequence of steps, each with a named output, in place of a guess | an ask that matches the skill's description, such as "is this ready to build?" |
| Avoid building the wrong reading | the strongest opposing reading has to be stated and answered | the same |
| Know when to stop asking | the halt has two named conditions, and an ask that changes nothing is dropped | the same |
| Tell the person how settled the ask is | it reports the tier the intent reached, and never certifies itself | the same |

## What it does not do

- **It does not decide for the person.** What matters is theirs to say; the skill draws it out
  and supplies the opposing reading.
- **Its halt is a convention.** It is a good stopping rule, not a proof that the intent is
  understood.
- **It is a practice, not a program.** A model applies it, and a model can apply it badly.
- **Its routing rests on one probe.** In the lab's one live routing reading it loaded on its
  probe (the ask above) every time. One probe is not a measure of every ask it should catch.
- **It names an agent that does not ship.** The skill text refers to a `bodhi` agent that is in
  no plugin; the skill works without it.
- **Its grounding is cited, not shipped.** The research it was distilled from is not in this
  repository.

## Next

- [SKILL.md](https://github.com/mox-labs/gnx/blob/main/components/skills/intent-hardening/SKILL.md):
  the skill itself.
- [rational-inquiry](/components/rational-inquiry): the validity gate it runs in its resolution
  step.
- [Install a plugin](/docs/install-a-plugin).
