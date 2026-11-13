# dao

Checks that a project's agent organization is written down and holds together. A **dao** is a
directed agent organization: the people and agents that maintain a project, what each may do,
who decides, and who is accountable. It lives in the repository as six short documents, and
`dao check` reports each as PRESENT, PARTIAL or ABSENT, never as a score. `dao book` renders
the six as one page a person can read.

dao checks an organization and shows it. It does not write one: there is no `dao init`. The
`dao` plugin's skill writes one with you, and calls this command to check it and render it.

## Install

```bash
uv tool install "git+https://github.com/mox-labs/gnx@dao-v0.2.0#subdirectory=components/capabilities/dao"
```

From a clone of the repository: `uv --project components/capabilities run dao --help`.

The skill that sets a project up is in the gnx marketplace: `/plugin marketplace add
mox-labs/gnx`, then `/plugin install dao@gnx`.

## First run

```bash
dao check <project>          # audit; exits 1 unless all six documents are PRESENT
dao check <project> -v       # plus what each document with a gap must contain, and why
dao check <project> --dir governance   # when the documents are not in dao/
dao check <project> --json   # the same verdicts, for a program
dao book <project>           # write dao/book.html: the organization on one page
dao book <project> --json    # the organization as data; writes nothing
dao spec --findings          # the six documents, each with the research finding behind it
dao policy                   # the audit's thresholds, as declared policy
dao --skill                  # how to set a project up, for an agent (-r documents, -r findings)
```

`dao check` reads files as text. It executes nothing and writes nothing. `dao book` writes one
file, `book.html` beside the documents: a page that runs no script and loads nothing
([SECURITY.md](SECURITY.md)).

## Next

- [docs/explanation/overview.md](docs/explanation/overview.md): the six documents, why PARTIAL
  is the useful verdict, the book, and a worked run.
- [CONTRACT.md](CONTRACT.md): the document formats, the book and the command surface, and how
  far each is promised.
- [CHANGELOG.md](CHANGELOG.md): what changed.
