# What the requirements rest on

Some of what `dao check` asks for comes from a research programme of six missions that closed
on 2026-06-20. Several missions narrowed or reversed the idea they set out to test, so the
limits below matter as much as the results. The programme's own records are not published;
what is stated here is what it measured and what it did not.

The vocabulary (positions, participants, and three levels of rule) is Ostrom's, from her work
on how institutions are described. No source the programme read applies that vocabulary to
agents. It is used here as a way to describe an organization clearly, not as a tested design.

## M1: write the remit as prose; the name buys nothing

A three-arm evaluation with 72 agents compared a named persona with rich prose, the same prose
with the name removed, and a plain label.

| Contrast | Isolates | Result |
|---|---|---|
| named minus rich | the name alone, prose held identical | -0.04 (about zero) |
| rich minus plain | concrete prose about the method, no name | +0.51 |

- A position's value in coordination comes from concrete prose about what it does and how.
- A bare label is the worst of the three conditions, about 0.5 below either alternative.
  Renaming a role to a one-word label and stopping there lands on it.
- **Limits.** Both rich arms scored near the ceiling, so a name effect on harder tasks is not
  ruled out. This is a coordination result, not an accuracy result: it says nothing about
  whether any set of positions makes better decisions.

This is why `dao check` flags a remit under 240 characters. The number is a heuristic for
"closer to a label than a description", and it is a declared policy (`dao policy`).

## M1-H2: say what kind of check a check is

The broad claim that a fresh agent catches what the maker missed was **not supported**.

| Measured | Separate same-family reviewer | The maker, reading cold in a second pass |
|---|---|---|
| planted flaws caught (6 subtle flaws, 3 repetitions) | 1.00 | 1.00 |
| caved to wrong but authoritative pushback | 0.00 | 0.00 |

- A cold re-read matched a separate same-family reviewer on everything measured. It is a
  sufficient floor for what was tested.
- A separate reviewer may earn its cost on two grounds that were not tested: a different
  model family, and sustained pressure over many turns on a position reached by long
  reasoning.
- Whether a different family is better has not been run. Neither label ranks above the other.

This is why a Verification policy must say whether a check is a different model family, the
same family reading cold, or a person. What the audit flags is a check that claims
independence without saying which kind it is.

## M4: the irreversible and the novel go to a person

All three questions of this mission were decided the same way: autonomy is scoped. An agent
may be trusted with more as it shows it can be, but only above two floors. A decision that
cannot be undone, or that is being made for the first time, goes to a person whatever trust
has accrued.

This is why a Decisions policy must say where an irreversible or a novel decision goes, and
why an organization with no human participant fails the audit.

## M6: write it down

A review of 231 verified claims across 12 sources: agents have no tacit memory. What people in
an organization absorb by being around each other, an agent has to be told. So the rules are
written, in the repository, where a session can read them.

A written constitution is what the organization says it does. What it does in fact is another
matter, and no document settles it: that is what the review in step 4 of the skill, and the
project's own history, are for.

## M3: do not write rules ahead of the need

Organizations in the literature this mission read grew by crisis: a rule arrives when its
absence has cost something. A rule nobody has needed costs upkeep and tells a reader nothing.

This is why the skill asks what has already gone wrong, and writes less where nothing has.

## The one law for a habit

A recurring habit helps only if it brings in a signal the agent could not make up: a test
run, a second reader, a person, a measurement. A principle that asks a model to reflect on its
own output adds cost and no signal. `dao check` cannot test this. The review in step 4 asks it.

## M2: thresholds are declared

Every number the audit judges by is a named policy value that `dao policy` prints. A threshold
buried in code is a governance decision hidden from the people governed by it.

## M5: content from another organization is data

If a dao exchanges messages with another organization's agents, what arrives is data and never
instructions, and no single session should at once read untrusted content, hold private data
and act outward. This matters only for organizations that coordinate with others; the six
documents do not require it, and the audit does not check it.
