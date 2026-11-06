"""recon's one seam to matrix: the only module of recon that names it (``layers.json``'s
``single_importer`` rule), so a move of the harness contract to another one (slick's) changes
this module and no other (owner ruling, REQ §2; RUBRIC H6).

**The registry** (ruling R2). recon's extension points are matrix ``Registry`` points, owned by
the distribution ``recon``:

- ``recon.collector``: a collector type, named in a config as ``type:``;
- ``recon.transform``: a transform, named in a normalize spec as ``path|$name``.

recon's built-ins register at them from recon's own ``matrix.extensions`` entry point
(:func:`register`), as a third party's do from theirs: a built-in by a short name
(``type: api``, ``$html2text``: ``recon.v1.collector.api``, ``recon.v1.transform.html2text``),
anyone else's by its full type URL (``type: acme.v1.collector.sql``), ruling R3. Code using
recon registers its own in-process (``recon.open(extensions=[register])``), at the same points
and under the same rules: each ``register(registry)`` is called with the session's registry,
and what it registers is trusted as its owner's (matrix's rule for direct registration).

matrix's own rules hold: a type URL with no version segment is refused, two registrations of
one type URL are refused naming both, and an extension that fails to load is quarantined whole,
every entry it registered with it (``recon types --json`` and ``recon status`` say which).
Discovery loads every installed ``matrix.extensions`` entry point, so a session imports each
installed package's registration (review C3).

What a point's entry builds is, for now, the descriptor recon's planner and core read
(:class:`~recon.core.domain.collector.CollectorType`, or a transform's callable): each entry is
built with no config and no need.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from matrix import MatrixError, Registry, default_registry

from recon.core.domain.collector import BrokenPlugin, CollectorType, Plugins
from recon.core.domain.errors import UsageError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from recon.core.domain.collector import Transform

#: recon's extension points on matrix's registry.
COLLECTOR = "recon.collector"
TRANSFORM = "recon.transform"
POINTS = (COLLECTOR, TRANSFORM)

#: The group a broken extension is reported under: matrix's.
GROUP = "matrix.extensions"

#: What matrix records as the origin of an entry registered directly, not from an entry point.
_DIRECT = "(direct)"

#: What recon calls an extension given to ``recon.open``.
IN_PROCESS = "in_process"

#: An in-process extension: called with the session's registry, it registers at recon's points.
type Register = Callable[[Registry], None]


def type_url(point: str, name: str) -> str:
    """A built-in's type URL from its short name (``api`` at ``recon.collector`` is
    ``recon.v1.collector.api``; a type URL's segments are kebab-case, so ``inverted_index``
    is ``recon.v1.transform.inverted-index``); a full type URL passes through."""
    if "." in name:
        return name
    return f"recon.v1.{point.removeprefix('recon.')}.{name.replace('_', '-')}"


def short(point: str, url: str) -> str:
    """The name a config uses for the entry at ``url``: a built-in's short name (its last
    segment, as a config spells it), anyone else's type URL as it stands."""
    prefix = f"recon.v1.{point.removeprefix('recon.')}."
    rest = url.removeprefix(prefix)
    return rest.replace("-", "_") if url.startswith(prefix) and "." not in rest else url


def register(registry: Registry) -> None:
    """recon's ``matrix.extensions`` entry point: its points, and its built-ins at them."""
    from recon.composition import builtins

    for point in POINTS:
        registry.add_point(point)
    collectors, transforms = builtins()
    for name, collector_type in collectors.items():
        registry.register(COLLECTOR, type_url(COLLECTOR, name), _giving(collector_type),
                          summary=collector_type.summary)  # fmt: skip
    for name, transform in transforms.items():
        summary = (transform.__doc__ or "").strip().split("\n", 1)[0]
        registry.register(TRANSFORM, type_url(TRANSFORM, name), _giving(transform),
                          summary=summary)  # fmt: skip


def plugins(extensions: Iterable[Register] = ()) -> Plugins:
    """Every collector type and transform at recon's points: those of every installed
    ``matrix.extensions`` entry point, recon's own among them, then those each of
    ``extensions`` registers in-process; with each extension that failed to load, and whose
    each collector type is (its distribution, or ``in_process``). An extension given that
    raises is not quarantined: its error is the caller's, and one matrix refuses (a type URL
    with no version, one registered already, a point recon does not have) is a UsageError
    saying why."""
    registry = default_registry()
    for extend in extensions:
        try:
            extend(registry)
        except MatrixError as exc:  # matrix's refusal, said as recon's
            raise UsageError(
                f"an extension given could not register: {exc.message}",
                fix=exc.fix
                or "register at recon.collector or recon.transform, under a type URL "
                "of your own (<namespace>.v<N>.<name>)",
            ) from exc
    broken = [
        BrokenPlugin(GROUP, failure.extension, failure.origin, failure.error)
        for failure in registry.failures
    ]
    types: dict[str, CollectorType] = {}
    packages: dict[str, str] = {}
    for entry in registry.entries(COLLECTOR) if COLLECTOR in registry.points else ():
        name = short(COLLECTOR, entry.type_url)
        built = _built(entry.build, entry.type_url, broken)
        if built is None:
            continue
        if not isinstance(built, CollectorType):
            broken.append(BrokenPlugin(GROUP, name, entry.type_url, "expected a recon "
                                       f"CollectorType, got {type(built).__name__}"))  # fmt: skip
            continue
        types[name] = built
        packages[name] = IN_PROCESS if entry.origin == _DIRECT else entry.origin
    transforms: dict[str, Transform] = {}
    for entry in registry.entries(TRANSFORM) if TRANSFORM in registry.points else ():
        name = short(TRANSFORM, entry.type_url)
        built = _built(entry.build, entry.type_url, broken)
        if built is None:
            continue
        if not callable(built):
            error = f"expected a callable, got {type(built).__name__}"
            broken.append(BrokenPlugin(GROUP, name, entry.type_url, error))
            continue
        transforms[name] = built
    return Plugins(types, transforms, broken, packages)


def _built(build: Callable[[], Any], url: str, broken: list[BrokenPlugin]) -> Any:
    """What an entry builds; None, said as broken, when building it raises."""
    try:
        return build()
    except Exception as exc:  # one extension's failure must not stop the others
        broken.append(BrokenPlugin(GROUP, url, url, f"{type(exc).__name__}: {exc}"))
        return None


def _giving(value: object) -> Callable[[], Any]:
    """An entry's build that gives ``value``: a built-in, made when recon registers it."""

    def build() -> Any:
        return value

    return build
