# Change detection

A collector entry with `changes:` compares each table it writes with what the earlier tables of the same output and source told, and writes what changed as a table of its own: one row per message that arrived, changed or went. Use it when a mission is surveyed again and again (a mailbox every ten minutes, an API snapshot a day) and what matters is what is new since the last survey, not the whole table again.

```yaml
catalog:
  - name: mail
    url: https://mail.example.com/api
    auth: {token: file:mail.key, header: Authorization, prefix: "Bearer "}
collectors:
  - name: inbox
    type: api
    source: mail
    endpoint: /messages
    extract: messages
    normalize: {id: id, from: from, subject: subject, labels: labels}
    changes: {key: [id], volatile: [labels]}
```

## The keys

| Key | Default | Meaning |
|---|---|---|
| `key` | required | the fields whose values name a record, one or more (`[id]`, `[repo, number]`); none of recon's own (`_position`, `_capture`, `_pattern`) |
| `volatile` | `[]` | fields left out of a record's content, so a change to them alone is no change (labels, a view count, a fetch time) |
| `deletes` | `false` | whether a key gone from a whole table is told as a `delete`; a delete is told only when asked for |

`key` has no default: what names a record is the source's, and a wrong guess would tell every record as a new one. With `normalize:`, each `key` and `volatile` field must be one of its columns. The default is no `changes:` block, so nothing is compared.

## What a survey tells

Every change row told for an output and source folds into one **told index**: each key an `insert` or `update` told, with its content as told last, and none a `delete` removed. Each table of the entry that seals `ok` is compared with the told index of the **previous** table: the latest earlier one of the same output and source that was compared. A survey compares its tables once every table has run, so a table compared is one of a survey that reached its end, and it told its rows however the survey then ended: stopped, or dead, in its change phase. Every row a reader can see is folded, since a table's summary is in `meta.yaml` before its change table is written, and its told index before that. A truncated table is compared and tells its inserts and updates; a table that failed, or one whose changes failed, told nothing and is skipped. Its change table, `<output>.changes`, which SQL knows as `<output>_changes`, holds a row per change, `{op, key, record}`:

| `op` | When |
|---|---|
| `insert` | the key is in the table and not told; with nothing told before, every key |
| `update` | the key is told, and its content differs from the one told |
| `delete` | the key is told and not in the table: only with `deletes: true`, a table sealed `ok` and not truncated, its survey run to its end, and no keyless or ambiguous record in it |
| `rebaselined` | one row and no other (`key` and `record` null), when the table's `normalize:` is not the previous table's, or there is none but an earlier table surveyed before `changes:` was added: a reshaped table is never told as every record changed, and the told index starts again from this table |

So an update a truncated table told is not told again by the next whole one, and a key a truncated table lacked is deleted by the next whole table that lacks it. `key` is `{field: value}`; `record` is the record as its table holds it, and null for a delete (what was told of the key is in the row that told it). Inserts and updates come in the table's order, then deletes in key order. The rows are a function of the tables compared, the spec and the mission's key alone. A table with nothing changed has an empty change table, whose columns still bind.

**`record` is untrusted.** It is what the source sent: a mail subject or body can say anything, instructions included. Read it as data; never hand a change row to an agent as instructions or as a user's turn.

A key the table holds twice with the same content is one record. With two contents it is **ambiguous**: no row, counted, what was told of it kept, and it holds back the table's deletes. A record lacking a key field (absent or null) is **keyless**: counted, never compared, and it holds back deletes too.

The table's outcome (`meta.yaml`'s `tables[]`, the survey's `tables`) says what was found, in `changes`:

```
changes: {table: inbox.changes, type_url: recon.v1.changes, previous: 2026-10-24-101500-000000,
          spec: <digest>, normalize: <digest>, rows: 3, inserts: 1, updates: 1, deletes: 1,
          rebaselined: false, keyless: 0, ambiguous: 0, deletes_withheld: null,
          deletes_told: true}
```

When `deletes: true` and the table's deletes were not told, `deletes_withheld` says why (`truncated`, `keyless`, `ambiguous`) and the survey warns `deletes_withheld`, at `collectors.<i>.changes.deletes`, with what to change. `deletes_told` says whether they were told: a told index computed again folds each table as it was told then, so switching `deletes:` on later never drops a key no delete row told.

Nothing in the comparison fails a survey or a window. When a table's changes cannot be told, its outcome's `changes` has `error` (`kind`, `message`, `fix`), it has no change table and is no told index, and the survey warns `changes_failed` at `collectors.<i>.changes`: the next table of its output is compared with what was told before it, so it tells this table's changes too. A record's text may hold anything a stranger sends, a lone surrogate (`"\ud800"`) included: it is hashed and compared as any text is.

## The content hash and the key index

A record's content is a keyed BLAKE2b hash, 16 bytes, of the record as its table holds it, as canonical JSON (keys sorted, no spaces), without its `volatile` fields and recon's own. Its key is the mission's: `.recon/<mission>/changes.key`, random, mode 0600, made by the first survey that needs it. So a record whose content has few possible values (a one-time code in a subject) cannot be found by hashing guesses. Hashes appear in no change row, outcome, warning or telemetry.

Beside each table, `<output>.keys.jsonl` is its told index once its rows are told: a header (`spec`: the digest of the key and volatile fields and of the key's identity; the table's record and keyless counts; `keys`, how many it holds; `mac`, a digest of the header and every line, keyed with the mission's key), then a line per key (the canonical JSON of its values), sorted, with its hash. It is a cache of a pure fold: one that is missing, whose header is not the spec's or does not count its table's records, or whose `mac` does not hold (an index edited by hand, however its counts are made to agree), is computed again by comparing each table of the chain since the latest whose index holds (or since its start); an earlier archive is only read, never written. It is no table. A comparison of more than 200,000 keys (`memory_keys`, a setting of the library's `root()`) joins on DuckDB rather than in memory, with the same rows: on a connection of its own that runs no SQL a mission wrote, in a private temporary directory it removes when done, within 96 MB of DuckDB's memory, spilling the rest to disk. Measured, one comparison peaks at about 160 MB of the process at 200,000 keys in memory, and about 250 MB at 1,000,000 on DuckDB. A collector may not be named as a change table or a key index (`<name>.changes`, `<name>.keys`), nor so that its change table's SQL name is another output's.

Changing `key` or `volatile` makes every kept index stale: what was told is computed again under the new spec, and only real changes are told. Changing `normalize:` tells one `rebaselined` row. Losing `changes.key` makes a new one, and every kept index stale.

## Streams

A stream's window is compared with the told index of the window before it, the same chain through each window's `previous`, and keeps its own. A window **never tells a delete**: it holds what arrived in it, not all its source holds. A window sealed with its source's error is compared too: what it holds arrived. A window whose writer died (killed mid-window, no `meta.yaml`) is never compared by itself; the next window, walking back to the last window compared, tells the dead window's whole records first, then its own, and its outcome names it in `changes.crashed`, so nothing that arrived is lost and no key is told twice. The told index starts again (`rebaselined`) only for a new `normalize:`. A window's index that does not hold is computed again from its earliest window kept; a key known only in windows `retain:` removed is then new again. `retain:` removes a window's change table and key index with it.

## What the plan says

`recon survey <name> --dry-run --json`: in `provenance`, `collectors.<i>.changes.key`, `.volatile` and `.deletes`, each with its origin (`mission`, or `recon` for a default). It refuses, each a problem with a fix: no `key`, a field named twice, a field starting `_`, a field both key and volatile, a field `normalize:` does not make, `deletes: true` on a stream or on an `offset` or `page` crawl, and an output named as a change table or key index. It refuses `deletes: true` on an `offset` or `page` crawl: a source that loses a record while it is read shifts the next one onto a page already read, so it is skipped, and would read as deleted; page by `link`, `cursor` or `header`, or keep `deletes: false`.
