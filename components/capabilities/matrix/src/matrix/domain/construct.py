"""The Construct — one run's blackboard: typed tables of artifacts, in the order they landed.

A flow's members exchange values through **topics** (slick SD-08: a binding maps a port to a
topic; topics are the rendezvous). Each topic carries exactly one type, so a topic is a table
with one schema, and the Construct is the set of those tables for one run. Rows are
:class:`Artifact` records: the value plus who produced it, on which port, when.

Rows are append-only and the executor is their only writer. Components never receive the
Construct; they get :class:`~matrix.domain.flow.Inputs` holding just what they bound. Several
producers on one topic (fan-in, slick SD-09) simply append rows to the same table.

Persisted, a Construct is a directory of typed JSONL tables (see ``ConstructStore``), the
same shape a recon archive and ix results use, so one reader serves all three.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from matrix.domain.errors import NotFoundError

#: The producer recorded for a value supplied to the flow from outside (a flow input).
INPUT_PRODUCER = "$input"


class Artifact(BaseModel):
    """One value produced on one topic during a run, with its provenance. Immutable."""

    model_config = ConfigDict(frozen=True)

    id: str
    run_id: str
    topic: str
    type_url: str
    #: The member alias that produced it, or ``$input`` for a flow input.
    producer: str
    #: The producer's port name (the flow input's name for ``$input``).
    port: str
    value: Any
    #: Which repetition of the flow produced it; 0 for a single run.
    episode: int = 0
    timestamp: datetime

    @staticmethod
    def create(
        *,
        run_id: str,
        topic: str,
        type_url: str,
        producer: str,
        port: str,
        value: Any,
        episode: int = 0,
    ) -> Artifact:
        """Stamp an id and a UTC time."""
        return Artifact(
            id=uuid.uuid4().hex,
            run_id=run_id,
            topic=topic,
            type_url=type_url,
            producer=producer,
            port=port,
            value=value,
            episode=episode,
            timestamp=datetime.now(UTC),
        )


class Construct:
    """Append-only typed tables, one per topic, plus the ledger of every row in order."""

    def __init__(self, topics: dict[str, str] | None = None) -> None:
        #: topic -> the one type_url it carries
        self._types: dict[str, str] = dict(topics or {})
        self._ledger: list[Artifact] = []
        self._rows: dict[str, list[Artifact]] = {}

    def append(self, artifact: Artifact) -> None:
        """Add a row. Only the executor (and stores loading a saved run) call this."""
        known = self._types.setdefault(artifact.topic, artifact.type_url)
        if known != artifact.type_url:
            raise ValueError(
                f"topic {artifact.topic!r} carries {known!r}, not {artifact.type_url!r}"
            )
        self._ledger.append(artifact)
        self._rows.setdefault(artifact.topic, []).append(artifact)

    def rows(self, topic: str) -> tuple[Artifact, ...]:
        """Every artifact on ``topic``, in the order it landed. Empty if none."""
        return tuple(self._rows.get(topic, ()))

    def values(self, topic: str) -> tuple[Any, ...]:
        """The values on ``topic``, in order."""
        return tuple(a.value for a in self._rows.get(topic, ()))

    def last(self, topic: str) -> Artifact:
        """The most recent artifact on ``topic``. Raises NotFoundError listing the topics."""
        rows = self._rows.get(topic)
        if not rows:
            available = ", ".join(sorted(self._rows)) or "(none)"
            raise NotFoundError(
                f"no artifact on topic {topic!r}. Topics with artifacts: {available}",
                topic=topic,
            )
        return rows[-1]

    def by_type(self, type_url: str) -> tuple[Artifact, ...]:
        """Every artifact of ``type_url`` across topics, in ledger order."""
        return tuple(a for a in self._ledger if a.type_url == type_url)

    def topics(self) -> dict[str, str]:
        """Each topic seen or declared, with the type it carries."""
        return dict(self._types)

    @property
    def ledger(self) -> tuple[Artifact, ...]:
        """Every artifact, in the order it was appended."""
        return tuple(self._ledger)

    def __contains__(self, topic: str) -> bool:
        return bool(self._rows.get(topic))

    def __len__(self) -> int:
        return len(self._ledger)

    def __repr__(self) -> str:
        trace = ", ".join(f"{a.producer}.{a.port} → {a.topic}" for a in self._ledger)
        return f"Construct({len(self._ledger)} artifacts: {trace})" if trace else "Construct(empty)"
