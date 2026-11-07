# dao

Audits whether a project's agent organization is there, or only looks like it is. `dao check`
reads a project's `.claude/` and `CLAUDE.md` and reports each of eleven parts as PRESENT,
PARTIAL or ABSENT, never as a score.

dao checks an organization. It does not write one: there is no `dao init`, and nothing in this
package stands one up.

## Install

```bash
uv tool install "git+https://github.com/mox-labs/gnx@dao-v0.1.0#subdirectory=components/capabilities/dao"
```

From a clone of the repository: `uv --project components/capabilities run dao --help`.

## First run

```bash
dao check <project>       # audit; exits 1 while the council floor is incomplete
dao check <project> -v    # plus the substantive test behind every gap
dao spec --findings       # the eleven parts, each with the research finding that shaped it
dao policy                # the audit's thresholds, as declared policy
```

`dao check` reads files as text. It executes nothing and writes nothing
([SECURITY.md](SECURITY.md)).

## Next

- [docs/explanation/overview.md](docs/explanation/overview.md): what the eleven parts are, why
  PARTIAL is the useful verdict, and a worked run.
- [CHANGELOG.md](CHANGELOG.md): what changed.
