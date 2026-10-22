# Adding a collector type or a transform

Collector types and normalize transforms are extensions, registered through Python entry points. recon's own built-ins register the same way (see `[project.entry-points]` in `pyproject.toml`), so a package you install alongside recon is wired exactly as `api` or `$html2text` are, without editing recon.

| Entry-point group | Name is | Object is |
|---|---|---|
| `recon.collectors` | the config's `type:` | a `recon.domain.collector.CollectorType` |
| `recon.transforms` | the `$name` in `path\|$name` (registered without the `$`) | a callable: value in, value out |

`recon status --json` lists what is installed under `plugins`, and lists separately anything that failed to load.

## A collector type

```python
# my_package/recon_sqlite.py
import sqlite3
from recon.domain.collector import CollectorType
from recon.domain.errors import CollectionError, Problem


class SqliteCollector:
    def __init__(self, ctx):
        self._mission_dir = ctx.mission_dir

    def collect(self, entry, source, *, captures=None):
        if source is None:
            raise CollectionError("sqlite needs a source", kind="config")
        with sqlite3.connect(source.url) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute(entry.run):
                yield dict(row)


def _check(entry):
    if entry.run:
        return []
    return [
        Problem("run", "a sqlite collector needs run:", kind="missing_key", fix="run: SELECT ...")
    ]


collector_type = CollectorType(
    create=SqliteCollector,
    effects=frozenset({"filesystem"}),
    summary="SQL against a local SQLite file → records",
    requires_source=True,
    check=_check,
    describe=lambda entry, source: {"database": source.url if source else None, "sql": entry.run},
)
```

```toml
# my_package's pyproject.toml
[project.entry-points."recon.collectors"]
sqlite = "my_package.recon_sqlite:collector_type"
```

The contract:

- **`create(ctx)`** builds the collector once per survey whose mission uses the type. `ctx` (`CollectorContext`) carries the shared `requester` (rate-limited HTTP, one bucket per source), `converter`, `shell` (runs a command, as the `cli` type does), `transforms`, `mission_dir`, `env`, and `user_agent` (what to send when a source sets none). Take what you need from it; never read `os.environ` directly, so tests can inject. Every request through `requester` is an HTTP client span in the survey's telemetry, with its `url.full` redacted, so a type that fetches through it is traced with nothing more to do.
- **`ctx.env` is not the process environment.** It holds only the variables the mission declares for your type: the `auth.env` names of the catalog sources your collectors run against. Reading any other name raises a config error whose `fix` says to name the variable as `auth.env` on such a source. A declared variable that is unset is simply absent (`ctx.env.get(name)` is `None`).
- **`collect(entry, source, *, captures=None)`** yields dicts, one per record. It is consumed lazily and closed if the consumer stops, so release resources in a `finally`. If the mission has `preserve_raw: true`, `captures` is the archive's capture log: call `captures.record(...)` with the bytes you fetched before parsing them.
- **`extracts=True`** says your type finds its records at `entry.extract` in a body, as `api` and `capture` do. A survey whose table of your type gets exactly one record holding a list of two or more objects, with neither `extract:` nor `normalize:`, then warns (`extract_missing`) and names the path to set.
- **`normalize:` is applied for you.** recon applies the entry's spec to every record you yield, so the collector above never mentions it. Pass `normalizes=True` to `CollectorType` only if you apply `entry.normalize` yourself, because the spec needs something the records do not carry (the built-in `capture` type does this to expose the capture line as `_capture`); recon then leaves your records alone.
- **Raise `NoRecordsError(message, problem_kind=..., field=..., fix=...)`** (`recon.domain.errors`) when the collector finds nothing to keep for a reason it can name, before it yields a record: a response that is not empty but holds none of what the entry asked for. The survey keeps the table, empty, and warns with your message and fix at `collectors.<i>.<field>`; a source that simply had nothing raises nothing. Raised after a record, it fails the table instead. `recon.domain.normalize.to_records` raises it for you when you find records with `extract`.
- **Raise `CollectionError(message, kind=...)`** on failure. `kind="transient"` (a retry may work) and `kind="auth"` (a credential was refused) decide the survey's exit code; `kind="config"` says the mission must change; anything else is `run_failed` (the default), and so is its survey (exit 8). Give the error a `fix`: the table's error keeps it.
- **`effects`** declares what a run can touch: any of `network`, `subprocess`, `filesystem`. `None` means unknown, and every plan then shows the run's effects as `unknown`. Declared effects are what `survey --dry-run` reports; recon does not enforce them.
- **`check(entry)`** and **`describe(entry, source)`** feed the plan. Both must be pure: no I/O, no network. `describe` returns what a reviewer needs to see of a run: for a request, `method`, `url` (every query parameter included), `headers` and `body`, with a credential as `REDACTED` (`recon.domain.http.reported_url` redacts a URL as telemetry does); for a command, `commands`. `check` returns `Problem`s with paths relative to the entry, dotted (`run`, `params.query`), each with a `kind` from the shared vocabulary where one fits (`missing_key`, `invalid_value`, `wrong_shape`, ...; `unresolved` for a `{placeholder}` nothing fills), a `fix`, the value written as `got` when there is one, and the legal values as `allowed` when the set is closed; the planner prefixes `collectors.<i>.`.

A collector's own settings use the existing `CollectorEntry` fields (`run`, `endpoint`, `params`, `path`, `extract`, …). Config models reject unknown keys, so a new type cannot invent new top-level fields.

## A transform

```python
# my_package/transforms.py
def domain(url):
    from urllib.parse import urlsplit

    return urlsplit(url).netloc if url else None
```

```toml
[project.entry-points."recon.transforms"]
domain = "my_package.transforms:domain"
```

```yaml
normalize:
  site: "url|$domain"
```

A transform receives whatever the path resolved to (often `None` when a field is missing) and should return a value rather than raise. An unknown `$name` is a config error at plan time, never a silently skipped step.

A transform that reads files the records name says so by taking a keyword-only `roots`: recon passes the directories the mission declares (its own and each `local` source's), and the transform reads only under them, as `$markitdown` does. recon binds `roots` to every transform that takes it; none is named.

## When a plugin is broken

A plugin that raises on import, registers the wrong kind of object, reuses a name another package registered, or whose `create` raises, is reported (in `recon status`, in `survey --dry-run` warnings, and on stderr) and left out. Every other collector type and transform still loads. A config that uses the broken type gets a plan problem that says it is installed but failed to load, with the error. When two packages register the same name, recon's own registration wins.
