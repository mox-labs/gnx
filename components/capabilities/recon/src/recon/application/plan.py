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
(``CollectorType.source_kinds``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

from pydantic import BaseModel, ValidationError

from recon.domain.archives import EVENT_LOG_NAME, table_name
from recon.domain.capture import CAPTURE_LOG_NAME
from recon.domain.errors import Problem
from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
from recon.domain.normalize import path_problem, spec_problems

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from recon.domain.collector import BrokenPlugin, CollectorType, Plugins

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

    def document(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "file": f"{self.output}.jsonl",
            "collector": self.collector,
            "type": self.type,
            "source": self.source,
            "type_url": self.type_url,
            "effects": self.effects if self.effects is not None else "unknown",
            **self.detail,
        }


@dataclass(frozen=True)
class Plan:
    """``complete`` is false when part of the config could not be checked, so more problems
    may follow a fix."""

    config: ReconConfig | None
    runs: list[PlannedRun] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    complete: bool = True

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
    entry: CollectorEntry, catalog: Mapping[str, SourceEntry]
) -> list[tuple[SourceEntry | None, str]]:
    """The (source, output name) pairs a collector entry runs against.

    Pinned (``source:``) → that source only, output named after the collector. No source
    and a non-empty catalog → every catalog entry, output ``<collector>-<source>``. No
    catalog → once, with no source. A pinned source missing from the catalog yields
    nothing; the planner reports it.
    """
    if entry.source:
        src = catalog.get(entry.source)
        return [(src, entry.name)] if src is not None else []
    if catalog:
        return [(src, f"{entry.name}-{src.name}") for src in catalog.values()]
    return [(None, entry.name)]


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
    problems += _source_kind_problems(sources, plugins)
    catalog = _catalog(sources, problems)
    unchecked = _unparsed_names(raw.get("catalog"), catalog) if not sources_whole else set()

    runs: list[PlannedRun] = []
    produced_by: dict[str, tuple[int, str]] = {}
    used_sources: set[str] = set()
    settings_whole = True
    for i, entry in entries:
        ctype = plugins.collector_types.get(entry.type)
        found, checked = _entry_problems(i, entry, catalog, plugins, unchecked)
        problems.extend(found)
        # Keys a type's schema could not judge (its type unknown, or settings that do not
        # validate) may hide more problems.
        settings_whole = settings_whole and (
            checked is not None or (ctype is None and not entry.settings)
        )
        for source, output in fan_out(entry, catalog):
            problems.extend(_output_problems(i, output, produced_by))
            for key in _table_keys(output):
                produced_by.setdefault(key, (i, output))
            if source is not None:
                used_sources.add(source.name)
            if ctype is not None and checked is not None:
                runs.append(_planned_run(checked, output, source, ctype))

    if is_directory is not None:
        problems += _directory_problems(sources, used_sources, is_directory, base)
    warnings += _credential_warnings(sources, used_sources, env)
    warnings += _mixed_fan_out_warnings(entries, catalog)
    config = None
    if not problems:
        config = ReconConfig(
            catalog=[src for _, src in sources],
            collectors=[entry for _, entry in entries],
            preserve_raw=raw.get("preserve_raw", False),
        )
    return Plan(
        config=config,
        runs=runs,
        problems=problems,
        warnings=warnings,
        complete=sources_whole and entries_whole and settings_whole,
    )


def _anchored(source: SourceEntry, base: Path | None) -> SourceEntry:
    if source.type != "local" or base is None or Path(source.url).is_absolute():
        return source
    return source.model_copy(update={"url": str(base / source.url)})


def _planned_run(
    entry: CollectorEntry, output: str, source: SourceEntry | None, ctype: CollectorType
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
    )


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
        *parents, last = err["loc"]
        holder: Any = pruned
        for part in parents:
            child = holder.get(part) if isinstance(holder, dict) else None
            if isinstance(child, dict):
                child = holder[part] = {**child}
            holder = child
        if isinstance(holder, dict):
            holder.pop(last, None)
    return pruned


def _from_pydantic(prefix: str, model: type[BaseModel], exc: ValidationError) -> list[Problem]:
    problems = []
    for err in exc.errors():
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
    if entry.source and entry.source not in catalog:
        if entry.source not in unchecked:
            known = ", ".join(sorted(catalog)) or "(empty catalog)"
            problems.append(
                Problem(
                    f"{at}.source",
                    f"source {entry.source!r} not in catalog; have: {known}",
                    kind="name_unknown",
                    fix="name a catalog source, add this one under catalog:, or omit source: "
                    "to fan out over the catalog",
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
                "over the catalog)",
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


def _source_kind_problems(
    sources: list[tuple[int, SourceEntry]], plugins: Plugins
) -> list[Problem]:
    installed = source_kinds(plugins)
    return [
        Problem(
            f"catalog.{i}.type",
            f"unknown source kind {src.type!r}; installed: {', '.join(installed) or '(none)'}",
            kind="name_unknown",
            fix=f"use one of: {', '.join(installed)}, or install the collector type that adds "
            f"{src.type!r} sources",
            got=src.type,
            allowed=installed,
        )
        for i, src in sources
        if src.type not in installed
    ]


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


def _mixed_fan_out_warnings(
    entries: list[tuple[int, CollectorEntry]], catalog: Mapping[str, SourceEntry]
) -> list[Problem]:
    """A collector with no ``source:`` runs against every catalog source. When those are of
    different types, adding a source to the mission has most likely fanned it out over one
    it was not written for: a command run in a web source, a URL asked of a directory."""
    types = sorted({src.type for src in catalog.values()})
    if len(types) < 2:
        return []
    names = ", ".join(f"{src.name} ({src.type})" for src in catalog.values())
    return [
        Problem(
            f"collectors.{i}.source",
            f"no source: pinned, so this {entry.type} collector runs against every catalog "
            f"source, and they are of different types: {names}",
            kind="fan_out_mixed",
            fix=f"pin it with source: <name> (one of: {', '.join(catalog)}), or keep it "
            "unpinned if it is meant for every one",
            allowed=tuple(catalog),
        )
        for i, entry in entries
        if not entry.source
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
    sources: list[tuple[int, SourceEntry]], used: set[str], env: Mapping[str, str]
) -> list[Problem]:
    return [
        Problem(
            f"catalog.{i}.auth.env",
            f"{src.auth.env} is not set; requests to {src.name!r} will be sent without credentials",
            kind="env_unset",
            fix=f"set {src.auth.env}, or leave it unset to send the requests without it",
            got=src.auth.env,
        )
        for i, src in sources
        if src.name in used and src.auth.env and not env.get(src.auth.env)
    ]
