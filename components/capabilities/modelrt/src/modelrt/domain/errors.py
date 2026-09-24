"""modelrt errors — every one names what failed and where to look.

A caller that catches ``ModelRuntimeError`` catches everything this package raises on
purpose. Nothing here ever carries a secret: backends scrub keys from provider messages
before wrapping them (see ``adapters/_out/_scrub.py``).
"""

from __future__ import annotations


class ModelRuntimeError(Exception):
    """Base class for every error modelrt raises deliberately."""


class ConfigError(ModelRuntimeError):
    """A model registry or backend configuration is invalid.

    The message always names the source (file or mapping) and the key path, so a typo in
    YAML is fixed by reading the error, not by reading this package.
    """


class UnknownModelError(ModelRuntimeError, KeyError):
    """A caller asked for a model name the registry does not hold."""

    def __str__(self) -> str:  # KeyError quotes its argument; keep the message readable
        return str(self.args[0]) if self.args else "unknown model"


class SecretError(ModelRuntimeError):
    """A secret reference could not be resolved. Names the reference, never the value."""


class BackendError(ModelRuntimeError):
    """The provider call failed: network, auth, rate limit, bad request."""


class ContractError(ModelRuntimeError):
    """A backend returned something other than the port's declared shape.

    The same double-entry idea as matrix's ContractError: the port declares what comes
    back, and the runtime checks it rather than trusting the adapter.
    """


class SchemaError(ModelRuntimeError):
    """Structured output did not validate after every allowed attempt.

    Carries the last raw text and the last validation message so the caller can decide
    what to do with a model that will not produce the shape it was asked for.
    """

    def __init__(self, message: str, *, last_text: str, validation: str, attempts: int) -> None:
        super().__init__(message)
        self.last_text = last_text
        self.validation = validation
        self.attempts = attempts
