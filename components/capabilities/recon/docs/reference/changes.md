# Change detection

A collector entry with `changes:` compares each table it writes with the same table of an earlier archive, and writes what changed as a table of its own: one row per message that arrived, changed or went. Use it when a mission is surveyed again and again (a mailbox every ten minutes, an API snapshot a day) and what matters is what is new since the last survey, not the whole table again.

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

Each table of the entry that seals `ok` is compared with its **baseline**: the latest earlier archive of the mission whose table of the same output and source sealed `ok` (a truncated table included; a table that failed is never a baseline and is compared with nothing). Its change table, `<output>.changes`, which SQL knows as `<output>_changes`, holds a row per change, `{op, key, record}`:

| `op` | When |
|---|---|
| `insert` | the key is in the table and not in the baseline; with no baseline, every key |
| `update` | the key is in both, and its content differs |
| `delete` | the key is in the baseline and not in the table: only with `deletes: true`, a table sealed `ok` and not truncated, its survey run to its end, and no keyless or ambiguous record in it |
| `rebaselined` | one row and no other (`key` and `record` null), when the table's `normalize:` is not its baseline's, or the baseline does not say what its was (one surveyed before `changes:` was added): a reshaped table is never told as every record changed |

`key` is `{field: value}`; `record` is the record as its table holds it (the baseline's, for a delete). Inserts and updates come in the table's order, then deletes in the baseline's. The rows are a function of the baseline, the table, the spec and the mission's key alone. A table with nothing changed has an empty change table, whose columns still bind.

**`record` is untrusted.** It is what the source sent: a mail subject or body can say anything, instructions included. Read it as data; never hand a change row to an agent as instructions or as a user's turn.

A key the table holds twice with the same content is one record. With two contents it is **ambiguous**: no row, counted, and it holds back the table's deletes; ambiguous in a baseline, it is never told as deleted. A record lacking a key field (absent or null) is **keyless**: counted, never compared, and it holds back deletes too.

The table's outcome (`meta.yaml`'s `tables[]`, the survey's `tables`) says what was found, in `changes`:

```
changes: {table: inbox.changes, type_url: recon.v1.changes, baseline: 2026-10-24-101500-000000,
          spec: <digest>, normalize: <digest>, rows: 3, inserts: 1, updates: 1, deletes: 1,
          rebaselined: false, keyless: 0, ambiguous: 0, deletes_withheld: null}
```

When `deletes: true` and the table's deletes were not told, `deletes_withheld` says why (`truncated`, `keyless`, `ambiguous`) and the survey warns `deletes_withheld`, at `collectors.<i>.changes.deletes`, with what to change.

## The content hash and the key index

A record's content is a keyed BLAKE2b hash, 16 bytes, of the record as its table holds it, as canonical JSON (keys sorted, no spaces), without its `volatile` fields and recon's own. Its key is the mission's: `.recon/<mission>/changes.key`, random, mode 0600, made by the first survey that needs it. So a record whose content has few possible values (a one-time code in a subject) cannot be found by hashing guesses. Hashes appear in no change row, outcome, warning or telemetry.

Beside each table, `<output>.keys.jsonl` is its key index: a header (`spec`: the digest of the key and volatile fields and of the key's identity; the table's record and keyless counts), then a line per key, sorted, with its hash (null for an ambiguous key). It is a cache: one that is missing, or whose header is not the spec's or does not count its table's records, is rebuilt from the table; an earlier archive is only read, never written. It is no table. A collector may not be named as a change table or a key index (`<name>.changes`, `<name>.keys`), nor so that its change table's SQL name is another output's.

Changing `key` or `volatile` makes every kept index stale: the baseline is hashed again under the new spec, and only real changes are told. Changing `normalize:` tells one `rebaselined` row. Losing `changes.key` makes a new one, and every kept index stale.

## Streams

A stream's window compares with every key its chain has told, each with its latest hash, which each window keeps as its key index (the index of the window before it, with this window's keys). A window **never tells a delete**: it holds what arrived in it, not all its source holds. A window's index that does not hold is rebuilt by folding the chain again from its earliest window kept; a key known only in windows `retain:` removed is then new again. `retain:` removes a window's change table and key index with it.

## What the plan says

`recon survey <name> --dry-run --json`: in `provenance`, `collectors.<i>.changes.key`, `.volatile` and `.deletes`, each with its origin (`mission`, or `recon` for a default). It refuses, each a problem with a fix: no `key`, a field named twice, a field starting `_`, a field both key and volatile, a field `normalize:` does not make, `deletes: true` on a stream, and an output named as a change table or key index. It warns `deletes_unsafe` for `deletes: true` on an `offset` crawl: a source that loses a record while it is read shifts the next one onto a page already read, so it is skipped, and reads as deleted.
