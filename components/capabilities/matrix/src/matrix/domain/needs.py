"""The shared things composition hands an extension that declares it needs them.

An extension names its needs when it registers (``needs={"agents"}``), and its ``build`` is
called with exactly those, by name: ``build(config, agents=...)``. :data:`NEEDS` is matrix's
table of them, each with the type of what is handed over: the one place they are declared,
which composition supplies, ``matrix describe`` prints and the skill teaches.

A host may hand over needs of its own besides (``compose(context={"acme_kit": kit})``); they
are the host's to declare, and a build that names one the host did not give is a
``ConfigError``. Nothing is matched by field name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

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
            type="Mapping[str, ModelRuntime]",
            summary="every model runtime the config builds, by name; an agent runtime that "
            "declares it makes its sessions' model calls through them",
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
        Need(
            name="model_runtime_entries",
            type="Mapping[str, Entry]",
            summary="the registry entry of each model runtime the config builds, by name: its "
            "type URL and what it drives, for the sessions a component records",
            stability="beta",
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
