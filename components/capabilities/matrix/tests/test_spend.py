"""A run's spend: a ceiling the executor holds agent sessions to, a budget per session on the
claude-sdk runtime, and the bound ``matrix plan`` states from them."""

from __future__ import annotations

import io
import json
import re
from typing import Any

import pytest

import matrix
from matrix import (
    AgentDefinition,
    AgentResponse,
    AgentStep,
    AgentStepConfig,
    BoundAgent,
    Executor,
    Limits,
    Retry,
    RunError,
    SpendError,
    wire,
)
from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntimeConfig
from matrix.adapters._out.runtime.model import ModelAgentRuntimeConfig
from matrix.domain.config import AgentConfig
from matrix.domain.spend import Spend

TASK = "matrix.v1.task"


class Billed:
    """A runtime whose every session reports ``cost_usd``, bounded at ``budget_usd``."""

    def __init__(self, cost_usd: float | None, budget_usd: float | None = None) -> None:
        self.cost_usd = cost_usd
        self.budget_usd = budget_usd
        self.calls = 0

    def check(self, definition: AgentDefinition) -> None:
        """Every definition runs."""

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        self.calls += 1
        return AgentResponse(content="done", cost_usd=self.cost_usd, stop="completed")


def _chain(runtime: Billed, steps: int) -> Any:
    """``steps`` agent steps in a row, each requiring the reply of the one before."""
    agents = {"a": BoundAgent(AgentDefinition(name="a"), runtime)}
    components: dict[str, Any] = {}
    previous = TASK
    for number in range(1, steps + 1):
        reply = f"acme.v1.reply-{number}"
        config = AgentStepConfig(agent="a", inputs={"t": previous}, task="{t}", response=reply)
        components[f"s{number}"] = AgentStep(config, agents=agents)
        previous = reply
    return wire(components, inputs=[TASK])


async def test_a_session_that_could_pass_the_ceiling_is_not_started() -> None:
    """Regression: there was no dollar cap. With a budget per session, the second session
    could take the run to $1.20 of a $1.00 ceiling, so it never starts, and the run says why."""
    runtime = Billed(cost_usd=0.5, budget_usd=0.6)
    with pytest.raises(RunError) as e:
        await Executor().run(_chain(runtime, 2), {TASK: "go"}, limits=Limits(spend_usd=1.0))
    run = e.value.run
    assert runtime.calls == 1 and run.spend_usd == 0.5
    assert (e.value.kind, e.value.cause_kind, e.value.exit_code) == ("run_failed", "spend", 8)
    assert run.components["s2"].status == "failed"
    (failure,) = run.failures
    assert failure.kind == "spend" and "$1.00 ceiling" in failure.message
    assert "matrix.limits.spend_usd" in (e.value.fix or "")
    assert run.document()["spend_usd"] == 0.5


async def test_without_a_budget_a_session_starts_only_below_the_ceiling() -> None:
    runtime = Billed(cost_usd=0.6)
    with pytest.raises(RunError) as e:
        await Executor().run(_chain(runtime, 3), {TASK: "go"}, limits=Limits(spend_usd=1.0))
    assert runtime.calls == 2 and e.value.run.spend_usd == pytest.approx(1.2)
    assert e.value.cause_kind == "spend"


async def test_without_a_ceiling_spend_is_recorded_and_nothing_is_refused() -> None:
    runtime = Billed(cost_usd=0.25)
    run = await Executor().run(_chain(runtime, 3), {TASK: "go"})
    assert run.status == "completed" and run.spend_usd == pytest.approx(0.75)
    unbilled = await Executor().run(_chain(Billed(cost_usd=None), 1), {TASK: "go"})
    assert unbilled.spend_usd is None and "spend_usd" not in unbilled.document()


def test_a_session_that_reports_no_cost_or_fails_counts_its_budget() -> None:
    spend = Spend(ceiling_usd=1.0)
    held = spend.hold("a", 0.4)
    spend.settle(held, None)
    assert spend.spent_usd == 0.4
    with pytest.raises(SpendError) as e:
        spend.hold("a", 0.7)
    assert e.value.details["budget_usd"] == 0.7 and e.value.exit_code == 8


def test_the_claude_sdk_runtime_passes_its_budget_to_the_sdk() -> None:
    """The SDK's own options carry ``max_budget_usd``: checked against the installed SDK."""
    sdk = pytest.importorskip("claude_agent_sdk")
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime

    runtime = ClaudeSdkRuntime(ClaudeSdkRuntimeConfig(max_budget_usd=0.5))
    assert runtime.budget_usd == 0.5
    options = runtime.options(AgentDefinition(name="a"))
    assert sdk.ClaudeAgentOptions(**options).max_budget_usd == 0.5
    assert "max_budget_usd" not in ClaudeSdkRuntime().options(AgentDefinition(name="a"))


def _plan(runtime: str, limits: str = "") -> Any:
    config = io.StringIO(
        "matrix:\n"
        f"  runtimes: {{r: {runtime}}}\n"
        "  agents: {a: {runtime: r, system_prompt: hi}}\n"
        "  inputs: [matrix.v1.task]\n"
        f"  limits: {{retry: {{attempts: 2}}{limits}}}\n"
        "  components: [{name: s, type: matrix.v1.agent-step, config: {agent: a}}]\n"
    )
    return matrix.plan_config("matrix", config, only=True).spend


def test_the_plan_states_the_bound_from_the_budgets_and_the_ceiling() -> None:
    """Regression: ``plan`` said "at most unknown" and named a runtime budget that did not
    exist. Two attempts at $0.75 each are $1.50, or the ceiling when it is lower."""
    pytest.importorskip("claude_agent_sdk")
    assert _plan("{type: claude-sdk, max_budget_usd: 0.75}").cost_usd_max == 1.5
    capped = _plan("{type: claude-sdk, max_budget_usd: 0.75}", ", spend_usd: 1.0")
    assert (capped.cost_usd_max, capped.ceiling_usd) == (1.0, 1.0)
    unbounded = _plan("{type: claude-sdk}")
    assert unbounded.cost_usd_max is None
    assert "set max_budget_usd on runtime 'r'" in unbounded.note
    assert "matrix.limits.spend_usd" in unbounded.note


def test_every_option_a_spend_note_names_exists() -> None:
    pytest.importorskip("claude_agent_sdk")
    notes = [
        _plan("{type: claude-sdk}").note,
        _plan("{type: claude-sdk}", ", spend_usd: 1.0").note,
        _plan("{type: claude-sdk, max_budget_usd: 0.5}", ", spend_usd: 1.0").note,
        matrix.skill("config"),
    ]
    named = {name for note in notes for name in re.findall(r"\b(max_[a-z_]+|spend_usd)\b", note)}
    fields = {
        *ClaudeSdkRuntimeConfig.model_fields,
        *ModelAgentRuntimeConfig.model_fields,
        *AgentConfig.model_fields,
        *Limits.model_fields,
        *Retry.model_fields,
    }
    assert named and named <= fields, named - fields


def test_a_run_on_the_mock_records_that_it_spent_nothing(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: ``plan`` said $0.00 for a config on the mock, and its run recorded
    ``spend_usd: null``, the mark of a runtime that reports no cost. The mock spends nothing
    and says so; ``matrix runs`` prints it."""
    from matrix.adapters._in.cli import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "live.yaml").write_text(
        "matrix:\n"
        "  runtimes: {r: {type: mock, default: done}}\n"
        "  agents: {a: {runtime: r, system_prompt: hi}}\n"
        "  inputs: [matrix.v1.task]\n"
        "  components: [{name: s, type: matrix.v1.agent-step, config: {agent: a}}]\n"
    )
    (tmp_path / "tasks.jsonl").write_text('"go"\n')
    planned = matrix.plan_config(config="live.yaml", only=True).spend
    assert planned.cost_usd_max == 0.0
    run = ["run", "live.yaml", "--only", "--input", "matrix.v1.task=tasks.jsonl"]
    assert main([*run, "--run-id", "one"]) == 0
    capsys.readouterr()
    record = tmp_path / ".matrix" / "runs" / "one" / "run.json"
    assert matrix.Run.model_fields["spend_usd"].default is None
    assert json.loads(record.read_text())["spend_usd"] == 0.0
    assert main(["runs", "live.yaml", "--only"]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert line.split()[:2] == ["one", "completed"] and "$0.0000" in line


def test_runs_prints_a_dash_for_a_run_whose_sessions_reported_no_cost() -> None:
    from matrix.adapters._in.cli import _spent

    assert (_spent(None), _spent(0.0), _spent(0.01234)) == ("-", "$0.0000", "$0.0123")


def test_the_plan_says_the_model_runtime_reports_no_cost() -> None:
    """The record's ``spend_usd`` is null only for runtimes that report no cost, and the
    plan says which ones those are."""
    pytest.importorskip("hardline")
    note = _plan("{type: model, models: {models: {m: {backend: mock, model: m, family: q}}}}").note
    assert "runtime 'r' (model) reports no cost" in note and "max_tokens" in note
