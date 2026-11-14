# Security — dao

dao's surface is small: it reads a corpus catalog, reads a project's `dao/` directory and its
agent instructions, prints a report, and with `dao book` writes three files into that
directory: the organization as data, a page, and the script that page runs. The command
executes nothing and fetches nothing.

Audience: anyone running `dao check` or `dao book` against a repository they did not write,
anyone opening a `book.html` someone else made, and anyone putting `<dao-book>` on a site.

## Blast radius

`dao spec`, `dao check`, `dao figures` and `dao policy` are read-only and end in a console
table or JSON. `dao book` writes exactly three files in the documents' directory:
`book.json`, `book.html` and `dao-book.js`.
There is deliberately **no `dao init`**: see below.

## Trust boundaries

### 1. Project directory → audit report

`dao check <project>` reads the six documents in the project's `dao/` directory (or the
directory `--dir` names), and `CLAUDE.md` and `AGENTS.md` at the project root, and reports
PRESENT / PARTIAL / ABSENT. Files are read as text and never executed, imported, or rendered
as a template.

**What dao will read.** A repository can carry a symbolic link to any file its reader can
open, and a document that was a link to `~/.aws/credentials` would be copied into a page made
for sharing. So every document is read by one rule (`application/organization.py`): a regular
file, not a symbolic link, in a directory that resolves inside the project, and no larger
than `document_max_bytes` (1 MiB). A document that fails the rule is not read and is reported
as a gap that says why. The book's three files are read by the same rule. `CLAUDE.md` and `AGENTS.md` may
be links, because one is often a link to the other, but only to a regular file inside the
project; their text is searched for the pointer and never shown.

Hard links are not detected. git does not carry them.

A participants table may name a file under `Defined in`. dao tests whether that path is a file
and does not open it. A path that resolves outside the project is reported as a gap and is not
tested, so a hostile document cannot use the report to probe the rest of the filesystem.

A hostile project can make the audit *wrong* (a padded position can pass a length test). It
should not be able to make the audit slow or make it fail: the patterns that read a document
do not backtrack, the work is linear in the size of the documents, and a path the system
rejects is reported as a gap. The tests hold each of those against the inputs that broke
them in review.

### 2. Project documents → the book

`dao book` turns documents that agents and contributors edit into something a person opens
in a browser. The page runs one script, the `<dao-book>` element, and that script is what
shows the documents' words. These layers keep a document from acting on the reader, each
there for the day the one before it has a hole:

1. **dao puts no document text into HTML.** The book travels as JSON: in `book.json`, and
   inside the page in a `<script type="application/json">` block, which a browser does not
   run. Every `<`, `>` and `&` in that block is written as a JSON escape, so no text can close
   the block or open a tag. The page's title is HTML-escaped.
2. **The element shows values as text.** Names, fields and gaps are set as text, never as
   markup. Data of the wrong type is dropped, and a book of a format the element was not
   built for is refused.
3. **Raw HTML in a document is shown as text.** The one place the element makes HTML is from
   a document's Markdown, rendered with HTML disabled (`web/src/markdown.ts`).
4. **Links and images are held to a list.** A link is kept when its address is `http:`,
   `https:` or `mailto:`, a place within the page, or a relative path. An image is kept when
   its address is a relative path or a `data:image/` address. Any other address, including one
   that starts with `/`, `//` or `\`, or has another scheme, is dropped and its text shown.
5. **The page allows one script and no outside load.** Its content security policy is
   `default-src 'none'; script-src 'self'`, with inline styles, `data:` images and images from
   the page's own origin the only other exceptions. An injected inline script would not run,
   and nothing is fetched from another origin when the page is opened.

Limits of that:

- **`'self'` allows any script beside the page**, not `dao-book.js` alone. Nothing dao
  writes can add a script tag, so this matters only if another layer fails.
- **A relative link or image may point above the page's directory** (`../x.png`): opened from
  disk that is a file on the reader's machine, and served from a site it is a request to that
  site, so a document can make a reader's browser ask the page's own origin for a path.
- **On a project's own site the fifth layer is the site's.** `<dao-book>` in a page dao did
  not write runs under that page's policy, and the first four layers are what hold. With
  `src`, the element makes one request, for that address, with same-origin credentials, and
  reads the answer as data.
- **The page was opened from disk in Chrome only.** How other browsers apply `'self'` to a
  page opened from disk was not tested.

The element's tests (`web/src/markdown.test.ts`) feed the renderer script tags, event-handler
attributes, `javascript:` and `data:` links, rooted and other-host addresses and remote
images, and hold that none reaches the page as markup or as an address. The package's tests
(`tests/test_book.py`) hold that no document text can leave the data block or the title, and
that the shipped script contains no call that evaluates text or opens a connection besides
the one request for its book.

**The script is built, and the build is checked.** `dao-book.js` is compiled from `web/`
(Svelte and markdown-it) and committed, because the Python package ships it. `just dao-book`
builds it again in CI and fails when the committed file differs, so the script is always the
build of source a reviewer can read. Its locked dependencies are in the repository's audit.

**Where it writes.** Three paths: `book.json`, `book.html` and `dao-book.js` in `<directory>`
under the project. `dao book` refuses when any of them is a symbolic link, or when the
directory resolves outside the project, so a hostile repository cannot aim a write elsewhere.
Each file is written to a new file beside its target and moved into place, which replaces a
link planted in the meantime and does not follow it. Existing files are replaced without
asking; they are generated copies.

**A book you did not make is somebody's HTML and somebody's script.** Nothing in a file
proves dao made it, and a `book.html` or a `dao-book.js` from a pull request or a download
has none of the layers above unless dao did. Before opening one, run `dao check`: it makes
each of the three files again from the documents and compares byte for byte, and reports any
difference under the front door. A changed `dao-book.js` is the finding that matters most,
since the page runs it. Or run `dao book` and open what it writes. The digest in the files is
not that proof; it only lets the check say that the documents changed, as distinct from the
file.

**What the book claims.** It says what `dao check` found when it was made. The comparison
covers the six documents. It does not cover an agent's defining file: a book made before one
was removed still shows that participant. `dao check` is the current answer; the book is a
copy.

### 3. Corpus catalog → grounding

`dao figures` reads `dao-corpus/corpora/agent-craft`'s CATALOG. The catalog's `informs`
edges decide which domains are offered as grounded for a project.

**The honesty property is the security property here.** A domain marked `RAW_GATHERED` has
sources collected but not distilled; presenting it as grounded would launder unreviewed
material into a project's positions. `dao figures` prints the count explicitly and
`CorpusDomain.is_grounded` returns true only for `DISTILLED`.

### 4. No generation

There is no `dao init`. Scaffolding six plausible files is exactly the artifact-shaped
hole the audit exists to catch, and the load-bearing part, what the documents commit to, is a
judgment task. The skill holds the judgment; the code holds the checking. `dao book` is not
that generator: it decides nothing, and every word in its book comes from the documents.

## Findings

The 2026-08 pass found no security-relevant defect. The book was added in 0.2.0 and was
reviewed twice before release, each time by a reviewer of the same model family as its
author working without the author's conversation. Everything found was fixed before release
and is held by a test, in `tests/test_book.py` or `web/src/*.test.ts`.

The first review was of reading a project and writing a page:

| Was | Now |
|---|---|
| A document that was a symbolic link was read, so a file outside the project could be copied into the page | documents are read by the rule above |
| A link to a device, or a very large file, was read until memory ran out | the same rule: regular files, 1 MiB |
| `dao check` called any HTML carrying the right digest "current" | the check makes the files again and compares bytes |
| One long line made two patterns take minutes | the patterns do not backtrack |
| A `Defined in` path the system rejects ended in a traceback | reported as a gap |
| Chains of checking took cubic time in the number of positions | each position is walked once |
| Rooted, other-host and unusual-scheme addresses became links and image sources | links and images are held to a list |
| The page was written after its path was checked, through whatever was there by then | written beside and moved into place |

The second was of the book as data, a page and the `<dao-book>` element. It found no way for
a document or a forged `book.json` to run script, leave the data block, or have a forged file
vouched for, and six other things:

| Was | Now |
|---|---|
| A participant listed twice, or a repeated name in a forged book, left the page blank while `dao check` passed | the element shows each name once and reports a failure to draw; the audit reports the repeat |
| One long heading line froze the reader's tab | the element splits headings in linear time |
| Many positions and participants made `dao check` and `dao book` take minutes | holders are worked out once |
| A link addressed to `constructor` or `__proto__` was taken for a link between documents | only the documents' own names are |
| `dao book` stopped half-written, with a traceback, when a target was a directory | refused before anything is written |
| A forged book was not examined when `README.md` was missing | a book's files are always compared |

That review did not open the page from disk, did not run the two slow cases at the full
1 MiB, and read the element's six runtime dependencies but not the build tools' own.

Two adjacent design properties worth recording, since both would be security-relevant if
reversed:

- **`GateVerdict.out_of_family` travels with the verdict**, not with the gate
  (`domain/models.py`). A stored or forwarded verdict cannot lose the one fact that
  determines its weight — M1-H2 measured a same-family reviewer as adding nothing over a
  cold re-read, so a verdict that shed its provenance could later be mistaken for the
  stronger kind.
- **Audit thresholds live in a declared `POLICY` map**, surfaced by `dao policy`
  (`application/audit.py`). M2's finding: a threshold buried in code is a governance
  decision hidden from governance.

## Not covered

- dao does not verify corpus provenance. It trusts the CATALOG's own grounding labels.
- The book has not been tested on Windows, and the page was opened from disk in Chrome only.
- The element was not tested with a screen reader. Its controls are buttons with names and
  states; the map's lines are decoration, and the same links are in the text beside it.
- dao checks form, not meaning, and three of its tests look for words. PARTIAL is a signal
  to look, not a proof, and PRESENT is not a review.
- The `dao` plugin's skill writes files into a project, as any agent session does, under the
  permissions of that session. That surface is the session's, not this package's.

## Reporting

Report a vulnerability privately, through GitHub's private vulnerability reporting: see the
repository's [SECURITY.md](../../../SECURITY.md). Do not open a public issue for one.
