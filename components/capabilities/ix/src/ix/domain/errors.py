"""ix errors. ``except IxError`` catches everything ix raises on purpose.

Messages name the file, subject, sensor or engine that is wrong and the legal values.
"""

from __future__ import annotations


class IxError(Exception):
    """Base class for every error ix raises deliberately."""


class ConfigError(IxError, ValueError):
    """An experiment, subject, sensor or engine config is invalid."""


class NotFoundError(IxError):
    """Something named does not exist: a lab, an experiment, a subject, or saved results."""


class LabNotFoundError(NotFoundError, FileNotFoundError):
    """No lab where one was named or searched for. Still a FileNotFoundError."""


class MissingExtraError(IxError, ImportError):
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
