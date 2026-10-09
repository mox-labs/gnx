"""Matrix errors. ``except MatrixError`` catches everything matrix raises on purpose.

Every error carries a **kind** — what the caller can do about it — so nobody parses messages
(AIP-193; the canon's rule 18). The kinds are shared with hardline and ix, and each maps to
the exit code their CLIs use:

=============  ====  ==========================================================
kind           exit  meaning, and the caller's next move
=============  ====  ==========================================================
``config``     3     a config, definition, flow or argument is invalid: fix it
``not_found``  4     a name or type URL is not registered or configured
``contract``   1     a component broke its declared ports (a bug in it)
``component``  1     a component raised while running (its failure)
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

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from matrix.domain.construct import Construct

ErrorKind = Literal["config", "not_found", "contract", "component", "transient", "auth", "unknown"]

EXIT_CODES: dict[str, int] = {
    "config": 3,
    "not_found": 4,
    "contract": 1,
    "component": 1,
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
    """A config, agent definition, flow, or component config is invalid.

    Always names where the bad value came from (a file, a key path, a type URL).
    """

    kind: ErrorKind = "config"


class NotFoundError(MatrixError, KeyError):
    """A name or type URL that is not registered or configured. Lists the ones that are."""

    kind: ErrorKind = "not_found"

    def __str__(self) -> str:  # KeyError repr-quotes its argument; keep the sentence readable
        return self.message


class CompilationError(ConfigError):
    """A flow is malformed: an unbound port, a type mismatch on a topic, or a cycle."""


class ContractError(MatrixError):
    """A component broke its declared ports: a missing, extra or ill-typed output."""

    kind: ErrorKind = "contract"


class ComponentError(MatrixError):
    """A component raised while the flow ran. The original exception is the cause."""

    kind: ErrorKind = "component"


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
    """A flow run failed. ``run`` is the partial :class:`Run`; the member's error is the cause.

    Nothing produced before the failure is lost: ``run.construct`` holds it.
    """

    def __init__(self, message: str, *, run: Any, member: str, cause_kind: ErrorKind) -> None:
        super().__init__(message, member=member)
        self.run = run
        self.member = member
        self._cause_kind = cause_kind

    @property
    def kind(self) -> ErrorKind:  # type: ignore[override]
        return self._cause_kind

    @property
    def construct(self) -> Construct:
        result: Construct = self.run.construct
        return result
