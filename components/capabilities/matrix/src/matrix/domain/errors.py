"""Matrix errors. ``except MatrixError`` catches everything matrix raises on purpose.

Every error carries a **kind**, what the caller can do about it, so nobody parses messages.
:data:`EXIT_CODES` maps each kind a command exits with to its code, :data:`EXIT_MEANINGS`
says what each means and :data:`EXIT_NEXT` what to do about it. The codes are those agreed in
``components/capabilities/exit-codes.json``, and the tests hold this table to it. Two kinds
are finer than an exit: ``contract`` and ``component`` exit as ``run_failed``
(:data:`EXITS_AS`).

Every error also carries a ``fix`` (one actionable sentence, or None), ``problems`` (each a
:class:`~matrix.domain.problems.Problem` with its key path), ``complete`` and ``details``
(structured fields), which :meth:`MatrixError.payload` renders as the ``matrix.v1.error``
envelope agreed in ``components/capabilities/error-envelope.json``. A lookup miss is also a
``KeyError`` and a config problem also a ``ValueError``, so stdlib handlers keep working.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

from matrix.domain.problems import counted, summary

if TYPE_CHECKING:
    from collections.abc import Sequence

    from matrix.domain.agent import AgentResponse
    from matrix.domain.construct import Construct
    from matrix.domain.executor import Run
    from matrix.domain.model_call import ModelCall
    from matrix.domain.problems import Problem

ErrorKind = Literal[
    "usage", "config", "not_found", "contract", "component", "run_failed", "store",
    "transient", "auth", "interrupted", "unknown",
]  # fmt: skip

#: The exit code of success and of each kind a command exits with. The shared kinds keep the
#: codes agreed in ``exit-codes.json``; matrix has no kind of its own that needs a code.
EXIT_CODES: dict[str, int] = {
    "ok": 0,
    "unknown": 1,
    "usage": 2,
    "config": 3,
    "not_found": 4,
    "transient": 5,
    "auth": 6,
    "run_failed": 8,
    "store": 9,
    "interrupted": 130,
}

#: Error kinds that exit under another kind's code: a component that broke its contract or
#: raised fails the run. The envelope keeps the finer kind (``error.details.cause``).
EXITS_AS: dict[str, str] = {
    "contract": "run_failed",
    "component": "run_failed",
}

#: What each kind in :data:`EXIT_CODES` means.
EXIT_MEANINGS: dict[str, str] = {
    "ok": "done",
    "unknown": "a failure matrix did not classify: a bug in matrix",
    "usage": "a bad command, flag or argument",
    "config": "a config, definition, wiring, input or argument is invalid",
    "not_found": "a name, type URL, file or run that is not registered, configured or there",
    "transient": "unavailable, rate-limited or timed out",
    "auth": "credentials missing or refused",
    "run_failed": "the run ran and a component failed: it raised or broke what it declares",
    "store": "the run's record could not be written or read",
    "interrupted": "the command was interrupted (Ctrl-C, SIGTERM) before it finished",
}

#: What to do next on each kind in :data:`EXIT_CODES`.
EXIT_NEXT: dict[str, str] = {
    "ok": "go on",
    "unknown": "report it as a bug in matrix, with the command that raised it",
    "usage": "correct the command; `matrix <command> --help` lists its options",
    "config": "fix each problem listed (`error.problems`: path, kind, fix); `matrix config "
    "--sources` shows which file set a value, `matrix describe <type URL>` the schema it must "
    "meet",
    "not_found": "use a name the message lists; `matrix catalog` lists every registered type "
    "URL, and a missing one needs the package that provides it installed",
    "transient": "retry later, or give the component a retry policy (`matrix.limits.retry`)",
    "auth": "set or renew the credentials the runtime needs",
    "run_failed": "read `error.details.cause` and the run's record (`matrix.v1.run`: failures), "
    "fix the component or what it was given, then run again (`--from RUN_ID` reuses what "
    "completed)",
    "store": "fix the store (space, permissions, a run id already taken), then run again",
    "interrupted": "run the command again; the record says how far the run got",
}

#: The repository tag of this release. matrix is not published to a package index: it is
#: installed from the repository at this tag. The tests hold it to the package's version.
RELEASE_TAG = "matrix-v0.7.0"

#: Where this release is installed from, as a uv source.
SOURCE = (
    f"git+https://github.com/mox-labs/gnx@{RELEASE_TAG}#subdirectory=components/capabilities/matrix"
)

#: This release as a requirement, for ``uv add`` and ``uv run --with``.
REQUIREMENT = f"matrix @ {SOURCE}"


class MatrixError(Exception):
    """Base class for every error matrix raises deliberately.

    ``problems`` lists each thing wrong (a :class:`~matrix.domain.problems.Problem` with its
    path); ``complete`` is False when checking stopped before every problem was found, so
    more may follow once these are fixed. ``details`` holds the error's own facts.
    """

    kind: ErrorKind = "unknown"

    def __init__(
        self,
        message: str,
        *,
        fix: str | None = None,
        problems: Sequence[Problem] = (),
        complete: bool = True,
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.fix = fix
        self.problems: tuple[Problem, ...] = tuple(problems)
        self.complete = complete
        self.details = details

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else type(self).__name__

    @property
    def exit_kind(self) -> str:
        """The kind whose code this error exits with."""
        return EXITS_AS.get(self.kind, self.kind)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.exit_kind, EXIT_CODES["unknown"])

    def payload(self) -> dict[str, Any]:
        """The ``matrix.v1.error`` envelope, the shape ``error-envelope.json`` agrees:
        ``{"schema", "error": {"kind", "message", "fix", "complete", "problems",
        "details"}}``. Every key is always present; ``fix`` falls back to what to do next on
        the error's kind."""
        details = {k: v for k, v in self.details.items() if v is not None}
        body = {
            "kind": self.kind,
            "message": self.message,
            "fix": self.fix or EXIT_NEXT.get(self.exit_kind, EXIT_NEXT["unknown"]),
            "complete": self.complete,
            "problems": [p.document() for p in self.problems],
            "details": details,
        }
        return {"schema": "matrix.v1.error", "error": body}


class UsageError(MatrixError):
    """A command was given a flag or an argument it does not take."""

    kind: ErrorKind = "usage"


class ConfigError(MatrixError, ValueError):
    """A config, agent definition, wiring, or component config is invalid.

    Always names where the bad value came from (a file, a key path, a type URL).
    """

    kind: ErrorKind = "config"


def missing_dependency(*, needed_by: str, module: str) -> ConfigError:
    """The error for selecting something whose dependency cannot be imported. matrix depends
    on every one it imports, so the install is incomplete; ``details`` carries the missing
    ``module`` so a caller can act without parsing the message.
    """
    return ConfigError(
        f"{needed_by} requires {module!r}, which is not installed",
        fix=reinstall(),
        missing=module,
    )


def reinstall() -> str:
    """How to repair an install that lacks one of matrix's dependencies: in a project, and in
    an installed tool."""
    return (
        f'reinstall matrix, which depends on it: uv add "{REQUIREMENT}" in a project, or '
        "uv tool install --reinstall <the tool's requirement> for an installed tool"
    )


def invalid(
    what: str,
    problems: Sequence[Problem],
    *,
    complete: bool = True,
    fix: str | None = None,
    error: type[ConfigError] | None = None,
    **details: Any,
) -> ConfigError:
    """One :class:`ConfigError` for every problem with ``what``, listed in its message. Its
    ``fix`` is the one problem's own when there is one and no other is given."""
    kind = error or ConfigError
    if fix is None and len(problems) == 1 and problems[0].fix:
        fix = problems[0].fix
    return kind(
        f"{what} ({counted(problems)}):\n{summary(problems)}",
        fix=fix or "fix each problem listed: its path names the key, its fix what to write",
        problems=problems,
        complete=complete,
        **details,
    )


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
    "refused": "component",
    "failed": "component",
}


class _ClassifiedError(MatrixError):
    """A runtime's failure with a classified ``reason``: whose problem it is, so a harness can
    keep infrastructure failures out of an agent's or a model's score.
    ``unavailable``/``rate_limited``/``timeout`` are worth retrying, ``auth`` needs
    credentials, ``incapable`` means the runtime cannot honour what it was asked (a config
    fault), ``refused`` means the model declined, and ``failed`` means it ran and broke.

    ``retry_after_s`` is the backend's own hint (a ``Retry-After``), when it gave one; a retry
    policy waits that long, up to its cap.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: RuntimeReason = "failed",
        fix: str | None = None,
        retry_after_s: float | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message, fix=fix, reason=reason, retry_after_s=retry_after_s, **details)
        self.reason: RuntimeReason = reason
        self.retry_after_s = retry_after_s

    @property
    def kind(self) -> ErrorKind:  # type: ignore[override]
        # The reason vocabulary is open: a runtime may report one matrix does not know, and
        # that is a failure of the session, never a crash of the error itself.
        return _REASON_KIND.get(self.reason, "component")

    @property
    def retryable(self) -> bool:
        return self.kind == "transient"


class AgentRuntimeError(_ClassifiedError):
    """An agent runtime could not complete a session, with a classified ``reason``. Every
    agent runtime raises this for an execution failure, whatever its backend.

    ``response`` is what the runtime knew of the failed session, when it knew anything: its
    id, usage, cost, turns and the model that answered (claude-sdk's, from a result that says
    the session failed). The session's row in ``matrix.v1.session`` is built from it."""

    def __init__(
        self, message: str, *, response: AgentResponse | None = None, **kwargs: Any
    ) -> None:
        super().__init__(message, **kwargs)
        self.response = response


class ModelRuntimeError(_ClassifiedError):
    """A model runtime could not complete a call, with a classified ``reason``. Every model
    runtime raises this for an execution failure, whatever its backend.

    ``call`` is what the runtime knew of the failed call, when it knew anything: for hardline,
    every attempt made (``attempt_log``) and the usage they reported. The call's row in
    ``matrix.v1.session`` is built from it."""

    def __init__(self, message: str, *, call: ModelCall | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.call = call


class RunError(MatrixError):
    """A run failed. ``run`` is the partial :class:`Run`; the component's error is the cause.

    Nothing recorded before the failure is lost: ``run.construct`` holds it.
    ``run.failures`` lists every component that failed, ``run.components`` how each one
    ended, and ``run.releases`` what became of each deferred release. ``component`` is the
    name of the first to fail (``$input`` when a run input could not be recorded), and
    ``fix`` the cause's own, when it has one.
    """

    def __init__(
        self,
        message: str,
        *,
        run: Run,
        component: str,
        cause_kind: ErrorKind,
        fix: str | None = None,
    ) -> None:
        super().__init__(message, fix=fix, component=component, cause=cause_kind, run_id=run.run_id)
        self.run = run
        self.component = component
        #: The kind of the error that failed the run (``contract``, ``component``, ``store``,
        #: ``transient``, ...).
        self.cause_kind: ErrorKind = cause_kind

    @property
    def kind(self) -> ErrorKind:  # type: ignore[override]
        """``run_failed`` when a component raised or broke its contract; otherwise the
        cause's own kind, whose remedy differs (``store``, ``transient``, ``auth``,
        ``config``)."""
        if self.cause_kind in ("contract", "component", "unknown"):
            return "run_failed"
        return self.cause_kind

    @property
    def construct(self) -> Construct:
        return self.run.construct


class RunCancelled(asyncio.CancelledError):  # noqa: N818 - it is a cancellation, not an Error
    """The task awaiting a run was cancelled (by its caller, Ctrl-C or SIGTERM).

    Raised only after the run stopped its components and called every deferred release, so it is
    still a ``CancelledError`` to asyncio and to callers. ``run`` is the partial :class:`Run`;
    its ``status`` says how far it got (``cancelled``, or ``completed``/``failed`` when the
    cancellation arrived as the run was already ending).
    """

    def __init__(self, run: Run) -> None:
        super().__init__(f"run {run.run_id} was cancelled")
        self.run = run
