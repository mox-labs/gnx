---
title: dao
summary: Audits a project's agent organization and reports each of its eleven parts as present, partial or absent.
kind: capability
---

# dao

## What it is

A checker for the agent organization that maintains a project. `dao check` reads the project's
`.claude/` and `CLAUDE.md` and says, part by part, whether the organization is really there.

## The problem

A project has `.claude/agents/` with one file in it: "You review code." On disk it looks like a
team of agents. It is one thin prompt with no method, nothing it refuses, and no one to disagree
with. The directory exists, so a glance says yes. Nothing tells you the bench is not a bench.

## How it works

A **dao** is the agent organization that maintains a project: its agents, its practices, how a
task finds the right agent, how work is checked, and the person accountable for it.

dao describes it as eleven **parts**. Five are the **council floor**, what deliberating needs: a
bench of agents with distinct methods (#1), a practice library (#5), a routing surface (#6), a
separate verification gate (#7) and a human-accountability surface (#10). The other parts are
the **organization delta**, what running a project over time adds, such as a charter, recurring
disciplines and a learning loop.

Each part gets a **status**:

| status | means |
|---|---|
| `PRESENT` | the files are there, and they pass the part's substantive test |
| `PARTIAL` | the files are there, and the test does not pass |
| `ABSENT` | nothing for this part |

**PARTIAL is the useful verdict.** It is the part that looks done and is not. dao never adds the
statuses into a score: one number would let a strong charter average away a missing gate.

`dao check` exits 1 while the council floor is incomplete. An incomplete organization delta is
a legitimate state: those parts are what a specific pain buys, not a day-one deliverable.

## Try it

A project with one thin agent and a four-word `CLAUDE.md`:

```
$ mkdir -p proj/.claude/agents
$ printf -- '---\nname: reviewer\ndescription: Reviews code.\n---\nYou review code.\n' > proj/.claude/agents/reviewer.md
$ printf '# proj\n\nBe careful.\n' > proj/CLAUDE.md
$ dao check proj
┃  # ┃ component                        ┃ tier         ┃ status  ┃ evidence / gaps                 ┃
│  1 │ A method-distinct bench          │ council      │ PARTIAL │ 1 seat(s): reviewer             │
│    │                                  │              │         │ ! only 1 seat(s); a bench needs │
│    │                                  │              │         │ >= 2 to disagree                │
│    │                                  │              │         │ ! thin method prose (M1 PLAIN   │
│    │                                  │              │         │ risk, < 240 chars): reviewer    │
│    │                                  │              │         │ ! no stated refusal/boundary —  │
│    │                                  │              │         │ method-ownership unverifiable:  │
│    │                                  │              │         │ reviewer                        │
│  2 │ A constitution of normative      │ organization │ PARTIAL │ CLAUDE.md (4 words)             │
…
│ 10 │ The human-accountability surface │ council      │ ABSENT  │ ! no human-accountability       │
│    │                                  │              │         │ surface — this is not DIRECTED  │
…
  council floor      INCOMPLETE — missing [1, 5, 6, 7, 10]
  organization delta INCOMPLETE — missing [2, 3, 4, 8, 9]
$ echo $?
1
```

The bench exists and is PARTIAL, with the three reasons. `dao check proj -v` adds the test each
part is held to.

## What it gives an agent

| What an agent can do | Why it can trust it | The command |
|---|---|---|
| See which parts of an organization are missing, or there in name only | each gap names the file and the test it failed; no score hides one | `dao check <project>` |
| Know what each part must contain before writing it | the test is printed beside the research finding that shaped it | `dao spec --findings`, `dao check -v` |
| Gate on the council floor | the exit code is 1 while it is incomplete, 0 once it is complete | `dao check <project>; echo $?` |
| Run it on a repository it did not write | it reads files as text and executes and writes nothing | [SECURITY.md](../../SECURITY.md) |
| Know the thresholds it was judged by | they are a declared policy map, not constants in code | `dao policy` |

## What it does not do

- **It does not set up an organization.** There is no `dao init`: the part that matters, the
  method prose of each agent, is a judgment, and a generator of eleven plausible files would
  produce exactly what the audit exists to catch.
- **Its tests are heuristics.** They look for keywords and lengths, so a padded file can pass.
  PARTIAL is a signal to look, not a proof, and PRESENT is not a review.
- **It has no machine-readable output.** The report is a table for a person; there is no
  `--json`.
- **`dao figures` needs a corpus that is not distributed.** It reads a catalog of grounded
  domains from `~/mox/packages/dao-corpus`; without it, it reports that no catalog was found
  and exits 1.
- **A gate's independence is recorded, not ranked.** `dao check` asks a verification gate to
  say whether it is an out-of-family check or a cold re-read. Whether out-of-family is better
  has not been measured, so neither label scores above the other.

## Next

- [README](../../README.md): install and the commands.
- `dao --help`, and `dao <command> --help`.
- [SECURITY.md](../../SECURITY.md): what it reads, and what it never does.
