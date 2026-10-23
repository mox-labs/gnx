"""CallObserver — what a logical call reports while it runs, to whoever is listening.

Retries, fallbacks and schema repairs are known only to the runtime. It reports each backend
call that failed (``AttemptFailed``), each move to a fallback (``FellBack``) and the answer
(``Answered``) as domain values, and the composition root chooses who hears them: an
OpenTelemetry adapter, a log, a test's recorder. The runtime's default hears nothing.

An observer is called inline, on the call's own task, so it must not block. One that raises is
logged and ignored: observing a call never fails it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from hardline.domain.types import CallEvent


@runtime_checkable
class CallObserver(Protocol):
    def observe(self, event: CallEvent) -> None: ...
