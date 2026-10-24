"""Planning: validate a mission config and resolve every run it would make, with no effects.

``build_plan`` is what ``survey --dry-run`` prints and what ``survey`` checks before it
creates an archive. It reports every problem it can find, each located by a path dotted
from the config's root (``collectors.2.normalize.title``) and carrying a kind and a fix,
rather than stopping at the first. An unknown key does not stop the checks of the entry it
is in: the entry is checked again without it. Only an entry that does not parse even then
is left unchecked, and the plan says so (``complete`` is false).

Pure: no network, no subprocess, no filesystem; whether a ``local`` source's directory
exists is asked of the predicate the caller passes. Collector types contribute through their
``CollectorType.settings`` schema, which validates an entry's keys beyond the ones recon
reads, and their ``check`` and ``describe`` hooks, which are pure by contract and see only an
entry whose settings the schema accepts. A source's kind must be one an installed type adds
(``CollectorType.source_kinds``); its keys beyond the shared ones are validated against that
type's ``source_settings`` schema, and its ``check_source`` hook judges the rest. What a run
needs of the host beyond this (a server that starts, a tool it lists) is not the plan's to
check: a dry run asks the collector types that declare a ``preflight``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

from pydantic import BaseModel, ValidationError

from recon.domain.archives import EVENT_LOG_NAME, SIDECARS, table_name
from recon.domain.capture import CAPTURE_LOG_NAME
from recon.domain.credentials import env_name
from recon.domain.documents import SourcedValue
from recon.domain.errors import Problem
from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
from recon.domain.normalize import path_problem, spec_problems
from recon.domain.paginate import crawl_floor_s
from recon.domain.stream import (
    DEFAULT_WINDOW_S,
    POSITION_FIELD,
    BufferSpec,
    WindowSpec,
    default_overflow,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

    from recon.domain.collector import BrokenPlugin, CollectorType, Plugins
    from recon.domain.documents import Origin
    from recon.domain.stream import Overflow, Retain

_TOP_LEVEL_KEYS = tuple(sorted(ReconConfig.model_fields))

#: A top-level key beginning with this is the config author's own: a note, or the holder of
#: a YAML anchor that collectors alias. recon ignores it. Every other unknown key is an error.
IGNORED_KEY_PREFIX = "_"

#: Output names that would overwrite a file recon itself writes into an archive.
RESERVED_OUTPUT_NAMES = frozenset(
    name.removesuffix(".jsonl") for name in (CAPTURE_LOG_NAME, EVENT_LOG_NAME)
)

#: What an output name may not hold: it is the stem of one file in the archive.
_NOT_IN_A_FILE_NAME = ("/", "\\", "..")

#: pydantic's error types, as problem kinds; a type error not listed is ``wrong_shape`` when
#: its name says the value had the wrong type, else ``invalid_value``.
_PYDANTIC_KINDS = {"extra_forbidden": "unknown_key", "missing": "missing_key"}
_QUOTED = re.compile(r"'([^']*)'")


@dataclass(frozen=True)
class PlannedRun:
    """One collector × source pair: the unit that produces one output table."""

    output: str
    collector: str
    type: str
    source: str | None
    type_url: str
    effects: list[str] | None  # None: the type did not declare its effects (unknown)
    detail: dict[str, Any]
    #: The stream the run is, by name, when it is one: its table is written window by window.
    stream: str | None = None

    def document(self) -> dict[str, Any]:
        stream = {} if self.stream is None else {"stream": self.stream}
        return {
            "output": self.output,
            "file": f"{self.output}.jsonl",
            "collector": self.collector,
            "type": self.type,
            "source": self.source,
            "type_url": self.type_url,
            "effects": self.effects if self.effects is not None else "unknown",
            **stream,
            **self.detail,
        }


@dataclass(frozen=True)
class PlannedStream:
    """A stream the plan resolved: its entry's position in ``collectors``, its name (the
    collector's), and its effective window, buffer and overflow, and what it retains."""

    index: int
    name: str
    window: WindowSpec
    buffer: BufferSpec
    overflow: Overflow
    retain: Retain


@dataclass(frozen=True)
class SkippedSource:
    """A catalog source an unpinned collector does not run against: its type does not accept
    the source's kind."""

    collector: str
    source: str
    kind: str

    def document(self) -> dict[str, str]:
        return {"collector": self.collector, "source": self.source, "kind": self.kind}


@dataclass(frozen=True)
class Plan:
    """``complete`` is false when part of the config could not be checked, so more problems
    may follow a fix. ``skipped`` names each catalog source an unpinned collector does not
    run against, since its type does not accept the source's kind. ``streams`` are the
    entries that are streams, with their effective settings, and ``provenance`` says where
    each of those settings came from."""

    config: ReconConfig | None
    runs: list[PlannedRun] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    complete: bool = True
    skipped: list[SkippedSource] = field(default_factory=list)
    streams: list[PlannedStream] = field(default_factory=list)
    provenance: list[SourcedValue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.problems and self.config is not None

    def effects(self) -> list[str]:
        """The union of declared effects; ``unknown`` when any run's type did not declare."""
        found: set[str] = set()
        for run in self.runs:
            found.update(run.effects if run.effects is not None else ["unknown"])
        return sorted(found)


def fan_out(
    entry: CollectorEntry,
    catalog: Mapping[str, SourceEntry],
    accepts: Collection[str] | None = None,
) -> list[tuple[SourceEntry | None, str]]:
    """The (source, output name) pairs a collector entry runs against.

    Pinned (``source:``) → that source only, output named after the collector. No source
    and a non-empty catalog → every catalog source of a kind in ``accepts``, the kinds its
    type accepts (``CollectorType.accepts_kinds``; None, every kind), output
    ``<collector>-<source>`` (owner ruling 2026-10-22). No catalog → once, with no source. A
    pinned source missing from the catalog, or a catalog with no source of an accepted kind,
    yields nothing; the planner reports either.
    """
    if entry.source:
        src = catalog.get(entry.source)
        return [(src, entry.name)] if src is not None else []
    if catalog:
        return [
            (src, f"{entry.name}-{src.name}")
            for src in catalog.values()
            if accepts is None or src.type in accepts
        ]
    return [(None, entry.name)]


def accepted_kinds(
    types: Mapping[str, CollectorType], entry: CollectorEntry
) -> frozenset[str] | None:
    """The source kinds the entry's type accepts; None (every kind) for a type that does not
    say, or one not installed."""
    ctype = types.get(entry.type)
    return ctype.accepts_kinds if ctype is not None else None


def build_plan(
    raw: Any,
    plugins: Plugins,
    env: Mapping[str, str],
    *,
    base: Path | None = None,
    is_directory: Callable[[Path], bool] | None = None,
) -> Plan:
    """Validate ``raw`` (the parsed config) and resolve its runs.

    Each catalog and collector entry is validated on its own, so one malformed entry does
    not hide the problems in the others. A ``local`` source's relative ``url`` is resolved
    against ``base`` (the project root), so a mission means the same directories wherever
    it is run from. Given ``is_directory``, a ``local`` source a run uses must name a
    directory: a command never runs somewhere its mission did not say. ``env`` is read only
    to warn about a credential variable a used source names and the environment does not
    set.
    """
    warnings = [plugin_warning(b) for b in plugins.broken]
    if not isinstance(raw, dict):
        got = "an empty file" if raw is None else type(raw).__name__
        problem = Problem(
            "",
            f"a config must be a mapping with collectors:, got {got}",
            kind="wrong_shape",
            fix="write a mapping with at least `collectors:`",
        )
        return Plan(config=None, problems=[problem], warnings=warnings, complete=False)

    raw = {k: v for k, v in raw.items() if not _ignored(k)}
    problems = _top_level_problems(raw)
    sources, sources_whole = _validate_items(raw.get("catalog"), "catalog", SourceEntry, problems)
    entries, entries_whole = _validate_items(
        raw.get("collectors"), "collectors", CollectorEntry, problems
    )
    sources = [(i, _anchored(src, base)) for i, src in sources]
    found, refused = _source_problems(sources, plugins)
    problems += found
    catalog = _catalog(sources, problems)
    unchecked = _unparsed_names(raw.get("catalog"), catalog) if not sources_whole else set()

    runs: list[PlannedRun] = []
    skipped: list[SkippedSource] = []
    produced_by: dict[str, tuple[int, str]] = {}
    used_sources: set[str] = set()
    settings_whole = True
    streams: list[PlannedStream] = []
    provenance: list[SourcedValue] = []
    for i, entry in entries:
        ctype = plugins.collector_types.get(entry.type)
        found, checked = _entry_problems(i, entry, catalog, plugins, unchecked)
        problems.extend(found)
        # Keys a type's schema could not judge (its type unknown, or settings that do not
        # validate) may hide more problems.
        settings_whole = settings_whole and (
            checked is not None or (ctype is None and not entry.settings)
        )
        accepts = ctype.accepts_kinds if ctype is not None else None
        if not entry.source and accepts is not None:
            passed = [src for src in catalog.values() if src.type not in accepts]
            skipped += [SkippedSource(entry.name, src.name, src.type) for src in passed]
            problems.extend(_no_accepted_source(i, entry, catalog, accepts, plugins, unchecked))
        is_stream = entry.window is not None or (ctype is not None and ctype.unbounded)
        targets = fan_out(entry, catalog, accepts)
        if is_stream:
            problems.extend(_stream_problems(i, entry, targets))
            if ctype is not None:
                stream, values, unset = _stream(i, entry, ctype)
                streams.append(stream)
                provenance += values
                warnings += unset
        else:
            problems.extend(_not_a_stream(i, entry))
            if entry.paginate is not None:
                problems.extend(_position_problems(i, entry))
                values, short = _paginate(i, entry, targets, raw.get("deadline"))
                provenance += values
                warnings += short
        for source, output in targets:
            problems.extend(_output_problems(i, output, produced_by))
            for key in _table_keys(output):
                produced_by.setdefault(key, (i, output))
            if source is not None:
                used_sources.add(source.name)
            # A type's describe sees only a source whose own settings were accepted.
            judged = source is None or source.name not in refused
            if ctype is not None and checked is not None and judged:
                problems.extend(_run_problems(i, checked, source, ctype))
                name = entry.name if is_stream else None
                runs.append(_planned_run(checked, output, source, ctype, stream=name))

    if is_directory is not None:
        problems += _directory_problems(sources, used_sources, is_directory, base)
    warnings += _credential_warnings(sources, used_sources - refused, env, plugins)
    config = None
    if not problems:
        config = ReconConfig(
            catalog=[src for _, src in sources],
            collectors=[entry for _, entry in entries],
            preserve_raw=raw.get("preserve_raw", False),
            deadline=raw.get("deadline"),
        )
    return Plan(
        config=config,
        runs=runs,
        problems=problems,
        warnings=warnings,
        complete=sources_whole and entries_whole and settings_whole and not refused,
        skipped=skipped,
        streams=streams,
        provenance=provenance,
    )


#: The source kinds whose ``url`` is a path on this host, relative to the project root.
_PATH_KINDS = frozenset({"local", "file"})


def _anchored(source: SourceEntry, base: Path | None) -> SourceEntry:
    if source.type not in _PATH_KINDS or base is None or Path(source.url).is_absolute():
        return source
    return source.model_copy(update={"url": str(base / source.url)})


def _planned_run(
    entry: CollectorEntry,
    output: str,
    source: SourceEntry | None,
    ctype: CollectorType,
    *,
    stream: str | None = None,
) -> PlannedRun:
    target = entry if output == entry.name else entry.model_copy(update={"name": output})
    return PlannedRun(
        output=output,
        collector=entry.name,
        type=entry.type,
        source=source.name if source else None,
        type_url=entry.type_url,
        effects=sorted(ctype.effects) if ctype.effects is not None else None,
        detail=ctype.describe(target, source),
        stream=stream,
    )


def _stream(
    i: int, entry: CollectorEntry, ctype: CollectorType
) -> tuple[PlannedStream, list[SourcedValue], list[Problem]]:
    """A stream entry's effective settings, where each came from, and the warning when it
    keeps every window by default."""
    at = f"collectors.{i}"
    values: list[SourcedValue] = []
    if entry.window is None:
        window = WindowSpec(seconds=DEFAULT_WINDOW_S)
        note = f"a {entry.type} collector's records do not end, and the entry sets no window:"
        values.append(SourcedValue(path=f"{at}.window.seconds", value=DEFAULT_WINDOW_S,
                                   origin="recon", note=note))  # fmt: skip
    else:
        window = entry.window
        values += [
            SourcedValue(path=f"{at}.window.{key}", value=value, origin="mission")
            for key, value in window.model_dump(exclude_none=True).items()
        ]
    given = entry.buffer or BufferSpec()
    for key in ("records", "bytes", "max_record_bytes"):
        origin: Origin = "mission" if key in given.model_fields_set else "recon"
        values.append(SourcedValue(path=f"{at}.buffer.{key}", value=getattr(given, key),
                                   origin=origin))  # fmt: skip
    overflow: Overflow
    if given.overflow is not None:
        overflow = given.overflow
        values.append(SourcedValue(path=f"{at}.buffer.overflow", value=overflow,
                                   origin="mission"))  # fmt: skip
    else:
        overflow = default_overflow(ctype.paced_by)
        note = f"the {entry.type} type declares paced_by: {ctype.paced_by}"
        values.append(SourcedValue(path=f"{at}.buffer.overflow", value=overflow, origin="type",
                                   note=note))  # fmt: skip
    retain: Retain = entry.retain if entry.retain is not None else "all"
    shown = retain if isinstance(retain, str) else retain.model_dump(exclude_none=True)
    unset = entry.retain is None
    values.append(
        SourcedValue(
            path=f"{at}.retain",
            value=shown,
            origin="recon" if unset else "mission",
            note="no window is removed unless the mission says so" if unset else "",
        )
    )
    stream = PlannedStream(i, entry.name, window, given.model_copy(update={"overflow": overflow}),
                           overflow, retain)  # fmt: skip
    return stream, values, [_retain_unset(at, entry.name, window)] if unset else []


def _retain_unset(at: str, name: str, window: WindowSpec) -> Problem:
    """The warning for a stream that keeps every window because its entry says nothing."""
    if window.seconds is not None:
        per_day = 86400 / window.seconds
        growth = f"up to {per_day:g} windows a day, one each {window.seconds:g}s"
        keep = f"retain: {{windows: {max(1, int(per_day))}}} keeps a day of them"
    else:
        bound = f"{window.records} records" if window.records else f"{window.bytes} bytes"
        growth = f"a window each {bound}"
        keep = "retain: {windows: 100} keeps the last 100"
    size = f", each at most {window.bytes} bytes of records" if window.bytes else ""
    return Problem(
        f"{at}.retain",
        f"stream {name} keeps every window it seals ({growth}{size}), and nothing removes them",
        kind="retain_unset",
        fix=f"{keep}; retain: {{seconds: 2592000}} keeps 30 days of them; or write retain: all "
        "to keep every window and say so",
    )


def _stream_problems(
    i: int, entry: CollectorEntry, targets: list[tuple[SourceEntry | None, str]]
) -> list[Problem]:
    """What a stream's entry may not hold: more than one run, a deadline, a normalize column
    named as a record's position, or a record larger than its buffer."""
    at = f"collectors.{i}"
    problems = []
    if len(targets) > 1:
        names = ", ".join(source.name for source, _ in targets if source is not None)
        problems.append(
            Problem(
                f"{at}.source",
                f"a stream runs against one source, and collector {entry.name!r} would run "
                f"against {len(targets)}: {names}",
                fix="pin one with source:, and add a collector for each other source to stream",
            )
        )
    if entry.deadline is not None:
        problems.append(
            Problem(
                f"{at}.deadline",
                "a stream's window ends by window:, and as a success; deadline: bounds a "
                "snapshot table, and would fail the window",
                fix="remove deadline:, and bound each window with window: {seconds: N}",
                got=entry.deadline,
            )
        )
    problems += _position_problems(i, entry)
    if entry.paginate is not None:
        problems.append(
            Problem(
                f"{at}.paginate",
                "a stream does not read its source page by page yet: paginate: is a snapshot "
                "table's",
                fix="remove window: to survey it as a snapshot table, or remove paginate:",
            )
        )
    buffer = entry.buffer
    if buffer is not None and buffer.max_record_bytes > buffer.bytes:
        problems.append(
            Problem(
                f"{at}.buffer.max_record_bytes",
                f"a record of up to {buffer.max_record_bytes} bytes is kept, and the buffer "
                f"holds {buffer.bytes}: such a record would fit no buffer",
                fix="raise buffer.bytes to max_record_bytes or more, or lower max_record_bytes",
                got=buffer.max_record_bytes,
            )
        )
    return problems


def _position_problems(i: int, entry: CollectorEntry) -> list[Problem]:
    """A ``normalize:`` column named as the field recon keeps a record's position in, in an
    entry whose records carry one (a stream's, a paginated table's)."""
    if POSITION_FIELD not in (entry.normalize or {}):
        return []
    return [
        Problem(
            f"collectors.{i}.normalize.{POSITION_FIELD}",
            f"{POSITION_FIELD} is the field recon keeps a record's position in its source in "
            "(a stream's, or a paginated table's), and writes beside the columns normalize: makes",
            kind="name_taken",
            fix=f"name the column something else; {POSITION_FIELD} is in every row already",
            got=POSITION_FIELD,
        )
    ]


def _paginate(
    i: int,
    entry: CollectorEntry,
    targets: list[tuple[SourceEntry | None, str]],
    deadline: Any,
) -> tuple[list[SourcedValue], list[Problem]]:
    """A paginated entry's cap and where it came from; for each run, the least seconds its
    crawl takes at its source's rate limit (``(pages - burst) / rps``), against the deadline
    that bounds its table; and a warning for each run whose deadline is shorter than that."""
    spec = entry.paginate
    assert spec is not None
    at = f"collectors.{i}.paginate"
    origin: Origin = "mission" if "pages" in spec.model_fields_set else "recon"
    values = [SourcedValue(path=f"{at}.pages", value=spec.pages, origin=origin)]
    warnings: list[Problem] = []
    # The mission's deadline as written: one that is not a number is its own problem.
    numeric = isinstance(deadline, int | float) and not isinstance(deadline, bool)
    written = [
        (entry.deadline, f"collectors.{i}.deadline"),
        (deadline if numeric else None, "deadline"),
    ]
    bounds = [(seconds, path) for seconds, path in written if seconds is not None]
    for source, output in targets:
        if source is None:
            continue
        rate = source.rate_limit
        floor = crawl_floor_s(spec.pages, burst=rate.burst, rps=rate.rps)
        bound = min(bounds, key=lambda bounded: bounded[0], default=None)
        against = f"the deadline ({bound[1]}: {bound[0]:g}s)" if bound else "no deadline: bounds it"
        note = (
            f"{output}: {spec.pages} page(s) at source {source.name!r}'s rate_limit "
            f"({rate.rps:g} rps, burst {rate.burst}) take at least {floor:g}s; {against}"
        )
        values.append(SourcedValue(path=f"{at}.floor_s", value=floor, origin="recon", note=note))
        if bound is not None and floor > bound[0]:
            warnings.append(
                Problem(
                    bound[1],
                    f"{output} may read {spec.pages} page(s), which take at least {floor:g}s at "
                    f"source {source.name!r}'s rate_limit, past the deadline of {bound[0]:g}s: "
                    "a crawl that long fails its table as transient",
                    kind="deadline_short",
                    fix=f"raise {bound[1]} to {floor:g} or more, lower paginate.pages on "
                    f"collector {entry.name!r}, or raise the source's rate_limit if the API "
                    "allows it",
                    got=bound[0],
                )
            )
    return values, warnings


def _not_a_stream(i: int, entry: CollectorEntry) -> list[Problem]:
    """``buffer:`` and ``retain:`` on an entry that is no stream: they would do nothing."""
    return [
        Problem(
            f"collectors.{i}.{key}",
            f"{key}: is a stream's, and collector {entry.name!r} is not one: its type's records "
            "end, and it sets no window:",
            fix=f"add window: to collect it as a stream, or remove {key}:",
        )
        for key, value in (("buffer", entry.buffer), ("retain", entry.retain))
        if value is not None
    ]


def _ignored(key: Any) -> bool:
    return isinstance(key, str) and key.startswith(IGNORED_KEY_PREFIX)


def _top_level_problems(raw: dict[Any, Any]) -> list[Problem]:
    problems = []
    for key in raw:
        if key not in _TOP_LEVEL_KEYS:
            problems.append(
                Problem(
                    str(key),
                    f"unknown key; a config has: {', '.join(_TOP_LEVEL_KEYS)} "
                    f"(a top-level key beginning with {IGNORED_KEY_PREFIX} is ignored)",
                    kind="unknown_key",
                    fix=f"remove {key!r}, correct its spelling, or prefix it with "
                    f"{IGNORED_KEY_PREFIX} to keep it as a note",
                    got=str(key),
                    allowed=_TOP_LEVEL_KEYS,
                )
            )
    if "collectors" not in raw:
        problems.append(
            Problem(
                "collectors",
                "required: list at least one collector",
                kind="missing_key",
                fix="add `collectors:` with at least one entry",
            )
        )
    elif isinstance(raw["collectors"], list) and not raw["collectors"]:
        problems.append(
            Problem(
                "collectors",
                "empty: list at least one collector",
                kind="missing_key",
                fix="add at least one entry under `collectors:`",
            )
        )
    deadline = raw.get("deadline")
    if deadline is not None and (
        isinstance(deadline, bool)
        or not isinstance(deadline, int | float)
        or not 0 < deadline < float("inf")  # NaN compares false either way
    ):
        problems.append(
            Problem(
                "deadline",
                "must be a finite number of seconds above 0",
                kind="invalid_value",
                fix="deadline: 600 (the seconds the whole survey may take), or remove it",
                got=_scalar(deadline),
            )
        )
    if not isinstance(raw.get("preserve_raw", False), bool):
        problems.append(
            Problem(
                "preserve_raw",
                "must be true or false",
                kind="wrong_shape",
                fix="preserve_raw: true",
                got=_scalar(raw["preserve_raw"]),
                allowed=("true", "false"),
            )
        )
    return problems


def _validate_items[M: BaseModel](
    raw: Any, key: str, model: type[M], problems: list[Problem]
) -> tuple[list[tuple[int, M]], bool]:
    """The entries under ``key`` that parse, and whether every entry did. An entry whose
    only fault is unknown keys is parsed again without them, so its other checks still run."""
    if raw is None:
        return [], True
    if not isinstance(raw, list):
        problems.append(
            Problem(
                key,
                f"must be a list, got {type(raw).__name__}",
                kind="wrong_shape",
                fix=f"make {key}: a list",
            )
        )
        return [], False
    parsed: list[tuple[int, M]] = []
    whole = True
    for i, item in enumerate(raw):
        try:
            parsed.append((i, model.model_validate(item)))
            continue
        except ValidationError as exc:
            problems.extend(_from_pydantic(f"{key}.{i}", model, exc))
            known = _without_unknown_keys(item, exc)
        try:
            parsed.append((i, model.model_validate(known)))
        except ValidationError:
            whole = False
    return parsed, whole


def _without_unknown_keys(item: Any, exc: ValidationError) -> Any:
    """``item`` with every key pydantic refused as unknown taken out."""
    if not isinstance(item, dict):
        return item
    pruned = {**item}
    for err in exc.errors():
        if err["type"] != "extra_forbidden":
            continue
        *parents, last = (part for part in err["loc"] if not _member_label(part))
        holder: Any = pruned
        for part in parents:
            child = holder.get(part) if isinstance(holder, dict) else None
            if isinstance(child, dict):
                child = holder[part] = {**child}
            holder = child
        if isinstance(holder, dict):
            holder.pop(last, None)
    return pruned


def _member_label(part: int | str) -> bool:
    """Whether a part of pydantic's error location names a member of a union, not a key:
    ``literal['all']``, ``function-after[_one_rule(), RetainSpec]``, ``RetainSpec``. A config
    key is lower case and holds no bracket."""
    return isinstance(part, str) and ("[" in part or part[:1].isupper())


def _other_member(err: Mapping[str, Any]) -> bool:
    """Whether an error under a union is the member the value was not written for: ``all``
    refused for a mapping, or a mapping's model refused for a word. Each value gets the
    errors of the shape it has."""
    value = err.get("input")
    if err["type"] == "literal_error":
        return isinstance(value, dict)
    return err["type"] in ("model_type", "dict_type") and isinstance(value, str)


def _from_pydantic(prefix: str, model: type[BaseModel], exc: ValidationError) -> list[Problem]:
    problems = []
    for err in exc.errors():
        if any(_member_label(part) for part in err["loc"]):
            if _other_member(err):
                continue
            err = {**err, "loc": tuple(p for p in err["loc"] if not _member_label(p))}
        path = ".".join([prefix, *(str(part) for part in err["loc"])])
        kind = _problem_kind(err)
        fix = {
            "unknown_key": f"remove {path} or correct its spelling",
            "missing_key": f"set {path}",
        }.get(kind, f"correct {path}")
        problems.append(
            Problem(
                path,
                err["msg"],
                kind=kind,
                fix=fix,
                got=None if kind == "missing_key" else _scalar(err.get("input")),
                allowed=_allowed(model, err),
            )
        )
    return problems


def _problem_kind(err: Mapping[str, Any]) -> str:
    error_type = str(err["type"])
    if error_type in _PYDANTIC_KINDS:
        return _PYDANTIC_KINDS[error_type]
    return "wrong_shape" if error_type.endswith(("_type", "_parsing")) else "invalid_value"


def _allowed(model: type[BaseModel], err: Mapping[str, Any]) -> tuple[str, ...]:
    """The legal values where pydantic's error implies a closed set: the keys a mapping takes,
    or a literal's values."""
    if err["type"] == "extra_forbidden":
        return _keys_at(model, err["loc"][:-1])
    if err["type"] == "literal_error":
        return tuple(_QUOTED.findall(str((err.get("ctx") or {}).get("expected", ""))))
    return ()


def _keys_at(model: type[BaseModel], loc: tuple[int | str, ...]) -> tuple[str, ...]:
    current = model
    for part in loc:
        info = current.model_fields.get(part) if isinstance(part, str) else None
        nested = _model_in(info.annotation) if info is not None else None
        if nested is None:
            return ()
        current = nested
    return tuple(sorted(current.model_fields))


def _model_in(annotation: Any) -> type[BaseModel] | None:
    """The model an annotation names, alone or in an optional."""
    for candidate in (annotation, *get_args(annotation)):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _scalar(value: Any) -> str | int | float | bool | None:
    return value if isinstance(value, str | int | float | bool) else None


def _catalog(
    sources: list[tuple[int, SourceEntry]], problems: list[Problem]
) -> dict[str, SourceEntry]:
    catalog: dict[str, SourceEntry] = {}
    for i, src in sources:
        if src.name in catalog:
            problems.append(
                Problem(
                    f"catalog.{i}.name",
                    f"duplicate source name {src.name!r}",
                    kind="name_taken",
                    fix="give each catalog source its own name",
                    got=src.name,
                )
            )
        catalog[src.name] = src
    return catalog


def _unparsed_names(raw: Any, catalog: Mapping[str, SourceEntry]) -> set[str]:
    """The names of the catalog sources that did not parse: a collector that names one is
    not told it is missing, since the source's own problems are already reported."""
    items = raw if isinstance(raw, list) else []
    names = {item.get("name") for item in items if isinstance(item, dict)}
    return {name for name in names if isinstance(name, str) and name not in catalog}


def _entry_problems(
    i: int,
    entry: CollectorEntry,
    catalog: Mapping[str, SourceEntry],
    plugins: Plugins,
    unchecked: set[str],
) -> tuple[list[Problem], CollectorEntry | None]:
    """The entry's problems, and the entry its type's hooks were run on: without the keys
    found unknown, so the rest is still checked; None when its settings could not be
    checked (an unknown type, or settings its schema refuses for more than unknown keys)."""
    at = f"collectors.{i}"
    problems = []
    ctype = plugins.collector_types.get(entry.type)
    checked: CollectorEntry | None = None
    if ctype is None:
        problems.append(_unknown_type(at, entry.type, plugins))
    else:
        found, checked = _settings_problems(at, entry, ctype)
        problems += found
    if ctype is not None and (mismatch := _kind_problem(at, entry, catalog, ctype, plugins)):
        problems.append(mismatch)
    if entry.source and entry.source not in catalog:
        if entry.source not in unchecked:
            known = ", ".join(sorted(catalog)) or "(empty catalog)"
            problems.append(
                Problem(
                    f"{at}.source",
                    f"source {entry.source!r} not in catalog; have: {known}",
                    kind="name_unknown",
                    fix="name a catalog source, add this one under catalog:, or omit source: "
                    "to fan out over the catalog's sources it can use",
                    got=entry.source,
                    allowed=tuple(sorted(catalog)),
                )
            )
    elif ctype is not None and ctype.requires_source and not entry.source and not catalog:
        problems.append(
            Problem(
                f"{at}.source",
                f"a {entry.type} collector needs a source",
                kind="missing_key",
                fix="add a source under catalog: and name it here (or omit source: to fan out "
                "over the catalog's sources it can use)",
            )
        )
    if ctype is not None and checked is not None:
        for p in ctype.check(checked):
            path = f"{at}.{p.path}" if p.path else at
            problems.append(Problem(path, p.message, p.kind, p.fix, p.got, p.allowed, p.source))
    if entry.extract and ctype is not None and not ctype.extracts:
        problems.append(
            Problem(
                f"{at}.extract",
                f"a {entry.type} collector reads no body, so it takes no extract:",
                kind="unknown_key",
                fix="remove extract:, or shape the records with normalize:",
                got=entry.extract,
                allowed=_entry_keys(ctype),
            )
        )
    elif entry.extract and (problem := path_problem(entry.extract)) is not None:
        problems.append(
            Problem(
                f"{at}.extract", problem, fix="use at most one * in the path", got=entry.extract
            )
        )
    paginate = entry.paginate
    if paginate is not None and ctype is not None and not ctype.paginates:
        problems.append(
            Problem(
                f"{at}.paginate",
                f"a {entry.type} collector does not read its source page by page, so it takes "
                "no paginate:",
                kind="unknown_key",
                fix="remove paginate: (an api collector reads pages)",
                allowed=_entry_keys(ctype),
            )
        )
    elif paginate is not None and paginate.next and (problem := path_problem(paginate.next)):
        problems.append(
            Problem(
                f"{at}.paginate.next",
                problem,
                fix="use at most one * in the path",
                got=paginate.next,
            )
        )
    transforms = tuple(sorted(f"${name}" for name in plugins.transforms))
    for column, message in spec_problems(entry.normalize or {}, plugins.transforms).items():
        unknown = message.startswith("unknown transform")
        problems.append(
            Problem(
                f"{at}.normalize.{column}",
                message,
                kind="name_unknown" if unknown else "invalid_value",
                fix="use an installed transform (recon status --json lists them) and at most "
                "one * per path",
                got=_scalar((entry.normalize or {}).get(column)),
                allowed=transforms if unknown else (),
            )
        )
    return problems, checked


def _run_problems(
    i: int, entry: CollectorEntry, source: SourceEntry | None, ctype: CollectorType
) -> list[Problem]:
    """The problems the entry's type finds in one of its runs, at paths under the entry."""
    at = f"collectors.{i}"
    return [
        Problem(
            f"{at}.{p.path}" if p.path else at, p.message, p.kind, p.fix, p.got, p.allowed, p.source
        )  # fmt: skip
        for p in ctype.check_run(entry, source)
    ]


def _kind_problem(
    at: str,
    entry: CollectorEntry,
    catalog: Mapping[str, SourceEntry],
    ctype: CollectorType,
    plugins: Plugins,
) -> Problem | None:
    """A problem when the entry is pinned to a source of a kind its type cannot use. A
    source of a kind nothing installed adds has its own problem, and gets no second one."""
    source = catalog.get(entry.source) if entry.source else None
    kinds = ctype.accepts_kinds
    if source is None or kinds is None or source.type in kinds:
        return None
    if source.type not in source_kinds(plugins):
        return None
    usable = tuple(name for name, src in catalog.items() if src.type in kinds)
    named = f"one of: {', '.join(usable)}" if usable else "one you add under catalog:"
    return Problem(
        f"{at}.source",
        f"a {entry.type} collector uses a source of kind {', '.join(sorted(kinds))}, and "
        f"{source.name!r} is a {source.type} source",
        kind="invalid_value",
        fix=f"pin a source of a kind it uses ({named}), or use a collector type for a "
        f"{source.type} source",
        got=source.name,
        allowed=usable,
    )


def _settings_problems(
    at: str, entry: CollectorEntry, ctype: CollectorType
) -> tuple[list[Problem], CollectorEntry | None]:
    """The problems in the keys that are the type's own, validated by its schema, and the
    entry to run its hooks on: without the keys found unknown, or None when its settings do
    not validate even without them."""
    settings = entry.settings
    schema = ctype.settings
    if schema is None:  # the type takes no key of its own: each is unknown
        problems = [_unknown_key(at, key, value, ctype) for key, value in settings.items()]
        return problems, _with_settings(entry, {})
    try:
        schema.model_validate(settings)
        return [], entry
    except ValidationError as exc:
        problems = [
            replace(problem, allowed=_entry_keys(ctype))
            if problem.kind == "unknown_key" and problem.path.count(".") == at.count(".") + 1
            else problem
            for problem in _from_pydantic(at, schema, exc)
        ]
        known = _without_unknown_keys(settings, exc)
    try:
        schema.model_validate(known)
    except ValidationError:
        return problems, None
    return problems, _with_settings(entry, known)


def _unknown_key(at: str, key: str, value: Any, ctype: CollectorType) -> Problem:
    return Problem(
        f"{at}.{key}",
        "unknown key: this collector type takes no such setting",
        kind="unknown_key",
        fix=f"remove {at}.{key} or correct its spelling",
        got=_scalar(value),
        allowed=_entry_keys(ctype),
    )


def _entry_keys(ctype: CollectorType) -> tuple[str, ...]:
    """Every key an entry of this type may hold: recon's own and the type's settings."""
    own = set(ctype.settings.model_fields) if ctype.settings is not None else set()
    shared = set(CollectorEntry.model_fields) - (set() if ctype.extracts else {"extract"})
    shared -= set() if ctype.paginates else {"paginate"}
    return tuple(sorted(shared | own))


def _with_settings(entry: CollectorEntry, settings: dict[str, Any]) -> CollectorEntry:
    """``entry`` with ``settings`` as its type's own keys."""
    shared = entry.model_dump(include=set(CollectorEntry.model_fields), exclude_unset=True)
    return CollectorEntry.model_validate({**shared, **settings})


def source_kinds(plugins: Plugins) -> tuple[str, ...]:
    """The source kinds installed: those the installed collector types add."""
    return tuple(
        sorted({kind for ct in plugins.collector_types.values() for kind in ct.source_kinds})
    )


def _kind_types(plugins: Plugins) -> dict[str, CollectorType]:
    """The installed type that adds each source kind, recon's own first (the registry's
    order), the first one when two add the same kind."""
    adders: dict[str, CollectorType] = {}
    for ct in plugins.collector_types.values():
        for kind in sorted(ct.source_kinds):
            adders.setdefault(kind, ct)
    return adders


def _source_problems(
    sources: list[tuple[int, SourceEntry]], plugins: Plugins
) -> tuple[list[Problem], set[str]]:
    """The problems in each source's kind and in the keys that are its kind's own, and the
    sources that could not be judged whole: one of a kind nothing installed adds that has
    keys of its own, or whose own keys its kind's schema refuses, may hide more problems,
    and no type's hook is shown it."""
    installed = source_kinds(plugins)
    adders = _kind_types(plugins)
    problems: list[Problem] = []
    refused: set[str] = set()
    for i, src in sources:
        at = f"catalog.{i}"
        ctype = adders.get(src.type)
        if ctype is None:
            problems.append(
                Problem(
                    f"{at}.type",
                    f"unknown source kind {src.type!r}; installed: "
                    f"{', '.join(installed) or '(none)'}",
                    kind="name_unknown",
                    fix=f"use one of: {', '.join(installed)}, or install the collector type "
                    f"that adds {src.type!r} sources",
                    got=src.type,
                    allowed=installed,
                )
            )
            if src.settings:
                refused.add(src.name)
            continue
        schema = ctype.source_settings
        if schema is None:
            allowed = _source_keys(None)
            for key, value in src.settings.items():
                problems.append(
                    Problem(
                        f"{at}.{key}",
                        f"unknown key: a {src.type} source takes no such setting",
                        kind="unknown_key",
                        fix=f"remove {at}.{key} or correct its spelling",
                        got=_scalar(value),
                        allowed=allowed,
                    )
                )
            if not src.url:
                problems.append(
                    Problem(
                        f"{at}.url",
                        f"a {src.type} source needs url:",
                        kind="missing_key",
                        fix=f"set {at}.url",
                    )
                )
            continue
        try:
            schema.model_validate(src.settings)
        except ValidationError as exc:
            problems += [
                replace(problem, allowed=_source_keys(schema))
                if problem.kind == "unknown_key" and problem.path.count(".") == at.count(".") + 1
                else problem
                for problem in _from_pydantic(at, schema, exc)
            ]
            refused.add(src.name)
            continue
        for p in ctype.check_source(src):
            path = f"{at}.{p.path}" if p.path else at
            problems.append(Problem(path, p.message, p.kind, p.fix, p.got, p.allowed, p.source))
    return problems, refused


def _source_keys(schema: type[BaseModel] | None) -> tuple[str, ...]:
    """Every key a source may hold: the ones every source has and its kind's own."""
    own = set(schema.model_fields) if schema is not None else set()
    return tuple(sorted(set(SourceEntry.model_fields) | own))


def _unknown_type(at: str, name: str, plugins: Plugins) -> Problem:
    broken = {b.name: b for b in plugins.broken if b.group == "recon.collectors"}
    installed = tuple(sorted(plugins.collector_types))
    if name in broken:
        message = f"collector type {name!r} is installed but failed to load: {broken[name].error}"
        fix = "fix or reinstall the package that registers it (recon status --json shows why)"
    else:
        message = f"unknown collector type {name!r}; installed: {', '.join(installed) or '(none)'}"
        fix = f"use one of: {', '.join(installed) or '(none)'}"
    return Problem(f"{at}.type", message, kind="name_unknown", fix=fix, got=name, allowed=installed)


def _table_keys(output: str) -> tuple[str, str]:
    """What two outputs may not share: the name SQL knows the table by, which ignores case,
    and the table's file name as a filesystem that ignores case sees it (macOS's default)."""
    return f"view:{table_name(output).lower()}", f"file:{output.casefold()}"


def _output_problems(
    i: int, output: str, produced_by: Mapping[str, tuple[int, str]]
) -> list[Problem]:
    at = f"collectors.{i}.name"
    problems = []
    if any(part in output for part in _NOT_IN_A_FILE_NAME):
        problems.append(
            Problem(
                at,
                f"output {output!r} is not one file name: it names the table's file in the "
                f"archive, so it holds no {', no '.join(_NOT_IN_A_FILE_NAME)}",
                fix="rename the collector (or, for an output <collector>-<source>, the source)",
                got=output,
            )
        )
    elif not output[:1].isalnum() and not output.startswith("_"):
        problems.append(
            Problem(
                at,
                f"output {output!r} must start with a letter, a digit or _: it names the "
                "table's file in the archive, which would otherwise be hidden or empty-named",
                fix="rename the collector",
                got=output,
            )
        )
    if output.casefold() in RESERVED_OUTPUT_NAMES:
        problems.append(
            Problem(
                at,
                f"output {output!r} is reserved for a file recon writes into every archive",
                kind="name_taken",
                fix="rename the collector",
                got=output,
            )
        )
    elif suffix := next((s for s in SIDECARS if output.casefold().endswith(f".{s}")), None):
        problems.append(
            Problem(
                at,
                f"output {output!r} is named as recon names a table's {suffix} file, which it "
                f"writes beside the table (<table>.{suffix}.jsonl)",
                kind="name_taken",
                fix="rename the collector",
                got=output,
            )
        )
    taken = next((produced_by[key] for key in _table_keys(output) if key in produced_by), None)
    if taken is not None and taken[1] == output:
        problems.append(
            Problem(
                at,
                f"output {output}.jsonl is also produced by collectors.{taken[0]}",
                kind="name_taken",
                fix="rename one of the two collectors so each output is unique",
                got=output,
            )
        )
    elif taken is not None:
        problems.append(
            Problem(
                at,
                f"output {output!r} would be one table with the output of collectors.{taken[0]} "
                f"({taken[1]!r}): SQL knows both as {table_name(output)}, ignoring case, and a "
                "filesystem that ignores case (macOS's) would keep one file of the two",
                kind="name_taken",
                fix="rename one of the two collectors so the names differ in more than case and "
                "in more than the characters that are not a letter, a digit or _",
                got=output,
            )
        )
    return problems


def plugin_warning(broken: BrokenPlugin) -> Problem:
    """The warning for a plugin that failed to load or to build."""
    return Problem(
        "",
        f"plugin {broken.describe()}",
        kind="plugin_broken",
        fix="fix or reinstall the package that registers it (recon status --json shows why)",
        got=f"{broken.group}:{broken.name}",
    )


def _no_accepted_source(
    i: int,
    entry: CollectorEntry,
    catalog: Mapping[str, SourceEntry],
    accepts: frozenset[str],
    plugins: Plugins,
    unchecked: set[str],
) -> list[Problem]:
    """A problem when an unpinned collector's non-empty catalog holds no source of a kind its
    type accepts, so it would run against nothing (owner ruling 2026-10-22). A catalog that
    may hold one the plan could not judge (a source that did not parse, or of a kind nothing
    installed adds, each its own problem) gets no second one."""
    if not catalog or any(src.type in accepts for src in catalog.values()):
        return []
    if unchecked or any(src.type not in source_kinds(plugins) for src in catalog.values()):
        return []
    kinds = ", ".join(sorted(accepts))
    there = ", ".join(f"{src.name} ({src.type})" for src in catalog.values())
    root = " (a local source's url: . is the project root)" if "local" in accepts else ""
    return [
        Problem(
            f"collectors.{i}.source",
            f"a {entry.type} collector runs against sources of kind {kinds}, and the catalog "
            f"has none: {there}",
            kind="missing_key",
            fix=f"add a source of kind {kinds} under catalog:{root}, or remove the collector",
        )
    ]


def _directory_problems(
    sources: list[tuple[int, SourceEntry]],
    used: set[str],
    is_directory: Callable[[Path], bool],
    base: Path | None,
) -> list[Problem]:
    relative = f"; a relative url is relative to the project root, {base}" if base else ""
    return [
        Problem(
            f"catalog.{i}.url",
            f"{src.url} is not a directory; a local source names the directory its commands run in",
            fix=f"create the directory, or correct url:{relative}",
            got=src.url,
        )
        for i, src in sources
        if src.type == "local" and src.name in used and not is_directory(Path(src.url))
    ]


def _credential_warnings(
    sources: list[tuple[int, SourceEntry]],
    used: set[str],
    env: Mapping[str, str],
    plugins: Plugins,
) -> list[Problem]:
    """A warning for each variable a used source names, as ``auth.token: env:NAME`` or
    through its kind's own settings (``CollectorType.source_env``), that ``env`` does not set."""
    adders = _kind_types(plugins)
    warnings = []
    for i, src in sources:
        if src.name not in used:
            continue
        named = env_name(src.auth.token)
        if named is not None and not env.get(named):
            warnings.append(
                Problem(
                    f"catalog.{i}.auth.token",
                    f"{named} is not set; requests to {src.name!r} will be sent without "
                    "credentials",
                    kind="env_unset",
                    fix=f"set {named}, or leave it unset to send the requests without it",
                    got=named,
                )
            )
        ctype = adders.get(src.type)
        if ctype is None or ctype.source_settings is None:
            continue
        for name, where in ctype.source_env(src).items():
            if not env.get(name):
                warnings.append(
                    Problem(
                        f"catalog.{i}.{where}",
                        f"{name} is not set; {src.name!r} will be reached without it",
                        kind="env_unset",
                        fix=f"set {name}, or leave it unset to reach {src.name!r} without it",
                        got=name,
                    )
                )
    return warnings
