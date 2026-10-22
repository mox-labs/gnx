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

Two members are optional, read with ``getattr`` as on an agent runtime: ``warnings``, and
``resolved``: JSON data for what the runtime settled when it was built that decides how it
answers (hardline's registry). Composition digests it into the reuse fingerprint of every
component that calls the runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matrix.domain.model_call import ModelCall, ModelRequest


@runtime_checkable
class ModelRuntime(Protocol):
    def check(self, model: str | None) -> None: ...

    async def call(self, request: ModelRequest) -> ModelCall: ...
