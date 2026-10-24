# Watch a survey while it runs

A survey of many sources can take minutes. You do not have to wait for it to end to read what it has collected: `recon watch` prints each record as the survey writes it, and returns when the survey ends.

## 1. Start the survey

```bash
recon survey my-scan
```

In a second terminal, or from an agent that started the survey in the background:

## 2. Watch it

```bash
recon watch my-scan
```

Each record is a line on stdout, `{"table": "...", "record": {...}}`, as soon as it is written (at most a quarter of a second after the collector gives it). Stderr says when each table is sealed, and the archive's state when the survey ends. `recon watch` then returns.

For an agent, `--json` prints each read as one `recon.v1.records-since` document a line, with its `state`, its `table_records`, the tables `exhausted` (read to their end) and `retracted` since, and a `cursor`:

```bash
recon watch my-scan --json
```

To look once and come back later, read once and keep the cursor:

```bash
recon watch my-scan --once --json          # note "archive" and "cursor"
recon watch my-scan --archive <archive> --cursor <cursor> --json
```

The cursor is recon's: pass it on unchanged. One recon did not give for that archive exits 2, and its fix says to read without it.

## 3. Read a failed table right

A table that fails is retracted: the records printed from it are void, and stderr (or `retracted`, under `--json`) names it. Its error is in the archive's `meta.yaml`, and `recon status my-scan` shows it once the survey ends.

## 4. Query what is sealed

`recon query` on an archive being written reads the tables already sealed, and says `state: writing`:

```bash
recon query my-scan "SELECT count(*) FROM first_table" --json
```

The table being written is not a table yet: query it once `recon watch` says every record of it is read (it is `exhausted`: sealed, and read to its end).

## 5. Follow a stream

A stream (a collector with `window:`, or a `tail` or `sse` one) writes window after window. `--stream` follows it from its latest window into each one after, and returns once nothing extends the stream:

```bash
recon stream my-feed --stream events --forever &   # or from cron: recon survey my-feed
recon watch my-feed --stream events --json
recon query my-feed "SELECT count(*) FROM events" --stream events --json
```

A stream run `--forever` is never done, so that watch never returns. An agent, whose tool call must return, polls instead: one read a call, each from the cursor the last printed.

```bash
recon watch my-feed --stream events --once --json                    # note "cursor"
recon watch my-feed --stream events --once --cursor <cursor> --json  # and again
```

A window whose source failed keeps the records it got: nothing printed from a stream is ever void.

The flags, the defaults and the library calls are in [docs/reference/live-reading.md](../reference/live-reading.md), and a stream's in [docs/reference/streaming.md](../reference/streaming.md).
