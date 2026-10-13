"""Matrix errors. ``except MatrixError`` catches everything matrix raises on purpose.

Every error carries a **kind** — what the caller can do about it — so nobody parses messages
(AIP-193; the canon's rule 18). The kinds are shared with hardline and ix, and each maps to
the exit code their CLIs use:

=============  ====  ==========================================================
kind           exit  meaning, and the caller's next move
=============  ====  ==========================================================
``config``     3     a config, definition, wiring or argument is invalid: fix it
``not_found``  4     a name or type URL is not registered or configured
``contract``   1     a component broke what it declares (a bug in it)
``component``  1     a component raised while running (its failure)
``store``      1     the run's record could not be written or read: fix the store
``transient``  5     unavailable, rate-limited or timed out: retry later
``auth``       6     credentials missing or refused: fix credentials
``unknown``    1     anything else that went wrong
=============  ====  ==========================================================

Every error also carries an optional ``fix`` (one actionable sentence) and ``details``
(structured fields), which :meth:`MatrixError.payload` renders as the JSON error document.
A lookup miss is also a ``KeyError`` and a config problem also a ``ValueError``, so stdlib
handlers keep working.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from matrix.domain.construct import Construct

ErrorKind = Literal[
    "config", "not_found", "contract", "component", "store", "transient", "auth", "unknown"
]

EXIT_CODES: dict[str, int] = {
    "config": 3,
    "not_found": 4,
    "contract": 1,
    "component": 1,
    "store": 1,
    "transient": 5,
    "auth": 6,
    "unknown": 1,
}


class MatrixError(Exception):
    """Base class for every error matrix raises deliberately."""

    kind: ErrorKind = "unknown"

    def __init__(self, message: str, *, fix: str | None = None, **details: Any) -> None:
        super().__init__(message)
        self.fix = fix
        self.details = details

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else type(self).__name__

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.kind]

    def payload(self) -> dict[str, Any]:
        """The machine-readable error: ``{"error": {"kind", "message", "fix", ...details}}``."""
        body: dict[str, Any] = {"kind": self.kind, "message": self.message}
        if self.fix:
            body["fix"] = self.fix
        body.update({k: v for k, v in self.details.items() if v is not None})
        return {"error": body}


class ConfigError(MatrixError, ValueError):
    """A config, agent definition, wiring, or component config is invalid.

    Always names where the bad value came from (a file, a key path, a type URL).
    """

    kind: ErrorKind = "config"


class NotFoundError(MatrixError, KeyError):
    """A name or type URL that is not registered or configured. Lists the ones that are."""

    kind: ErrorKind = "not_found"

    def __str__(self) -> str:  # KeyError repr-quotes its argument; keep the sentence readable
        return self.message


class CompilationError(ConfigError):
    """Components cannot be wired: a kind nobody provides, two providers of one, a cycle."""


class ContractError(MatrixError):
    """A component broke what it declares: a row of a kind it does not provide, or an
    ill-typed one."""

    kind: ErrorKind = "contract"


class ComponentError(MatrixError):
    """A component raised while it ran. The original exception is the cause."""

    kind: ErrorKind = "component"


class StoreError(MatrixError):
    """The run's record could not be kept or read back: a write failed, a run id is already
    taken, or a stored record contradicts itself. A run whose record cannot be kept fails."""

    kind: ErrorKind = "store"


RuntimeReason = Literal[
    "unavailable", "rate_limited", "timeout", "auth", "incapable", "refused", "failed"
]

_REASON_KIND: dict[str, ErrorKind] = {
    "unavailable": "transient",
    "rate_limited": "transient",
    "timeout": "transient",
    "auth": "auth",
    "incapable": "config",
    "refused": "unknown",
    "failed": "unknown",
}


class AgentRuntimeError(MatrixError):
    """An agent runtime could not complete a session, with a classified ``reason``.

    ``reason`` says whose problem it is, so a harness can keep infrastructure failures out of
    an agent's score: ``unavailable``/``rate_limited``/``timeout`` are worth retrying,
    ``auth`` needs credentials, ``incapable`` means the runtime cannot honour the definition
    (a config fault), ``refused`` means the model declined, and ``failed`` means the session
    ran and broke. Every runtime raises this for an execution failure, whatever its backend.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: RuntimeReason = "failed",
        fix: str | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message, fix=fix, reason=reason, **details)
        self.reason: RuntimeReason = reason

    @property
    def kind(self) -> ErrorKind:  # type: ignore[override]
        # The reason vocabulary is open: a runtime may report one matrix does not know, and
        # that is a failure of the session, never a crash of the error itself.
        return _REASON_KIND.get(self.reason, "unknown")

    @property
    def retryable(self) -> bool:
        return self.kind == "transient"


class RunError(MatrixError):
    """A run failed. ``run`` is the partial :class:`Run`; the component's error is the cause.

    Nothing recorded before the failure is lost: ``run.construct`` holds it.
    ``run.failures`` lists every component that failed, ``run.components`` how each one
    ended, and ``run.releases`` what became of each deferred release. ``component`` is the
    name of the first to fail (``$input`` when a run input could not be recorded).
    """

    def __init__(self, message: str, *, run: Any, component: str, cause_kind: ErrorKind) -> None:
        super().__init__(message, component=component)
        self.run = run
        self.component = component
        self._cause_kind = cause_kind

    @property
    def kind(self) -> ErrorKind:  # type: ignore[override]
        return self._cause_kind

    @property
    def construct(self) -> Construct:
        result: Construct = self.run.construct
        return result


class RunCancelled(asyncio.CancelledError):  # noqa: N818 - it is a cancellation, not an Error
    """The task awaiting a run was cancelled (by its caller, Ctrl-C or SIGTERM).

    Raised only after the run stopped its components and called every deferred release, so it is
    still a ``CancelledError`` to asyncio and to callers. ``run`` is the partial :class:`Run`;
    its ``status`` says how far it got (``cancelled``, or ``completed``/``failed`` when the
    cancellation arrived as the run was already ending).
    """

    def __init__(self, run: Any) -> None:
        super().__init__(f"run {run.run_id} was cancelled")
        self.run = run
