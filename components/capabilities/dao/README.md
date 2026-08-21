# dao

Audits whether a project's agent organization is actually there, or only looks like it is.

## What this is

A **dao** is the agentic organization that maintains a project: a bench of method-distinct
practitioners, a charter, habits, a routing surface, a verification gate, and a named human
who is accountable. Eleven components in total, split into a **council floor** (what
deliberating requires — #1 bench, #5 practice library, #6 routing, #7 gate, #10 human
accountability) and an **organization delta** (what running a project adds).

The failure this exists to catch is the artifact-shaped hole: `.claude/agents/` containing
one thin prompt is a directory, not a bench. So the audit reports **PRESENT / PARTIAL /
ABSENT** per component and never a score — a single number lets a project average away a
missing gate. **PARTIAL is the useful verdict**: the artifact exists and the substantive
test does not pass.

`dao check` exits non-zero while the council floor is incomplete. An incomplete
*organization* delta is a legitimate state — M3 found that transition is crisis-driven, so
those components are what a specific pain buys, not a day-one deliverable.

### What it is not

**dao does not scaffold.** There is deliberately no `dao init`. Emitting eleven plausible
files is precisely the hole the audit exists to catch, and the load-bearing part — the
method prose in each seat — is a judgment task, not a template. The judgment belongs to the
`dao` skill; the checking belongs to this code.

**Right now the judgment half is benched.** The `dao` skill and its two agents (`bodhi`,
`hades`) sit in `incubator/` after a live routing eval found the skill failing to activate
on trigger phrases quoted verbatim in its own description. So what ships here is the
checker without its conductor: it can grade an organization, but nothing currently stands
one up. That is a known, deliberate half-state, not an oversight.

## Using it

```bash
dao spec --findings          # the eleven components, each with the research that constrains it
dao check <project>          # audit; non-zero while the council floor is incomplete
dao check <project> -v       # plus the substantive test behind every gap
dao figures <project>        # grounded domains available, honest about which are RAW_GATHERED
dao policy                   # the audit's thresholds, as declared policy
```

`dao check` walks `.claude/` and `CLAUDE.md`, reads them as text, and reports. It executes
nothing and writes nothing — see `SECURITY.md`.

`dao figures` reads the `dao-corpus` catalog and will tell you when a domain is
`RAW_GATHERED` — sources collected but not distilled. A seat projected from those is
standing on unreviewed material, and the tool says so rather than implying the corpus
validated it.

## Two properties worth not breaking

**`GateVerdict.out_of_family` travels with the verdict, not the gate** — as a record, not a
ranking. M1-H2 refuted the broad justification for a separate gate by measuring a two-pass
cold re-read as sufficient on everything tested. It did **not** measure out-of-family as
better; that is run-03, specified in `verifier-error-correlation/DESIGN.md` and never run.
The flag rides along so a stored verdict keeps its provenance and so the eventual
measurement has something to correlate against.

Phase 1 of that protocol did run (2026-08-21) and produced one number gate design should
consume now: **two verifiers fail together ~1.4× more often than independence predicts from
item difficulty alone**, before model family enters the picture. Stacking gates buys less
than naive composition implies.

**Thresholds are a declared `POLICY` map**, surfaced by `dao policy`. A threshold buried in
code is a governance decision hidden from governance.

## Install

dao is part of the gnx catalog and will be installed by `gnx add`. Until that lands:

```bash
uv tool install "dao @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/dao"
```
