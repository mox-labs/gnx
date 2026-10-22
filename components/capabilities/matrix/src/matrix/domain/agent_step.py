"""``matrix.v1.agent-step``: an agent as an ordinary component.

What it requires and provides comes from its config, so the wiring knows nothing about
agents:

* it **requires** the kinds its ``inputs`` name, and renders their rows into the task;
* it **provides** the agent's reply, an :class:`AgentResponse` carrying the task as sent and
  pointing at its session's row in ``matrix.v1.session``, under the kind ``response`` names;
  and, when ``output`` names a kind, the JSON the agent returned as one row of that kind,
  which the executor validates against the kind's schema like any other row. The reply must
  then be one JSON document, alone or in a fenced ```` ```json ```` block, so the agent's
  prompt or task must ask for it. An agent that does not return parseable JSON broke its
  contract, and the run says so.

A kind has one provider, so two agent steps in one run each name their own ``response``:
the default, ``matrix.v1.agent-response``, serves one.

Config::

    agent: reviewer                       # a composed agent, by name
    inputs: {diff: acme.v1.diff}          # placeholder -> the kind it stands for
    task: "Review this change:\\n{diff}"   # {placeholder}s, checked against inputs when built
    response: acme.v1.review-reply        # default: matrix.v1.agent-response; one per step
    output: acme.v1.review                # optional: the reply, parsed as one JSON document
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from matrix.domain.errors import ConfigError, ContractError, NotFoundError
from matrix.domain.ids import check_type_url
from matrix.domain.template import check_template, json_document, render

if TYPE_CHECKING:
    from collections.abc import Mapping

    from matrix.domain.agent import AgentResponse
    from matrix.domain.component import Inputs, RunContext
    from matrix.domain.ports._out.agent_runtime import Agent

AGENT_STEP = "matrix.v1.agent-step"
AGENT_RESPONSE = "matrix.v1.agent-response"
TASK = "matrix.v1.task"


class AgentStepConfig(BaseModel):
    """An agent step: `agent` (a configured agent) run on `task`, a template whose
    {placeholders} `inputs` fill with the rows of the kinds they name. `response` is the kind
    its reply is provided as; a kind has one provider, so each agent step in a run names its
    own (the default, matrix.v1.agent-response, serves one). `output`, when set, is a kind for
    the reply parsed as JSON: the reply must then be one JSON document, so say so in the
    agent's prompt or the task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    #: placeholder name -> the kind whose rows fill it
    inputs: dict[str, str] = {"task": TASK}
    #: ``None``: a single input's value is the task as it stands.
    task: str | None = None
    #: The kind the agent's :class:`AgentResponse` is provided as: one per step in a run.
    response: str = AGENT_RESPONSE
    #: The kind the agent's reply is provided as, parsed: the reply must be one JSON document.
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
        if config.task is not None:
            check_template(
                config.task, config.inputs, where=f"agent-step task for {config.agent!r}"
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
        where = f"agent-step for {self._config.agent!r}"
        return render(self._config.task, self._config.inputs, inputs, where=where)

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        task = self.render(inputs)
        response: AgentResponse = await self._agent.run(task)
        response = response.model_copy(update={"task": task})
        rows: dict[str, Any] = {self._config.response: response.as_reply()}
        if self._config.output is not None:
            # One row, whatever its shape: a JSON array the agent returned is not several.
            rows[self._config.output] = (
                _json_result(response.content, self._config.output, self._config.agent),
            )
        return rows


#: How much of a reply a contract error quotes.
_EXCERPT = 200


def _json_result(content: str, kind: str, agent: str) -> Any:
    try:
        return json_document(content)
    except LookupError:
        pass
    excerpt = content.strip()[:_EXCERPT] or "(empty)"
    raise ContractError(
        f"agent {agent!r} was to reply with JSON for {kind} (the step's `output`), but its "
        f"reply holds no JSON document: {excerpt!r}",
        fix=f"tell agent {agent!r} to reply with one JSON document for {kind}, alone or in a "
        "```json block, in its system_prompt or the step's task; on the mock runtime, make its "
        "reply that JSON (`by_agent`, `responses` or `default`); or drop `output` to keep the "
        "reply as text",
        table=kind,
        agent=agent,
    )
