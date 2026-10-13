"""``matrix.v1.agent-step``: an agent as an ordinary component.

What it requires and provides comes from its config, so the wiring knows nothing about
agents:

* it **requires** the kinds its ``inputs`` name, and renders their rows into the task;
* it **provides** the agent's reply, an :class:`AgentResponse`, under the kind ``response``
  names; and, when ``output`` names a kind, the JSON the agent returned as one row of that
  kind, which the executor validates against the kind's schema like any other row. An agent
  that does not return parseable JSON broke its contract, and the run says so.

A kind has one provider, so two agent steps in one run each name their own ``response``.

Config::

    agent: reviewer                       # a composed agent, by name
    inputs: {diff: acme.v1.diff}          # placeholder -> the kind it stands for
    task: "Review this change:\\n{diff}"   # {placeholder}s; {{ }} for literal braces
    response: acme.v1.review-reply        # default: matrix.v1.agent-response
    output: acme.v1.review                # optional structured result kind
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
    from matrix.domain.component import Inputs, RunContext
    from matrix.domain.ports._out.agent_runtime import Agent

AGENT_STEP = "matrix.v1.agent-step"
AGENT_RESPONSE = "matrix.v1.agent-response"
TASK = "matrix.v1.task"

_FENCED = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


class AgentStepConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    #: placeholder name -> the kind whose rows fill it
    inputs: dict[str, str] = {"task": TASK}
    #: ``None``: a single input's value is the task as it stands.
    task: str | None = None
    #: The kind the agent's :class:`AgentResponse` is provided as.
    response: str = AGENT_RESPONSE
    output: str | None = None


class AgentStep:
    """Runs one agent with the rows it requires rendered into the task."""

    def __init__(self, config: AgentStepConfig, *, agents: Mapping[str, Agent]) -> None:
        for placeholder, kind in config.inputs.items():
            check_type_url(kind, where=f"agent-step inputs.{placeholder}")
        check_type_url(config.response, where="agent-step response")
        if config.output is not None:
            check_type_url(config.output, where="agent-step output")
        if config.task is None and len(config.inputs) != 1:
            raise ConfigError(
                f"agent-step for {config.agent!r} has {len(config.inputs)} inputs and no task "
                "template",
                fix="give a task template with a {placeholder} for each input",
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
        self.requires: frozenset[str] = frozenset(config.inputs.values())
        self.provides: frozenset[str] = frozenset(
            {config.response} | ({config.output} if config.output is not None else set())
        )

    def render(self, inputs: Mapping[str, tuple[Any, ...]]) -> str:
        """The task text for these rows: one row as itself, several as a JSON list."""
        values: dict[str, str] = {}
        for placeholder, kind in self._config.inputs.items():
            rows = inputs[kind]
            if not rows:
                raise ContractError(
                    f"agent-step for {self._config.agent!r} requires {kind!r}, but its "
                    "provider handed over no rows",
                    table=kind,
                )
            values[placeholder] = _text(rows[0] if len(rows) == 1 else list(rows))
        if self._config.task is None:
            return next(iter(values.values()))
        try:
            return self._config.task.format_map(values)
        except (KeyError, IndexError, ValueError) as e:
            raise ConfigError(
                f"agent-step task template for {self._config.agent!r} is invalid: {e}",
                fix="use {placeholder} for inputs and {{ }} for literal braces",
            ) from e

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        response: AgentResponse = await self._agent.run(self.render(inputs))
        rows: dict[str, Any] = {self._config.response: response}
        if self._config.output is not None:
            # One row, whatever its shape: a JSON array the agent returned is not several.
            rows[self._config.output] = (_json_result(response.content, self._config.output),)
        return rows


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)
    if isinstance(value, list):
        value = [v.model_dump(mode="json") if isinstance(v, BaseModel) else v for v in value]
    return json.dumps(value, indent=2, default=str)


def _json_result(content: str, kind: str) -> Any:
    for candidate in (content.strip(), *(m.group(1) for m in _FENCED.finditer(content))):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    raise ContractError(
        f"the agent was to return a {kind} as JSON, but its reply holds no JSON document",
        table=kind,
    )
