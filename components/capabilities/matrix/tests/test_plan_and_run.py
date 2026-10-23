"""``matrix plan`` says what a run would do; ``matrix run`` runs it. Both read the
tiers plus a named CONFIG (``-`` is standard input), print one document, and exit by the
agreed table."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

import matrix
from matrix.adapters._in.cli import main

#: A config that runs offline: one agent step on the mock runtime.
CONFIG = """\
matrix:
  agent_runtimes:
    offline: {type: mock, default: '{"verdict": "ok"}'}
  agents:
    reviewer: {runtime: offline, system_prompt: "Review the change.", model: m1}
  inputs: [acme.v1.diff]
  components:
    - name: review
      type: matrix.v1.agent-step
      config: {agent: reviewer, inputs: {diff: acme.v1.diff}, task: "Review: {diff}",
               output: acme.v1.review}
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    for variable in ("MATRIX_CONFIG", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_TRACES_EXPORTER"):
        monkeypatch.delenv(variable, raising=False)
    (tmp_path / "matrix.yaml").write_text(CONFIG)
    (tmp_path / "diffs.jsonl").write_text('"- a\\n+ b"\n\n"- c"\n')
    return tmp_path


def _cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, Any, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    try:
        data = json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        data = out
    return code, data, err


# --- plan -------------------------------------------------------------------------------


def test_plan_says_the_order_agents_and_agent_calls_and_runs_nothing(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Changed requirement: the plan states no spend (spend was removed), and no effects
    (owner ruling 2026-10-22: removed); the agent-call counts the spend record held are the plan's
    own."""
    code, plan, _ = _cli(capsys, "plan", "--json")
    assert code == 0 and plan["schema"] == "matrix.v1.plan"
    assert plan["inputs"] == ["acme.v1.diff"] and plan["stages"] == [["review"]]
    (review,) = plan["components"]
    assert (review["agent"], review["runtime"]) == ("reviewer", "offline")
    assert "effects" not in review and "effects" not in plan
    assert review["requires"] == ["acme.v1.diff"]
    assert plan["agents"] == [
        {"name": "reviewer", "runtime": "offline", "runtime_type": "mock", "model": "m1"}
    ]
    assert plan["agent_calls_min"] == plan["agent_calls_max"] == 1
    assert "spend" not in plan
    assert plan["store"]["location"].endswith(".matrix/runs/RUN_ID")
    assert not (project / ".matrix").exists()  # nothing ran


def test_the_plan_counts_an_agent_call_for_each_attempt_on_claude_sdk(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: this test was named for the model runtime, configured ``claude-sdk`` and
    failed where the SDK is not installed. One step, three attempts: one to three calls."""
    pytest.importorskip("claude_agent_sdk")
    (project / "matrix.yaml").write_text(
        CONFIG.replace('{type: mock, default: \'{"verdict": "ok"}\'}', "{type: claude-sdk}")
        + "  limits: {retry: {attempts: 3}}\n"
    )
    code, plan, _ = _cli(capsys, "plan", "--json")
    assert code == 0
    assert (plan["agent_calls_min"], plan["agent_calls_max"]) == (1, 3)
    (runtime,) = plan["agent_runtimes"]
    assert runtime["stand_in"] is False


class _Tally:
    requires = ("acme.v1.review",)
    provides = ("acme.v1.tally",)

    async def run(self, inputs: Any, ctx: Any) -> None:
        return None


@pytest.mark.parametrize(("needs", "bounded"), [((), True), (("agents",), False)])
def test_the_call_counts_are_open_only_for_a_component_handed_the_runtimes(
    project: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    needs: tuple[str, ...],
    bounded: bool,
) -> None:
    """Changed requirement (owner ruling 2026-10-22: effects are removed). Whether a component that
    is neither an agent step nor a model step may make a session is read from its needs, which the
    registry checks, not from a label: one handed matrix's agents or model runtimes may, so
    the plan leaves the most calls open; one handed neither cannot reach a runtime."""
    from matrix.composition import container

    def registry(*, discover: bool = True) -> matrix.Registry:
        found = matrix.default_registry(discover=False)
        found.register("component", "acme.v1.tally", lambda **_: _Tally(), needs=set(needs))
        return found

    monkeypatch.setattr(container, "default_registry", registry)
    (project / "matrix.yaml").write_text(CONFIG + "    - {name: tally, type: acme.v1.tally}\n")
    code, plan, _ = _cli(capsys, "plan", "--json")
    assert code == 0, plan
    assert plan["agent_calls_min"] == 1
    assert plan["agent_calls_max"] == (1 if bounded else None)
    assert plan["model_calls_max"] == (0 if bounded else None)


def test_plan_reports_every_problem_at_once(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S14: a bad runtime type and an agent naming a runtime that is not there, together."""
    (project / "matrix.yaml").write_text(
        "matrix:\n  agent_runtimes:\n    m: {type: nope}\n"
        "  agents:\n    a: {runtime: zz, system_prompt: hi}\n"
    )
    code, _, err = _cli(capsys, "plan", "--json")
    error = json.loads(err)["error"]
    assert code == 3 and error["complete"] is True
    assert [p["path"] for p in error["problems"]] == [
        "matrix.agent_runtimes.m.type",
        "matrix.agents.a.runtime",
    ]
    assert all(p["source"] == str(project / "matrix.yaml") for p in error["problems"])


def test_a_config_that_does_not_compose_leaves_its_components_unchecked(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "matrix.yaml").write_text(CONFIG.replace("type: mock,", "type: mok,"))
    code, _, err = _cli(capsys, "plan", "--json")
    assert code == 3 and json.loads(err)["error"]["complete"] is False


def test_a_spend_ceiling_is_refused_not_ignored(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: spend was removed. A config that still sets ``limits.spend_usd`` would
    run with no ceiling at all, so the key is a config problem, named where it is."""
    (project / "matrix.yaml").write_text(CONFIG + "  limits: {spend_usd: 1.0}\n")
    code, _, err = _cli(capsys, "plan", "--json")
    (found,) = json.loads(err)["error"]["problems"]
    assert code == 3 and found["path"] == "matrix.limits.spend_usd"


def test_limits_naming_a_component_that_is_not_configured(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "matrix.yaml").write_text(
        CONFIG + "  limits: {components: {reveiw: {deadline_s: 5}}}\n"
    )
    code, _, err = _cli(capsys, "plan", "--json")
    (found,) = json.loads(err)["error"]["problems"]
    assert code == 3 and found["path"] == "matrix.limits.components.reveiw"
    assert found["allowed"] == ["review"]


def test_a_named_config_sits_above_the_tiers_and_can_be_standard_input(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    piped = io.StringIO("matrix:\n  agents:\n    reviewer: {model: m2}\n")
    piped.name = "<stdin>"
    monkeypatch.setattr(sys, "stdin", piped)
    code, plan, _ = _cli(capsys, "plan", "-", "--json")
    assert code == 0 and plan["agents"][0]["model"] == "m2"
    assert plan["sources"][-1] == "<stdin>"
    other = project / "other.yaml"
    other.write_text("matrix:\n  agents:\n    reviewer: {model: m3}\n")
    code, plan, _ = _cli(capsys, "plan", str(other), "--json")
    assert plan["agents"][0]["model"] == "m3"


def test_only_reads_the_named_configs_alone_in_the_order_given(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: a second config could only be read on top of the tiers, so its runtimes,
    agents and definitions merged with the project's and clashed."""
    other = project / "other.yaml"
    other.write_text(CONFIG.replace("model: m1", "model: m9"))
    top = project / "top.yaml"
    top.write_text("matrix:\n  agents:\n    reviewer: {model: m3}\n")
    code, plan, _ = _cli(capsys, "plan", str(other), "--only", "--json")
    assert code == 0 and plan["sources"] == [str(other)]
    assert plan["agents"][0]["model"] == "m9"
    code, plan, _ = _cli(capsys, "plan", str(other), str(top), "--only", "--json")
    assert plan["sources"] == [str(other), str(top)] and plan["agents"][0]["model"] == "m3"
    code, plan, _ = _cli(capsys, "plan", str(top), "--json")
    assert plan["sources"][-1] == str(top) and str(project / "matrix.yaml") in plan["sources"]
    code, _, err = _cli(capsys, "plan", "--only", "--json")
    assert code == 2 and "--only" in json.loads(err)["error"]["message"]
    for command in ("config", "check", "agents", "runs"):
        assert main([command, str(other), "--only", "--json"]) == 0, command
    capsys.readouterr()


def test_without_json_each_problem_is_printed_with_its_own_fix(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: the text output printed only the error's message and its general fix,
    never a problem's own fix, so the remedy for two agent steps that clash was nowhere."""
    (project / "matrix.yaml").write_text(
        CONFIG
        + "    - name: again\n      type: matrix.v1.agent-step\n"
        + "      config: {agent: reviewer, inputs: {diff: acme.v1.diff}}\n"
    )
    code, _, err = _cli(capsys, "plan")
    assert code == 3
    assert "matrix.components.again.config.response: steps again, review" in err
    assert "    fix: give each step its own `response:` kind" in err


def test_describe_says_which_models_each_runtime_takes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: no model names were discoverable from ``describe`` or the skill."""
    described = {
        url: matrix.describe_extension(f"matrix.v1.{url}").entries[0].config or {}
        for url in (
            "agent-runtime.claude-sdk",
            "agent-runtime.mock",
            "model-runtime.hardline",
            "model-runtime.mock",
        )
    }
    claude = described["agent-runtime.claude-sdk"]
    assert all(alias in claude["description"] for alias in ("`haiku`", "`sonnet`", "`opus`"))
    assert "max_budget_usd" in claude["properties"]
    assert "hardline registry" in described["model-runtime.hardline"]["description"]
    assert "by_agent" in described["agent-runtime.mock"]["properties"]
    assert "by_model" in described["model-runtime.mock"]["properties"]
    assert "`sonnet`" in matrix.skill() and "`sonnet`" in matrix.skill("agents")


# --- run --------------------------------------------------------------------------------


def test_run_prints_the_run_id_and_where_it_is_recorded(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, ran, _ = _cli(
        capsys, "run", "--input", "acme.v1.diff=diffs.jsonl", "--run-id", "first", "--json"
    )
    assert code == 0 and ran["schema"] == "matrix.v1.run"
    assert (ran["run_id"], ran["status"]) == ("first", "completed")
    assert ran["location"] == ".matrix/runs/first" and ran["warnings"] == []
    rows = (project / ".matrix/runs/first/acme.v1.review.jsonl").read_text().splitlines()
    assert [json.loads(r) for r in rows] == [{"verdict": "ok"}]
    assert ran["tables"]["acme.v1.review"] == {"file": "acme.v1.review.jsonl", "rows": 1}
    code, text, _ = _cli(capsys, "run", "--input", "acme.v1.diff=diffs.jsonl")
    assert code == 0 and text.startswith("run ") and "completed: .matrix/runs/" in text


def test_run_names_the_files_of_its_record_and_runs_lists_them(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: ``matrix run`` printed only the directory, and there was no way to list
    runs as ``ix runs`` does."""
    code, text, _ = _cli(capsys, "run", "--input", "acme.v1.diff=diffs.jsonl", "--run-id", "one")
    assert code == 0
    for line in ("run.json", "1 row of acme.v1.review", "matrix.v1.ledger.jsonl", "events.jsonl"):
        assert line in text, line
    assert "matrix --skill -r running" in text
    response = json.loads((project / ".matrix/runs/one/matrix.v1.agent-response.jsonl").read_text())
    assert response["task"] == 'Review: [\n  "- a\\n+ b",\n  "- c"\n]'  # as sent
    assert main(["run", "--input", "acme.v1.diff=diffs.jsonl", "--run-id", "two"]) == 0
    capsys.readouterr()
    code, listed, _ = _cli(capsys, "runs", "--json")
    assert code == 0 and listed["schema"] == "matrix.v1.runs"
    assert [(r["run_id"], r["status"]) for r in listed["runs"]] == [
        ("one", "completed"),
        ("two", "completed"),
    ]
    assert listed["runs"][0]["location"] == ".matrix/runs/one" and listed["runs"][0]["started"]
    assert "spend_usd" not in listed["runs"][0]  # spend was removed
    code, text, _ = _cli(capsys, "runs")
    assert code == 0 and text.splitlines()[0].split()[:2] == ["one", "completed"]


def test_runs_lists_the_damaged_records_it_skipped_with_their_reasons(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner ruling 2026-10-22: ``matrix runs`` and ``matrix.v1.runs`` name each record the
    store could not list, with why, where it was only logged."""
    assert main(["run", "--input", "acme.v1.diff=diffs.jsonl", "--run-id", "one"]) == 0
    (project / ".matrix/runs/torn").mkdir()
    (project / ".matrix/runs/torn/run.json").write_text("{trunc")
    capsys.readouterr()
    code, listed, _ = _cli(capsys, "runs", "--json")
    assert code == 0
    assert [r["run_id"] for r in listed["runs"]] == ["one"]
    ((location, reason),) = [(d["location"], d["reason"]) for d in listed["damaged"]]
    assert location.endswith(".matrix/runs/torn") and "JSONDecodeError" in reason
    code, text, _ = _cli(capsys, "runs")
    assert code == 0 and "damaged" in text and ".matrix/runs/torn" in text


def test_inputs_can_come_from_standard_input(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO('"- x"\n'))
    code, ran, _ = _cli(capsys, "run", "--input", "acme.v1.diff=-", "--json")
    assert code == 0 and ran["status"] == "completed"


def test_standard_input_is_read_once(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _cli(capsys, "run", "-", "--input", "acme.v1.diff=-", "--json")
    assert code == 2 and json.loads(err)["error"]["kind"] == "usage"


def test_a_missing_or_unknown_input_is_refused_before_anything_runs(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _cli(capsys, "run", "--json")
    (found,) = json.loads(err)["error"]["problems"]
    assert code == 3 and (found["path"], found["kind"]) == ("inputs.acme.v1.diff", "missing_key")
    code, _, err = _cli(capsys, "run", "--input", "acme.v1.diff=nope.jsonl", "--json")
    assert code == 4
    (project / "bad.jsonl").write_text("not json\n")
    code, _, err = _cli(capsys, "run", "--input", "acme.v1.diff=bad.jsonl", "--json")
    (found,) = json.loads(err)["error"]["problems"]
    assert code == 3 and found["source"] == "bad.jsonl" and "line 1" in found["message"]
    assert not (project / ".matrix").exists()


def test_a_run_that_fails_exits_run_failed_and_still_prints_its_record(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "matrix.yaml").write_text(CONFIG.replace('\'{"verdict": "ok"}\'', "'no json'"))
    code, ran, err = _cli(capsys, "run", "--input", "acme.v1.diff=diffs.jsonl", "--json")
    error = json.loads(err)["error"]
    assert code == 8 == matrix.EXIT_CODES["run_failed"]
    assert (error["kind"], error["details"]["cause"]) == ("run_failed", "contract")
    assert error["details"]["location"] == ran["location"]
    assert ran["status"] == "failed" and ran["failures"][0]["kind"] == "contract"


def test_run_from_a_record_reuses_it(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--input", "acme.v1.diff=diffs.jsonl", "--run-id", "first"]) == 0
    capsys.readouterr()
    code, ran, _ = _cli(capsys, "run", "--from", "first", "--run-id", "again", "--json")
    assert code == 0 and ran["reused_from"] == "first"
    assert ran["components"]["review"]["status"] == "reused"
    code, _, err = _cli(
        capsys, "run", "--from", "first", "--input", "acme.v1.diff=diffs.jsonl", "--json"
    )
    assert code == 2
    code, _, err = _cli(capsys, "run", "--from", "never", "--json")
    assert code == 4 and json.loads(err)["error"]["kind"] == "not_found"


def test_a_run_id_already_taken_is_a_store_error(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "--input", "acme.v1.diff=diffs.jsonl", "--run-id", "one"]) == 0
    code, _, err = _cli(
        capsys, "run", "--input", "acme.v1.diff=diffs.jsonl", "--run-id", "one", "--json"
    )
    assert code == 9 and json.loads(err)["error"]["kind"] == "store"


# --- the audit's smaller findings --------------------------------------------------------


def test_version_names_the_tool(capsys: pytest.CaptureFixture[str]) -> None:
    """S10: ``matrix, version X`` like its siblings."""
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"matrix, version {matrix.__version__}"


def test_a_permissive_runtime_is_a_warning_inside_the_documents(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """S9: the bypassPermissions warning travels in the --json document, not only on stderr.
    Under --json stderr carries none of it; without, it is said once, as a warning line, and
    the line the runtime logs is not printed beside it."""
    pytest.importorskip("claude_agent_sdk")
    from matrix.adapters._out.runtime import claude_sdk

    monkeypatch.setattr(claude_sdk, "_LOGGED", set())
    (project / "matrix.yaml").write_text(
        CONFIG.replace(
            '{type: mock, default: \'{"verdict": "ok"}\'}',
            "{type: claude-sdk, permission_mode: bypassPermissions}",
        )
    )
    for command in ("check", "agents", "plan"):
        code, document, err = _cli(capsys, command, "--json")
        assert code == 0, command
        (warning,) = document["warnings"]
        assert warning["path"] == "matrix.agent_runtimes.offline.permission_mode", command
        assert (warning["kind"], warning["got"]) == ("permissive", "bypassPermissions")
        assert "bypassPermissions" not in err, (command, err)
    monkeypatch.setattr(claude_sdk, "_LOGGED", set())
    code, _, err = _cli(capsys, "check")
    assert "warning: matrix.agent_runtimes.offline.permission_mode" in err
    assert err.count("without per-call confirmation") == 1, err


SLOW_RUN = """
import asyncio, sys
import matrix
from matrix.composition import container

class Slow:
    requires = ()
    provides = ("acme.v1.tick",)

    async def run(self, inputs, ctx):
        ctx.defer(lambda: print("released", file=sys.stderr, flush=True))
        print("started", file=sys.stderr, flush=True)
        await asyncio.sleep(60)

def registry(*, discover=True):
    found = matrix.default_registry(discover=False)
    found.register("component", "acme.v1.slow", Slow)
    return found

container.default_registry = registry
from matrix.adapters._in.cli import main
sys.exit(main(["run", "--json"]))
"""


def test_a_signal_stops_the_run_cleanly_and_exits_130(project: Path) -> None:
    """SIGINT and SIGTERM reach the run as its cancellation: the component is stopped, its
    release runs, the record says ``cancelled``, and the command exits 130."""
    import os
    import signal
    import subprocess
    import time

    (project / "matrix.yaml").write_text(
        "matrix:\n  components:\n    - {name: slow, type: acme.v1.slow}\n  limits: {grace_s: 0}\n"
    )
    for sig in (signal.SIGINT, signal.SIGTERM):
        child = subprocess.Popen(
            [sys.executable, "-c", SLOW_RUN],
            cwd=project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "HOME": str(project / "home")},
        )
        assert child.stderr is not None
        deadline = time.monotonic() + 20
        while "started" not in child.stderr.readline():
            assert time.monotonic() < deadline, "the component never started"
        child.send_signal(sig)
        out, err = child.communicate(timeout=20)
        assert child.returncode == 130, (sig, err)
        assert json.loads(out)["status"] == "cancelled" and "released" in err
        assert json.loads(err.strip().splitlines()[-1])["error"]["kind"] == "interrupted"
