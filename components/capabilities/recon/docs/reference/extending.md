# Adding a collector type, a source kind, a converter or a transform

Each is an extension, registered at one of recon's points on matrix's registry. recon's own built-ins register the same way, from recon's `matrix.extensions` entry point (`recon._matrix:register`), so a package you install alongside recon is wired exactly as `api` or `$html2text` are, without editing recon; and code using recon registers its own in-process, at the same points, with `recon.open(extensions=[register])`.

Everything an extension names is in `recon.extension`, the published contract; import nothing else of recon.

| Point (`POINTS[...]`) | A config names it | An entry is |
|---|---|---|
| `recon.collector` (`"collector"`) | `type:`: a built-in by its short name (`api`, for `recon.v1.collector.api`), any other by its type URL (`acme.v1.collector.sqlite`) | `build(**needs)` giving a `Collector`; its `config` the schema of the type's own keys |
| `recon.source` (`"source"`) | a catalog source's `type:` (`api`, `local`, `file`, `mcp`, or a type URL) | `build()` giving a `SourceKind`; its `config` the schema of the kind's own keys |
| `recon.converter` (`"converter"`) | a body's `converter=` (`json`, `xml`, `lines`, `document`), or its media type | `build()` giving a `Converter` |
| `recon.transform` (`"transform"`) | `path\|$name`: a built-in by its short name (`$html2text`), any other by its type URL (`$acme.v1.transform.domain`) | `build()` giving a callable: value in, value out |

matrix's rules hold: a type URL is `<namespace>.v<N>.<name>`, kebab-case segments; one registered twice is refused, naming both; an extension that fails to load is quarantined whole, every entry it registered with it, and the others load. `recon types --json` lists what is registered, whose each is, each type's and kind's config schema, and each converter's media types; `recon status --json` lists anything that failed to load.

## A collector type

A type talks to its source, and gives recon what it got as **items**. recon does the rest, for every type the same way: it captures what you fetched (with `preserve_raw`), converts each body by its media type, finds the records in it (`extract:`), shapes them (`normalize:`), lands them, and, for a type it crawls, drives the crawl. Your type never applies `normalize:` or writes a capture: there is no way for it to.

```python
# my_package/recon_sqlite.py
import sqlite3

from pydantic import BaseModel, ConfigDict

from recon.extension import POINTS, PlainKind, Record, source_needed


class SqliteSettings(BaseModel):
    """The keys a sqlite collector takes beyond recon's own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sql: str
    timeout_s: float = 5.0


class SqliteCollector:
    effects = frozenset({"filesystem"})
    records = "finite"
    accepts = frozenset({"acme.v1.source.sqlite"})

    def __init__(self, *, telemetry):
        self._telemetry = telemetry
        self._db = None

    def describe(self, run):
        return {
            "database": run.source.url if run.source else None,
            "sql": run.settings_as(SqliteSettings).sql,
        }

    def check(self, run):
        return source_needed(run, "sqlite")

    def collect(self, run, resume):
        settings = run.settings_as(SqliteSettings)
        if self._db is None:  # one connection for every table of the act
            self._db = sqlite3.connect(run.source.url, timeout=settings.timeout_s)
            self._db.row_factory = sqlite3.Row
        with self._telemetry.span("sqlite.query", {"db.system.name": "sqlite"}):
            rows = self._db.execute(settings.sql).fetchall()
        for row in rows:
            yield Record(dict(row))

    def close(self):
        if self._db is not None:
            self._db.close()


class SqliteSource(PlainKind):
    """A SQLite file: its url."""


def register(registry):
    registry.register(
        POINTS["collector"],
        "acme.v1.collector.sqlite",
        SqliteCollector,
        config=SqliteSettings,
        needs={"telemetry"},
        summary="SQL against a local SQLite file → records",
    )
    registry.register(
        POINTS["source"], "acme.v1.source.sqlite", SqliteSource, summary="a SQLite file"
    )
```

```toml
# my_package's pyproject.toml
[project.entry-points."matrix.extensions"]
acme = "my_package.recon_sqlite:register"
```

```yaml
catalog:
  - name: app-db
    type: acme.v1.source.sqlite
    url: data/app.db
collectors:
  - name: users
    type: acme.v1.collector.sqlite
    source: app-db
    sql: SELECT id, email FROM users
```

In-process, without a package: `recon.open(extensions=[register])`.

The contract (`recon.extension`; the module docstrings of `recon.core.domain.collector` say each in full):

- **Building.** Your entry is `build(**needs)`, called once per act (a survey, a stream's run, a checked dry run) with exactly the needs your registration names, each a keyword argument: `requester` (the act's rate-limited HTTP, one span a request, within the act's deadline), `shell` (runs a command), `clock` (the clock port: wait on it, never on `time.sleep`), `telemetry` (a span you open nests under the table's), `deadline` (what is left of the act's and the table's bounds), `rate_limiter` (one bucket per source: draw from it before each call that does not go through `requester`), `credentials` (a source's credential, resolved as every kind's is: `token`, `helped`, `resolve("env:NAME" | "file:PATH")`, `helper(command, source)`, `secrets`, `redacted`), `env` (the variables the mission declares for your type's sources, and no other: reading one it does not declare raises a config error whose `fix` says where to declare it), `user_agent`, `project` (the person's project root) and `capture_logs`. Never read `os.environ`, and never import an adapter of recon's. **Building must be pure**: recon also builds your type once per session with stand-ins for these needs, which fail if used, to read what it declares.
- **One collector serves every run of its type in an act**, so it may hold a session across them (a connection, a server it started) and end it in `close()`, which recon calls once when the act ends, however it ends. A `close()` that raises is a `plugin_broken` warning, and every other collector is still closed.
- **`effects`** declares what a run can touch (`network`, `subprocess`, `filesystem`); `None` means unknown, which every plan shows as `unknown`. **`records`** says whether a run's records end (`finite`) and, when they do not, who paces them (`unbounded_recon_paced`, `unbounded_source_paced`). **`accepts`** names the source kinds your runs use: an unpinned collector fans out over the catalog's sources of those kinds alone (the plan names the others in `skipped`), and one pinned to another kind is a plan problem. `None` takes any source, and none.
- **`config`** (your registration's) is the schema of the keys your type takes beyond recon's own (`name`, `type`, `source`, `type_url`, `extract`, `normalize`, `deadline`, `window`, `buffer`, `retain`, `changes`, `range`, and `paginate` for a `Paged` type). It must forbid unknown keys. The plan validates each entry's other keys against it, each problem at its path (`collectors.0.sql`), and every member is handed the validated value as `run.settings` (`run.settings_as(YourSettings)`), filled for the run: a ranged table's `{range.start}` is already its step's bound.
- **`describe(run)`** and **`check(run)`** are pure: no I/O, no network. `describe` returns what a reviewer needs to see of a run: for a request `method`, `url` (every query parameter, a credential as `REDACTED`: `reported_url` redacts a URL as telemetry does), `headers` and `body`; for a command `commands` (shell lines) and `cwd`; for a server `server`. `check` returns `Problem`s at paths relative to the entry, each with a `kind` from the shared vocabulary where one fits (`missing_key`, `invalid_value`, `unresolved`, ...), a `fix`, `got` and `allowed`; the planner prefixes `collectors.<i>.`. A type whose runs need a source returns `source_needed(run)`.
- **`collect(run, resume)`** gives items, lazily; it is closed if the consumer stops, so release a table's resources in a `finally`:
  - **`Body(media_type, data, ...)`**: bytes as you fetched them. Name its converter (`converter="json"`), or let its media type pick one. Give recon what the capture records: `request` (what you asked, as a capture may keep it), `kind` (the capture's `kind`; your type's name by default), `status`, `headers`, and `secrets`, the credential values you sent, which recon keeps out of the capture and out of nothing else. `origin` names it in an error. `data` may be an iterator of chunks (a command's stdout as it flows): recon converts and captures it as it arrives, and its generator may return a `BodyEnd(status=..., request=...)` known only at its end.
  - **`Record(data, position=None, scope={})`**: a record you have already structured (a line of a file, an event). recon still finds the records in it (`extract:`), shapes and captures it.
  - **`PageMark(index, token)`** before you ask for each page of a crawl you run yourself (recon stops reading once a stream has ended, so the page is not asked for), and **`Truncated(by)`** when it stopped short; **`Gap(start, end, resumed)`** for a stretch your source was not read; **`Dropped()`** for a record you let go rather than hold.
  - `fields` (on a `Body`) are recon's own fields every record of it carries, written with it; `scope` is what a normalize spec may read beside a record's fields (`_capture.*`), never written by itself. Each key starts with `_`.
- **`extract:`** applies to what every type gives: a body's converted values (each line's, for a body of lines) and each record's data. A body or record it does not fit gives nothing; when none of a table's does, the table is kept empty and warned with the reason (`extract_unresolved`, `records_not_objects`).
- **Raise `NoRecordsError(message, problem_kind=..., field=..., fix=...)`** when your type finds nothing to keep for a reason it can name, before its first item: the survey keeps the table, empty, and warns with your message and fix at `collectors.<i>.<field>`. A source that simply had nothing raises nothing. Raised after a record, it fails the table instead.
- **Raise `CollectionError(message, kind=...)`** on failure. `kind="transient"` (a retry may work) and `kind="auth"` (a credential was refused) decide the survey's exit code; `kind="config"` says the mission must change; anything else is `run_failed` (the default). Give it a `fix`: the table's error keeps it. Redact what your source echoed of a credential from its message (`credentials.redacted(source, error)`).

The built-in types are written the same way (`recon.adapters._out.api_collector.ApiCollector` and its siblings), each type's keys declared in its own schema, so a key one type takes is unknown on another (`run:` on an `api` collector).

### Optional: recon crawls it (`Paged`)

A type that reads its source page by page as recon asks has `page(run, token) -> Page(body, next=None, token=None)`: the page `token` names (None for the first; for a `page` crawl, the first page's number), its `Body`, the `next` token it names (`None` when it names none; recon counts `offset` and `page` crawls itself), and `token` when the page was asked for with none but has a name a later page could link back to (a `link` crawl's first page, by its URL). Only such a type takes `paginate:` (`recon.extension.PaginateSpec`). recon runs the crawl: it stops as `paginate:`'s rules say, keeps the page log beside the table, positions each record `{page, i}`, marks a table cut short `truncated` with its warning, and, for a stream's crawl resumed after a stop, asks again for the page it left off on and finds the last record it kept there by its content, shaped as it was kept (a page that moved is said `paginate_shifted`; a resumed token your source refuses with `run_failed` begins the crawl again, `paginate_restarted`). How a page is asked for by its token is your type's knowledge of its source.

### Optional: a preflight (`Preflight`)

A type whose runs need something of the host that a plan cannot see has `preflight` (a sentence saying what checking does: "starts each stdio server a run names ...") and `requirements(run) -> [Requirement(need, holds, why, fix)]`. A checked dry run (`--dry-run --check`, `recon.plan(..., check=True)`) builds your collector, prints the sentence, lists each requirement with whether it holds and the runs that need it, says `runnable: false` when one does not, and closes it. An unchecked plan builds nothing for it. A survey does not ask: check the same in `collect` and raise `CollectionError(kind="config")` with the requirement's fix, as the built-in `mcp` type does.

### Checking your type: the conformance kit

```python
from recon.testing import CollectorConformance


class TestSqliteConforms(CollectorConformance):
    type_name = "acme.v1.collector.sqlite"

    def register(self, registry):
        my_package.recon_sqlite.register(registry)

    def entry(self):
        return {"name": "users", "type": self.type_name, "source": "db", "sql": "SELECT 1 AS a"}

    def source(self):
        return {"name": "db", "type": "acme.v1.source.sqlite", "url": str(TEST_DB)}
```

pytest collects the suite's checks: that recon reads what your type declares, that its schema refuses an unknown key, that `describe` and `check` are pure, that the plan accepts the entry, that a run's items are items recon turns into records, that a run let go early and a collector closed from another thread stop, that a stream's run resumes after a position, and that each optional protocol you keep does what it says (give `paged_entry()` for a `Paged` type, and `needs()` for what your build names beyond the kit's defaults). `recon.testing.records(collector, run)` is recon's pipeline for one run, for your own tests. Every built-in passes the kit; the contract freezes at recon 1.0, after three independent types have.

## A collector type whose records do not end

Declare it in `records`: `unbounded_recon_paced` for a source recon reads when it is ready (a file, a command, a polled API), `unbounded_source_paced` for one that sends regardless (server-sent events). Each entry of your type is then a stream, collected into windows (`recon --skill -r streaming`); one with no `window:` gets the default, `{seconds: 300}`, and its overflow follows the pacing (`block` for the first, which loses nothing; `drop_newest` for the second), each reported in the plan's `provenance`. An entry of any type with `window:` is a stream too.

- **`collect`** is called once per run of the stream, on a thread of the stream's own, and may yield for ever. It opens no telemetry span (the stream's telemetry belongs to the thread that writes the windows), and waits, when it waits, on the `clock` need.
- **A record's position**: when your source can say where a record is (an offset, an event id, a cursor), give it as `Record(data, position=...)`, any JSON value. A stream resumed after a stop calls `collect(run, Position(<the last position kept>))`, and you give what comes after it, so nothing is collected twice. recon writes the position as the record's `_position`, in the same line.
- **`close()`** must end a read in progress, from another thread: a stream that stops closes its collector and waits 5 seconds for its thread, then leaves it with a `source_stuck` warning, whose fix tells the user to report it to your package or upgrade it.
- Raise `CollectionError` for a failure a reconnect cannot mend: the window being filled is sealed `error`, keeping the records it got, and the stream stops.

## A source kind

A kind is what a catalog source's `type:` names: `PlainKind` for one with no keys of its own (its sources need `url:`), or a class with `check(source)` (the problems in a source whose own keys your `config` accepted, paths relative to the source) and `env(source)` (the variables such a source reads beyond `auth.token`, with where it names each: the mission then declares them for every type whose runs use the source, and the plan warns `env_unset` about one not set). Both are pure. Register it at `POINTS["source"]` with `config=` the schema of its own keys, which must forbid unknown keys.

## A converter

A converter turns bytes of a media type into values recon finds records in. It has `media_types` (what it is picked for: `type/subtype`, `+suffix`, `type/*`) and `convert(chunks, media_type, origin)`, an iterator of values from an iterable of byte chunks: join them for a format that needs the whole body (JSON), or convert as they arrive for one that does not (lines). Read the media type's parameters yourself (`charset`). Raise `CollectionError` for bytes that are not what their media type says, naming `origin`. A body names a converter by its config name (`converter="lines"`); otherwise the most specific claim of its media type picks one. recon's own keep their claims: a converter that claims a media type another converts is left out, said as broken.

## A transform

```python
# my_package/transforms.py
def domain(url):
    from urllib.parse import urlsplit

    return urlsplit(url).netloc if url else None
```

```python
def register(registry):
    registry.register(POINTS["transform"], "acme.v1.transform.domain", lambda: domain)
```

```yaml
normalize:
  site: "url|$acme.v1.transform.domain"
```

A transform receives whatever the path resolved to (often `None` when a field is missing) and should return a value rather than raise. An unknown `$name` is a config error at plan time, never a silently skipped step.

A transform that reads files the records name says so by taking a keyword-only `roots`: recon passes the directories the mission declares (its own and each `local` source's), and the transform reads only under them, as `$markitdown` does. recon binds `roots` to every transform that takes it; none is named. A transform may also take a keyword-only `base`, the directory a relative path in a record is relative to: recon binds a `local` source's directory for the records of a run against it.

## When a plugin is broken

A plugin that raises on import, registers the wrong kind of object, names a need recon does not supply, has a `config` that lets unknown keys through, reuses a name another package registered, or whose build raises, is reported (in `recon status`, in `survey --dry-run` warnings, and on stderr) and left out. Every other extension still loads. A config that uses the broken type gets a plan problem that says it is installed but failed to load, with the error. When two packages register the same type URL, its namespace's owner keeps it (matrix's rule); recon's own converters keep their media types.
