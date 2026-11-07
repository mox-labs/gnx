# Changelog — dao

## 0.1.0 — unreleased

The first version. It has been in this repository since 2026-08-18 and has no release tag yet.

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
