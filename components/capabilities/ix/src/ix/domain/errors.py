"""ix configuration errors — messages name the file or key that is wrong."""

from __future__ import annotations


class ConfigError(ValueError):
    """An experiment, subject, sensor or engine config is invalid.

    A ValueError subclass so the CLI's existing handling reports it as a usage problem.
    """
