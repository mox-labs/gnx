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

from pydantic import BaseModel, ConfigDict

from recon.domain.collector import CollectorType


class SqliteSettings(BaseModel):
    """The keys a sqlite collector takes beyond recon's own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sql: str
    timeout_s: float = 5.0


class SqliteCollector:
    def __init__(self, ctx):
        self._telemetry = ctx.telemetry
        self._db = None

    def collect(self, entry, source, *, captures=None):
        settings = SqliteSettings.model_validate(entry.settings)
        if self._db is None:  # one connection for every table of the survey
            self._db = sqlite3.connect(source.url, timeout=settings.timeout_s)
            self._db.row_factory = sqlite3.Row
        with self._telemetry.span("sqlite.query", {"db.system.name": "sqlite"}):
            rows = self._db.execute(settings.sql).fetchall()
        for row in rows:
            yield dict(row)

    def close(self):
        if self._db is not None:
            self._db.close()


collector_type = CollectorType(
    create=SqliteCollector,
    effects=frozenset({"filesystem"}),
    summary="SQL against a local SQLite file → records",
    settings=SqliteSettings,
    source_kinds=frozenset({"sqlite"}),
    requires_source=True,
    describe=lambda entry, source: {
        "database": source.url if source else None,
        "sql": entry.settings.get("sql"),
    },
)
```

```yaml
catalog:
  - name: app-db
    type: sqlite
    url: data/app.db
collectors:
  - name: users
    type: sqlite
    source: app-db
    sql: SELECT id, email FROM users
```

```toml
# my_package's pyproject.toml
[project.entry-points."recon.collectors"]
sqlite = "my_package.recon_sqlite:collector_type"
```

The contract:

- **`settings`** is the schema of the keys your type takes beyond the ones recon reads for every type (`name`, `type`, `source`, `type_url`, `normalize`, and `extract` for a type with `extracts=True`): a pydantic model that forbids unknown keys. The plan validates an entry's other keys (`entry.settings`, a dict) against it and reports each problem at its path (`collectors.0.sql`), and your hooks see only an entry it accepts; parse `entry.settings` with it where you need the values. Without `settings`, your type takes no key of its own, and any other key is a plan problem.
- **`source_kinds`** are the catalog source kinds (a source's `type:`) your type adds: a source of a kind no installed type adds is a plan problem naming the installed kinds (`api`, `local`, `mcp` and `web` are the built-in types').
- **`accepts_kinds`** are the source kinds a collector of your type can be pinned to: a collector pinned to a source of another kind is a plan problem at `collectors.<i>.source` naming the sources it could use. Leave it `None` to take a source of any kind (as `capture` does, which only filters by the source's name). The built-ins: `cli` takes `local`, `api` and `web` take `api` or `web`, `mcp` takes `mcp`.
- **`source_settings`** is the schema of the keys a source of your kinds takes beyond the ones every source has (`name`, `type`, `url`, `auth`, `rate_limit`, `timeout`, `max_bytes`, `user_agent`), as `settings` is for an entry: the plan validates `source.settings` against it, each problem at its path (`catalog.0.command`). Without one, a source of your kinds takes no key of its own and needs `url:`; with one, **`check_source(source)`** says what it needs of the shared keys (the built-in `mcp` kind needs `url:` or its own `command:`, not both), returning `Problem`s with paths relative to the source. **`source_env(source)`** names the environment variables such a source reads beyond `auth.env`, each with where it names it (`{"GITHUB_TOKEN": "env.GITHUB_TOKEN"}`): the mission then declares them for every type whose collectors run against the source (`ctx.env`), and the plan warns (`env_unset`) about one that is not set. All three are pure, and see only settings the schema accepts.
- **`create(ctx)`** builds the collector once per survey whose mission uses the type. `ctx` (`CollectorContext`) carries the shared `requester` (rate-limited HTTP, one bucket per source), `converter`, `shell` (runs a command, as the `cli` type does), `telemetry` (the survey's: a span you open with `ctx.telemetry.span(name, attributes)` nests under the table's `recon.collect` span, in the event log and in OpenTelemetry), `rate_limiter` (the survey's one bucket per source, which the requester draws from too: a type that reaches a source another way calls `ctx.rate_limiter.acquire(source, ctx.deadline)` before each call), `deadline` (what is left of the survey's and the table's bounds in time: `remaining()`, `limit(seconds)`, `check()`; a type that waits or runs something keeps to it, as `requester` does), `transforms`, `mission_dir`, `env`, and `user_agent` (what to send when a source sets none). Take what you need from it; never read `os.environ` directly, so tests can inject. Every request through `requester` is an HTTP client span in the survey's telemetry, with its `url.full` redacted, so a type that fetches through it is traced with nothing more to do.
- **`ctx.env` is not the process environment.** It holds only the variables the mission declares for your type: the `auth.env` names of the catalog sources your collectors run against. Reading any other name raises a config error whose `fix` says to name the variable as `auth.env` on such a source. A declared variable that is unset is simply absent (`ctx.env.get(name)` is `None`).
- **`collect(entry, source, *, captures=None)`** yields dicts, one per record. It is consumed lazily and closed if the consumer stops, so release a table's resources in a `finally`. If the mission has `preserve_raw: true`, `captures` is the archive's capture log: call `captures.record(...)` with the bytes you fetched before parsing them.
- **`requirements(entry, source)`**, if your collector has one and your type declares a **`preflight`** (a sentence saying what checking does: "starts each stdio server a run names ..."), is what a dry run asks for each run: a list of `Requirement(need, holds, why, fix)` (`recon.domain.collector`), each checked now against the host or what the run reaches (a server that starts, a tool it lists). The dry run builds your collector for this, prints your `preflight` sentence, lists each requirement with whether it holds and the runs that need it, says `runnable: false` when one does not hold, and closes the collector; the plan stays valid. A type without a `preflight` is never built by a dry run. A survey does not ask: check the same in `collect` and raise `CollectionError(kind="config")` with the requirement's fix, as the built-in `mcp` type does.
- **`close()`**, if your collector has one, is called once when the survey ends, however it ends (finished, failed, interrupted). One collector serves every table of its type in a survey, so it may hold a session across them (a connection, a stdio server it started on its first table) and end it there, as the example does. A `close()` that raises is a `plugin_broken` warning, and every other collector is still closed.
- **`extracts=True`** says your type finds its records at `entry.extract` in a body, as `api` and `capture` do. A survey whose table of your type gets exactly one record holding a list of two or more objects, with neither `extract:` nor `normalize:`, then warns (`extract_missing`) and names the path to set.
- **`normalize:` is applied for you.** recon applies the entry's spec to every record you yield, so the collector above never mentions it. Pass `normalizes=True` to `CollectorType` only if you apply `entry.normalize` yourself, because the spec needs something the records do not carry (the built-in `capture` type does this to expose the capture line as `_capture`); recon then leaves your records alone.
- **Raise `NoRecordsError(message, problem_kind=..., field=..., fix=...)`** (`recon.domain.errors`) when the collector finds nothing to keep for a reason it can name, before it yields a record: a response that is not empty but holds none of what the entry asked for. The survey keeps the table, empty, and warns with your message and fix at `collectors.<i>.<field>`; a source that simply had nothing raises nothing. Raised after a record, it fails the table instead. `recon.domain.normalize.to_records` raises it for you when you find records with `extract`.
- **Raise `CollectionError(message, kind=...)`** on failure. `kind="transient"` (a retry may work) and `kind="auth"` (a credential was refused) decide the survey's exit code; `kind="config"` says the mission must change; anything else is `run_failed` (the default), and so is its survey (exit 8). Give the error a `fix`: the table's error keeps it.
- **`effects`** declares what a run can touch: any of `network`, `subprocess`, `filesystem`. `None` means unknown, and every plan then shows the run's effects as `unknown`. Declared effects are what `survey --dry-run` reports; recon does not enforce them.
- **`check_run(entry, source)`** checks one run against the source it runs against (`None` for a run with no source), for what `check` cannot see without the catalog: the built-in `cli` type refuses `{url}` in a run with no source here. It returns `Problem`s with paths relative to the entry, as `check` does, and must be pure.
- **`check(entry)`** and **`describe(entry, source)`** feed the plan, for an entry whose settings `settings` accepts. Both must be pure: no I/O, no network. `describe` returns what a reviewer needs to see of a run: for a request, `method`, `url` (every query parameter included), `headers` and `body`, with a credential as `REDACTED` (`recon.domain.http.reported_url` redacts a URL as telemetry does); for a command, `commands`. `check` returns `Problem`s with paths relative to the entry, dotted (`run`, `params.query`), each with a `kind` from the shared vocabulary where one fits (`missing_key`, `invalid_value`, `wrong_shape`, ...; `unresolved` for a `{placeholder}` nothing fills), a `fix`, the value written as `got` when there is one, and the legal values as `allowed` when the set is closed; the planner prefixes `collectors.<i>.`.

The built-in types declare their keys the same way (`recon.adapters._out.api_collector.ApiSettings` and its siblings), so a key one type takes is unknown on another (`run:` on an `api` collector).

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

A transform that reads files the records name says so by taking a keyword-only `roots`: recon passes the directories the mission declares (its own and each `local` source's), and the transform reads only under them, as `$markitdown` does. recon binds `roots` to every transform that takes it; none is named. A transform may also take a keyword-only `base`, the directory a relative path in a record is relative to: the `cli` type binds the directory its command ran in.

## When a plugin is broken

A plugin that raises on import, registers the wrong kind of object, reuses a name another package registered, or whose `create` raises, is reported (in `recon status`, in `survey --dry-run` warnings, and on stderr) and left out. Every other collector type and transform still loads. A config that uses the broken type gets a plan problem that says it is installed but failed to load, with the error. When two packages register the same name, recon's own registration wins.
