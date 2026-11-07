"""ModelRuntime: makes one model call for a request.

One call in full: the turns of a conversation, the tools offered, an output type, and the
``max_tokens`` and ``temperature`` overrides (:class:`~matrix.domain.model_call.ModelRequest`).
The reply may ask for tools; the runtime runs none. Whoever holds a loop (an agent runtime)
runs them and calls again, so an agent framework reaches any model runtime through this one
port: the pydantic-ai agent runtime does, and so does :func:`matrix.pydantic_ai_model`.

**Assumes** a request whose ``model`` and ``uses`` passed ``check``. **Guarantees** exactly
one of: a :class:`ModelCall` naming the model that answered, or a
:class:`~matrix.domain.errors.ModelRuntimeError` with a ``reason`` (``unavailable``,
``rate_limited``, ``timeout``, ``auth``, ``incapable``, ``refused``, ``failed``). With an
``output`` type, the call's ``value`` is the reply validated against it, or the call raises.
A part of the request the runtime cannot carry is refused, ``incapable``, never dropped.
Content is a sample, never guaranteed; a caller judges it, the runtime does not.

``check(model, *, uses=frozenset())`` is required: it raises ``ConfigError`` for a model name
the runtime cannot resolve (an unknown name, or none where there is no default), and for
what it cannot do on that model: ``tools`` offered or tool turns sent to a model that cannot
call them, an ``output`` type, or both together. A step or an agent that names it is then
refused when it is built, before anything runs. A runtime that resolves every name and does
everything implements it as a no-op.

What only the runtime knows of a call goes in the call's ``provenance``, under a type URL of
its own whose schema its package registers.

**Optional members** are declared protocols, checked with ``isinstance``
(:mod:`matrix.domain.optional`): ``SupportsWarnings`` (problems a reader of its config should
see, which the plan shows); ``SupportsResolved``, what the runtime settled when it was built
that decides how it answers (a model registry it read), which composition digests into the
reuse fingerprint of every component that calls the runtime; ``SupportsAclose``, which closes
the connections the runtime's calls opened in the running event loop.

**Counted once.** A model runtime counts the tokens of the calls it makes, if anything does;
matrix counts no model call's tokens, and no agent session's whose calls went through a model
runtime (``matrix.domain.agent.BoundAgent``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matrix.domain.model_call import ModelCall, ModelRequest, Use


@runtime_checkable
class ModelRuntime(Protocol):
    def check(self, model: str | None, *, uses: frozenset[Use] = frozenset()) -> None: ...

    async def call(self, request: ModelRequest) -> ModelCall: ...
