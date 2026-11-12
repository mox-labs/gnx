# dao's contracts

What each published surface of dao consists of, its defaults, how it fails, and how far it is
promised. dao is before 1.0: a break is a minor release whose CHANGELOG says so under
"Changed (breaking)" or "Removed (breaking)".

Stability is one of three words:

- **Stable**: changes only by addition, or by a break announced as above.
- **Public**: may change in a minor release, said in the CHANGELOG.
- **Not contract**: may change without notice.

## The document formats

**Stability: stable.** These are what a project writes and `dao check` reads. A project that
is COMPLETE under one release stays COMPLETE under the next unless the CHANGELOG says a format
broke.

The documents are UTF-8 Markdown in one directory, `dao/` by default. A document's parts are
its `## ` sections; a heading inside a fenced code block is not a heading. Headings are matched
by a word, in any case.

| File | Required | Rules |
|---|---|---|
| `constitution.md` | sections matching `purpose`; `commitment` or `non-negotiable`; `amend` or `chang` | each at least `section_min_chars`; the amendment section contains the name of a position, and a human holds one of the positions it names |
| `policies.md` | sections matching `decision` or `decid`; `verif`, `review` or `check`; `chang` or `amend` | each at least `section_min_chars`; Decisions mentions the irreversible or the novel; Verification names its kind of check (a different family, the same family or a cold re-read, or a person) |
| `principles.md` | at least `principles_min` list items or `## ` sections | |
| `positions.md` | at least `positions_min` `## ` sections, each a position | fields `Does:`, `Must not:`, `Checked by:`; see below |
| `participants.md` | a table whose header has `Participant` and `Position` | columns `Kind` and `Defined in`; see below |
| `README.md` | contains the file name of each of the other five | `CLAUDE.md` or `AGENTS.md` at the project root contains `<directory>/` |

**A position** is a `## ` section; its name is the heading, compared without case, code ticks
or emphasis. A field starts a line as `Name:`, optionally bold and optionally a list item, and
runs to the next field or the end of the section.

- `Does:` and `Must not:` are required, and together run to `position_remit_min_chars`.
- `Checked by:` holds one or more position names, separated by commas or "and". At least one
  must be a different position that exists. A position held only by humans may omit it or
  write `none`.
- No two positions share a name.

**A participant** is a row of the first table whose header has `Participant` and `Position`.

- `Kind` is `human` (or `person`) or `agent`. Anything else is reported, not guessed.
- `Position` holds one or more position names, each of which must exist. Every position must
  be held by someone. At least one participant is a human.
- `Defined in` is optional: a path relative to the project root. When given, it must be a file
  inside the project.

## The command

**Stability: public.**

| Command | Does | Exit |
|---|---|---|
| `dao check [PROJECT] [--dir PATH] [-v]` | audits PROJECT (default `.`) | 0 when all six are PRESENT, else 1; 2 when PROJECT is not a directory |
| `dao spec [--findings]` | prints the six elements and what each must contain | 0 |
| `dao policy` | prints the thresholds | 0 |
| `dao --skill [-r NAME]` | prints the skill text, or one of its references (`documents`, `findings`), as written | 0; 2 for an unknown name, or `-r` without `--skill` |
| `dao figures [PROJECT] [--corpus PATH]` | lists grounded domains of a local corpus catalog | 1 when no catalog is found or none informs PROJECT |

The statuses are `PRESENT`, `PARTIAL` and `ABSENT`, and there is never a score. The wording of
a gap, and the table's layout, are **not contract**: read the exit code, not the text.

`dao check` reads the six documents, `CLAUDE.md` and `AGENTS.md`. It tests whether a
`Defined in` path is a file and does not read it. It writes nothing and runs nothing.

## The policy

**Stability: public.** `dao policy` prints it; `dao.application.audit.POLICY` holds it.

| Key | Default | Governs |
|---|---|---|
| `directory` | `dao` | where the documents live; `--dir` overrides it for one run |
| `agent_instructions` | `CLAUDE.md`, `AGENTS.md` | the files that may hold the pointer |
| `position_remit_min_chars` | 240 | below this a remit is reported as a label |
| `positions_min` | 2 | |
| `principles_min` | 3 | |
| `section_min_chars` | 40 | below this a required section counts as empty |

The policy is not configurable per project in this release.

## The library

**Stability: not contract**, with one exception. `dao.application.audit.audit(root, directory)`
returning a list of `Coverage`, and `complete(coverage)`, are **public**. Everything else under
`dao.` may change without notice.
