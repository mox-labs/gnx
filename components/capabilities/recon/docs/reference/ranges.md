# Ranges

A collector entry with `range:` asks its source for **source time**, step by step, run after run: each survey asks for the time since what its table last committed, so a scheduled mission collects every span once and leaves none between. Use it for a source queried by time: metrics, logs, events, audit trails, anything with `from` and `to`, `since` and `until`, `start_time` and `end_time`. Without `range:`, every survey asks the same question again.

recon fills the bounds into the entry's own settings, for any collector type: write `{range.start}` and `{range.end}` where the source takes them (an endpoint's query, an MCP tool's arguments, a command). A stream's windows are its time already, so `range:` is a snapshot entry's.

## Contents

- [An example](#an-example)
- [The three kinds](#the-three-kinds)
- [The keys](#the-keys)
- [The placeholders](#the-placeholders)
- [The checkpoint](#the-checkpoint)
- [Backfills, rebases and restarts](#backfills-rebases-and-restarts)
- [What a run writes](#what-a-run-writes)
- [What the plan says](#what-the-plan-says)
- [Refusals and warnings](#refusals-and-warnings)

## An example

A metrics API's MCP tool, asked for each ten minutes once, every ten minutes:

```yaml
catalog:
  - name: datadog
    type: mcp
    command: npx
    args: ["-y", "@example/datadog-mcp"]
schedule: {every: 600}
collectors:
  - name: cpu
    type: mcp
    source: datadog
    tool: query_metrics
    arguments:
      query: "avg:system.cpu.user{*}"
      from: "{range.start:epoch}"
      to: "{range.end:epoch}"
    extract: series
    range: {step: 600, align: 60}
    retain: {archives: 144}
```

Each tick asks for `[checkpoint, now - settle)`, at most `max_steps` steps of `step` seconds. A tick missed while the host slept is caught up by the next one, one request a step. The same for an `api` endpoint, `params: {since: "{range.start}", until: "{range.end}"}`, or a command, `run: "my-tool --from {range.start} --to {range.end}"`.

## The three kinds

Which kind a range is is which keys it writes:

| Keys | Kind | Each run asks for | Moves the checkpoint |
|---|---|---|---|
| neither `end` nor `last` | continuing | from its checkpoint (or `start`) to the horizon | yes |
| `start` and `end` | fixed | the same, never past `end`; at `end` it is complete | yes |
| `last` | rolling | the latest `last` seconds, `[horizon - last, horizon)`, again each run | no |

The **horizon** is `now - settle`, to the whole second, and down to a multiple of `align` when it is set. Every interval is half-open, `[start, end)`: two steps that meet share no instant.

A continuing range with no `start` begins one step back from the horizon on its first run. A rolling range keeps no checkpoint: use it for a source whose recent past still changes (a dashboard's last hour), with `changes:` to tell what moved.

## The keys

| Key | Default | Meaning |
|---|---|---|
| `start` | none | the first instant to collect, ISO 8601 with its time zone (`2026-10-01T00:00:00Z`); none: one step back from the horizon |
| `end` | none | a fixed range's end, exclusive; with `start` |
| `last` | none | seconds: a rolling range, the latest span read each run |
| `step` | `3600`; a rolling range's whole `last` | seconds one request asks for at most (> 0) |
| `settle` | `60` | seconds a run's end stays behind now, for what its source has not yet settled (>= 0) |
| `max_steps` | `24` | the most steps one run asks for (>= 1): a long backlog drains over several runs, never one unbounded run |
| `align` | none | seconds: the horizon and a first `start` are floored to a multiple of it since 1970, so the steps keep to the source's buckets; `step` must be a multiple of it |

Every key in effect is in the plan's `provenance` with its origin: one left out is `origin: recon`, its default.

## The placeholders

| Placeholder | Is |
|---|---|
| `{range.start}` | the step's first instant |
| `{range.end}` | the step's end, exclusive |
| `{range.last}` | the step's last instant, the end less one unit of its format: for an API whose end bound is inclusive |

Each takes a format after a colon: `iso` (the default, `2026-10-24T10:00:00Z`, always UTC), `epoch` (whole seconds since 1970), `epoch_ms` (milliseconds) or `date` (`2026-10-24`; `{range.last:date}` is the day before the end). A value that is one placeholder in a number's format, `from: "{range.start:epoch}"`, becomes a number, so a JSON argument or body keeps the type its schema wants; inside other text it is text. Write `{{range.start}}` for the literal text. The bounds are recon's own, digits and `-:TZ`: nothing a source returns reaches a placeholder.

## The checkpoint

A ranged table's **checkpoint** is the end of the last range it committed. A range is committed with its table's data, in one commit: the table sealed `ok`, whole (not `truncated`), in an archive whose survey ended and recorded its meta. So:

- **A failed run commits nothing**, and the next run asks for the same interval again: a refetch of what was never committed, never a gap.
- **Nothing committed is asked for twice**, and the committed ranges, in order, each begin where the last ended.
- **A survey killed** before its meta is recorded commits nothing; one killed after has committed.
- **A crawl cut short** (`truncated`, past `paginate.pages`) commits nothing: part of its time is missing. Narrow `step` or raise `pages`; it is never skipped.
- **Retention never loses it**: the tombstone of each archive retired keeps the ranges it committed, and the checkpoint is read from the archives kept and the tombstones alike.
- **Steps are all or nothing per run**: the run's steps land in one table, which commits whole.

The checkpoint is read under the mission's hold, so no other survey's commit comes between. Status shows each table's `range`; to read what a span returned, query the archive that committed it (`recon status <name> --json` lists them).

A checkpoint belongs to the question it was asked with: the digest of the entry's type, its own settings as written (placeholders unfilled), `extract:` and its source. `normalize:`, `range:`'s own keys and every other key recon reads are no part of it, so reshaping records, or a new `step`, keeps the checkpoint. A changed question is refused (`checkpoint_request_changed`) until it is rebased or restarted.

## Backfills, rebases and restarts

| Flag | Does |
|---|---|
| `recon survey <name> --range START..END` | asks every ranged entry for `[START, END)` instead, ISO 8601 each (UTC unless it says): a backfill, which moves no checkpoint; overlap with committed time is warned, and it stays in that archive alone. Refused past `max_steps` steps |
| `--rebase OUTPUT` | goes on from OUTPUT's checkpoint under its changed question, once; repeat for more outputs |
| `--restart OUTPUT` | begins OUTPUT's range again from its `start` (or one step back), once; what it committed before stays in its archives |

Each works with `--dry-run` too. The library: `recon.survey(name, interval=(start, end), rebase=[...], restart=[...])`, and the same on `recon.plan`. An output no ranged entry makes, an interval with no time zone or no length, and `--range` on a mission with no ranged entry are `usage` errors.

## What a run writes

Every record of a ranged table carries `_range`, `{start, end}`: the step it came from, in ISO form. The table's outcome in the survey document, in the archive's meta and in `recon status` has `range`:

```json
{"start": "2026-10-24T10:00:00Z", "end": "2026-10-24T11:00:00Z", "kind": "continuing",
 "from": "checkpoint", "request": "3f2a9c1d0b7e4a65", "steps": 6}
```

`from` is where the run began: `checkpoint`, `start`, `default` (one step back), `override` (`--range`), `rebase` or `restart`.

## What the plan says

`recon survey <name> --dry-run --json` gives each ranged run a `range` (as above, without `request`), read from what is committed now, and describes its first step's request with the bounds filled. Its `provenance` has `collectors.<i>.range.kind` and each key in effect (`.start`, `.end` or `.last` as the kind has them, `.step`, `.settle`, `.max_steps`, `.align`), each with its `origin` (`mission` or `recon`) and, for a default, a `note` saying what it does.

## Refusals and warnings

A problem refuses the plan; a warning lets the run go on (`recon --skill -r errors`):

| Kind | When | Fix |
|---|---|---|
| `unresolved` (problem) | a `{range.*}` in an entry with no `range:` | give it `range:`, or remove the placeholder |
| `range_unused` (problem) | `range:`, and no setting names a placeholder: each step would ask the same | name the bounds where the source takes them |
| `range_ambiguous` (problem) | `end` beside `last` | keep one kind |
| `missing_key` (problem) | `end` without `start` | give `start` |
| `invalid_value` (problem) | `end` not after `start`; `step` no multiple of `align`; a placeholder nothing fills (`{range.begin}`, `{range.start:unix}`); `range:` on a stream | as the problem says |
| `range_deletes` (problem) | `changes: {deletes: true}` on a continuing or fixed range: each table holds new time alone, so a missing key did not go | remove `deletes`, or use `last` |
| `range_steps` (problem) | a `--range`, or a rolling `last`, of more than `max_steps` steps | narrow it, or raise `step` or `max_steps` |
| `checkpoint_request_changed` (problem) | the entry asks another question than its checkpoint was committed under | `--rebase OUTPUT` or `--restart OUTPUT` once |
| `range_behind` (warning) | the run stopped at `max_steps` with time left: the backlog, in seconds | the next runs drain it; or raise `max_steps` or `step` |
| `range_current` (warning) | nothing past `settle` is new since the checkpoint: the table is empty | nothing to do |
| `range_complete` (warning) | a fixed range is collected to its `end` | move `end`, or remove it to go on |
| `range_overlaps` (warning) | a `--range` overlaps committed time | narrow it, or keep the re-read |
