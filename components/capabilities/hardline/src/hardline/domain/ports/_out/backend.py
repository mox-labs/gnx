"""ModelBackend — the one port a provider adapter implements.

Two members. ``structured_modes`` lets composition reject a registry row that asks a
backend for a structured mode it cannot honour, before any call is made: a frozenset that
always holds ``prompt`` (the schema stated in an instruction, which any backend can send).
``complete`` runs one attempt, async; retries, schema validation and provenance belong to the
runtime. It returns a ``RawCompletion``, whose ``finish_reason`` it should set, or no reply it
returns is ever a whole answer. ``hardline.backend_problems`` and ``hardline.answer_problems``
check all of this against an installed hardline.

Two optional members. A backend whose rows can be wrong in ways it alone knows (its
``options``, a key it cannot run without) defines ``check(spec)`` (``CheckedBackend``), and the
runtime calls it for every row when it is built, so a row that would fail its first call fails
composition instead, and ``hardline check`` with it. A backend that holds connections defines
``aclose()`` (``ClosableBackend``), and the runtime's own ``aclose()`` calls it; the mock has
none to close. A backend that serves one provider declares ``default_base_url``, which the
composition root writes into each of its rows that names no ``base_url``. And a backend that can
call tools declares ``supports_tools = True``: it sends a ``Request``'s ``tools`` and its
messages' tool parts (an assistant turn's ``tool_calls``, a ``tool`` turn's result) on its wire,
and returns the calls the model asked for as ``RawCompletion.tool_calls``. The runtime refuses
a call that offers tools, or sends tool turns, to a row whose backend does not declare it, so a
backend written before tool calling never silently drops them.

Structural typing: an adapter satisfies this without importing hardline's classes, the
same way a matrix Component does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hardline.domain.types import ModelSpec, RawCompletion, Request


@runtime_checkable
class ModelBackend(Protocol):
    @property
    def structured_modes(self) -> frozenset[str]: ...

    async def complete(
        self, spec: ModelSpec, request: Request, api_key: str | None
    ) -> RawCompletion: ...


@runtime_checkable
class CheckedBackend(Protocol):
    def check(self, spec: ModelSpec) -> Sequence[str]:
        """What is wrong with ``spec`` for this backend; empty when it can serve the row.

        Each problem starts with the row key it is about, as ``options.token_param: ...``.
        """
        ...


@runtime_checkable
class ClosableBackend(Protocol):
    async def aclose(self) -> None:
        """Close the connections opened in the running event loop. Callable more than once."""
        ...
