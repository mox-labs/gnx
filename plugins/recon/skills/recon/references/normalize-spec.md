# Normalize spec

A normalize spec maps each output column to a path into the record, optionally piped through a transform: `output_column: "source.path|$transform"`. It applies to every collector type, after `extract`.

## Contents

- Path syntax
- Pipe syntax
- Built-in transforms
- Capture collectors: `_capture`
- Response formats
- Extract path

## Path syntax

A path is keys separated by `.`:

| Pattern | Meaning | Example |
|---------|---------|---------|
| `field` | a key | `title` → `record["title"]` |
| `a.b.c` | a nested key | `openAccessPdf.url` → `record["openAccessPdf"]["url"]` |
| `a.0.b` | a list index | `authors.0.name` → the first author's name |
| `a.*.b` | the rest of the path, from every item | `authors.*.name` → `[a["name"] for a in record["authors"]]` |
| `a.*.b.c` | the same, deeper | `authorships.*.author.display_name` |
| `a.*` | the list itself (an object's values, for an object) | `items.*` |

A path that does not resolve gives `null`, never an error: one record lacking a field must not fail a table. With `*`, the whole column is `null` when any item lacks the rest of the path. One `*` per path; a second is a plan problem. A digit is a key in an object and an index in a list. A key that itself holds a `.` cannot be reached.

### Examples from real APIs

```yaml
# Semantic Scholar
title: title                          # direct
authors: "authors.*.name"             # list-map
pdf_url: "openAccessPdf.url"          # nested
citations: citationCount              # rename

# OpenAlex
title: display_name                   # rename
authors: "authorships.*.author.display_name"  # deep list-map
abstract: "abstract_inverted_index|$inverted_index"  # path + transform

# Zenodo
title: "metadata.title"              # nested
authors: "metadata.creators.*.name"  # nested + list-map
abstract: "metadata.description|$html2text"  # nested + transform

# arXiv (after xmltodict)
venue: "arxiv:primary_category.@term"  # XML attribute key
authors: "author.*.name"              # list-map
```

## Pipe syntax

Append `|$transform` to apply a transform after path resolution:

```yaml
abstract: "metadata.description|$html2text"
#          ^^^^^^^^^^^^^^^^^^^^^ ^^^^^^^^^
#          path                   transform
```

The pipe is always `|` (no spaces required). Transform names start with `$`.

**An unknown transform is a plan problem.** `recon survey --dry-run` (and every survey, which plans first) reports it at `collectors.<i>.normalize.<column>` with the installed transforms, and exits 3 before anything runs. So is a path with two `*` (`a.*.b.*.c`) or a pipe without a `$name`.

Transforms are extensions: each is registered under the entry-point group `recon.transforms`, and `recon status --json` lists what is installed (`plugins.transforms`). A package installed alongside recon can add its own.

## Built-in transforms

| Transform | Input | Output | Use case |
|-----------|-------|--------|----------|
| `$html2text` | HTML string | Plain text | Strip tags from API descriptions |
| `$inverted_index` | `{"word": [pos, ...]}` | Reconstructed text | OpenAlex abstract format |
| `$join` | `list` | `str` (comma-separated) | Flatten author lists to string |
| `$first` | `list` | First element or `None` | Keep the first of a list-map |
| `$markitdown` | Local file path string | Markdown text | Convert a PDF, DOCX, PPTX, XLSX, EPub, CSV, image or audio file a record names (markitdown) |

### $html2text

Strips HTML tags, scripts, styles. Preserves paragraph breaks. Uses stdlib `html.parser`.

```yaml
description: "content|$html2text"
# "<p>A <b>bold</b> claim.</p>" → "A bold claim."
```

### $inverted_index

Reconstructs text from OpenAlex inverted index format where words map to position arrays.

Normalize this field, or leave it out, before querying: an object keyed by words often holds two that differ only in case ("The", "the"), and SQL cannot make columns of those, so a table that keeps the raw index cannot be queried (`table_unreadable`).

```yaml
abstract: "abstract_inverted_index|$inverted_index"
# {"hello": [0], "world": [1]} → "hello world"
```

### $join

Joins list elements with `, ` separator.

```yaml
departments: "departments.*.name|$join"
# ["Engineering", "Platform"] → "Engineering, Platform"
```

### $first

Returns first element of a list, or `None` if empty.

```yaml
match: "data.submatches.*.match.text|$first"    # rg --json: the first matched text
# ["TODO", "FIXME"] → "TODO"
```

On a string, `$first` returns its first character: apply it only to a list.

### $markitdown

Converts the local file a field names to markdown via markitdown. A value that is not a path (null, empty, a number) gives an empty string. A file that is missing or unreadable, or that markitdown cannot convert, fails the table with an error naming the file, because an empty column would read as an empty document; a converter whose dependency is missing is a `config` error.

```yaml
full_text: "pdf_path|$markitdown"
```

The path comes from collected data, so it is confined to what the mission declares: the file must be under the mission directory (`.recon/<mission>/`) or under a catalog source with `type: local`. A relative path is resolved against the directory `recon` runs in. A path outside those directories fails the table with a `config` error whose `fix` says to add the directory as a `local` source.

`$markitdown` never fetches. A value that is a URL (`http:`, `https:`, `file:`, `data:`) fails the table the same way. To fetch and convert a remote document, use a `web` collector instead: it converts by content type, through the rate limiter and the capture log.

## Capture collectors: `_capture`

In a `capture` collector, a normalize path may start with `_capture` to read the capture line the record came from, as well as the record itself:

```yaml
normalize:
  title: title                                  # from the body
  tool: _capture.collector                      # e.g. mcp__exa__web_search_exa
  query: _capture.request.tool_input.query      # what the tool was asked
  captured_at: _capture.captured_at
```

`_capture` exists only while normalizing; without a normalize spec, records are written exactly as parsed.

## Response formats

An `api` collector parses its body as `response_format`:

| Format | Parser | Use case |
|--------|--------|----------|
| `json` (default) | JSON | most REST APIs |
| `xml` | xmltodict, with namespace handling | arXiv, PubMed |

`html` and `text` are a plan problem on an `api` collector, since they would give a table with no records; convert a page with a `web` collector instead. A `capture` collector takes all four: `html` and `text` bodies are read line by line, as a `cli` collector reads stdout.

### XML Namespace Handling

XML parsing keeps keys short for the namespaces it knows; any other namespace's elements are keyed `<namespace URI>:<name>`:

```yaml
response_format: xml
# "http://www.w3.org/2005/Atom" → stripped (no prefix)
# "http://arxiv.org/schemas/atom" → "arxiv:" prefix
# "http://a9.com/-/spec/opensearch/1.1/" → "opensearch:" prefix
```

`entry`, `author`, `link` and `category` are always lists, even with one element, so `author.*.name` works for a single author.

Access XML attributes with `@` prefix: `"arxiv:primary_category.@term"`.

## Extract path

The `extract` field navigates to the record array before normalize specs apply:

```yaml
extract: "data"              # S2: response.data[...]
extract: "feed.entry"        # arXiv: response.feed.entry[...]
extract: "results"           # OpenAlex: response.results[...]
extract: "hits.hits"         # Zenodo: response.hits.hits[...]
```

It uses the path syntax above. A single object at the path is one record; in a list, anything that is not an object is dropped.

Without the `extract` a response needs, its records stay inside one record, the whole response. A survey that gets exactly one record holding a list of two or more objects, from a collector with neither `extract:` nor `normalize:`, warns (`extract_missing`) and names the list's path to set as `extract`. A collector with `extract:` is never warned: its one record is a record, and a list inside it (a paper's `author`) is that record's own.
