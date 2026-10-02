"""ix errors. ``except IxError`` catches everything ix raises on purpose.

Messages name the file, subject, sensor or engine that is wrong and the legal values.
"""

from __future__ import annotations


class IxError(Exception):
    """Base class for every error ix raises deliberately."""


class ConfigError(IxError, ValueError):
    """An experiment, subject, sensor or engine config is invalid."""


class EngineError(IxError):
    """An engine could not complete a repeat (e.g. the Inspect evaluation did not finish).

    Distinct from a failed trial: a trial that errors is a failed *reading*; an engine
    error means there are no readings to aggregate.
    """


class ResultsError(IxError):
    """Results that a command needs are missing or do not line up (e.g. compare)."""
