"""Matrix types: Artifact, Construct, ConstructView, Component, AgentResponse.

Kind-agnostic DAG orchestration types + agent response model.
Artifacts are self-describing data units following xDS TypedExtensionConfig.

Type URL convention: ``<namespace>.v<version>/<resource>`` — e.g. ``matrix.v1/runtime.claude-sdk``,
``ix.v1/probe.stimulus``, ``hardline.v1/completion``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel

from matrix.domain.errors import ContractError, NotFoundError

if TYPE_CHECKING:
    from collections.abc import Iterable


class TypedStruct(NamedTuple):
    """Self-describing output — component declares both type and value.

    Follows the xDS TypedExtensionConfig pattern. The component owns its
    output contract: type_url says what it is, value carries the data.

    NamedTuple for zero overhead, structural typing compatibility,
    and no import required for external consumers.
    """

    type_url: str
    value: Any


class Artifact(BaseModel, frozen=True):
    """Self-describing data unit in the Construct ledger.

    Follows the xDS TypedExtensionConfig pattern: every piece of data
    flowing through the DAG is typed, traceable, and replayable.

    type_url convention: <namespace>.v<version>/<resource>
      - matrix.v1/agent.response
      - ix.v1/eval.readings
      - test.v1/...
    """

    type_url: str
    producer: str
    data: Any
    id: str
    timestamp: datetime

    @staticmethod
    def create(*, type_url: str, producer: str, data: Any) -> Artifact:
        """Factory that stamps UUID + UTC timestamp."""
        return Artifact(
            type_url=type_url,
            producer=producer,
            data=data,
            id=str(uuid.uuid4()),
            timestamp=datetime.now(UTC),
        )


class Construct:
    """Append-only artifact ledger for DAG execution.

    Components append typed Artifacts. Downstream components query by type_url.
    The ledger IS the trace — self-describing, replayable execution history.
    """

    def __init__(self) -> None:
        self._ledger: list[Artifact] = []
        self._by_type: dict[str, list[Artifact]] = {}

    def append(self, artifact: Artifact) -> None:
        """Append an artifact to the ledger."""
        self._ledger.append(artifact)
        self._by_type.setdefault(artifact.type_url, []).append(artifact)

    def query(self, type_url: str) -> list[Artifact]:
        """All artifacts of the given type, in append order."""
        return list(self._by_type.get(type_url, []))

    def last(self, type_url: str) -> Artifact:
        """Most recent artifact of the given type. Raises :class:`NotFoundError` on missing."""
        artifacts = self._by_type.get(type_url)
        if not artifacts:
            available = ", ".join(sorted(self._by_type)) or "(none)"
            raise NotFoundError(f"No artifact for type_url {type_url!r}. Available: {available}")
        return artifacts[-1]

    @property
    def ledger(self) -> tuple[Artifact, ...]:
        """Immutable snapshot of the full ledger."""
        return tuple(self._ledger)

    def __getitem__(self, type_url: str) -> Any:
        """The data of the most recent artifact of ``type_url`` — the common read."""
        return self.last(type_url).data

    def __contains__(self, type_url: str) -> bool:
        return type_url in self._by_type

    def kinds(self) -> frozenset[str]:
        """The distinct type URLs present in the ledger."""
        return frozenset(self._by_type)

    def __len__(self) -> int:
        """The number of artifacts in the ledger (``len(construct.kinds())`` counts kinds)."""
        return len(self._ledger)

    def __repr__(self) -> str:
        """The ledger in order, one producer → kind per artifact — what a debugger needs."""
        trace = ", ".join(f"{a.producer} → {a.type_url}" for a in self._ledger)
        return f"Construct({len(self._ledger)} artifacts: {trace})" if trace else "Construct(empty)"


@runtime_checkable
class ConstructReader(Protocol):
    """The read side of a Construct — what a Component is handed.

    The Orchestrator hands each component a :class:`ConstructView` restricted to the kinds
    it declared in ``requires``. A plain :class:`Construct` also satisfies this protocol,
    which is what tests and ad-hoc callers use.
    """

    def query(self, type_url: str) -> list[Artifact]: ...

    def last(self, type_url: str) -> Artifact: ...

    def __getitem__(self, type_url: str) -> Any: ...

    def __contains__(self, type_url: str) -> bool: ...

    def kinds(self) -> frozenset[str]: ...


class ConstructView:
    """A Construct as one component is allowed to see it: only its declared ``requires``.

    This is the state contract made real. Before, ``requires`` was checked at compile time
    and then the whole ledger was handed over at run time, so a component could read a kind
    it never declared and the compiler's edges would be wrong without anyone knowing. Now an
    undeclared read raises :class:`ContractError` naming the component, the kind, and what it
    did declare.
    """

    def __init__(self, construct: Construct, *, reader: str, allowed: Iterable[str]) -> None:
        self._construct = construct
        self._reader = reader
        self._allowed = frozenset(allowed)

    def _check(self, type_url: str) -> None:
        if type_url not in self._allowed:
            declared = ", ".join(sorted(self._allowed)) or "(nothing)"
            raise ContractError(
                f"{self._reader!r} read {type_url!r}, which it does not declare in requires "
                f"(declared: {declared})"
            )

    def query(self, type_url: str) -> list[Artifact]:
        self._check(type_url)
        return self._construct.query(type_url)

    def last(self, type_url: str) -> Artifact:
        self._check(type_url)
        return self._construct.last(type_url)

    def __getitem__(self, type_url: str) -> Any:
        self._check(type_url)
        return self._construct[type_url]

    def __contains__(self, type_url: str) -> bool:
        self._check(type_url)
        return type_url in self._construct

    def kinds(self) -> frozenset[str]:
        return self._construct.kinds() & self._allowed


class AgentResponse(BaseModel, frozen=True):
    """Structured response from an agent execution.

    Captures everything the Agent SDK returns: content, tool calls,
    token usage, timing, cost, and turn count. Replaces the old
    string-only return that threw away structured SDK data.

    tool_calls are plain dicts ({name, input}) — no wrapper type.
    Stays flat for DataFrame compatibility.
    """

    content: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    tokens_input: int = 0
    tokens_output: int = 0
    duration_ms: int = 0
    cost_usd: float | None = None
    num_turns: int = 0
    #: The model family that produced this response (``claude``, ``qwen``, ``gemini``...),
    #: stamped by the runtime. What an eval harness reads to know whether a judge was out of
    #: family with the subject it judged. ``None`` means the runtime could not say.
    family: str | None = None
    #: The model id or registry name the runtime used, when it knows it.
    model: str | None = None


@runtime_checkable
class Component(Protocol):
    """A processing unit in the DAG. Structural typing — implement without importing matrix."""

    name: str
    requires: frozenset[str]
    provides: str

    async def run(self, construct: ConstructReader) -> TypedStruct:
        """Execute and return self-describing output.

        ``construct`` is restricted to the kinds in ``requires``; reading anything else
        raises ContractError.

        Returns TypedStruct(type_url, value) — the component owns its output
        contract. The Orchestrator validates type_url matches self.provides
        (double-entry bookkeeping) and unwraps value into an Artifact.
        """
        ...
