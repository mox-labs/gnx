"""Loading: documents read by a source, validated against the type the caller names.

A :class:`~matrix.domain.ports._out.document_source.DocumentSource` reads plain data and
knows nothing of types; a :class:`Loader` takes the pydantic model from its caller and does
one of three things with what the source read:

* :meth:`Loader.one`: one document is one model (an experiment file, a run's config);
* :meth:`Loader.each`: every document in one or more folders is one model each (agent
  definitions, probes, declarations), a name defined twice refused;
* :meth:`Loader.merge`: documents layered as tiers, later tiers winning, are one model per
  section (settings: what :func:`matrix.load_config` reads).

Each validates once, and reports **every** problem as a
:class:`~matrix.domain.problems.Problem` with its key path and the file it is in, in one
:class:`~matrix.domain.errors.ConfigError`. What a key means, the ``body`` of a markdown file
included, is the model's business: a field takes it by name or alias.

Two markers on a model's top-level fields change how a value is placed, never what it means:
:class:`BesideFile` resolves a relative path against the folder of the file that wrote it,
and :class:`Accumulate` keeps a list from every tier rather than the last one's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from matrix.domain.errors import NotFoundError, invalid
from matrix.domain.problems import from_validation_error, problem

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from matrix.domain.ports._out.document_source import Document, DocumentSource
    from matrix.domain.problems import Problem

M = TypeVar("M", bound=BaseModel)

#: The suffixes :meth:`Loader.each` reads from a folder unless told otherwise.
DOCUMENT_SUFFIXES = (".yaml", ".yml", ".md")


@dataclass(frozen=True)
class BesideFile:
    """Field metadata: a relative path in this field (a string, or each string of a list) is
    resolved against the folder of the file that wrote it, so a document means the same
    wherever it is read from. ``Annotated[tuple[str, ...], BesideFile()]``."""


@dataclass(frozen=True)
class Accumulate:
    """Field metadata for :meth:`Loader.merge`: this list keeps the entries of every tier,
    in tier order and each once, instead of the last tier's list."""


@dataclass(frozen=True)
class Merged:
    """What :meth:`Loader.merge` produced: each section's model, and every location
    consulted, lowest priority first, each marked when it was absent or empty."""

    sections: dict[str, BaseModel]
    sources: tuple[str, ...]
    #: Dotted key path -> the file that set it: what a later check names a problem's file by.
    origins: dict[str, str]


def _marked(model: type[BaseModel], marker: type) -> list[str]:
    """The keys of ``model``'s top-level fields that carry ``marker``."""
    return [
        field.alias or name
        for name, field in model.model_fields.items()
        if any(isinstance(item, marker) for item in field.metadata)
    ]


class Loader:
    """Validates what ``source`` reads against the models its callers name."""

    def __init__(self, source: DocumentSource) -> None:
        self._source = source

    @property
    def source(self) -> DocumentSource:
        return self._source

    def one(self, model: type[M], location: Any) -> M:  # noqa: UP047
        """The document at ``location`` as a ``model``.

        Raises NotFoundError when nothing is there, and ConfigError listing every problem
        when the document cannot be read or does not validate.
        """
        found = self._source.document(location)
        if found is None:
            raise NotFoundError(
                f"there is no document at {location}",
                fix="pass the path of an existing file, or - for standard input",
                path=str(location),
            )
        if found.problem is not None:
            raise invalid(f"{found.source} cannot be read", [found.problem], complete=False)
        data = self._placed(model, found.data or {}, found)
        try:
            return model.model_validate(data)
        except ValidationError as e:
            problems = from_validation_error(e, source=found.source)
            raise invalid(f"{found.source} is not a valid {model.__name__}", problems) from None

    def each(  # noqa: UP047
        self,
        model: type[M],
        *locations: Any,
        key: str | None = "name",
        name_from_file: bool = False,
        suffixes: Collection[str] = DOCUMENT_SUFFIXES,
    ) -> tuple[M, ...]:
        """Every document in ``locations`` (folders, or files as folders of one) as a
        ``model``, in location order and then file-name order.

        ``key`` names the field two documents may not share (None: no such field);
        ``name_from_file`` fills it from the file's stem where a document leaves it out.
        Raises NotFoundError for a location with nothing there, and ConfigError listing every
        problem in every document.
        """
        loaded: list[M] = []
        problems: list[Problem] = []
        seen: dict[Any, str] = {}
        for location in locations:
            found = self._source.documents(location, suffixes)
            if found is None:
                raise NotFoundError(
                    f"there is no folder or file at {location}",
                    fix="create it, or correct the path",
                    path=str(location),
                )
            for document in found:
                if document.problem is not None:
                    problems.append(document.problem)
                    continue
                data = self._placed(model, document.data or {}, document)
                if key is not None and name_from_file:
                    data.setdefault(key, document.stem)
                try:
                    item = model.model_validate(data)
                except ValidationError as e:
                    problems += from_validation_error(e, source=document.source)
                    continue
                name = getattr(item, key, None) if key is not None else None
                if name is not None and name in seen:
                    problems.append(
                        problem(
                            key or "",
                            "name_taken",
                            f"{key} {name!r} is also defined in {seen[name]}",
                            fix=f"rename one of the two, or remove one of {seen[name]} and "
                            f"{document.source}",
                            got=name,
                            source=document.source,
                        )
                    )
                    continue
                if name is not None:
                    seen[name] = document.source
                loaded.append(item)
        if problems:
            raise invalid(f"the documents are not valid {model.__name__}s", problems)
        return tuple(loaded)

    def merge(self, sections: Mapping[str, type[BaseModel]], locations: Sequence[Any]) -> Merged:
        """The ``sections`` of the documents at ``locations``, layered lowest priority first:
        mappings merge key by key, anything else is replaced by a later tier, and a field
        marked :class:`Accumulate` keeps every tier's entries. A location with nothing there
        is an empty tier.

        Raises ConfigError listing every problem, each with the file that set the value it
        is about; ``complete`` is False when a file could not be read, since what it would
        have set was never checked.
        """
        merged: dict[str, Any] = {}
        origins: dict[tuple[str, ...], str] = {}
        consulted: list[str] = []
        unreadable: list[Problem] = []
        #: (section, field) -> each tier's entries of an accumulated field, with its file
        kept: dict[tuple[str, str], list[tuple[Any, str]]] = {}
        #: (section, field) -> an accumulated field a tier wrote as something else, and where
        misshapen: dict[tuple[str, str], tuple[Any, str]] = {}
        for location in locations:
            found = self._source.document(location)
            if found is None:
                consulted.append(f"{location} (absent)")
                continue
            consulted.append(found.source + ("" if found.data else " (empty)"))
            if found.problem is not None:
                unreadable.append(found.problem)
                continue
            tier: dict[str, Any] = dict(found.data or {})
            for section, model in sections.items():
                part = tier.get(section)
                if not isinstance(part, dict):
                    continue  # absent, or the wrong shape, which validation reports
                part = self._placed(model, part, found)
                for name in _marked(model, Accumulate):
                    if name not in part:
                        continue
                    value = part.pop(name)
                    if isinstance(value, list):
                        entries = kept.setdefault((section, name), [])
                        entries += [(entry, found.source) for entry in value]
                    else:  # kept for validation to report, never overwritten by the others
                        misshapen.setdefault((section, name), (value, found.source))
                tier[section] = part
            _merge(merged, tier, origins, found.source, ())
        if unreadable:
            raise invalid("the config cannot be read", unreadable, complete=False)
        for (section, name), entries in kept.items():
            _accumulated(merged.setdefault(section, {}), section, name, entries, origins)
        for (section, name), (value, source) in misshapen.items():
            merged.setdefault(section, {})[name] = value
            origins[(section, name)] = source

        def source_of(path: tuple[str, ...]) -> str:
            for end in range(len(path), 0, -1):
                if path[:end] in origins:
                    return origins[path[:end]]
            return ""

        validated: dict[str, BaseModel] = {}
        problems: list[Problem] = []
        for section, model in sections.items():
            try:
                validated[section] = model.model_validate(merged.get(section) or {})
            except ValidationError as e:
                problems += from_validation_error(e, prefix=section, sources=source_of)
        if problems:
            raise invalid(
                "invalid config",
                problems,
                fix="fix each problem listed; `matrix config --sources` shows which file set "
                "a value",
                sources=consulted,
            )
        return Merged(
            sections=validated,
            sources=tuple(consulted),
            origins={".".join(path): file for path, file in origins.items()},
        )

    def _placed(
        self, model: type[BaseModel], data: dict[str, Any], found: Document
    ) -> dict[str, Any]:
        """``data`` with the paths in ``model``'s :class:`BesideFile` fields resolved."""
        placed = dict(data)
        for name in _marked(model, BesideFile):
            value = placed.get(name)
            if isinstance(value, str):
                placed[name] = self._source.beside(found, value)
            elif isinstance(value, list):
                placed[name] = [
                    self._source.beside(found, v) if isinstance(v, str) else v for v in value
                ]
        return placed


def _accumulated(
    section: dict[str, Any],
    at: str,
    name: str,
    entries: list[tuple[Any, str]],
    origins: dict[tuple[str, ...], str],
) -> None:
    """Set the accumulated field ``name`` to every tier's entries, each once, noting the file
    each came from (and every file that contributed, for the field as a whole)."""
    kept: list[Any] = []
    for entry, source in entries:
        if entry in kept:  # by equality: an entry may be a mapping, which does not hash
            continue
        origins[(at, name, str(len(kept)))] = source
        kept.append(entry)
    section[name] = kept
    origins[(at, name)] = ", ".join(dict.fromkeys(source for _, source in entries))


def _merge(
    base: dict[str, Any],
    override: Mapping[str, Any],
    origins: dict[tuple[str, ...], str],
    source: str,
    at: tuple[str, ...],
) -> None:
    """Merge ``override`` into ``base`` in place, noting ``source`` for every key path set."""
    for key, value in override.items():
        path = (*at, str(key))
        origins[path] = source
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            _merge(base[key], value, origins, source, path)
            continue
        for stale in [p for p in origins if len(p) > len(path) and p[: len(path)] == path]:
            del origins[stale]
        base[key] = _copy(value)
        if isinstance(value, dict):
            _note(value, origins, source, path)


def _note(
    value: Mapping[str, Any], origins: dict[tuple[str, ...], str], source: str, at: tuple[str, ...]
) -> None:
    for key, inner in value.items():
        origins[(*at, str(key))] = source
        if isinstance(inner, dict):
            _note(inner, origins, source, (*at, str(key)))


def _copy(value: Any) -> Any:
    """A mapping copied deeply enough that a later merge never changes a tier's own data."""
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    return value
