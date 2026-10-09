"""``matrix.v1.agent-step``: an agent as an ordinary flow member.

slick's Agent kind declares typed ports (SD-04); the executor's half of that contract
("conformance C") is to inject the agent's inputs and hold it to its outputs. This component
does both without the compiler knowing anything about agents:

* its **requires** are whatever its config declares (``inputs``), each rendered into the task;
* it **provides** ``response`` (the :class:`AgentResponse`) and, when ``output`` names a payload
  type, ``result``: the JSON the agent returned, which the executor validates against that
  type's schema like any other output. An agent that does not return parseable JSON broke
  its contract, and the run says so.

Config::

    agent: reviewer                       # a composed agent, by name
    inputs: {diff: acme.v1.diff}          # port -> type URL
    task: "Review this change:\\n{diff}"   # {port} placeholders; {{ }} for literal braces
    output: acme.v1.review                # optional structured result type
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from matrix.domain.errors import ConfigError, ContractError, NotFoundError
from matrix.domain.ids import check_type_url

if TYPE_CHECKING:
    from collections.abc import Mapping

    from matrix.domain.agent import AgentResponse
    from matrix.domain.flow import Inputs
    from matrix.domain.ports._out.agent_runtime import Agent

AGENT_STEP = "matrix.v1.agent-step"
AGENT_RESPONSE = "matrix.v1.agent-response"
TASK = "matrix.v1.task"

_FENCED = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


class AgentStepConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    inputs: dict[str, str] = {"task": TASK}
    #: ``None``: a single input's value is the task as it stands.
    task: str | None = None
    output: str | None = None


class AgentStep:
    """Runs one agent with its inputs rendered into the task."""

    def __init__(self, config: AgentStepConfig, *, agents: Mapping[str, Agent]) -> None:
        for port, url in config.inputs.items():
            check_type_url(url, where=f"agent-step inputs.{port}")
        if config.output is not None:
            check_type_url(config.output, where="agent-step output")
        if config.task is None and len(config.inputs) != 1:
            raise ConfigError(
                f"agent-step for {config.agent!r} has {len(config.inputs)} inputs and no task "
                "template",
                fix="give a task template with a {port} placeholder for each input",
            )
        agent = agents.get(config.agent)
        if agent is None:
            raise NotFoundError(
                f"agent-step: no agent named {config.agent!r}. "
                f"Composed: {', '.join(sorted(agents)) or '(none)'}",
                fix="name an agent defined under matrix.agents",
            )
        self._config = config
        self._agent = agent
        self.requires: dict[str, str] = dict(config.inputs)
        self.provides: dict[str, str] = {"response": AGENT_RESPONSE}
        if config.output is not None:
            self.provides["result"] = config.output

    def render(self, inputs: Mapping[str, Any]) -> str:
        """The task text for these inputs."""
        values = {port: _text(value) for port, value in inputs.items()}
        if self._config.task is None:
            return next(iter(values.values()))
        try:
            return self._config.task.format_map(values)
        except (KeyError, IndexError, ValueError) as e:
            raise ConfigError(
                f"agent-step task template for {self._config.agent!r} is invalid: {e}",
                fix="use {port} for inputs and {{ }} for literal braces",
            ) from e

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        response: AgentResponse = await self._agent.run(self.render(inputs))
        outputs: dict[str, Any] = {"response": response}
        if self._config.output is not None:
            outputs["result"] = _json_result(response.content, self._config.output)
        return outputs


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)
    return json.dumps(value, indent=2, default=str)


def _json_result(content: str, type_url: str) -> Any:
    for candidate in (content.strip(), *(m.group(1) for m in _FENCED.finditer(content))):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    raise ContractError(
        f"the agent was to return a {type_url} as JSON, but its reply holds no JSON document",
        type_url=type_url,
    )
