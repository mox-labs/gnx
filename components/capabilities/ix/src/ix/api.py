"""The library API: thin functions over the composition root.

Each function takes a **source**: the path of an experiment file (or of a directory holding
``experiment.yaml``), or an :class:`~ix.Experiment` built in memory. They return versioned
document records; the command line prints the same records.

The root is built on first use, so importing ``ix`` wires nothing.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Any

    from ix.composition import Root, Telemetry
    from ix.domain.design import Design
    from ix.domain.experiment import Experiment
    from ix.domain.genre import Genres
    from ix.domain.kind import Kinds
    from ix.domain.plan import Plan
    from ix.domain.problem import Validation
    from ix.domain.registration import Deviations, Registered
    from ix.domain.report import Report, Runs

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


def run(
    source: Source,
    *,
    exploratory: bool = False,
    dry_run: bool = False,
    repeats: int | None = None,
    base: PathLike | None = None,
    registration: PathLike | None = None,
    on_event: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> Report:
    """Run the experiment and return its report.

    A run starts the subjects, applies the probes and takes the measurements the experiment
    declares, one trial at a time, and records everything under
    ``<experiment directory>/.ix/runs/<run id>/``.

    Raises before anything starts: :class:`InvalidExperimentError`; :class:`ConfigError`
    when a kind the experiment names has no adapter, or something it requires of this host
    does not hold (:attr:`Plan.requirements` lists each beforehand, with whether it holds);
    :class:`UnregisteredError` when the experiment has no registration and neither
    ``exploratory`` nor ``dry_run`` is set. An exploratory run gives no verdict.

    ``dry_run`` runs the experiment as written with every subject on a stand-in (an agent
    session on matrix's ``mock`` runtime, replying with each probe's ``mock_response``) and
    the options only the real runtime takes set aside, each noted in ``Report.stood_in``. It
    is exploratory, calls no model, and checks the harness, never the subject: its report
    gives no verdict and no indication. A subject with no stand-in (a ``process``) is
    refused with :class:`ConfigError`. The environment has no stand-in: it is brought up for
    real (a ``compose`` stack's containers, a ``process`` environment's services).

    ``repeats`` runs a pilot with that many repeats in place of the file's, noted in
    ``Report.overridden``; only with ``exploratory`` or ``dry_run`` (else
    :class:`UsageError`: a run that gives a verdict is held to its registered design), and
    never below the genre's minimum (:class:`InvalidExperimentError`).
    ``Report.best_case`` says when the run is too small for even every trial passing to
    show a pass rate's threshold.

    Once started, a run that is stopped (Ctrl-C, SIGTERM) or that fails still returns its
    report: ``Report.status`` says how it ended and ``Report.exit_code`` what a command
    should exit with.

    ``on_event(name, fields)`` is called with the run's progress events (``ix.trial.start``,
    ``ix.trial.end``, ...). They are telemetry and may be dropped; read results from the
    report. Blocking: it runs its own event loop, so call it from synchronous code.
    """
    return _root().run(
        source,
        exploratory=exploratory,
        dry_run=dry_run,
        repeats=repeats,
        base=base,
        registration=registration,
        on_event=on_event,
    )


def report(run: PathLike) -> Report:
    """The report of a run, computed again from its record. ``run`` is a run's directory, or
    an experiment (its file or directory), meaning its latest run. Works for a run in any
    state, one that was killed included. Raises :class:`NotFoundError` when there is none."""
    return _root().report(run)


def runs(source: PathLike | None = None) -> Runs:
    """The runs kept beside an experiment, oldest first; with no ``source``, those under the
    current directory."""
    return _root().runs(source)


def genres(lab: PathLike | None = None) -> Genres:
    """The installed genres, plus those in ``<lab>/genres``. Refused genre files are in
    ``Genres.rejected``."""
    return _root().genres(lab)


def kinds(lab: PathLike | None = None) -> Kinds:
    """The installed subject kinds, probe kinds, sensors and environment kinds, plus those
    in ``<lab>/kinds``. Refused kind files are in ``Kinds.rejected``."""
    return _root().kinds(lab)


def configure_telemetry() -> Telemetry:
    """Install the OpenTelemetry SDK so ix's spans (``ix.plan``, ``ix.run``, ``ix.trial``,
    ``ix.sensor``) and matrix's are exported, as the standard ``OTEL_*`` variables say; when
    they ask for nothing this does nothing. For a program's entry point (the ``ix`` command
    calls it), never for a library: the providers it installs are the process's own.

    Returns its ``warnings`` (export asked for in an install that cannot do it), which a
    command's documents carry, and ``shutdown()``, to call once the work is done.
    """
    return _root().configure_telemetry()


def skill(reference: str | None = None) -> str:
    """The skill an agent reads (what ix is for, a working example, the commands, the exit
    codes), or one of the references it lists; both generated from the installed genres and
    kinds. Raises :class:`~ix.UsageError` for a reference that does not exist."""
    return _root().skill(reference)
