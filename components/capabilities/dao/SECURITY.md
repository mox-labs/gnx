# Security — dao

dao's surface is small: it reads a corpus catalog, reads a project's `dao/` directory and its
agent instructions, prints a report, and with `dao book` writes one HTML file into that
directory. It executes nothing and fetches nothing.

Audience: anyone running `dao check` or `dao book` against a repository they did not write,
and anyone opening a `book.html` someone else made.

## Blast radius

`dao spec`, `dao check`, `dao figures` and `dao policy` are read-only and end in a console
table or JSON. `dao book` writes exactly one file, `book.html` in the documents' directory.
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
as a gap that says why. `book.html` is read by the same rule. `CLAUDE.md` and `AGENTS.md` may
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

`dao book` turns documents that agents and contributors edit into a page a person opens in a
browser. Three layers keep a document from acting on that person, each there for the day the
one before it has a hole:

1. **Raw HTML in a document is shown as text.** The Markdown is rendered with HTML disabled.
2. **Links and images are held to a list.** A link is kept when its address is `http:`,
   `https:` or `mailto:`, a place within the page, or a relative path. An image is kept when
   its address is a relative path or a `data:image/` address. Any other address, including one
   that starts with `/`, `//` or `\`, or has another scheme, is dropped and its text shown.
3. **The template escapes every value** it places: names, fields, gaps.
4. **The page forbids scripts and outside loads.** Its content security policy is
   `default-src 'none'`, with inline styles, `data:` images and images from the page's own
   origin the only exceptions. A script that reached the page would not run, and nothing is
   fetched from another origin when it is opened.

Two limits of that. A relative link or image may point above the page's directory
(`../x.png`): opened from disk that is a file on the reader's machine, and served from a site
it is a request to that site, so a document can make a reader's browser ask the page's own
origin for a path. And the policy's `'self'` was tested over `http://` only; how a browser
applies it to a page opened from disk was not.

The tests feed the renderer script tags, event-handler attributes, a `javascript:` link,
rooted and other-host addresses, and a participant name that closes its attribute, and hold
that none reaches the page as markup or as an address.

**Where it writes.** One path: `<directory>/book.html` under the project. `dao book` refuses
when that path is a symbolic link, or when the directory resolves outside the project, so a
hostile repository cannot aim the write elsewhere. The page is written to a new file beside
the target and moved into place, which replaces a link planted in the meantime and does not
follow it. An existing `book.html` is replaced without asking; the file is a generated copy.

**A `book.html` you did not make is somebody's HTML.** Nothing in a file proves dao made it,
and one from a pull request or a download has none of the layers above unless dao did. Before
opening one, run `dao check`: when a book is there, the check renders the page again from the
documents and compares the two byte for byte, and reports any difference under the front
door. Or run `dao book` and open what it writes. The digest in the page is not that proof; it
only lets the check say that the documents changed, as distinct from the page.

**What the page claims.** The page says what `dao check` found when it was made. The
comparison covers the six documents. It does not cover an agent's defining file: a page made
before one was removed still shows that participant. `dao check` is the current answer; the
page is a copy.

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
that generator: it decides nothing, and every word on its page comes from the documents.

## Findings

The 2026-08 pass found no security-relevant defect. The book was added in 0.2.0 and was
reviewed once before release, by a reviewer of the same model family as its author working
without the author's conversation. That review found eight things, all fixed before release
and each held by a test in `tests/test_book.py`:

| Was | Now |
|---|---|
| A document that was a symbolic link was read, so a file outside the project could be copied into the page | documents are read by the rule above |
| A link to a device, or a very large file, was read until memory ran out | the same rule: regular files, 1 MiB |
| `dao check` called any HTML carrying the right digest "current" | the check renders again and compares bytes |
| One long line made two patterns take minutes | the patterns do not backtrack |
| A `Defined in` path the system rejects ended in a traceback | reported as a gap |
| Chains of checking took cubic time in the number of positions | each position is walked once |
| Rooted, other-host and unusual-scheme addresses became links and image sources | links and images are held to a list |
| The page was written after its path was checked, through whatever was there by then | written beside and moved into place |

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
- The book has not been tested on Windows, or opened from disk under a browser's `file:`
  rules.
- dao checks form, not meaning, and three of its tests look for words. PARTIAL is a signal
  to look, not a proof, and PRESENT is not a review.
- The `dao` plugin's skill writes files into a project, as any agent session does, under the
  permissions of that session. That surface is the session's, not this package's.

## Reporting

Report a vulnerability privately, through GitHub's private vulnerability reporting: see the
repository's [SECURITY.md](../../../SECURITY.md). Do not open a public issue for one.
