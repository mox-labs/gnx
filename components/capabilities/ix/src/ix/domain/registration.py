"""Registration: an experiment's design, frozen before anything runs.

A registration stores the resolved design (what the author chose and what the genre
supplied), the digests of the probe set, the experiment and the reviewed plan, and the
digest of each file and folder the subjects and sensors name (``contents``, see
:mod:`ix.domain.provenance`). Whatever differs later is a **deviation**: recorded and
printed in the report, so a decision cannot move after the numbers are in without the
reader seeing it. A design value that only one of
two records holds is not compared, so a record written by a newer ix still loads.

**Amending** replaces the frozen design on purpose; the superseded record is kept inside the
new one with the reason and the deviations it accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, field_validator

from ix.domain import schema
from ix.domain.errors import EXIT_CODES, ConfigError
from ix.domain.problem import Problem
from ix.domain.provenance import Content
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

    def text(self) -> str:
        """The deviation in one line, each value as an author writes it in YAML."""
        return f"{self.field}: registered {_yaml(self.registered)}, now {_yaml(self.current)}"


def _yaml(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


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
    #: The digest of each file and folder the subjects and sensors name: what was tested.
    contents: tuple[Content, ...] = ()
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
    #: What ``validate`` warns of in the experiment registered (a typo in prompt text):
    #: problems that do not stop it.
    warnings: tuple[Problem, ...] = ()


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
        return EXIT_CODES["deviations" if self.deviations else "ok"]


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
    contents: tuple[Content, ...] = (),
    registered_at: str = "",
    ix_version: str = "",
) -> Registration:
    """The registration of an experiment as it stands now, ``contents`` being what the
    files its subjects and sensors name hold."""
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
        contents=contents,
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


def _ancestors(path: str) -> list[str]:
    """``a.b.c`` gives ``a`` and ``a.b``."""
    parts = path.split(".")
    return [".".join(parts[:end]) for end in range(1, len(parts))]


def _at(value: Mapping[str, Any], path: str) -> Any:
    """What ``value`` holds at the dotted ``path``; None where it holds nothing."""
    held: Any = value
    for part in path.split("."):
        if not isinstance(held, Mapping):
            return None
        held = held.get(part)
    return held


def deviations(registered: Registration, current: Registration) -> tuple[Deviation, ...]:
    """Every frozen value in which ``current`` differs from ``registered``.

    Design values are compared path by path over the paths both records hold, so a value an
    older record never stored is not a deviation; a mapping on one side that is a value on
    the other (a margin removed: ``margin.value`` against ``margin: null``) is compared
    whole, at its own path. Then the three digests, then each file and folder the subjects
    and sensors name, by what it is (``skill review changed after registration``).
    """
    found: list[Deviation] = []
    before = {k: v for k, v in registered.design.items() if k not in _NOT_COMPARED}
    after = {k: v for k, v in current.design.items() if k not in _NOT_COMPARED}
    was, now = _leaves(before), _leaves(after)
    changed = {path for path in was.keys() & now.keys() if was[path] != now[path]}
    # A path one side holds below a value the other holds: the whole mapping became that
    # value, or the value became a mapping.
    changed |= {
        ancestor
        for path in was.keys() ^ now.keys()
        for ancestor in _ancestors(path)
        if ancestor in was or ancestor in now
    }
    for path in sorted(changed, key=list((*was, *now)).index):
        found.append(
            Deviation(
                field=f"design.{path}",
                registered=_at(before, path),
                current=_at(after, path),
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
    found += _contents(registered.contents, current.contents)
    return tuple(found)


def _contents(registered: tuple[Content, ...], current: tuple[Content, ...]) -> list[Deviation]:
    """Each file or folder that differs from what was frozen: its digest moved, it was
    deleted or created, or the experiment stopped or started naming it. A registration that
    froze no contents (written before ix recorded them) is not compared."""
    if not registered:
        return []
    was = {content.key: content for content in registered}
    now = {content.key: content for content in current}
    found: list[tuple[Content, str, str, str]] = []
    for key, before in was.items():
        after = now.get(key)
        if after is None:
            found.append((before, before.digest, "", "was dropped from the experiment"))
        elif after.digest != before.digest:
            how = (
                "was deleted" if not after.digest
                else "was created" if not before.digest
                else "changed"
            )  # fmt: skip
            found.append((after, before.digest, after.digest, how))
    found += [
        (after, "", after.digest, "was added to the experiment")
        for key, after in now.items()
        if key not in was
    ]
    return [
        Deviation(
            field=content.field,
            registered=before or None,
            current=after or None,
            message=f"{content.label or content.field} {how} after registration",
        )
        for content, before, after, how in found
    ]


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
