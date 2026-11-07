"""The shared things composition hands an extension that declares it needs them.

An extension names its needs when it registers (``needs={"agents"}``), and its ``build`` is
called with exactly those, by name: ``build(config, agents=...)``. :data:`NEEDS` is matrix's
table of them, each with the type of what is handed over: the one place they are declared,
which composition supplies, ``matrix describe`` prints and the skill teaches.

A host may hand over needs of its own besides (``compose(context={"acme_kit": kit})``); they
are the host's to declare, and a build that names one the host did not give is a
``ConfigError``. Nothing is matched by field name.

A host that hands over one of matrix's needs itself, building through the registry rather than
through ``compose``, hands it as its type says: ``model_runtimes`` is a :class:`ModelRuntimes`.

A key that names one of them defaults to the only one composed (:func:`model_runtime_named`):
a model step's ``runtime``, the pydantic-ai runtime's ``model_runtime``. It is resolved when
the extension is built, from the runtimes it is handed, and never at a call. Such a key carries
:data:`ONLY_COMPOSED` in its annotation, so the plan reports the runtime it resolves to.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from matrix.domain.errors import invalid
from matrix.domain.problems import problem

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from matrix.domain.ports._out.model_runtime import ModelRuntime

#: How far a need is promised: ``stable`` changes only by addition; ``beta`` may change in a
#: minor release, said in the changelog.
NeedStability = Literal["stable", "beta"]


@dataclass(frozen=True, kw_only=True)
class Need:
    """One need: its name, the type of what is handed over, and what it is."""

    name: str
    #: The type of the value, as an annotation reads (``Mapping[str, Agent]``).
    type: str
    summary: str
    stability: NeedStability = "stable"
    #: Whether only a component may declare it: its value depends on the configured instance.
    components_only: bool = False


class ModelRuntimes(Mapping[str, "ModelRuntime"]):
    """The ``model_runtimes`` need: each model runtime composition built, by name, read-only,
    and what each was built as, which the session rows of the calls made on it name.

    ``type_url(name)`` is the type URL of the registry entry the runtime was built from (a
    short name in config resolved: ``mock`` is ``matrix.v1.model-runtime.mock``), and
    ``drives(name)`` the distributions that entry drives. A caller that records a call it makes
    on one passes them on (``call_model(..., runtime_url=..., drives=...)``), as the model step
    does. Each raises ``KeyError`` for a name the mapping does not hold, as indexing does.

    ``compose`` builds one. A host that builds its runtimes some other way (ix builds each when
    it is asked for) subclasses this and implements the mapping and both methods.
    """

    @abstractmethod
    def type_url(self, name: str) -> str: ...

    @abstractmethod
    def drives(self, name: str) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class OnlyComposed:
    """The marker of a key that names a model runtime and, unset, is the only one composed:
    ``Annotated[str | None, ONLY_COMPOSED] = None``."""

    #: What the plan says of the key when it was left to the default.
    note: str = "the only model runtime composed"


ONLY_COMPOSED = OnlyComposed()


def model_runtime_named(name: str | None, composed: Collection[str], *, key: str) -> str:
    """``name`` when it is given, as it stands; unset, the only model runtime ``composed``
    holds. A ConfigError at ``key`` when none is composed, or several are and none is named,
    listing them: a second runtime added to a config never changes which one answers."""
    if name is not None:
        return name
    names = sorted(composed)
    if len(names) == 1:
        return names[0]
    if not names:
        found = problem(
            key,
            "missing_key",
            "no model runtime is named, and none is composed to be the default",
            fix="define one under matrix.model_runtimes",
        )
    else:
        found = problem(
            key,
            "missing_key",
            f"no model runtime is named, and {len(names)} are composed: "
            f"{', '.join(names)}; the default is the only one",
            fix=f"name one of {', '.join(names)}",
            allowed=names,
        )
    raise invalid("no model runtime is named", [found])


#: matrix's needs, by name.
NEEDS: dict[str, Need] = {
    need.name: need
    for need in (
        Need(
            name="agents",
            type="Mapping[str, Agent]",
            summary="every agent the config binds, bound to its agent runtime, by name",
        ),
        Need(
            name="model_runtimes",
            type="ModelRuntimes",
            summary="every model runtime the config builds, by name, each with what it was "
            "built as (type_url(name), drives(name)) for the session rows of the calls made on "
            "it; an agent runtime that declares it makes its sessions' model calls through them",
        ),
        Need(
            name="schemas",
            type="Mapping[str, type[BaseModel]]",
            summary="the registered payload schemas, by kind",
        ),
        Need(
            name="cwd",
            type="str | None",
            summary="the working directory the host composed for (`compose(context={'cwd': "
            "...})`); None when it gave none, which is the process's",
        ),
        Need(
            name="name",
            type="str",
            summary="the component's name in the run",
            components_only=True,
        ),
    )
}

#: What a need outside :data:`NEEDS` is, as a description says it.
HOST_NEED = "the host's own: handed over by whoever composes, compose(context=...)"


def described(names: Iterable[str]) -> list[dict[str, Any]]:
    """Each need in ``names``, in name order, as JSON data: ``{name, type, summary,
    stability}``, ``type`` and ``stability`` None for a need of the host's own."""
    found: list[dict[str, Any]] = []
    for name in sorted(names):
        need = NEEDS.get(name)
        if need is None:
            found.append({"name": name, "type": None, "summary": HOST_NEED, "stability": None})
        else:
            found.append(
                {
                    "name": name,
                    "type": need.type,
                    "summary": need.summary,
                    "stability": need.stability,
                }
            )
    return found
