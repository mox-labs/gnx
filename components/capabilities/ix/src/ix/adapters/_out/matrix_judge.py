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

The judge runs with no tools and one turn, on the runtime its ``runtime`` names: there is
no default, because two of them call a model provider, which is never implicit. It runs in
a fresh temporary directory, removed after, and on ``claude-sdk`` reads no settings
(``setting_sources: []`` unless its ``runtime_options`` name some), so a CLAUDE.md, a
hook or a user's settings where ix happens to run cannot shape its scores. What its
runtime reported it cost is kept beside its runtime and model in the readings' ``detail``,
a fact about the judge that nothing adds up. In a dry run it stands in on ``mock``, which
gives every reply the same score: the sensor's path is exercised and no model is called.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix

from ix.domain.ports import Sensed, StoodIn

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ix.adapters._out.matrix_agents import Runtimes
    from ix.domain.run import Interaction, Target

#: What a judge standing in on ``mock`` replies to every task.
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


class JudgeSensor:
    """Scores a response against a rubric with a model: see the module docstring.

    Options: ``rubric`` or ``rubric_file``; ``runtime`` (required), ``model``,
    ``runtime_options``; ``repeats`` (1); ``pass_at`` (0.5)."""

    def __init__(self, runtimes: Runtimes) -> None:
        self._runtimes = runtimes

    def watch(self, target: Target, options: Mapping[str, Any]) -> None:
        return None

    def stand_in(self, options: Mapping[str, Any]) -> StoodIn:
        """The judge on ``mock``, scoring every reply 0.5: its readings say nothing."""
        return StoodIn(
            options={
                **options,
                "runtime": "mock",
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

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        response = interaction.observations["response"]
        task = TASK.format(
            rubric=self._rubric(options).strip(),
            prompt=str(response.get("prompt", "")).strip(),
            answer=str(response.get("text", "")).strip(),
        )
        definition = matrix.AgentDefinition(
            name="judge",
            system_prompt=SYSTEM,
            model=options.get("model"),
            tools=(),
            max_turns=1,
        )
        if not options.get("runtime"):
            raise ValueError("the judge needs a runtime: give runtime (model, claude-sdk or mock)")
        name = str(options["runtime"])
        config = dict(options.get("runtime_options") or {})
        if name.removeprefix("matrix.v1.runtime.") == "claude-sdk":
            config = {"setting_sources": [], **config}
        repeats = max(1, int(options.get("repeats") or 1))
        with tempfile.TemporaryDirectory(prefix="ix-judge-") as workdir:
            runtime = self._runtimes.build(name, config, cwd=workdir)
            judge = matrix.BoundAgent(definition, runtime)
            replies = [await judge.run(task) for _ in range(repeats)]
        scored = [_score(reply.content) for reply in replies]
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
        return Sensed(
            values=values,
            detail={
                "scores": scores,
                "reasons": [reason for _, reason in scored],
                "judge": {
                    "runtime": name,
                    "model": replies[0].model,
                    "family": mine,
                    "cost_usd": round(sum(costs), 6) if costs else None,
                },
                "subject_family": theirs,
            },
        )
