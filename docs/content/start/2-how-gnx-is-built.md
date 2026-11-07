---
title: How gnx is built
section: start
mode: explanation
status: shipped
register: public
---

# How gnx is built

One person decides. A model does most of the work. The repository holds the record of both,
so you can judge the result without taking either on trust.

## Who decides

**yzavyas** owns gnx and makes its decisions: the rulings a design waits on, every merge to
`main`, every release.

**Claude**, Anthropic's model, does most of the design, the code and the review, under those
rulings. When the owner leaves a call to Claude, the record says the call was Claude's.

## The evidence

| What | Where | What it shows |
|---|---|---|
| Design records | `docs/design/` in [hardline](https://github.com/mox-labs/gnx/tree/main/components/capabilities/hardline/docs/design), [matrix](https://github.com/mox-labs/gnx/tree/main/components/capabilities/matrix/docs/design), [ix](https://github.com/mox-labs/gnx/tree/main/components/capabilities/ix/docs/design) and [recon](https://github.com/mox-labs/gnx/tree/main/components/capabilities/recon/docs/design) | Each decision with the alternatives weighed, the verdict and why, its named weakness, and its evidence marked *observed* or *reported*. An owner's ruling is named as one; the rest are Claude's. |
| Commit trailers | `git log` | A commit Claude wrote ends with `Co-Authored-By: Claude …`, and recent ones with a `Claude-Session:` link to the session that produced it. |
| The gates | the [`justfile`](https://github.com/mox-labs/gnx/blob/main/justfile); CI runs `just ci` | Tests, strict type checks and lint for the packages; each package installed alone from its wheel and driven through a smoke test; the docsite check; a secret scan. |
| Cold readers | their findings land in the commits that fix them; the reports are not committed | Agents with no context follow the docs from a fresh clone toward a real goal, run what the docs say, and report every place they got stuck. |

The gates are the verification. A model reviewing a model's work is a cold re-read: it finds
real problems, but it shares the author's blind spots, so it is never counted as independent
evidence.

## A correction, on the record

ix's mutation testing credited a kill to any test that failed on the mutant and passed on the
original code. The first acceptance run, over recon's test suite, showed that rule was wrong:
seven of eight mutants that only the whole-suite pass killed were credited to one recon test
that ran none of their lines, a test that failed when trials ran side by side and passed alone.
A kill now counts only when the test fails again run alone on the mutant, and passes run alone
on the original. The rerun, with this and two other corrections, read fewer kills, and the
record keeps both runs: [ix design record, §32](https://github.com/mox-labs/gnx/blob/main/components/capabilities/ix/docs/design/2026-10-19-record.md).

## Limits

- **Claude keeps no memory between sessions.** What carries a decision from one session to the
  next is the repository: the design records, the changelogs, the commit messages. A decision
  that is not written down is gone.
- **Some rulings are cited by name only.** A record may cite an owner's ruling by its id; the
  full text of some lives outside the repository.
- **The trailer is the only signal of who wrote a commit.** Every commit is authored as
  yzavyas. A `Co-Authored-By: Claude` trailer marks Claude's work, and nearly every commit
  apart from merges carries one; the `Claude-Session:` link is more recent. Nothing in the
  record marks a commit as the owner's own work.
- **A green gate checks what it checks.** It does not show that a design is right. The design
  records and the acceptance runs are where that case is made, or fails.

## Why it is run this way

gnx asks agents to work in a certain way: decisions recorded where the next reader finds them,
a claim measured before it is made, a person holding the decisions. gnx is built the same way.
If it were not, the tools would be asking you for trust the project does not ask of itself.

## Next

- **[What gnx is](/docs/what-is-gnx)**: the components, and where to start.
- **[CONTRIBUTING.md](https://github.com/mox-labs/gnx/blob/main/CONTRIBUTING.md)**: the workflow
  and the gates, for a change of your own.
