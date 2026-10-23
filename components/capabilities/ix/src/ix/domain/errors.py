"""ix errors and the one exit-code table. ``except IxError`` catches everything ix raises on
purpose.

Every error has a ``kind`` that says what the caller should do next, a message that names
what is wrong, a ``fix``, and ``details``: structured fields, by convention ``path`` (where
the wrong value is: a file, or a key in one) and ``value`` (what it was). :data:`EXIT_CODES`
gives the exit code of every kind and every outcome, :data:`EXIT_MEANINGS` what each means
and :data:`EXIT_NEXT` what to do about it; the command line, the skill and the tests read
these and no other.
The kinds every tool shares (``ok``, ``unknown``, ``usage``, ``config``, ``not_found``,
``conflict``, ``run_failed``, ``interrupted``) have the codes agreed in
``components/capabilities/exit-codes.json``, and the tests hold ix to it; ix's own kinds take
codes from 10 up, one kind to a code. ``interrupted`` (130) is the command cut short by
Ctrl-C; ``run_interrupted`` (12) is a run that was stopped and whose report is printed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ix.domain.problem import Problem
    from ix.domain.registration import Deviation

#: The exit code of each error kind and each outcome a command reports.
EXIT_CODES: dict[str, int] = {
    "ok": 0,
    "unknown": 1,
    "usage": 2,
    "config": 3,
    "not_found": 4,
    "conflict": 7,
    "run_failed": 8,
    "fails": 10,
    "inconclusive": 11,
    "run_interrupted": 12,
    "running": 13,
    "deviations": 14,
    "unregistered": 15,
    "simulated": 16,
    "interrupted": 130,
}

#: What each kind in :data:`EXIT_CODES` means.
EXIT_MEANINGS: dict[str, str] = {
    "ok": "done; for validate, the experiment is valid; for run and report, the verdict "
    "holds, or there is no verdict to give and none was asked for (exploratory or dry)",
    "unknown": "a failure ix did not classify",
    "usage": "a bad flag or argument",
    "config": "the experiment is invalid, or it cannot be carried out here as written (a kind "
    "with no adapter, a subject that cannot start, an unsupported analysis)",
    "conflict": "register met an experiment that changed since its registration",
    "not_found": "no experiment file, no registration, or no run",
    "unregistered": "ix run was given an experiment with no registration",
    "simulated": "a stand-in (a mock runtime) answered the trials of a run held to its "
    "registration: it measured the harness, not the subject, and gives no gate result",
    "run_failed": "the harness could not finish the run; the record is kept",
    "deviations": "the experiment differs from its registration; from run and report, the "
    "run was not held to it and gives no gate result",
    "fails": "the verdict is fails",
    "inconclusive": "the verdict is inconclusive",
    "run_interrupted": "the run stopped before it finished; the record is kept and it gives no "
    "verdict",
    "running": "the run has not finished yet",
    "interrupted": "the command itself was interrupted (Ctrl-C) before it finished",
}

#: What to do next on each kind in :data:`EXIT_CODES`.
EXIT_NEXT: dict[str, str] = {
    "ok": "go on: validate, plan, register, run, in that order; after a run with no verdict "
    "(exploratory or dry), register and run it for one",
    "unknown": "report it as a bug in ix, with the command that raised it",
    "usage": "correct the command; `ix <command> --help` lists its arguments",
    "config": "fix each problem at its `path` as its `fix` says, then validate again; for a "
    "kind with no adapter, use a kind `ix kinds` marks runnable",
    "conflict": "run `ix deviations <path>`; restore the design, or "
    '`ix register <path> --amend --reason "<why>"`',
    "not_found": "check the path; register before `ix deviations`, run before `ix report`",
    "unregistered": "run `ix register <path>` and run again, or add `--exploratory` for a run "
    "that gives no verdict",
    "simulated": "for a gate, give each agent subject a runtime that calls a model, amend the "
    'registration (`ix register <path> --amend --reason "<why>"`) and run again; to check the '
    "harness on purpose, run `ix run <path> --dry-run`",
    "run_failed": "read the report's errors and fix their cause (an environment service that "
    "could not start, was not up in time, found its address in use or went down), run again",
    "deviations": "`ix deviations <path>` lists them; restore the registered design, or amend "
    'the registration with a reason (`ix register <path> --amend --reason "<why>"`), and run '
    "again",
    "fails": "the claim does not hold: do not act on it; read the findings",
    "inconclusive": "nothing was shown either way, and it does not pass a gate: report it as "
    "inconclusive; extend the experiment only if the person decides to, amending with a reason "
    "that names this result",
    "run_interrupted": "run again; `ix report <run directory>` reads what was kept",
    "running": "wait, then run `ix report` again",
    "interrupted": "run the command again",
}

#: The kinds an error can have, as ``error.kind`` in the ``ix.v1.error`` envelope.
ERROR_KINDS: tuple[str, ...] = (
    "unknown",
    "usage",
    "config",
    "conflict",
    "not_found",
    "unregistered",
    "interrupted",
)


class IxError(Exception):
    """Base class for every error ix raises deliberately."""

    kind: str = "unknown"

    def __init__(
        self,
        message: str = "",
        *,
        fix: str | None = None,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.fix = fix
        self.details: dict[str, Any] = dict(details or {})
        self.problems: tuple[Problem, ...] = ()
        self.deviations: tuple[Deviation, ...] = ()
        #: False when checking stopped before every problem was found (the file did not
        #: load as an experiment): fix ``problems`` and check again.
        self.complete = True

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.kind]


class UsageError(IxError):
    """A command was given a flag or an argument it does not take."""

    kind = "usage"


class ConfigError(IxError, ValueError):
    """An experiment, a genre, a kind or a stored record is invalid, or what it names cannot
    be carried out here as written."""

    kind = "config"


class NotFoundError(IxError):
    """Something named does not exist: an experiment file, or its registration."""

    kind = "not_found"


class UnregisteredError(IxError):
    """``run`` was asked to run an experiment that has no registration, and not told the run
    is exploratory."""

    kind = "unregistered"
