"""hardline errors — every one names what failed and where to look.

A caller that catches ``HardlineError`` catches everything this package raises on
purpose. Nothing here ever carries a secret: backends scrub keys from provider messages
before wrapping them (see ``adapters/_out/_scrub.py``).
"""

from __future__ import annotations

from typing import Literal

#: Why a provider call failed, in terms a caller can act on (cf. Google AIP-193's
#: machine-readable reason). The first three are transient: the same call may succeed later.
Reason = Literal["rate_limit", "timeout", "unavailable", "auth", "bad_request", "unknown"]
RETRYABLE: frozenset[str] = frozenset({"rate_limit", "timeout", "unavailable"})


class HardlineError(Exception):
    """Base class for every error hardline raises deliberately."""


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

    def __init__(self, message: str, *, last_text: str, validation: str, attempts: int) -> None:
        super().__init__(message)
        self.last_text = last_text
        self.validation = validation
        self.attempts = attempts
