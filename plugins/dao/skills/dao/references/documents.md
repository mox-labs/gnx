# The six documents

The formats below are what `dao check` reads. They are plain Markdown, so the documents read
well on a forge, and the grammar is small: a document's parts are its `## ` sections, a
position has three named fields, and participants are rows of a table. Headings are matched by
the word in bold below, in any case, so "## Our purpose" is a Purpose section.

All six live in one directory, `dao/` unless `dao check --dir` says otherwise. The example is a
small library with one maintainer and two agents.

## constitution.md

Three sections are required: **Purpose**, **Commitments** (or "Non-negotiables") and
**Amendment** (or a heading with "change"). The Amendment section must name a position from
`positions.md`, and a person must hold it.

```markdown
# Constitution

## Purpose

tidepool is a small library for reading tide tables. It exists so that people who build
coastal tools get correct predictions without reading the harmonic-constituent literature.

## Commitments

- A released version never changes. A fix is a new version.
- No prediction ships without a test against a published tide table.
- Nothing is merged that its author is the only one to have read.
- An agent never publishes a release, and never changes this file or policies.md.

## Amendment

Only the owner changes this constitution, in a commit that says why. Anyone, person or agent,
may propose a change by opening an issue.
```

## policies.md

Three sections are required:

- **Decisions**: what a participant decides alone and what goes to a named position. It must
  say where a decision that is irreversible or novel goes.
- **Verification** (or "Review", "Checks"): how work is checked. It must say what kind of
  check it is: a different model family, the same family reading cold, or a person.
- **Changing the rules** (or a heading with "amend"): how a policy or a principle is changed.

```markdown
# Governing policies

## Decisions

| Decision | Who decides |
|---|---|
| A fix or a refactor with tests | the builder, once the reviewer approves |
| A new public function, a new dependency | the owner |
| Anything irreversible (a release, a deleted branch, a rewritten history) | the owner |
| Anything being done here for the first time | the owner |

## Verification

Every change is read by the reviewer before it merges. The reviewer is the same model family
as the builder, so this is a cold re-read, not an independent check: it reads the diff and the
tests without the builder's conversation. The owner reads every change to the public API.

## Changing the rules

A principle changes when the owner or the reviewer proposes it and the owner agrees. A policy
changes only by the owner. Either change is one commit that says what happened to prompt it.
```

## principles.md

At least three principles, each a list item or a `## ` section.

```markdown
# Operating principles

- Run the tests before saying something works, and paste what they printed.
- One change does one thing. A fix does not carry a refactor.
- When a table and the code disagree, the table is right until someone shows otherwise.
- Stop and ask after two failed attempts at the same thing.
```

## positions.md

Each position is a `## ` section with three fields: `Does:`, `Must not:` and `Checked by:`. A
field may be bold or a list item, and may run over several lines. At least two positions.

- `Does:` and `Must not:` together run to at least 240 characters. Shorter is a label.
- `Checked by:` names a different position. A position that only people hold may leave it
  out: a person's accountability is where the chain ends.

```markdown
# Positions

## owner

Does: Sets the direction of the library, decides what is in its public interface, and makes
every release. Reads each change to the public interface before it merges, and answers
proposals to change these documents.

Must not: Merge their own change without the reviewer having read it.

## builder

Does: Writes changes: reads the issue and the code around it, writes the failing test first,
makes it pass, and opens a pull request that says what changed and how it was tested.

Must not: Merge anything. Add a dependency or a public function without the owner's decision.
Edit the documents in dao/.

Checked by: reviewer

## reviewer

Does: Reads every pull request cold, from the diff and the tests alone: looks for the case
the tests do not cover, runs the suite, and either approves or returns it with reasons.

Must not: Fix what it finds; it returns the change to the builder. Approve its own work.

Checked by: owner
```

## participants.md

A table with **Participant**, **Kind** and **Position** columns, and optionally **Defined
in**. Kind is `human` or `agent`. A participant may hold several positions, separated by
commas. Defined in is the file that defines an agent, relative to the project root; leave it
empty for a person, and for an agent that is the session itself.

At least one participant is a human, every position is held by someone, and every named file
exists.

```markdown
# Participants

| Participant | Kind | Position | Defined in |
|---|---|---|---|
| ada | human | owner | |
| the session | agent | builder | |
| cold-reader | agent | reviewer | .claude/agents/cold-reader.md |
```

## README.md

The front door. It links the other five documents by file name, and it is written for someone
who has never seen the project: what this organization is, who is in it, how a decision gets
made, and how to raise a problem.

```markdown
# How tidepool is run

tidepool is maintained by one person and two agents. This directory says who does what, who
decides, and what never happens here. Every change to it is a commit, so its history is the
record of how the rules came to be.

- [constitution.md](constitution.md): what the project is for and what it commits to.
- [policies.md](policies.md): who decides what, and how work is checked.
- [principles.md](principles.md): how day-to-day work is done.
- [positions.md](positions.md): the roles.
- [participants.md](participants.md): who holds them.

To raise a problem or propose a change to any of this, open an issue. The owner answers it.
```

## The pointer

`CLAUDE.md` or `AGENTS.md` at the project root must mention the directory, for example:

```markdown
This project is run as a dao. Read `dao/README.md` at the start of a session, and act within
the position you hold in `dao/participants.md`.
```
