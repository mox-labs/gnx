"""hardline errors — every one names what failed and where to look.

A caller that catches ``HardlineError`` catches everything this package raises on
purpose. Nothing here ever carries a secret: backends scrub keys from provider messages
before wrapping them (see ``adapters/_out/_scrub.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hardline.domain.types import Attempt, Limit, Problem, Usage

#: Why a provider call failed, in terms a caller can act on (cf. Google AIP-193's
#: machine-readable reason). The first three are transient: the same call may succeed later.
Reason = Literal["rate_limit", "timeout", "unavailable", "auth", "bad_request", "unknown"]
RETRYABLE: frozenset[str] = frozenset({"rate_limit", "timeout", "unavailable"})


class HardlineError(Exception):
    """Base class for every error hardline raises deliberately.

    ``fix`` is the next step that can succeed, when the error knows one more exact than its
    kind's; ``problems`` lists each thing wrong, by key path, when there is more than a message
    to say (a registry with three bad rows is one error with three problems).
    """

    def __init__(
        self, message: str, *, fix: str | None = None, problems: Sequence[Problem] = ()
    ) -> None:
        super().__init__(message)
        self.fix = fix
        self.problems: tuple[Problem, ...] = tuple(problems)


class ConfigError(HardlineError):
    """A model registry or backend configuration is invalid.

    The message always names the source (file or mapping) and the key path, so a typo in
    YAML is fixed by reading the error, not by reading this package.
    """


class UnknownModelError(HardlineError, KeyError):
    """A caller asked for a model name the registry does not hold."""

    def __str__(self) -> str:  # KeyError quotes its argument; keep the message readable
        return str(self.args[0]) if self.args else "unknown model"


class SecretError(HardlineError):
    """A secret reference could not be resolved. Names the reference, never the value."""


class BackendError(HardlineError):
    """The provider call failed. ``reason`` says why; ``retryable`` says whether to try again.

    ``retry_after`` is the provider's own hint in seconds (a ``Retry-After`` header), when
    it sent one. The runtime retries retryable failures with backoff and then tries the
    row's fallbacks; a non-retryable one (bad key, malformed request) is raised at once,
    because a retry or another model would hide a configuration fault.
    """

    #: Set by the runtime on the error a caller sees: every backend call the logical call made,
    #: and the usage those calls reported (an ``extract`` repair that answered included).
    attempt_log: tuple[Attempt, ...] = ()
    usage: Usage | None = None
    #: Set by the runtime when one of the call's ``limits`` stopped it, naming which; None when
    #: the call ran out of retries and fallbacks, or failed outright.
    limit: Limit | None = None

    def __init__(
        self, message: str, *, reason: Reason = "unknown", retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.reason: Reason = reason
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.reason in RETRYABLE


class ContractError(HardlineError):
    """A backend returned something other than the port's declared shape.

    The same double-entry idea as matrix's ContractError: the port declares what comes
    back, and the runtime checks it rather than trusting the adapter.
    """


class SchemaError(HardlineError):
    """Structured output did not validate after every allowed attempt.

    Carries the last raw text and the last validation message so the caller can decide
    what to do with a model that will not produce the shape it was asked for.
    """

    def __init__(
        self,
        message: str,
        *,
        last_text: str,
        validation: str,
        attempts: int,
        attempt_log: tuple[Attempt, ...] = (),
        usage: Usage | None = None,
        limit: Limit | None = None,
    ) -> None:
        super().__init__(message)
        self.last_text = last_text
        self.validation = validation
        self.attempts = attempts
        #: Every backend call made, and the usage they reported: what the failure cost.
        self.attempt_log = attempt_log
        self.usage = usage
        #: The one of the call's ``limits`` that stopped the repairs; None when they ran out.
        self.limit = limit
