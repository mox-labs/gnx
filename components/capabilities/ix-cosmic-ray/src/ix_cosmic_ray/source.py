"""The registration: cosmic-ray's operators at ix's ``ix.mutant-source`` point.

It imports nothing of cosmic-ray, so a session that loads it (``ix plan``, which asks which
sources are installed) pays nothing for cosmic-ray's dependencies; the source is built, and
cosmic-ray imported, only when ``ix mutants sample`` asks for it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ix.extension import MUTANT_SOURCE

if TYPE_CHECKING:
    from matrix import Registry

    from ix_cosmic_ray.generator import CosmicRay

#: The source's type URL, under this distribution's own namespace.
TYPE_URL = "ix-cosmic-ray.v1.mutant-source"


def build() -> CosmicRay:
    """The source, cosmic-ray imported with it."""
    from ix_cosmic_ray.generator import CosmicRay

    return CosmicRay()


def register(registry: Registry) -> None:
    """Register the source at ix's point; it drives cosmic-ray, whose version a population
    records."""
    registry.register(
        MUTANT_SOURCE,
        TYPE_URL,
        build,
        summary="cosmic-ray's mutation operators, as a generator of mutants only",
        drives=("cosmic-ray",),
    )
