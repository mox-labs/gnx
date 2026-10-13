"""Registration: an experiment's design, frozen before anything runs.

A registration stores the resolved design (what the author chose and what the genre
supplied: comparison, margin, threshold, guardrails, control, repeats, seed, order,
calibration, estimators, the analysis and its parameters, the sensors), the digest of the
probe set, the digest of the experiment and the digest of the plan that was reviewed.
Whatever differs later is a **deviation**: a fact to be recorded and printed in the report,
so a decision cannot move after the numbers are in without the reader seeing it.

**What is not a deviation.** The digests are over meaning (see :mod:`ix.domain.schema`), so
a comment, key order or a default written out changes nothing. A reworded question or
rationale changes the experiment's digest and not the plan's.

**Stored, so tolerant.** A registration written by a newer ix loads: unknown fields are
ignored, and a design value one record lacks is not compared.

**Amending** replaces the frozen design on purpose. The superseded record is kept inside the
new one with the stated reason and the deviations it accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, field_validator

from ix.domain import schema
from ix.domain.errors import EXIT_BY_KIND, ConfigError
from ix.domain.schema import Record, Stored

if TYPE_CHECKING:
    from ix.domain.design import Design
    from ix.domain.experiment import Experiment, ProbeSet

#: Parts of a stored design that are derived prose or bookkeeping, never compared.
_NOT_COMPARED = frozenset({"schema", "hypotheses", "provenance"})

_DIGESTS = {
    "probes_digest": "the probe set",
    "experiment_digest": "the experiment's declaration",
    "plan_digest": "the plan",
}


class Deviation(Stored):
    """One frozen value that no longer has its registered value."""

    field: str
    registered: Any = None
    current: Any = None
    message: str = ""


class Amendment(Stored):
    """A registration that was replaced on purpose: why, when, what it accepted, and the
    record it superseded."""

    reason: str
    amended_at: str = ""
    deviations: tuple[Deviation, ...] = ()
    superseded: dict[str, Any] = {}


class Registration(Stored):
    """What was frozen, and when. ``registered_at``, ``ix_version`` and ``amendments`` are
    provenance and are not compared."""

    schema_: str = Field(default=schema.REGISTRATION, alias="schema")
    experiment: str
    experiment_digest: str = ""
    probes_digest: str = ""
    plan_digest: str = ""
    #: The ``ix.v1.design`` document as it stood, without its provenance.
    design: dict[str, Any] = {}
    registered_at: str = ""
    ix_version: str = ""
    #: Earlier registrations this one replaced, oldest first.
    amendments: tuple[Amendment, ...] = ()

    @field_validator("schema_")
    @classmethod
    def _supported(cls, value: str) -> str:
        if value != schema.REGISTRATION:
            raise ValueError(f"this ix reads {schema.REGISTRATION}; the record says {value!r}")
        return value


class Registered(Record):
    """The outcome of ``register``: what happened, where the record is, and the record."""

    schema_: Literal["ix.v1.registered"] = Field(default=schema.REGISTERED, alias="schema")
    outcome: Literal["created", "unchanged", "amended"]
    location: str
    registration: Registration


class Deviations(Record):
    """An experiment held against its registration."""

    schema_: Literal["ix.v1.deviations"] = Field(default=schema.DEVIATIONS, alias="schema")
    experiment: str
    location: str = ""
    registered_at: str = ""
    deviations: tuple[Deviation, ...] = ()

    @property
    def exit_code(self) -> int:
        """What a command that printed this document exits with."""
        return EXIT_BY_KIND["deviations" if self.deviations else "ok"]


class RegistrationConflictError(ConfigError):
    """The experiment is already registered and now differs from its registration."""

    kind = "conflict"

    def __init__(self, message: str, found: tuple[Deviation, ...], *, fix: str | None = None):
        super().__init__(message, fix=fix)
        self.deviations = found


def freeze(
    experiment: Experiment,
    design: Design,
    probes: ProbeSet,
    *,
    plan_digest: str,
    registered_at: str = "",
    ix_version: str = "",
) -> Registration:
    """The registration of an experiment as it stands now."""
    frozen = design.document()
    frozen.pop("provenance", None)
    for metric in frozen.get("metrics", []):
        metric.pop("description", None)
    return Registration(
        experiment=experiment.name,
        experiment_digest=experiment.digest(),
        probes_digest=probes.digest,
        plan_digest=plan_digest,
        design=frozen,
        registered_at=registered_at,
        ix_version=ix_version,
    )


def _leaves(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """A nested mapping as dotted path → value. A list is one value: its order and length
    are part of what it says."""
    flat: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}{key}"
        if isinstance(item, Mapping):
            flat.update(_leaves(item, f"{path}."))
        else:
            flat[path] = item
    return flat


def deviations(registered: Registration, current: Registration) -> tuple[Deviation, ...]:
    """Every frozen value in which ``current`` differs from ``registered``.

    Design values are compared path by path over the paths both records hold, so a value an
    older record never stored is not a deviation. Then the three digests.
    """
    found: list[Deviation] = []
    was = _leaves({k: v for k, v in registered.design.items() if k not in _NOT_COMPARED})
    now = _leaves({k: v for k, v in current.design.items() if k not in _NOT_COMPARED})
    for path, value in was.items():
        if path in now and now[path] != value:
            found.append(
                Deviation(
                    field=f"design.{path}",
                    registered=value,
                    current=now[path],
                    message=f"{path} changed after registration",
                )
            )
    for field, what in _DIGESTS.items():
        before, after = getattr(registered, field), getattr(current, field)
        if before and after and before != after:
            found.append(
                Deviation(
                    field=field,
                    registered=before,
                    current=after,
                    message=f"{what} changed after registration",
                )
            )
    return tuple(found)


def amend(
    registered: Registration,
    current: Registration,
    *,
    reason: str,
    accepted: tuple[Deviation, ...],
    amended_at: str = "",
) -> Registration:
    """``current`` as the new registration, holding ``registered`` as the record it replaced."""
    superseded = registered.document()
    earlier = superseded.pop("amendments", [])
    return current.model_copy(
        update={
            "amendments": (
                *(Amendment.model_validate(item) for item in earlier),
                Amendment(
                    reason=reason,
                    amended_at=amended_at,
                    deviations=accepted,
                    superseded=superseded,
                ),
            )
        }
    )
