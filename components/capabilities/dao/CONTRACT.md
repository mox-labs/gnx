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

The documents are UTF-8 Markdown in one directory, `dao/` by default. Each is a regular
file of at most `document_max_bytes`, in a directory inside the project. A document that is a
symbolic link, larger than that, or in a directory that resolves outside the project is not
read, and is reported ABSENT with the reason. A document's parts are
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

- Each participant has one row; one who holds several positions lists them in it.
- `Kind` is `human` (or `person`) or `agent`. Anything else is reported, not guessed.
- `Position` holds one or more position names, each of which must exist. Every position must
  be held by someone. At least one participant is a human.
- `Defined in` is optional: a path relative to the project root. When given, it must be a file
  inside the project.

## The book

**Stability: public.** `dao book` writes three files into the documents' directory:

| File | Is |
|---|---|
| `book.json` | the organization as data, in the shape below |
| `book.html` | a page a person opens, from disk or from a site; it carries the same data |
| `dao-book.js` | the `<dao-book>` element that draws the book, for that page and for a project's own site |

- **The book says only what the documents say**, plus three things worked out from them: who
  holds each position, whose work each position reads, and the chains of checking. It carries
  each document's Markdown as written, and no HTML.
- **The same documents and the same dao version give the same bytes** in all three files.
  None holds a date or a path. `dao check` relies on this (see below).
- **Each records its sources**: a SHA-256 over the six documents' names and bytes, as
  `sources` in the data and `<meta name="dao-sources" content="...">` in the page.
- **The page loads one script and nothing else.** Its content security policy allows
  `dao-book.js` from beside it, inline styles, and images from beside it or carried in the
  page; no network load.

### The data

`book.json`, and what `dao book --json` prints, is `dao.domain.book.Book`:

| Field | Holds |
|---|---|
| `format` | `1`. Raised when a field is removed or changes meaning, not when one is added. A reader refuses a format it was not built for. |
| `title` | the README's title line, or "How this project is run" |
| `directory`, `complete`, `sources` | where the documents are; whether all six were PRESENT; the digest |
| `documents` | the six, each with `key`, `name`, `file`, `summary`, `status`, `gaps`, and `markdown` (the text as written, without its title line) |
| `positions` | each with `name`, `does`, `must_not`, `checked_by`, `held_by`, `held_only_by_people`, `checks` |
| `participants` | each with `name`, `kind`, `positions`, `defined_in` |
| `chains` | lists of position names, each read by the next |

A chain starts at a position nobody else is checked by and follows each position's first
checker. When it reaches a position an earlier chain passed through, it names that position
and stops.

### The element

`<dao-book>` is a custom element in one classic script with nothing else to load. It takes
its book from the first of these that is given:

| Source | How |
|---|---|
| the `book` property | an object in the shape above, set from script |
| a child block | `<dao-book><script type="application/json">…</script></dao-book>` |
| the `src` attribute | the address of a `book.json`, fetched with same-origin credentials |

```html
<script src="dao-book.js"></script>
<dao-book src="book.json"></dao-book>
```

- **Theme.** It follows the reader's light or dark setting; `theme="light"` or `theme="dark"`
  on the element fixes one.
- **Colours and type** are custom properties on the element, each overridable from the page:
  `--dao-bg`, `--dao-surface`, `--dao-sunk`, `--dao-ink`, `--dao-muted`, `--dao-line`,
  `--dao-line-strong`, `--dao-accent`, `--dao-mark`, `--dao-human`, `--dao-agent`,
  `--dao-good`, `--dao-warn`, `--dao-bad`, `--dao-sans`, `--dao-serif`, `--dao-mono`.
- **What it does with a document's words.** Raw HTML in the Markdown is shown as text. A link
  from one document to another moves within the element. Other links are kept when they are
  `http:`, `https:`, `mailto:`, within the page, or a relative path; images when they are a
  relative path or `data:image/`. Anything else is shown as its text.
- **It lays itself out by its own width**, not the window's, so it fits a column of a site.

The element's markup, class names and appearance are **not contract**. The tag name, the
three sources, the `theme` attribute and the custom properties are.

### The book and the check

A project need not have a book, or all three files: a site that draws the data its own way
keeps `book.json` alone. `dao check` makes the files again from the documents and compares
each one that is on disk, byte for byte. Any difference makes the front door PARTIAL: the
documents changed since, the file was edited, or another version of dao made it. `dao book`
puts it right. A book made by one dao version is therefore reported by another whose page or
element differs; run the check with the version that made the book.

## The command

**Stability: public.**

| Command | Does | Exit |
|---|---|---|
| `dao check [PROJECT] [--dir PATH] [-v] [--json]` | audits PROJECT (default `.`) | 0 when all six are PRESENT, else 1; 2 when PROJECT is not a directory |
| `dao book [PROJECT] [--dir PATH] [--json]` | writes `book.json`, `book.html` and `dao-book.js` into `<dir>`; with `--json`, prints the data and writes nothing | 0, also when the organization is incomplete; 1 when there is no documents directory, or a file would land outside PROJECT; 2 when PROJECT is not a directory |
| `dao spec [--findings]` | prints the six elements and what each must contain | 0 |
| `dao policy` | prints the thresholds | 0 |
| `dao --skill [-r NAME]` | prints the skill text, or one of its references (`documents`, `findings`), as written | 0; 2 for an unknown name, or `-r` without `--skill` |
| `dao figures [PROJECT] [--corpus PATH]` | lists grounded domains of a local corpus catalog | 1 when no catalog is found or none informs PROJECT |

The statuses are `PRESENT`, `PARTIAL` and `ABSENT`, and there is never a score. The wording of
a gap, and the table's layout, are **not contract**: read the exit code, or `--json`.

`dao check --json` prints one object: `directory`, `complete`, and `elements`, a list in the
order of the six documents, each with `key`, `name`, `file`, `status`, `evidence` and `gaps`.
The keys are `constitution`, `policies`, `principles`, `positions`, `participants` and
`front-door`.

`dao check` reads the six documents, the book's three files, `CLAUDE.md` and `AGENTS.md`. It tests whether
a `Defined in` path is a file and does not read it. It writes nothing and runs nothing.
`dao book` reads the six documents and writes the book's three files; it does not write
through a symbolic link or outside PROJECT.

## The policy

**Stability: public.** `dao policy` prints it; `dao.application.policy.POLICY` holds it.

| Key | Default | Governs |
|---|---|---|
| `directory` | `dao` | where the documents live; `--dir` overrides it for one run |
| `agent_instructions` | `CLAUDE.md`, `AGENTS.md` | the files that may hold the pointer |
| `book` | `book.html` | the page `dao book` writes and `dao check` holds to the documents |
| `book_data` | `book.json` | the data, written and held the same way |
| `book_element` | `dao-book.js` | the element, written and held the same way |
| `document_max_bytes` | 1048576 | the largest file dao reads from a project |
| `position_remit_min_chars` | 240 | below this a remit is reported as a label |
| `positions_min` | 2 | |
| `principles_min` | 3 | |
| `section_min_chars` | 40 | below this a required section counts as empty |

The policy is not configurable per project in this release.

## The library

**Stability: not contract**, with one exception. `dao.application.audit.audit(root, directory)`
returning a list of `Coverage`, and `complete(coverage)`, are **public**. `audit` judges the
six documents and does not look at a book. Also public, in `dao.application.book`:
`build(root, directory)` returning a `Book`, and `check(root, directory, render=...)`, which
is `audit` plus the comparison of the book's files and is what `dao check` runs; `render`
gives each file's text by name. Everything else under `dao.`
may change without notice.
