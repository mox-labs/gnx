# Code rubric: matrix, ix, recon, hardline

The instrument an evaluator scores a change against and an optimizer fixes to. It is the
code counterpart of `docs/RUBRIC.md`. A verdict you disagree with means this file needs
adjusting, not ignoring.

Sources: the shared constitution (`~/.claude/CLAUDE.md`), the software-design canon card
(`research/drafts/software-design-canon/synthesis/CANON.md` §5 and the agent-facing
addendum), and the owner's rulings for these packages. Canon rules are cited as `C<n>`.

## Hard gates

A change that fails any hard gate is RETURN, whatever else it does well.

**H1. Correct on failure.** Behaviour on failure is part of the contract (C16). Every
failure lands with the party that caused it, nothing fails silently (C17), and no error is
swallowed by a broad `except` that also catches unrelated bugs. In ix the owner of a failure
decides what the analysis does with it: a subject failure counts against the subject, a
harness failure is missing data. Misattributing one is a wrong verdict, so it is a hard
gate, not a style point.

**H2. Hexagonal, library first.** The domain imports no adapter, no host and no I/O library.
Adapters meet ports only in the composition root. The CLI calls only the public API
(`__all__`). The import-direction test enforces what it can; the evaluator reads for what
it cannot (a domain function that shells out through a helper, a port that leaks an
adapter's type).

**H3. One registry, composed from config.** Everything that plugs in registers through the
one registry (matrix's `Registry`, discovered from the `matrix.extensions` entry point)
under a point its namespace owns. The composition root builds what config names by
resolving it through the registry. A hard-coded table of adapters in a composition root is
a fail: it makes the package unextensible without editing it.
Ruled exception (the owner, 2026-10-14, under H6): genre and kind *declarations* are data,
not components. They stay YAML files found through ix's `ix.genre` / `ix.kind` entry
points, are only ever parsed, and meet the registry's adapters in ix's one composition
seam. Do not report this as an H3 failure; report only if that seam leaks.

**H6. Architect for evolution: no unexamined one-way doors.** The owner's main goal. A
one-way door is anything outside code depends on once released: a document schema
(`ix.v1.*`, `matrix.v1.*`), a file layout (experiment, registration, run record), a type URL,
an entry-point group, a registry point name, a CLI flag or exit code, and the `--skill` text
agents learn from. Each new one must be needed now, named and versioned, and as small as
the need. Everything else stays internal (underscore, out of `__all__`). The registry and
some core APIs will move to slick: a package reaches the registry through one seam in its
composition root, so the swap is one change in one place, and builds nothing on matrix's
registry beyond what it uses today.

**H4. Tests verify requirements.** No test is weakened, skipped or deleted to make a change
pass. A changed requirement is named as such. A new bug gets a regression test that fails
without the fix. `just ci` is green.

**H5. Nothing invented.** No claim in a docstring, README or report that the code does not
back. No statistic printed as measured that is a bound or a default (a p-value floor is
not a p-value).

## Criteria (1 to 5 each)

**L. Legible.** Code reads top to bottom without a guide.
- Names say what a thing is in the domain's words (`subject`, `trial`, `reading`), not how
  it is built. A name that needs a comment is the wrong name.
- A function does one thing at one level. Over about 60 lines, or more than six positional
  parameters, it is a candidate to split; a call site with many positional arguments of the
  same type is a fail.
- Small private state is a frozen dataclass or a pydantic record, not a class that is
  mutated field by field after construction.
- `Any` only at a real boundary (raw JSON, a third-party object). Not for a type the
  package defines.
- No dead parameters, no declared options nothing reads, no identity maps.

**D. Docstrings and comments, sparing.** The owner's ruling: hyper-legible code, no
comments all over.
- A docstring states the contract a signature cannot: preconditions, what it raises, what
  it guarantees on failure, ordering. It never restates the signature or the name.
- A `#:` field note says what the name cannot (units, who sets it, when it is empty).
  `#: The probe's id.` on `probe: str` is noise and goes.
- A module docstring orients a reader: what the module owns and the one or two rules that
  are not visible in the code. It is short. The exception is a module whose docstring is
  the published specification of a method (the analysis), which may be long and must then
  match the code exactly.
- Inline comments only for a non-obvious why. Never what, never history.
- No history anywhere: "previously", "no longer", "the old mock", "was renamed". Git holds
  history.
- No mention of the predecessor repository, by name or allusion.
- No claims about the wider landscape (counts, versions, "upcoming").

**A. Architecture and the canon.**
- Each module owns one decision and hides it (C2). Likely changes touch no interface (C4).
- Deep, not wide: a lot behind a small interface (C7). A port exists because it hides a
  decision likely to change, not because there might be a second adapter (owner's ruling).
- Each condition is checked in one place (C12). A rule that the run path enforces at run
  time and validation could have caught first is checked in validation.
- When in doubt, leave it out (C14). No speculative options.
- Conceptual integrity (C21): the same idea is done the same way across matrix, ix, recon
  and hardline. One stack: pydantic v2 for every record, rich-click for every CLI, rich
  for terminal output, httpx for HTTP (async). Errors with `kind`, `message`, `fix`; exit
  codes from the shared `exit-codes.json`. A package that departs from the stack names why
  in one line, and the owner has accepted it.
- No backwards compatibility shims. This is pre-release; the owner has said so.

**G. Agent drivability** (the canon's addendum).
- Errors carry a machine-readable kind, the value that was wrong, and a fix.
- Data on stdout, commentary on stderr, `--json` everywhere, exit codes for the important
  outcomes.
- Plan before spend: anything that costs (time, money, load on a third party) can be
  shown before it happens.
- Documentation an agent reads (help, `--skill`, genre guidance) agrees with what the code
  does. Disagreement is a defect, because an agent acts on the prose.

**T. Tests.** Requirements, not implementation. Fakes are small and honest about what they
stand in for. Every failure owner and every terminal state has a test. Timing-sensitive
tests assert direction only where the margin is wide, and say so.

## Verdict

- **SHIP**: every hard gate passes, and every criterion scores 4 or 5.
- **TIGHTEN**: every hard gate passes, and some criterion scores 3. List each fix.
- **RETURN**: any hard gate fails, or any criterion scores 2 or less. Name the gate, the
  file and line, and the fix.

Each finding the evaluator reports carries: the rule (H1..H5, L, D, A, G, T), the file and
line, the defect in one sentence, a concrete failure scenario or the offending text quoted,
and the fix. A finding without a file and line is dropped. "Nothing found" for a criterion
is a valid answer and must be stated.

## Discipline for the loop

- The evaluator does not fix; the optimizer does not grade its own work.
- The optimizer may reject a finding, with a reason, when it is wrong; a rejected finding
  is reported, never silently dropped.
- Before reporting a count of findings, open the cited lines. A checker's verdict is a
  claim, not a fact.
- Once every hard gate passes and a clean pass scores 4 or more everywhere, stop: further
  rounds thrash.
