"""Recon domain exceptions.

Every error carries a ``kind``: what the caller should do next, not where the error arose.
The CLI maps kinds to exit codes (see ``adapters/_in/cli.py``); nothing parses messages.

Kinds:

    config      the input must change: an invalid mission config, an unknown collector
                type or transform, a malformed capture payload
    not_found   a named thing does not exist: a mission, an archive, a template
    conflict    the operation would clobber something that differs (a mission config)
    query       the SQL failed against the archive's tables
    transient   a failure retrying later may fix: HTTP 429 / 5xx, a transport error
    auth        the remote refused the credentials (HTTP 401 / 403)
    collection  a collector failed for a reason not otherwise classified
    unknown     anything else
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ErrorKind = Literal[
    "config",
    "not_found",
    "conflict",
    "query",
    "transient",
    "auth",
    "collection",
    "unknown",
]


@dataclass(frozen=True)
class Problem:
    """One validation finding, located by a path into the config (``collectors[2].type``)."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class ReconError(Exception):
    """Base for all recon errors."""

    kind: ErrorKind = "unknown"

    def __init__(self, message: str, *, kind: ErrorKind | None = None) -> None:
        super().__init__(message)
        if kind is not None:
            self.kind = kind


class ConfigError(ReconError):
    """Invalid configuration. ``problems`` lists every finding, not just the first."""

    kind: ErrorKind = "config"

    def __init__(self, message: str, problems: list[Problem] | None = None) -> None:
        super().__init__(message)
        self.problems: list[Problem] = list(problems or [])


class NotFoundError(ReconError):
    """A named mission, archive or template does not exist."""

    kind: ErrorKind = "not_found"


class ConflictError(ReconError):
    """The operation would overwrite something that differs from what it would write."""

    kind: ErrorKind = "conflict"


class QueryError(ReconError):
    """SQL against an archive failed."""

    kind: ErrorKind = "query"


class CollectionError(ReconError):
    """Collection failed. ``kind`` says whether retrying (transient) or a new key (auth) helps."""

    kind: ErrorKind = "collection"
