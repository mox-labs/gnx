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
from recon.domain.exceptions import CollectionError, Problem


class SqliteCollector:
    def __init__(self, ctx):
        self._mission_dir = ctx.mission_dir
        self._transforms = ctx.transforms

    def collect(self, entry, source, *, captures=None):
        if source is None:
            raise CollectionError("sqlite needs a source", kind="config")
        with sqlite3.connect(source.url) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute(entry.run):
                yield dict(row)


def _check(entry):
    return [] if entry.run else [Problem("run", "a sqlite collector needs run: (the SELECT)")]


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

- **`create(ctx)`** builds the collector once per survey. `ctx` (`CollectorContext`) carries the shared `requester` (rate-limited HTTP, one bucket per source), `converter`, `transforms`, `mission_dir` and `env`. Take what you need from it; never read `os.environ` directly, so tests can inject.
- **`collect(entry, source, *, captures=None)`** yields dicts, one per record. It is consumed lazily and closed if the consumer stops, so release resources in a `finally`. If the mission has `preserve_raw: true`, `captures` is the archive's capture log: call `captures.record(...)` with the bytes you fetched before parsing them.
- **Raise `CollectionError(message, kind=...)`** on failure. `kind="transient"` (a retry may work) and `kind="auth"` (a credential was refused) decide the survey's exit code; anything else is `collection`.
- **`effects`** declares what a run can touch: any of `network`, `subprocess`, `filesystem`. `None` means unknown, and every plan then shows the run's effects as `unknown`. Declared effects are what `survey --dry-run` reports; recon does not enforce them.
- **`check(entry)`** and **`describe(entry, source)`** feed the plan. Both must be pure: no I/O, no network. `check` returns `Problem`s with paths relative to the entry (`run`, `endpoint`); the planner prefixes `collectors[i].`.

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

## When a plugin is broken

A plugin that raises on import, registers the wrong kind of object, reuses a name another package registered, or whose `create` raises, is reported (in `recon status`, in `survey --dry-run` warnings, and on stderr) and left out. Every other collector type and transform still loads. A config that uses the broken type gets a plan problem that says it is installed but failed to load, with the error. When two packages register the same name, recon's own registration wins.
