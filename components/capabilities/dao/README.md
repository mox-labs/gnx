# dao

Checks that a project's agent organization is written down and holds together. A **dao** is a
directed agent organization: the people and agents that maintain a project, what each may do,
who decides, and who is accountable. It lives in the repository as six short documents, and
`dao check` reports each as PRESENT, PARTIAL or ABSENT, never as a score. `dao book` makes
the six a book a person can explore: who holds what, who reads whose work, and the rules.

dao checks an organization and shows it. It does not write one: there is no `dao init`. The
`dao` plugin's skill writes one with you, and calls this command to check it and make its book.

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
dao book <project>           # write the book: dao/book.html to open, book.json, dao-book.js
dao book <project> --json    # the organization as data; writes nothing
dao spec --findings          # the six documents, each with the research finding behind it
dao policy                   # the audit's thresholds, as declared policy
dao --skill                  # how to set a project up, for an agent (-r documents, -r findings)
```

`dao check` reads files as text. It executes nothing and writes nothing. `dao book` writes
three files beside the documents: the organization as data, a page to open, and the
`<dao-book>` element that draws it. The page runs that one script and loads nothing from the
network ([SECURITY.md](SECURITY.md)).

To show the book on a project's own site, put the element and the data where the site serves
them:

```html
<script src="dao-book.js"></script>
<dao-book src="book.json"></dao-book>
```

## Next

- [docs/explanation/overview.md](docs/explanation/overview.md): the six documents, why PARTIAL
  is the useful verdict, the book, and a worked run.
- [CONTRACT.md](CONTRACT.md): the document formats, the book and the command surface, and how
  far each is promised.
- [CHANGELOG.md](CHANGELOG.md): what changed.
