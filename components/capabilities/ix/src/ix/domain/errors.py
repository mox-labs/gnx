"""ix errors. ``except IxError`` catches everything ix raises on purpose.

Messages name the file, subject, sensor or engine that is wrong and the legal values. Every
error has a ``kind`` from the vocabulary matrix and hardline share (``config``,
``not_found``, ``transient``, ``auth``, ...), so the CLI turns it into the right exit code
without guessing; :func:`from_matrix` keeps a matrix error's kind and fix when ix adds
context to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from matrix import MatrixError


class IxError(Exception):
    """Base class for every error ix raises deliberately."""

    kind: str = "unknown"

    def __init__(self, message: str = "", *, fix: str | None = None, kind: str | None = None):
        super().__init__(message)
        self.fix = fix
        if kind is not None:
            self.kind = kind


class ConfigError(IxError, ValueError):
    """An experiment, subject, sensor or engine config is invalid."""

    kind = "config"


class NotFoundError(IxError):
    """Something named does not exist: a lab, an experiment, a subject, or saved results."""

    kind = "not_found"


class LabNotFoundError(NotFoundError, FileNotFoundError):
    """No lab where one was named or searched for. Still a FileNotFoundError."""


class MissingExtraError(IxError, ImportError):
    kind = "config"

    """A configured engine or sensor needs an optional extra that is not installed.

    Still an ImportError; the message names the extra and the install command.
    """


class EngineError(IxError):
    """An engine could not complete a repeat (e.g. the Inspect evaluation did not finish).

    Distinct from a failed trial: a trial that errors is a failed *reading*; an engine
    error means there are no readings to aggregate.
    """


class ResultsError(IxError):
    """Results that a command needs are missing or do not line up (e.g. compare)."""


class ResultsNotFoundError(ResultsError, NotFoundError):
    """No saved results for the subject asked for. The message lists those that have some."""


def from_matrix(error: MatrixError, context: str = "") -> IxError:
    """An ix error carrying ``error``'s message (prefixed by ``context``), kind and fix."""
    message = f"{context}: {error.message}" if context else error.message
    cls: type[IxError] = {"config": ConfigError, "not_found": NotFoundError}.get(
        error.kind, IxError
    )
    result: Any = cls(message, fix=error.fix, kind=error.kind)
    return result  # type: ignore[no-any-return]
