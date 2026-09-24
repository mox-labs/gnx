"""SimulatedRuntime — a dry-run AgentRuntime for skill-activation experiments. No API calls.

Implements matrix's AgentRuntime (``run(definition, task) -> AgentResponse``), so it binds
to a subject's definition exactly as a real runtime does and sensors see the same response
shape in simulated and live runs.

* A probe with a canned ``mock_response`` gets it verbatim — so sensors that grade content
  (FunctionTestSensor) are exercised without credentials. This exercises the *sensor*; it
  measures nothing about a model.
* Otherwise, with activation expectations supplied: must-trigger probes call the Skill tool
  90% of the time, should-not-trigger probes 10%, seeded per trial.
* With no expectation for a probe: always activate.

Type URL: ``ix.v1/runtime.mock`` (shadows matrix's generic mock for ix subjects).
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from matrix import AgentResponse

if TYPE_CHECKING:
    from matrix import AgentDefinition

FAMILY = "mock"


class SimulatedRuntime:
    def __init__(
        self,
        expected_skill: str = "build-eval",
        seed: int | None = None,
        expectations: dict[str, bool] | None = None,
        skill_map: dict[str, str] | None = None,
        responses: dict[str, str] | None = None,
    ) -> None:
        self._expected_skill = expected_skill
        self._rng = random.Random(seed)
        self._expectations = expectations or {}
        self._skill_map = skill_map or {}
        self._responses = responses or {}

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        canned = self._responses.get(task)
        if canned is not None:
            return AgentResponse(content=canned, family=FAMILY, model="simulated")

        skill = self._skill_map.get(task, self._expected_skill)
        should_activate = self._expectations.get(task)
        if should_activate is None:
            return self._activated(skill, task)
        rate = 0.9 if should_activate else 0.1
        if self._rng.random() < rate:
            return self._activated(skill, task)
        return AgentResponse(
            content=f"Here's a direct answer about '{task[:40]}'", family=FAMILY, model="simulated"
        )

    @staticmethod
    def _activated(skill: str, task: str) -> AgentResponse:
        return AgentResponse(
            content=f"I'll use the Skill tool with {skill} for '{task[:40]}'",
            tool_calls=({"name": "Skill", "input": {"skill": skill}},),
            family=FAMILY,
            model="simulated",
        )
