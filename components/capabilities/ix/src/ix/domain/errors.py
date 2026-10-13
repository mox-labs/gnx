"""ix errors and the one exit-code table. ``except IxError`` catches everything ix raises on
purpose.

Every error has a ``kind`` that says what the caller should do next, a message that names
what is wrong, and a ``fix``. :data:`EXIT_CODES` says what each process exit code means;
the command line, the skill document and the tests all read that table and no other.
"""

from __future__ import annotations

from typing import Any

#: The one exit-code table: what each code tells the caller to do next. Help text, the
#: skill document and the tests read this and nothing else.
EXIT_CODES: dict[int, str] = {
    0: "done; for validate, the experiment is valid",
    1: "a failure ix did not classify",
    2: "usage: a bad flag or argument",
    3: "config: the experiment is invalid, or register met a changed experiment",
    4: "not found: no experiment file, or no registration",
    9: "deviations: the experiment differs from its registration (ix deviations)",
}

#: Exit code by outcome. ``deviations`` is not an error kind: it is how ``ix deviations``
#: exits when it finds any.
EXIT_BY_KIND: dict[str, int] = {
    "ok": 0,
    "failure": 1,
    "usage": 2,
    "config": 3,
    "conflict": 3,
    "not_found": 4,
    "deviations": 9,
}


class IxError(Exception):
    """Base class for every error ix raises deliberately."""

    kind: str = "failure"

    def __init__(self, message: str = "", *, fix: str | None = None):
        super().__init__(message)
        self.fix = fix
        #: Validation problems behind the error, when it has any (records with ``document``).
        self.problems: tuple[Any, ...] = ()
        #: Registration deviations behind the error, when it has any.
        self.deviations: tuple[Any, ...] = ()

    @property
    def exit_code(self) -> int:
        return EXIT_BY_KIND.get(self.kind, EXIT_BY_KIND["failure"])


class ConfigError(IxError, ValueError):
    """An experiment, a genre, a kind or a stored record is invalid."""

    kind = "config"


class NotFoundError(IxError):
    """Something named does not exist: an experiment file, or its registration."""

    kind = "not_found"


class ProbeSourceError(ConfigError):
    """A probe source could not be read: a missing directory, a probe file that does not
    parse, an option it does not take. Carries the ``path`` of the offending key."""

    def __init__(self, message: str, *, fix: str, path: str = "probes"):
        super().__init__(message, fix=fix)
        self.path = path
