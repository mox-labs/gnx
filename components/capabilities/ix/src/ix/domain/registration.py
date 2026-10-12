"""Registration: the decision parameters of an experiment, frozen before anything runs.

A registration records the digest of the experiment file, the digest of the probe set, the
genre, the primary metric, the margin, the repeats, the seed and the spend ceiling. Whatever
differs later is a **deviation**: a fact to be recorded and printed in the report, so a
decision rule cannot move after the numbers are in without the reader seeing it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ix.domain import schema
from ix.domain.errors import ConfigError
from ix.domain.experiment import Margin, MetricRef
from ix.domain.schema import Record

if TYPE_CHECKING:
    from ix.domain.design import ResolvedDesign
    from ix.domain.experiment import Experiment, ProbeSet

#: The fields a registration freezes, in the order deviations are listed.
FROZEN: tuple[str, ...] = (
    "file_digest",
    "probes_digest",
    "genre",
    "primary",
    "margin",
    "repeats",
    "seed",
    "spend_ceiling",
)

_WHAT = {
    "file_digest": "the experiment file",
    "probes_digest": "the probe set",
    "genre": "the genre",
    "primary": "the primary metric",
    "margin": "the margin",
    "repeats": "the repeats",
    "seed": "the seed",
    "spend_ceiling": "the spend ceiling",
}


class Registration(Record):
    """What was frozen, and when. ``registered_at`` and ``ix_version`` are provenance and
    are not compared."""

    schema_: Literal["ix.v1.registration"] = Field(default=schema.REGISTRATION, alias="schema")
    experiment: str
    file_digest: str
    probes_digest: str
    genre: str
    primary: MetricRef | None
    margin: Margin | None
    repeats: int
    seed: int
    spend_ceiling: float | None
    registered_at: str = ""
    ix_version: str = ""


class Deviation(Record):
    """One frozen field that no longer has its registered value."""

    field: str
    registered: Any
    current: Any
    message: str


class RegistrationConflictError(ConfigError):
    """The experiment is already registered and now differs from its registration."""

    kind = "conflict"

    def __init__(self, message: str, found: tuple[Deviation, ...], *, fix: str | None = None):
        super().__init__(message, fix=fix)
        self.deviations = found


def freeze(
    experiment: Experiment,
    design: ResolvedDesign,
    probes: ProbeSet,
    *,
    file_digest: str,
    registered_at: str = "",
    ix_version: str = "",
) -> Registration:
    """The registration of an experiment as it stands now."""
    return Registration(
        experiment=experiment.name,
        file_digest=file_digest,
        probes_digest=probes.digest,
        genre=design.genre,
        primary=design.primary,
        margin=design.margin,
        repeats=design.repeats,
        seed=design.seed,
        spend_ceiling=experiment.spend.ceiling if experiment.spend else None,
        registered_at=registered_at,
        ix_version=ix_version,
    )


def deviations(registered: Registration, current: Registration) -> tuple[Deviation, ...]:
    """Every frozen field in which ``current`` differs from ``registered``."""
    was, now = registered.document(), current.document()
    return tuple(
        Deviation(
            field=field,
            registered=was[field],
            current=now[field],
            message=f"{_WHAT[field]} changed after registration",
        )
        for field in FROZEN
        if was[field] != now[field]
    )
