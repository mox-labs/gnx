# Changelog — dao

## 0.2.0 — 2026-11-12

dao now describes an organization in Ostrom's terms: **positions**, **participants**, and three
levels of rule (a **constitution**, **governing policies** and **operating principles**), with a
front-door README. They are six Markdown documents in the project's `dao/` directory, and
`dao check` audits those. The setup skill ships as the `dao` plugin in the gnx marketplace.

### Changed (breaking)

- **`dao check` audits six documents in `dao/`, not eleven parts spread over `.claude/` and
  `CLAUDE.md`.** A project that passed 0.1.0 is ABSENT on all six until the documents are
  written. The formats are in [CONTRACT.md](CONTRACT.md).
- **One verdict.** `dao check` exits 0 only when all six documents are PRESENT. The council
  floor and the organization delta are gone, and with them the two-line summary.
- **`dao spec` prints the six documents** and what each must contain.
- **`dao policy` prints a typed policy**: `directory`, `agent_instructions`,
  `position_remit_min_chars`, `positions_min`, `principles_min`, `section_min_chars`.
  `seat_method_min_chars`, `bench_min_seats` and `ratchet_requires_trigger` are gone.

### Added

- **Checks that the documents agree with each other.** A participant's position exists; every
  position is held; a position's checker is a different position; the position that may amend
  the constitution is held by a person; a file named for an agent is on disk and inside the
  project; the README links all five other documents; `CLAUDE.md` or `AGENTS.md` points at the
  directory.
- **`dao check --dir <path>`** for documents kept somewhere other than `dao/`.
- **The `dao` plugin**: the skill that reads a project, asks its owner a few questions, writes
  the six documents, runs `dao check`, and then reviews what the documents say.
- **`dao --skill`** prints the skill text the plugin carries, and `-r documents` or
  `-r findings` one of its references. The skill lives in the package; the plugin is projected
  from it.
- [CONTRACT.md](CONTRACT.md).

### Kept, under the new documents

- A position's remit must be prose, not a label (was the bench's method prose, M1).
- A verification policy must say what kind of check it is (was the gate's label, M1-H2).
- Irreversible and novel decisions go to a person (was the human-accountability surface, M4).
- The constitution needs commitments and an amendment rule (was the charter).

### Removed (breaking)

- **Five checks have no counterpart in 0.2.0**: the practice library (`.claude/skills/`), the
  learning loop (`ratchet.md` and its triggers), the perimeter and drift ladder, the recurring
  disciplines and their "signal from outside" test, and the peer-content rule. The last two
  were keyword tests of meaning, which this audit no longer pretends to do; the skill's review
  asks them of a reader instead.
- **`Seat`, `Charter`, `FigureSet` and the `ProjectionTarget` port**, which nothing used.
  `Position` and `Participant` are the types the audit reads.
- **`dao.domain.spec`'s `Tier`, `Group`, `Component`, `COUNCIL_FLOOR`, `ORGANIZATION_DELTA`,
  `CONDITIONAL` and `by_number`**, replaced by `Key`, `Status`, `Element` and `by_key`.

## 0.1.0 — 2026-11-07

The first version, tagged `dao-v0.1.0`.

### What it holds

- `dao check <project>`: audits `.claude/` and `CLAUDE.md` against the eleven parts of the dao
  spec and reports each as PRESENT, PARTIAL or ABSENT, never a score. Exits 1 while the
  council floor is incomplete.
- `dao spec`: the eleven parts, with `--findings` for the research finding behind each.
- `dao policy`: the audit's thresholds, as a declared map.
- `dao figures`: the grounded domains of a local corpus catalog, saying which are only
  gathered, not distilled.

### Changed since it arrived

- **The help text and the package description say what ships: an audit** (2026-11-07). They
  said dao would "stand up and audit" an organization; nothing in the package stands one up.
- **A copy of the repository's LICENSE ships in the package** (2026-11-07), so a wheel built
  from this directory alone carries it.
- **Out-of-family verification is recorded, not ranked** (2026-08-21). The audit had described
  an out-of-family gate as the stronger kind, which the research has not measured.
  `GateVerdict.out_of_family` travels with a verdict as a record, and the gate check accepts an
  out-of-family anchor or an honest cold re-read label alike.
- **pydantic is a declared dependency** (2026-10-09), and the `dev` extra is gone (2026-10-23):
  the test tools come from the workspace's dev group.
