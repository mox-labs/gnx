---
title: dao
summary: Checks that a project's agent organization (constitution, policies, principles, positions, participants) is written down and holds together, and renders it as one page.
kind: capability
---

# dao

## What it is

A checker for the organization that maintains a project when agents do much of the work.
`dao check` reads six short documents in the project's `dao/` directory and says, one by one,
whether each is really there. `dao book` renders the six as one page for a person to read.

## The problem

A project has three agents and one maintainer. One agent writes changes, one reviews them, and
nobody has written down which is which. The reviewer is the same model as the writer and is
described as "independent". Nothing says whether an agent may publish a release. When the
maintainer is away, an agent decides something that cannot be undone, and afterwards nobody
can say whether it was allowed to.

The organization exists, in habits and in prompts. It is not anywhere a person or a new
session can read it, so nobody can check it or change it on purpose.

## How it works

A **dao** is a directed agent organization: the people and agents that maintain a project,
what each may do, who decides, and who is accountable. dao describes it in the terms Ostrom
used for institutions, as six documents:

| Document | Says |
|---|---|
| `constitution.md` | what the organization is for, what it commits to, and who may change its rules |
| `policies.md` | the **governing policies**: who decides what, how work is checked, how a rule is changed |
| `principles.md` | the **operating principles**: how day-to-day work is done |
| `positions.md` | the **positions**: each role, what it does, what it must not do, who checks it |
| `participants.md` | the **participants**: which person or agent holds each position |
| `README.md` | the front door: all of it, explained to someone who has never seen the project |

The three rule documents are three levels. Principles govern the work, policies govern how
principles and policies change, and the constitution governs who may change anything.

Each document gets a **status**:

| status | means |
|---|---|
| `PRESENT` | the file is there, and it passes its test |
| `PARTIAL` | the file is there, and the test does not pass |
| `ABSENT` | no file |

**PARTIAL is the useful verdict.** It is the document that looks done and is not. dao never
adds the statuses into a score: one number would let a careful constitution average away a
position that checks its own work.

Most of what the test looks at is whether the documents agree with each other: every
participant holds a position that exists, every position is held, a position's checker is a
different position, the position that may amend the constitution is held by a person, and the
project's `CLAUDE.md` points at the directory so that an agent reads it at all.

`dao check` exits 0 only when all six are PRESENT.

### The book

Six Markdown files are the right source and the wrong thing to hand a person who asks how the
project is run. `dao book` writes `dao/book.html`: the same organization on one page. It adds
no words of its own. What it adds is the cross-referencing a reader would otherwise do by
hand: under each position, who holds it and whose work it reads, and above them the chains of
checking, such as builder, read by reviewer, read by owner.

The page is one file. It runs no script and loads nothing from the network, so it opens from
a clone, a file share or a static host and cannot be made to act by what a document contains.
When a project has a book, `dao check` renders the page again and compares the two. If the
documents have changed since, or the file is not what dao writes, the front door is PARTIAL
until `dao book` is run again. A page that has fallen behind tells its reader something the
organization no longer says.

## Try it

A project where someone has started and stopped:

```
$ mkdir -p proj/dao
$ printf '# Constitution\n\n## Purpose\n\nBe good.\n' > proj/dao/constitution.md
$ printf '# Positions\n\n## reviewer\n\nDoes: Reviews code.\n' > proj/dao/positions.md
$ dao check proj
┃ element              ┃ file            ┃ status  ┃ evidence / gaps                               ┃
│ Constitution         │ constitution.md │ PARTIAL │ 6 words, 1 section(s)                         │
│                      │                 │         │ ! the 'Purpose' section is empty or a single  │
│                      │                 │         │ line                                          │
│                      │                 │         │ ! no Commitments section                      │
│                      │                 │         │ ! no Amendment section                        │
│ Governing policies   │ policies.md     │ ABSENT  │ ! no dao/policies.md                          │
│ Operating principles │ principles.md   │ ABSENT  │ ! no dao/principles.md                        │
│ Positions            │ positions.md    │ PARTIAL │ 1 position(s): reviewer                       │
│                      │                 │         │ ! only 1 position(s); it takes >= 2 for one   │
│                      │                 │         │ to check another                              │
│                      │                 │         │ ! no 'Must not:' field: reviewer              │
│                      │                 │         │ ! remit under 240 characters, closer to a     │
│                      │                 │         │ label than a method (M1): reviewer            │
│                      │                 │         │ ! reviewer has no 'Checked by:' naming        │
│                      │                 │         │ another position                              │
│ Participants         │ participants.md │ ABSENT  │ ! no dao/participants.md                      │
│ Front door           │ README.md       │ ABSENT  │ ! no dao/README.md                            │
…
  INCOMPLETE: Constitution, Governing policies, Operating principles, Positions, Participants, Front
door
$ echo $?
1
```

The positions file exists and is PARTIAL, with the four reasons. `dao check proj -v` adds what
each document must contain and the finding behind it.

To write the documents, install the `dao` plugin and ask your agent to set the project up as
a dao. The skill reads the project, asks you a few questions, writes the six documents, runs
this check, and renders the book:

```
$ dao book proj
  wrote dao/book.html: open it in a browser
  dao check finds gaps in the documents. The page lists them at the top; `dao check` says more.
```

## What it gives an agent

| What an agent can do | Why it can trust it | The command |
|---|---|---|
| See which documents are missing, or there in name only | each gap names the document and what failed; no score hides one | `dao check <project>` |
| Know what a document must contain before writing it | the requirements are printed beside the research finding that shaped them | `dao spec --findings`, `dao check -v` |
| Gate on the organization being whole | the exit code is 0 only when all six are PRESENT | `dao check <project>; echo $?` |
| Read the verdicts without parsing a table | one JSON object, with a key, a status and the gaps for each document | `dao check <project> --json` |
| Give the owner one page to read and share | the page says only what the documents say, and `dao check` reports it when it falls behind | `dao book <project>` |
| Read the organization as data | positions with their holders and checkers, participants, and each document's text | `dao book <project> --json` |
| Run it on a repository it did not write | it reads regular files inside the project, as text, never through a symbolic link, and executes nothing; `dao book` writes one file, inside the project | [SECURITY.md](../../SECURITY.md) |
| Know the thresholds it was judged by | they are a declared policy, not constants in code | `dao policy` |

## What it does not do

- **It does not set up an organization.** There is no `dao init`: what matters in these
  documents is what was decided, and a generator of six plausible files would produce exactly
  what the audit exists to catch. The plugin's skill writes them with the project's owner.
- **It checks form, not meaning.** It reads sections, fields, table rows and whether the
  documents agree. A constitution with well-formed, empty sentences passes. PRESENT is not a
  review; the skill's last step is one, done by a reader.
- **It does not check that anyone follows the documents.** Whether an agent in a position
  keeps to it is a question about behaviour, and nothing here measures it.
- **Three of its tests look for words.** Whether Decisions mentions the irreversible, whether
  Verification names its kind of check, and the 240-character remit are heuristics. A padded
  file can pass them.
- **The book has one look.** There is no theme and no template option. A project that wants
  its own page builds it from `dao book --json`.
- **The book is a copy.** `dao check` holds it to the documents, not to an agent's defining
  file, and a book checked by a dao version other than the one that made it may be reported
  when nothing is wrong with it. `dao check` is the current answer.
- **`dao figures` needs a corpus that is not distributed.** It reads a catalog of grounded
  domains from `~/mox/packages/dao-corpus`; without it, it reports that no catalog was found
  and exits 1.
- **The model is not a tested design.** Positions, participants and three levels of rule are
  a clear way to describe an organization. No study read here shows that an agent
  organization described this way works better than one that is not.

## Next

- [README](../../README.md): install and the commands.
- [CONTRACT.md](../../CONTRACT.md): the document formats, exactly.
- `dao --help`, and `dao <command> --help`.
- [SECURITY.md](../../SECURITY.md): what it reads, and what it never does.
