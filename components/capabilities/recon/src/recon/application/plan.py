"""Planning — validate a mission config and resolve every run it would make, with no effects.

``build_plan`` is what ``survey --dry-run`` prints and what ``survey`` checks before it
creates an archive. It reports *every* problem it can find, each located by a path into
the config (``collectors[2].normalize.title``), rather than stopping at the first.

Pure: no network, no subprocess, no filesystem. Collector types contribute through their
``CollectorType.check`` and ``describe`` hooks, which are pure by contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from recon.application.transforms import spec_problems
from recon.domain.capture import CAPTURE_LOG_NAME
from recon.domain.exceptions import Problem
from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from recon.domain.collector import CollectorType, Transform

_TOP_LEVEL_KEYS = frozenset(ReconConfig.model_fields)

#: Output names that would overwrite a file recon itself writes into an archive.
RESERVED_OUTPUT_NAMES = frozenset({CAPTURE_LOG_NAME.removesuffix(".jsonl")})


@dataclass(frozen=True)
class BrokenPlugin:
    """An entry point that could not be loaded. Reported, never fatal to other plugins."""

    group: str
    name: str
    value: str
    error: str

    def describe(self) -> str:
        return f"{self.group}:{self.name} ({self.value}) failed to load: {self.error}"


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

    def as_dict(self) -> dict[str, Any]:
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
    config: ReconConfig | None
    runs: list[PlannedRun] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.problems and self.config is not None

    def effects(self) -> list[str]:
        """The union of declared effects; ``unknown`` when any run's type did not declare."""
        out: set[str] = set()
        for run in self.runs:
            out.update(run.effects if run.effects is not None else ["unknown"])
        return sorted(out)


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


def _loc_path(prefix: str, loc: tuple[int | str, ...]) -> str:
    path = prefix
    for part in loc:
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
    return path


def _validate_items(
    raw: Any, key: str, model: type[BaseModel], problems: list[Problem]
) -> list[tuple[int, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append(Problem(key, f"must be a list, got {type(raw).__name__}"))
        return []
    parsed: list[tuple[int, Any]] = []
    for i, item in enumerate(raw):
        try:
            parsed.append((i, model.model_validate(item)))
        except ValidationError as exc:
            for err in exc.errors():
                problems.append(Problem(_loc_path(f"{key}[{i}]", err["loc"]), err["msg"]))
    return parsed


def build_plan(
    raw: Any,
    *,
    collector_types: Mapping[str, CollectorType],
    transforms: Mapping[str, Transform],
    broken: list[BrokenPlugin] | None = None,
    env: Mapping[str, str] | None = None,
) -> Plan:
    """Validate ``raw`` (the YAML-loaded config) and resolve its runs.

    Each catalog and collector entry is validated on its own, so one malformed entry does
    not hide the problems in the others.
    """
    broken = broken or []
    env = env if env is not None else {}
    problems: list[Problem] = []
    warnings = [f"plugin {b.describe()}" for b in broken]

    if not isinstance(raw, dict):
        got = "an empty file" if raw is None else type(raw).__name__
        problems.append(Problem("", f"a config must be a mapping with collectors:, got {got}"))
        return Plan(config=None, problems=problems, warnings=warnings)

    for key in raw:
        if key not in _TOP_LEVEL_KEYS:
            allowed = ", ".join(sorted(_TOP_LEVEL_KEYS))
            problems.append(Problem(str(key), f"unknown key; a config has: {allowed}"))
    if "collectors" not in raw:
        problems.append(Problem("collectors", "required: list at least one collector"))
    elif isinstance(raw["collectors"], list) and not raw["collectors"]:
        problems.append(Problem("collectors", "empty: list at least one collector"))
    preserve_raw = raw.get("preserve_raw", False)
    if not isinstance(preserve_raw, bool):
        problems.append(Problem("preserve_raw", "must be true or false"))

    sources: list[tuple[int, SourceEntry]] = _validate_items(
        raw.get("catalog"), "catalog", SourceEntry, problems
    )
    entries: list[tuple[int, CollectorEntry]] = _validate_items(
        raw.get("collectors"), "collectors", CollectorEntry, problems
    )

    catalog: dict[str, SourceEntry] = {}
    for i, src in sources:
        if src.name in catalog:
            problems.append(Problem(f"catalog[{i}].name", f"duplicate source name {src.name!r}"))
        catalog[src.name] = src

    broken_types = {b.name: b for b in broken if b.group == "recon.collectors"}
    installed = ", ".join(sorted(collector_types)) or "(none)"
    runs: list[PlannedRun] = []
    produced_by: dict[str, int] = {}
    used_sources: set[str] = set()

    for i, entry in entries:
        at = f"collectors[{i}]"
        ctype = collector_types.get(entry.type)
        if ctype is None:
            if entry.type in broken_types:
                msg = f"collector type {entry.type!r} is installed but failed to load: "
                msg += broken_types[entry.type].error
            else:
                msg = f"unknown collector type {entry.type!r}; installed: {installed}"
            problems.append(Problem(f"{at}.type", msg))

        if entry.source and entry.source not in catalog:
            known = ", ".join(sorted(catalog)) or "(empty catalog)"
            problems.append(
                Problem(f"{at}.source", f"source {entry.source!r} not in catalog; have: {known}")
            )
        elif ctype is not None and ctype.requires_source and not entry.source and not catalog:
            problems.append(
                Problem(
                    f"{at}.source",
                    f"a {entry.type} collector needs a source: add one under catalog: "
                    "and name it here (or omit source: to fan out over the catalog)",
                )
            )

        if ctype is not None:
            problems.extend(
                Problem(f"{at}.{p.path}" if p.path else at, p.message) for p in ctype.check(entry)
            )
        if entry.normalize:
            for column, message in spec_problems(entry.normalize, transforms).items():
                problems.append(Problem(f"{at}.normalize.{column}", message))

        for source, output in fan_out(entry, catalog):
            if output in RESERVED_OUTPUT_NAMES:
                problems.append(
                    Problem(f"{at}.name", f"output {output!r} is reserved for the capture log")
                )
            if output in produced_by:
                j = produced_by[output]
                problems.append(
                    Problem(
                        f"{at}.name",
                        f"output {output}.jsonl is also produced by collectors[{j}]: "
                        "rename one so each output is unique",
                    )
                )
            produced_by.setdefault(output, i)
            if source is not None:
                used_sources.add(source.name)
            if ctype is None:
                continue
            target = entry if output == entry.name else entry.model_copy(update={"name": output})
            runs.append(
                PlannedRun(
                    output=output,
                    collector=entry.name,
                    type=entry.type,
                    source=source.name if source else None,
                    type_url=entry.type_url,
                    effects=sorted(ctype.effects) if ctype.effects is not None else None,
                    detail=ctype.describe(target, source),
                )
            )

    for i, src in sources:
        if src.name in used_sources and src.auth.env and not env.get(src.auth.env):
            warnings.append(
                f"catalog[{i}].auth.env: {src.auth.env} is not set; "
                f"requests to {src.name!r} will be sent without credentials"
            )

    config: ReconConfig | None = None
    if not problems:
        config = ReconConfig.model_validate(raw)
    return Plan(config=config, runs=runs, problems=problems, warnings=warnings)
