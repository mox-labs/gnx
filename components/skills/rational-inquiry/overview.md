---
title: rational-inquiry
summary: A skill that tests whether a "therefore" holds, against five named ways a reason fails to support its conclusion.
kind: skill
---

# rational-inquiry

## What it is

A skill for Claude Code. Given an argument of the form "X, therefore Y", it checks whether the
reason actually forces the conclusion, and names the failure if it does not.

## The problem

"p95 latency dropped the week we added the cache, so the cache made the service faster." It
reads well, and the next decision builds on it. Nobody asks whether latency also dropped where
no cache was added, or what else changed that week. A fluent argument goes through because it
is fluent.

## How it works

An argument has a **reason** (latency dropped after the cache), offered for a **conclusion**
(the cache made it faster). It holds only if the reason is really present, and the reason
reliably goes with the conclusion everywhere, not just this once. That second condition is
**pervasion**.

The skill runs the argument through a **gate** of five named **defeat conditions**, from the
Nyāya tradition of logic. A reason fails when it doesn't discriminate (it occurs where the
conclusion is false too), proves the opposite, is cancelled by an equally strong
counter-argument, is itself unestablished, or is overturned by stronger evidence.

Then it looks for a **hidden condition**: something the link depends on that the argument
never names. Where the pervasion rests only on things observed together, the skill says so:
frequency is not pervasion.

## Try it

Install it in Claude Code:

```
/plugin marketplace add mox-labs/gnx
/plugin install rational-inquiry@gnx
```

Then ask, in a session: *"Is this valid? p95 latency dropped the week we added the cache, so
the cache made the service faster."* An answer from a Claude session with the skill loaded,
trimmed:

```
Reason present: yes. The cache shipped and p95 fell the same week.
Pervasion: not shown. One co-occurrence, no counterexample sought. Grounding: weak.
At risk: savyabhicāra, the reason that doesn't discriminate. If p95 also fell that week
  for services with no cache, the drop is no evidence for the cache.
Hidden condition to rule out: load. A quieter week lowers p95 with or without a cache.
Verdict: the conclusion is not earned. To earn it, compare the service with and without
  the cache under the same load.
```

The answer is a model's, so it varies from run to run; the five conditions it checks do not.

## What it gives an agent

| What an agent can do | Why it can trust it | The call |
|---|---|---|
| Test a load-bearing "therefore" before building on it | each of five named conditions is checked, and the verdict names the one that fired and where | an ask that matches the skill's description, such as "is this valid?" |
| Find the hidden condition an argument depends on | it asks for the condition explicitly, so a spurious link has to survive the question | the same |
| Say how strong a causal or universal claim is | it grades the grounding (necessity, no counterexample, or observation only) instead of passing it | the same |
| Stop when there is nothing to test | the skill says to name the condition an argument passes and stop | the same |

## What it does not do

- **It checks the reasoning, not the facts.** It can say a reason is unestablished; it does not
  go and establish it.
- **It is a practice, not a program.** A model applies it, and a model can apply it badly.
  For a claim that matters, measure it: the latency example above is an
  [ix](/components/ix) `compare`.
- **Its routing is unmeasured.** The lab's routing experiment has no probe that expects this
  skill, so how reliably it loads on its trigger phrases has not been measured.
- **Its grounding is cited, not shipped.** The skill names the research it was distilled from;
  those sources are not in this repository.

## Next

- [SKILL.md](https://github.com/mox-labs/gnx/blob/main/components/skills/rational-inquiry/SKILL.md):
  the skill itself.
- [intent-hardening](/components/intent-hardening): runs this gate inside its resolution step.
- [Install a plugin](/docs/install-a-plugin).
