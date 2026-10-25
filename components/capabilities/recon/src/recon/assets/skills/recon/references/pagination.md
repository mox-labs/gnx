# Pagination

An `api` collector with `paginate:` reads its source page by page into one table. Without it, a collector makes one request per source. Use it when an API returns its results a page at a time and the table should hold all of them. A crawl too long for one survey to finish (or to lose to a crash) is a stream of windows bounded by pages, which goes on where it left off: [A crawl that resumes](#a-crawl-that-resumes).

## Contents

- [The keys](#the-keys)
- [How a crawl stops](#how-a-crawl-stops)
- [What a crawl writes](#what-a-crawl-writes)
- [What the plan says](#what-the-plan-says)
- [A survey stopped mid-crawl](#a-survey-stopped-mid-crawl)
- [A crawl that resumes](#a-crawl-that-resumes)

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
| `type` | required | how the next page is asked for: `link`, `header`, `cursor`, `offset` or `page` |
| `pages` | `100` | the most pages read (1 or more); a crawl that reaches it with more to read is truncated |
| `next` | required by `link` and `cursor` | the path, in `extract:`'s syntax, of the next page's URL (`link`) or its cursor (`cursor`) in a page's body |
| `param` | required by `cursor`, `offset` and `page` | the query parameter the cursor (`cursor`), the offset (`offset`) or the page's number (`page`) is sent as |

Each type takes the keys it reads and needs each of them, and no other: where an API puts its next page is the API's, so recon guesses no default.

| `type` | The next page is | Sent as |
|---|---|---|
| `link` | the URL at `next` in the body (`links.next`), resolved against the page's own URL | that URL, with the source's credential parameter and no other query: the link carries the rest |
| `header` | the `Link` header's `rel="next"` URL (GitHub, RFC 8288) | as `link` |
| `cursor` | the value at `next` in the body (`meta.next_cursor`), a string or a number | `params` and `<param>=<cursor>` |
| `offset` | the records read so far | `params` and `<param>=<offset>`; the first page is sent with `params` alone |
| `page` | the next page's number, from 1 (`page=1,2,3...`) | `params` and `<param>=<number>`, the first page's (1) too |

Every page is a request through recon's requester, with the collector's `method` and `body`: each draws a token from the source's `rate_limit`, is retried as any request is, and with `preserve_raw` is captured. A `link` or `header` crawl follows a URL only at the source's origin (its scheme, host and port): a next page anywhere else fails the table (`run_failed`), since the source's credential would go with it. A page past the first whose body has nothing at `extract:` fails the table too, rather than reading as the end.

## How a crawl stops

Every stop is recorded. The first that holds ends the crawl:

| Stop | Table | Warning |
|---|---|---|
| a page names no next one (`next` absent, null or `""`; no `Link: rel="next"`) | the whole table | none |
| an `offset` or `page` page with no records | the whole table | none |
| `pages` pages read, and the last names a next one | `truncated`, `truncated_by: pages` | `paginate_pages`, at `collectors.<i>.paginate.pages` |
| a page names a next one the crawl already used (any earlier one, the first page's URL included) | `truncated`, `truncated_by: loop` | `paginate_loop`, at `collectors.<i>.paginate` |
| three empty pages in a row (`link`, `header`, `cursor`) that each still name a next one | `truncated`, `truncated_by: empty` | `paginate_empty`, at `collectors.<i>.paginate` |

An empty page that names a next one is read past, until the third in a row. A page a loop names is not asked for again, so no page is fetched twice. A truncated table is sealed `ok`, its outcome (`meta.yaml`'s `tables[]`, the survey's `tables`) says `truncated: true` and `truncated_by`, and the warning's fix says what to change. A truncated table is not a whole snapshot of its source.

## What a crawl writes

Each record carries `_position`, `{"page": <the page's ordinal, from 0>, "i": <its index on the page>}`, beside the columns `normalize:` makes:

```
{"title": "t0", "_position": {"page": 0, "i": 0}}
```

Beside the table, `<output>.pages.jsonl` holds one line a page, `{"page", "token"}`: the token that asked for it (null for the first), a URL as recon reports one (the source's `auth.param` redacted). A survey's is written once the table is sealed (a stream's window adds each page as it is read); it is no table (`recon query` and `recon watch` never list it), and a table that fails keeps none. A collector may not be named so that its table would be named as a page log (`<name>.pages`); a `normalize:` column may not be named `_position`, and `params:` may not hold the parameter `paginate.param` sends.

With `preserve_raw`, each page is one capture whose `request` names its `page`, so a `capture` collector reading the crawl's captures gives each record the same `_position`, and the table replays byte for byte.

## What the plan says

`recon survey <name> --dry-run --json`:

- the run's `paginate`, as it will crawl;
- in `provenance`, `collectors.<i>.paginate.pages` (`origin`: `mission`, or `recon` for the default), its `note` saying, for each run, the least seconds the crawl takes at its source's rate limit, `(pages - burst) / rps`, against the deadline that bounds the table;
- a `deadline_short` warning, at the `deadline:` that is shorter than that floor, with the values to raise or lower.

For a stream that crawls, `collectors.<i>.paginate.pages` is the cap of one crawl across its windows, and `collectors.<i>.window.pages` says, in its `note`, the least seconds a window of that many pages takes at the source's rate limit.

It refuses, each a problem with a fix: `changes: {deletes: true}` on an `offset` or `page` crawl (a source that loses a record while it is read shifts the next onto a page already read, so it would be told deleted: page by `link`, `cursor` or `header`, or keep `deletes: false`), and on a stream at all; `paginate:` on a type that does not read pages (only `api` does, of recon's own; `unknown_key`); `window: {pages}` on an entry without `paginate:` (its windows would never end by pages); a type's missing or extra key, a `next` path with two `*`, a `normalize:` column named `_position`, a `params:` key that `paginate.param` sends, an output named as a page log.

## A survey stopped mid-crawl

A paginated table is all or nothing, as any snapshot table is. A survey killed during a crawl leaves its archive `incomplete`, `ended: crashed`, its table unsealed and with no page log; the next survey starts the crawl again from its first page.

## A crawl that resumes

Give a paginated entry `window:` and it is a stream (`recon --skill -r streaming`): the crawl goes on across its windows, each window an archive of its own, and a run goes on where the last left off, after a stop, a crash or the end of `--windows`. `window: {pages: N}` seals each window once it holds N pages; `seconds`, `records` and `bytes` bound it as for any stream, the first reached ending it.

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
    paginate: {type: header, pages: 1000}
    window: {pages: 20}
    retain: all
```

```
recon stream m --stream issues --dry-run --json   # how many pages a window, and how long one takes
recon stream m --stream issues --windows 5         # five windows of 20 pages, or the crawl's end
recon stream m --stream issues --forever           # until the crawl's end
```

| | What happens |
|---|---|
| a window's end | `window: {pages: N}`: the window holds N pages and is sealed `closed_by: pages`; the next page is asked for only when a window will keep it, so a run that ends there asks for no page it does not keep |
| the crawl's end | a page that names no next one, or a crawl cut short by a stop of [How a crawl stops](#how-a-crawl-stops) (its table `truncated`, its warning given): the window is sealed `closed_by: eof` and the stream ends, whatever `--windows` or `--forever` said. The crawl does not poll its source again: a run after it crawls again from the first page, and a schedule (`recon --skill -r schedules`) is how a crawl is made again and again |
| `paginate.pages` | the most pages one crawl reads, counted across its windows and runs (100 by default) |
| `--windows N` | at most N windows, fewer when the crawl ends first; the next run goes on from there |
| `--until TIME` | the window it cuts is sealed `until`; the next run goes on from there |
| `--forever` | until the crawl's end, or a stop |

**Where a run goes on.** Each window's page log (`<output>.pages.jsonl` in the window) names each page as the crawl is about to ask for it, after the records before it are written, so a window whose writer died names no page whose records come before what it holds. Each record's `_position` is `{page, i}`: the page's ordinal in its crawl and the record's index there. A run goes on from the last page the page logs name:

- none of that page's records was kept (the run was killed asking for it): it is asked for first, by its token, and read whole;
- some were (the window before ended within the page, or at its end): it is asked for again by its token, and read past the last record kept, which the crawl finds by its content: a page asked for again is the earlier window's, not counted in the new one's `pages`.

So every record is kept once. The restart is a gap in the run's first window, `resumed: true`.

**A page that moved under the crawl.** A page number or an offset (and a link that carries one) moves when records are added or removed ahead of it between two runs. The last record kept is looked for by its content at its index, then elsewhere on its page, then on the next page:

| Found | The crawl | Recorded |
|---|---|---|
| at its index | goes on after it | nothing |
| elsewhere (records were inserted ahead of it, or removed) | goes on after it, so nothing is kept twice and nothing after it is lost; records inserted ahead of it are not in this crawl, and the next crawl reads them | `paginate_shifted`, `got` how far it moved (negative: earlier) |
| nowhere (it changed at the source, or moved past the next page), or off its index on a page that holds its content more than once, so which is the boundary cannot be told | goes on after its index: records about it may be missing or kept twice | `paginate_shifted`, `got` null, and a gap, `resumed: false` |

The warning is at `collectors.<i>.paginate`, its fix to page by a cursor or a link where the API has one. A record's `_position` says where it was when it was read, so after a shift two records may give the same one.

**A token the source no longer takes.** A cursor that expired, or a page that is gone, is refused with a status that is neither a retry's (429, 5xx) nor a credential's (401, 403). The crawl then begins again from its first page, in the same window: what it reads again is beside what earlier windows hold, the window records a gap `resumed: false` (and `stream.sources[0].restarted`, the page it was begun again at, in its `meta.yaml`), and the run warns `paginate_restarted` at `collectors.<i>.paginate` with a fix. The pages it reads again, up to the one it had reached, are none of the window's `pages`: a window of N pages reads N pages past where the crawl was, so a schedule whose cursors expire between ticks still gets further each tick and ends. A window that ends before then (cut by its `records`, `bytes` or `seconds`) leaves the next run to begin again at the same page: two windows in a row begun again at one page warn `paginate_starved`, `got` the page, with the fix `window.pages` at `paginate.pages` or more (one window crawls the whole list) or a schedule's `every` below the source's token lifetime; a tick's line of `schedule.log` names it in `warning_kinds`. Nothing kept is lost and the stream goes on, so a schedule that meets an expired cursor is not stopped by it; with `changes:` (`recon --skill -r changes`), what the crawl reads again tells nothing, only what changed. A link the page log keeps is redacted (the source's `auth.param` and any signature parameter): the credential is sent again as the source's `auth` says, but a link signed for one use cannot be asked for again from the page log.
