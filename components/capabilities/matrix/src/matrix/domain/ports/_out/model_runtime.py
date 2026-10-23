"""ModelRuntime: makes one model call for a request.

**Assumes** a request whose ``model`` passed ``check``. **Guarantees** exactly one of: a
:class:`ModelCall` naming the model that answered, or a
:class:`~matrix.domain.errors.ModelRuntimeError` with a ``reason`` (``unavailable``,
``rate_limited``, ``timeout``, ``auth``, ``incapable``, ``refused``, ``failed``). With an
``output`` type, the call's ``value`` is the reply validated against it, or the call raises.
Content is a sample, never guaranteed; a caller judges it, the runtime does not.

``check(model)`` is required: it raises ``ConfigError`` for a model name the runtime cannot
resolve (an unknown name, or none where there is no default), so a step that names it is
refused when it is built, before anything runs.

Four members are optional, read with ``getattr`` as on an agent runtime: ``warnings``;
``resolved``: JSON data for what the runtime settled when it was built that decides how it
answers (hardline's registry), which composition digests into the reuse fingerprint of every
component that calls the runtime; ``aclose()``, an async method that closes the
connections the runtime's calls opened in the running event loop. A connection can be closed
only in its own loop, so the caller that made the calls closes them there: a model step
defers it to the end of the run that made them. The runtime stays usable after it. And
``pydantic_ai_model(model)``: pydantic-ai's ``Model`` on a model name the runtime resolves,
its requests made as the runtime makes its own calls, for a caller that runs pydantic-ai
itself (an evaluator's judge); it raises ``ConfigError`` as ``check`` does, and the ``Model``
it gives has an async ``aclose()`` of its own. A runtime that gives none cannot serve such a
caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matrix.domain.model_call import ModelCall, ModelRequest


@runtime_checkable
class ModelRuntime(Protocol):
    def check(self, model: str | None) -> None: ...

    async def call(self, request: ModelRequest) -> ModelCall: ...
