"""The ``judge`` sensor: a model scores the response against a rubric, on a matrix runtime.

A model's reading is a sample, not a measurement ix can stand behind, so its metrics are
declared ``judged`` and never decide a verdict (validation refuses one as a primary or a
guardrail in a genre that gives one). What the sensor adds is the means to tell how far a
judge can be trusted here:

* **Agreement with itself.** With ``repeats`` above 1 the judge reads each response that
  many times. ``judge_agreement`` is the share of its readings that agree with their
  majority on pass or fail, and ``judge_inconsistent`` flags a response they split on; a
  report prints the flag whenever any trial raised it.
* **Family.** ``judge_in_family`` compares the family the judge's runtime stamped on its
  reply with the family the subject's runtime stamped on the session (``claude``,
  ``qwen``, ...). A judge of the subject's own family may favour it; the report prints that
  flag too. Both flags are read whenever the sensor is in use.

The judge runs on one of matrix's two runtime ports, named by one of two options; there is
no default, because a runtime may call a model provider, which is never implicit, and there
is no adapter between the ports:

* ``model_runtime`` (``hardline``, ``mock``, or any model runtime installed): each reading
  is one model call, the rubric's task as its prompt.
* ``agent_runtime`` (``claude-sdk``, ``mock``, or any agent runtime installed): each reading
  is a one-turn session with no tools, in a fresh temporary directory, removed after. It is
  given the config the runtime declares keeps a session from reading the machine it runs on
  (``hermetic`` of its :class:`matrix.AgentCapabilities`; claude-sdk's is
  ``setting_sources: []``), under the judge's own ``runtime_options``, so a CLAUDE.md, a
  hook or a user's settings where ix happens to run cannot shape its scores.

What its runtime reported it cost, where it reports one, is kept beside its runtime and model
in the readings' ``detail``, a fact about the judge that nothing adds up. Each of its calls or
sessions goes through matrix's own path (``matrix.call_model``, a ``BoundAgent``), so it is a
span of matrix's work and a row of the run's session table, failed or not, and its readings
name them (``sessions``). In a dry run it
stands in on the ``mock`` of its own port, which gives every reply the same score: the
sensor's path is exercised and no model is called.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix

from ix.adapters._out.matrix_agents import (
    AGENT_RUNTIME,
    MODEL_RUNTIME,
    STAND_IN,
    runtime_requirement,
)
from ix.domain.kind import placeholders
from ix.domain.ports import Requirement, Sensed, StoodIn
from ix.domain.problem import Problem, problem

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ix.adapters._out.matrix_agents import Point, Runtimes
    from ix.domain.ports import Surroundings
    from ix.domain.run import Interaction, Target

#: The judge's two options, each naming a runtime at one of matrix's points.
PORTS: dict[str, Point] = {"model_runtime": MODEL_RUNTIME, "agent_runtime": AGENT_RUNTIME}

#: What a judge standing in on ``mock`` replies to every task or prompt.
STAND_IN_REPLY = '{"score": 0.5, "reason": "dry run: no model judged this reply"}'

SYSTEM = (
    "You grade an AI agent's answer against a rubric. You are strict and specific. Reply "
    'with one JSON object and nothing else: {"score": <a number from 0 to 1>, '
    '"reason": "<one sentence>"}.'
)

TASK = """Rubric:
{rubric}

The task the agent was given:
{prompt}

The agent's answer:
{answer}
"""

_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _score(reply: str) -> tuple[float, str]:
    """The score and reason in a judge's reply. Raises ValueError for a reply that holds no
    score from 0 to 1."""
    found = _OBJECT.search(reply)
    try:
        data = json.loads(found.group(0)) if found else None
    except json.JSONDecodeError:
        data = None
    score = data.get("score") if isinstance(data, dict) else None
    if isinstance(score, bool) or not isinstance(score, int | float) or not 0 <= score <= 1:
        raise ValueError(f"the judge's reply holds no score from 0 to 1: {reply[:200]!r}")
    return float(score), str(data.get("reason", "")) if isinstance(data, dict) else ""


def _port(options: Mapping[str, Any]) -> tuple[Point, str]:
    """The point of the option that names the judge's runtime, and the runtime it names.
    Raises ValueError for a judge that names none, or both."""
    named = [key for key in PORTS if options.get(key)]
    if len(named) != 1:
        raise ValueError(
            "the judge needs one runtime: give model_runtime (one model call, as hardline) "
            "or agent_runtime (a one-turn session, as claude-sdk)" + (", not both" if named else "")
        )
    (key,) = named
    return PORTS[key], str(options[key])


@dataclass(frozen=True)
class _Reply:
    """One reading's reply, from either port."""

    text: str
    family: str | None
    model: str | None
    cost_usd: float | None


class JudgeSensor:
    """Scores a response against a rubric with a model: see the module docstring.

    Options: ``rubric`` or ``rubric_file``; ``model_runtime`` or ``agent_runtime`` (one is
    required), ``model``, ``runtime_options``; ``repeats`` (1); ``pass_at`` (0.5). A
    :class:`~ix.domain.ports.Review`, a :class:`~ix.domain.ports.Preflight` (its runtime
    builds and can judge) and a :class:`~ix.domain.ports.StandIn`."""

    def __init__(self, runtimes: Runtimes) -> None:
        self._runtimes = runtimes

    def watch(self, target: Target, options: Mapping[str, Any]) -> None:
        return None

    def review(self, options: Mapping[str, Any], around: Surroundings) -> Sequence[Problem]:
        """A judge that names no runtime, or both: it would never start, or start the
        wrong one."""
        named = [key for key in PORTS if options.get(key)]
        if len(named) == 1 or any(placeholders(options.get(key)) for key in named):
            return []
        if named:
            return [
                problem(
                    f"{around.path}.{key}",
                    kind="option_invalid",
                    message="the judge names a model runtime and an agent runtime; it runs on one",
                    fix="keep model_runtime for one model call, or agent_runtime for a "
                    "one-turn session",
                )
                for key in named
            ]
        return [
            problem(
                f"{around.path}.model_runtime",
                kind="option_missing",
                message="the judge names no runtime, and there is no default: a model "
                "provider is never called implicitly",
                fix="add model_runtime (hardline: one model call; mock: canned, calls no "
                "model) or agent_runtime (claude-sdk: a one-turn session with no tools; mock: "
                "canned, calls no model)",
            )
        ]

    def requirements(self, options: Mapping[str, Any], *, base: str) -> list[Requirement]:
        """That its runtime builds and can judge: a model runtime resolves the model, an
        agent runtime can play a one-turn session of it. Nothing for a judge that names no
        runtime or both (the review reports it), or while an option holds a placeholder."""
        if any(placeholders(value) for value in options.values()):
            return []
        try:
            point, name = _port(options)
        except ValueError:
            return []
        config = dict(options.get("runtime_options") or {})
        model = None if options.get("model") is None else str(options["model"])

        def judges() -> None:
            if point == MODEL_RUNTIME:
                self._runtimes.build(MODEL_RUNTIME, name, config).check(model)
                return
            definition = matrix.AgentDefinition(
                name="judge", system_prompt=SYSTEM, model=model, tools=(), max_turns=1
            )
            self._runtimes.build(AGENT_RUNTIME, name, config).check(definition)

        noun = "model runtime" if point == MODEL_RUNTIME else "agent runtime"
        return [runtime_requirement(f"the judge's {noun} {name!r} builds and can judge", judges)]

    def stand_in(self, options: Mapping[str, Any]) -> StoodIn:
        """The judge on the ``mock`` of its own port, scoring every reply 0.5: its readings
        say nothing."""
        key = next((key for key in PORTS if options.get(key)), "model_runtime")
        return StoodIn(
            options={
                **options,
                key: STAND_IN,
                "runtime_options": {"default": STAND_IN_REPLY},
            },
            notes=("judged on mock, which scores every reply 0.5: the readings say nothing",),
        )

    def _rubric(self, options: Mapping[str, Any]) -> str:
        if options.get("rubric"):
            return str(options["rubric"])
        if options.get("rubric_file"):
            return Path(str(options["rubric_file"])).read_text(encoding="utf-8")
        raise ValueError("the judge needs a rubric: give rubric or rubric_file")

    async def _by_model(
        self, name: str, config: Mapping[str, Any], task: str, model: str | None, repeats: int
    ) -> list[_Reply]:
        runtime = self._runtimes.build(MODEL_RUNTIME, name, config)
        runtime.check(model)
        request = matrix.ModelRequest(prompt=task, system=SYSTEM, model=model)
        url = matrix.model_runtime_type_url(name)
        drives = tuple(self._runtimes.drives(MODEL_RUNTIME, name))
        calls = [
            await matrix.call_model(runtime, request, name=name, runtime_url=url, drives=drives)
            for _ in range(repeats)
        ]
        return [_Reply(call.text, call.family, call.model, None) for call in calls]

    async def _by_agent(
        self, name: str, config: Mapping[str, Any], task: str, model: str | None, repeats: int
    ) -> list[_Reply]:
        definition = matrix.AgentDefinition(
            name="judge", system_prompt=SYSTEM, model=model, tools=(), max_turns=1
        )
        does = self._runtimes.capabilities(AGENT_RUNTIME, name, config)
        if does is not None:  # what keeps the machine out, under the judge's own options
            config = {**does.hermetic, **config}
        with tempfile.TemporaryDirectory(prefix="ix-judge-") as workdir:
            runtime = self._runtimes.build(AGENT_RUNTIME, name, config, cwd=workdir)
            url = matrix.agent_runtime_type_url(name)
            drives = tuple(self._runtimes.drives(AGENT_RUNTIME, name))
            judge = matrix.BoundAgent(definition, runtime, runtime_url=url, drives=drives)
            replies = [await judge.run(task) for _ in range(repeats)]
        return [_Reply(r.content, r.family, r.model, r.cost_usd) for r in replies]

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        response = interaction.observations["response"]
        task = TASK.format(
            rubric=self._rubric(options).strip(),
            prompt=str(response.get("prompt", "")).strip(),
            answer=str(response.get("text", "")).strip(),
        )
        point, name = _port(options)
        config = dict(options.get("runtime_options") or {})
        model = None if options.get("model") is None else str(options["model"])
        repeats = max(1, int(options.get("repeats") or 1))
        by = self._by_model if point == MODEL_RUNTIME else self._by_agent
        replies = await by(name, config, task, model, repeats)
        scored = [_score(reply.text) for reply in replies]
        scores = [score for score, _ in scored]
        pass_at = float(options.get("pass_at", 0.5))
        passes = sum(score >= pass_at for score in scores)
        mean = sum(scores) / len(scores)
        values: dict[str, Any] = {
            "judged_score": mean,
            "judged_pass": mean >= pass_at,
            "judge_agreement": max(passes, len(scores) - passes) / len(scores),
            "judge_inconsistent": 0 < passes < len(scores),
        }
        mine = replies[0].family
        theirs = interaction.observations.get("usage", {}).get("family")
        if mine and theirs:
            values["judge_in_family"] = str(mine).lower() == str(theirs).lower()
        costs = [reply.cost_usd for reply in replies if reply.cost_usd is not None]
        url = (
            matrix.model_runtime_type_url(name)
            if point == MODEL_RUNTIME
            else matrix.agent_runtime_type_url(name)
        )
        return Sensed(
            values=values,
            detail={
                "scores": scores,
                "reasons": [reason for _, reason in scored],
                "judge": {
                    "port": point,
                    "runtime": url,
                    "model": replies[0].model,
                    "family": mine,
                    "cost_usd": round(sum(costs), 6) if costs else None,
                },
                "subject_family": theirs,
            },
        )
