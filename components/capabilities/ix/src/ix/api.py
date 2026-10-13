"""The library API of the experiment model: thin functions over the composition root.

Each function takes a **source**: the path of an experiment file (or of a directory holding
``experiment.yaml``), or an :class:`~ix.Experiment` built in memory. They return versioned
document records; the command line prints the same records.

The root is built on first use, so importing ``ix`` wires nothing.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ix.composition import Root
    from ix.domain.design import Design
    from ix.domain.experiment import Experiment
    from ix.domain.genre import Genres
    from ix.domain.kind import Kinds
    from ix.domain.plan import Plan
    from ix.domain.problem import Validation
    from ix.domain.registration import Deviations, Registered

type PathLike = str | os.PathLike[str]
type Source = PathLike | Experiment


def _root() -> Root:
    from ix.composition import root

    return root()


def load(path: PathLike) -> Experiment:
    """The experiment at ``path`` as its file declares it.

    Raises :class:`InvalidExperimentError` (with every problem) when the file is not an
    experiment, and :class:`NotFoundError` when there is no file.
    """
    return _root().load(path)


def validate(source: Source, *, base: PathLike | None = None) -> Validation:
    """Every problem with the experiment. Runs nothing; never raises on a wrong experiment,
    only on a missing file.

    ``base`` is the directory relative paths resolve against when ``source`` is an
    experiment object (default: the current directory).
    """
    return _root().validate(source, base=base)


def resolve(source: Source, *, base: PathLike | None = None) -> Design:
    """The design: the experiment plus everything its genre and its kinds supply, each value
    with its source. Raises :class:`InvalidExperimentError` when the experiment is invalid."""
    return _root().design(source, base=base)


def plan(
    source: Source, *, base: PathLike | None = None, registration: PathLike | None = None
) -> Plan:
    """What a run would do. Runs nothing and writes nothing.

    ``Plan.digest`` is stable against a comment, key order, a default written out, a
    reworded question, note or description, and a field a later ix adds with a default. It
    changes when a value a run would use changes, including one the genre supplied.

    ``registration`` names the registration file to compare with; for a path source it
    defaults to the file beside the experiment. Raises :class:`InvalidExperimentError` when
    the experiment is invalid.
    """
    return _root().plan(source, base=base, registration=registration)


def register(
    source: Source,
    *,
    base: PathLike | None = None,
    registration: PathLike | None = None,
    amend: str | None = None,
) -> Registered:
    """Freeze the experiment's design in its registration file.

    Does nothing when the experiment is registered and unchanged. Raises
    :class:`RegistrationConflictError` when it has changed, unless ``amend`` gives the
    reason: then the new record replaces the old one and keeps it, with the reason, inside.
    An experiment object needs ``registration``: it has no file to register beside.
    """
    return _root().register(source, base=base, registration=registration, amend=amend)


def deviations(
    source: Source, *, base: PathLike | None = None, registration: PathLike | None = None
) -> Deviations:
    """How the experiment differs from its registration. Raises :class:`NotFoundError` when
    there is none."""
    return _root().deviations(source, base=base, registration=registration)


def genres(lab: PathLike | None = None) -> Genres:
    """The installed genres, plus those in ``<lab>/genres``. Refused genre files are in
    ``Genres.rejected``."""
    return _root().genres(lab)


def kinds(lab: PathLike | None = None) -> Kinds:
    """The installed subject kinds, probe kinds, sensors and environment kinds, plus those
    in ``<lab>/kinds``. Refused kind files are in ``Kinds.rejected``."""
    return _root().kinds(lab)


def skill() -> str:
    """The document that teaches an agent to write an experiment: generated from the
    installed genres and kinds."""
    return _root().skill()
