"""Matrix errors. ``except MatrixError`` catches everything matrix raises on purpose.

Every message names where to look: the config source and key path, the component, or the
registered alternatives. A lookup miss is also a ``KeyError`` so ``except KeyError`` keeps
working; a config problem is also a ``ValueError``, the stdlib's word for a bad value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matrix.domain.types import Construct


class MatrixError(Exception):
    """Base class for every error matrix raises deliberately."""


class ConfigError(MatrixError, ValueError):
    """A matrix config, agent definition, or component config is invalid.

    Always names where the bad value came from (a file, a registry type_url, a definition
    path) so the fix starts from the message.
    """


class NotFoundError(MatrixError, KeyError):
    """A name or type URL that is not registered or configured. Lists the ones that are."""

    def __str__(self) -> str:  # KeyError repr-quotes its argument; keep the sentence readable
        return str(self.args[0]) if self.args else "not found"


class ContractError(MatrixError):
    """A component broke its declared contract: wrong output kind, or an undeclared read."""


class CompilationError(MatrixError):
    """A component graph is malformed: missing producer, duplicate output, or a cycle."""


class ComponentError(MatrixError):
    """A component raised while the DAG ran.

    ``component`` names it; ``construct`` is the ledger as it stood when it failed, so the
    artifacts produced before the failure are not lost. The original exception is the cause.
    """

    def __init__(self, message: str, *, component: str, construct: Construct) -> None:
        super().__init__(message)
        self.component = component
        self.construct = construct


class AgentRuntimeError(MatrixError):
    """An agent runtime could not run a definition: the SDK failed, the provider refused.

    Every runtime raises this for an execution failure, whatever its backend, so a caller
    handles a Claude session and a local model the same way. The backend's own exception is
    the cause.
    """
