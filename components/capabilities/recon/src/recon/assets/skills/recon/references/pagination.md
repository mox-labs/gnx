# Pagination

An `api` collector with `paginate:` reads its source page by page into one table. Without it, a collector makes one request per source. Use it when an API returns its results a page at a time and the table should hold all of them.

```yaml
catalog:
  - name: github
    url: https://api.github.com
    rate_limit: {rps: 1, burst: 3}
collectors:
  - name: issues
    type: api
    source: github
    endpoint: /repos/mox-labs/gnx/issues
    params: {per_page: "100", state: "all"}
    paginate: {type: header, pages: 20}
```

## The keys

| Key | Default | Meaning |
|---|---|---|
| `type` | required | how the next page is asked for: `link`, `header`, `cursor` or `offset` |
| `pages` | `100` | the most pages read (1 or more); a crawl that reaches it with more to read is truncated |
| `next` | required by `link` and `cursor` | the path, in `extract:`'s syntax, of the next page's URL (`link`) or its cursor (`cursor`) in a page's body |
| `param` | required by `cursor` and `offset` | the query parameter the cursor (`cursor`) or the offset (`offset`) is sent as |

Each type takes the keys it reads and needs each of them, and no other: where an API puts its next page is the API's, so recon guesses no default.

| `type` | The next page is | Sent as |
|---|---|---|
| `link` | the URL at `next` in the body (`links.next`), resolved against the page's own URL | that URL, with the source's credential parameter and no other query: the link carries the rest |
| `header` | the `Link` header's `rel="next"` URL (GitHub, RFC 8288) | as `link` |
| `cursor` | the value at `next` in the body (`meta.next_cursor`), a string or a number | `params` and `<param>=<cursor>` |
| `offset` | the records read so far | `params` and `<param>=<offset>`; the first page is sent with `params` alone |

Every page is a request through recon's requester, with the collector's `method` and `body`: each draws a token from the source's `rate_limit`, is retried as any request is, and with `preserve_raw` is captured. A `link` or `header` crawl follows a URL only at the source's origin (its scheme, host and port): a next page anywhere else fails the table (`run_failed`), since the source's credential would go with it. A page past the first whose body has nothing at `extract:` fails the table too, rather than reading as the end.

## How a crawl stops

Every stop is recorded. The first that holds ends the crawl:

| Stop | Table | Warning |
|---|---|---|
| a page names no next one (`next` absent, null or `""`; no `Link: rel="next"`) | the whole table | none |
| an `offset` page with no records | the whole table | none |
| `pages` pages read, and the last names a next one | `truncated`, `truncated_by: pages` | `page_cap`, at `collectors.<i>.paginate.pages` |
| a page names a next one the crawl already used (any earlier one, the first page's URL included) | `truncated`, `truncated_by: loop` | `paginate_loop`, at `collectors.<i>.paginate` |
| three empty pages in a row (`link`, `header`, `cursor`) that each still name a next one | `truncated`, `truncated_by: empty` | `paginate_empty`, at `collectors.<i>.paginate` |

An empty page that names a next one is read past, until the third in a row. A page a loop names is not asked for again, so no page is fetched twice. A truncated table is sealed `ok`, its outcome (`meta.yaml`'s `tables[]`, the survey's `tables`) says `truncated: true` and `truncated_by`, and the warning's fix says what to change. A truncated table is not a whole snapshot of its source.

## What a crawl writes

Each record carries `_position`, `{"page": <the page's ordinal, from 0>, "i": <its index on the page>}`, beside the columns `normalize:` makes:

```
{"title": "t0", "_position": {"page": 0, "i": 0}}
```

Beside the table, `<output>.pages.jsonl` holds one line a page, `{"page", "token"}`: the token that asked for it (null for the first), a URL as recon reports one (the source's `auth.param` redacted). It is written once the table is sealed, is no table (`recon query` and `recon watch` never list it), and a table that fails keeps none. A collector may not be named so that its table would be named as a page log (`<name>.pages`); a `normalize:` column may not be named `_position`, and `params:` may not hold the parameter `paginate.param` sends.

With `preserve_raw`, each page is one capture whose `request` names its `page`, so a `capture` collector reading the crawl's captures gives each record the same `_position`, and the table replays byte for byte.

## What the plan says

`recon survey <name> --dry-run --json`:

- the run's `paginate`, as it will crawl;
- in `provenance`, `collectors.<i>.paginate.pages` (`origin`: `mission`, or `recon` for the default) and, for each run, `collectors.<i>.paginate.floor_s`: the least seconds the crawl takes at its source's rate limit, `(pages - burst) / rps`, with a note comparing it to the deadline that bounds the table;
- a `deadline_short` warning, at the `deadline:` that is shorter than that floor, with the values to raise or lower.

It refuses, each a problem with a fix: `paginate:` on a type that does not read pages (only `api` does, of recon's own; `unknown_key`), on a stream (a stream does not read its source page by page yet), a type's missing or extra key, a `next` path with two `*`, a `normalize:` column named `_position`, a `params:` key that `paginate.param` sends, an output named as a page log.

## A survey stopped mid-crawl

A paginated table is all or nothing, as any snapshot table is. A survey killed during a crawl leaves its archive `incomplete`, `ended: crashed`, its table unsealed and with no page log; the next survey starts the crawl again from its first page.
