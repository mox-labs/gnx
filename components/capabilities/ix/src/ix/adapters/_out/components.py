"""DAG components — ProbeNode, SubjectNode, TrialNode, SensorNode.

Four-node inner DAG, one per probe × trial, run by the native engine:

  ProbeNode ──┐
              ├──▶ TrialNode ──▶ SensorNode
  SubjectNode ┘

Each is a matrix Component. Every read is declared in ``requires``; matrix enforces it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from matrix import TypedStruct

from ix.domain import type_urls
from ix.eval.measure import measure_trial, run_trial

if TYPE_CHECKING:
    from matrix import ConstructReader

    from ix.domain.ports import AgentFactory, Sensor
    from ix.domain.types import Probe, Subject, Trial


class ProbeNode:
    """Data node — provides one probe stimulus."""

    name = "probe"
    requires: frozenset[str] = frozenset()
    provides = type_urls.PROBE

    def __init__(self, probe: Probe) -> None:
        self._probe = probe

    async def run(self, construct: ConstructReader) -> TypedStruct:
        return TypedStruct(type_url=self.provides, value=self._probe)


class SubjectNode:
    """Data node — provides the subject under test."""

    name = "subject"
    requires: frozenset[str] = frozenset()
    provides = type_urls.SUBJECT

    def __init__(self, subject: Subject) -> None:
        self._subject = subject

    async def run(self, construct: ConstructReader) -> TypedStruct:
        return TypedStruct(type_url=self.provides, value=self._subject)


class TrialNode:
    """Agent node — builds the subject's agent and runs the probe through it."""

    name = "trial"
    requires: frozenset[str] = frozenset({type_urls.PROBE, type_urls.SUBJECT})
    provides = type_urls.TRIAL

    def __init__(self, agents: AgentFactory, trial_index: int = 0, run_index: int = 0) -> None:
        self._agents = agents
        self._trial_index = trial_index
        self._run_index = run_index

    async def run(self, construct: ConstructReader) -> TypedStruct:
        probe: Probe = construct[type_urls.PROBE]
        subject: Subject = construct[type_urls.SUBJECT]
        agent = self._agents(subject, self._trial_index, self._run_index)
        trial = await run_trial(agent, probe.id, probe.prompt, self._trial_index)
        return TypedStruct(type_url=self.provides, value=trial)


class SensorNode:
    """Logic node — measures the trial, produces readings."""

    name = "sensor"
    requires: frozenset[str] = frozenset({type_urls.TRIAL})
    provides = type_urls.READINGS

    def __init__(self, sensor: Sensor) -> None:
        self._sensor = sensor

    async def run(self, construct: ConstructReader) -> TypedStruct:
        trial: Trial = construct[type_urls.TRIAL]
        return TypedStruct(type_url=self.provides, value=measure_trial(self._sensor, trial))
