"""ModelBackend — the one port a provider adapter implements, and the four things a backend may
also declare it can do.

Two members are required. ``structured_modes`` lets composition reject a registry row that asks a
backend for a structured mode it cannot honour, before any call is made: a frozenset that
always holds ``prompt`` (the schema stated in an instruction, which any backend can send).
``complete`` runs one attempt, async; retries, schema validation and provenance belong to the
runtime. It is handed an ``Endpoint``, the registry row as a backend needs it (where to send,
how, and the row's ``options``), the ``Request``, and the key the runtime resolved, if the row
names one; the runtime's retry, fallback and limit policy and the key's reference stay with the
runtime. It returns a ``RawCompletion``, whose ``finish_reason`` it should set, or no reply it
returns is ever a whole answer. ``hardline.backend_problems`` and ``hardline.answer_problems``
check all of this against an installed hardline.

What only some backends do is a protocol of its own, below, each ``runtime_checkable``. hardline
reads each with ``isinstance``, once, from the backend as it was loaded, before the composition
root wraps it for telemetry; the wrapper declares none of them. Each one's absence has a stated
meaning that changes nothing else the runtime computes:

- ``CheckedBackend``, ``check(endpoint)``: what is wrong with a row for this backend (its
  ``options``, a key it cannot run without). The runtime calls it for every row when it is
  built, so a row that would fail its first call fails composition instead, and ``hardline
  check`` with it. Without it, every row the backend can serve by its modes is accepted.
- ``ClosableBackend``, ``aclose()``: the backend holds connections, and the runtime's own
  ``aclose()`` closes them. Without it, there is nothing to close.
- ``ToolCallingBackend``, ``supports_tools`` true: the backend sends a ``Request``'s ``tools`` and
  its messages' tool parts (an assistant turn's ``tool_calls``, a ``tool`` turn's result) on its
  wire, and returns the calls the model asked for as ``RawCompletion.tool_calls``. Without it, a
  call that offers tools or sends tool turns to a row of this backend is refused before anything
  is sent, so a backend written before tool calling never silently drops them. The value is read
  as ``is True``: a backend that sets it to anything else cannot call tools.
- ``LocatedBackend``, ``default_base_url``: the backend serves one provider, and the composition
  root writes this URL into each of its rows that names no ``base_url``, so the row records where
  it sends. Without it, a row names its own location or has none.

Structural typing: an adapter satisfies these without importing hardline's classes, the same way
a matrix Component does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hardline.domain.types import Endpoint, RawCompletion, Request


@runtime_checkable
class ModelBackend(Protocol):
    @property
    def structured_modes(self) -> frozenset[str]: ...

    async def complete(
        self, endpoint: Endpoint, request: Request, api_key: str | None
    ) -> RawCompletion: ...


@runtime_checkable
class CheckedBackend(Protocol):
    def check(self, endpoint: Endpoint) -> Sequence[str]:
        """What is wrong with ``endpoint`` for this backend; empty when it can serve the row.

        Each problem starts with the row key it is about, as ``options.token_param: ...``.
        """
        ...


@runtime_checkable
class ClosableBackend(Protocol):
    async def aclose(self) -> None:
        """Close the connections opened in the running event loop. Callable more than once."""
        ...


@runtime_checkable
class ToolCallingBackend(Protocol):
    @property
    def supports_tools(self) -> Literal[True]:
        """True: the backend carries tools and tool turns on its wire."""
        ...


@runtime_checkable
class LocatedBackend(Protocol):
    @property
    def default_base_url(self) -> str:
        """Where a row of this backend that names no ``base_url`` sends: an http(s) URL."""
        ...
