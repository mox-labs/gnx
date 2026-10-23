"""Agent evaluations run through the whole path: the registry, the use cases, matrix's
components and store, and the report.

Every session and call runs on one of matrix's ``mock`` runtimes or on a scripted runtime
registered as a third party would register one: nothing here calls a model.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix
import pytest
import yaml
from click.testing import CliRunner
from matrix.adapters._out.observers.otel import OtelObserver
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel, ConfigDict, ValidationError

import ix
from ix.adapters._in.cli import main
from ix.adapters._out.matrix_agents import Loop
from ix.adapters._out.otel_tracing import OtelTracer
from ix.composition import Root, using
from ix.composition.extension import register
from ix.composition.settings import IxSettings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

PACKAGE = Path(__file__).resolve().parents[1]
EXAMPLE = PACKAGE / "examples" / "harness-integrity"


#: The ``commands_on`` fixture: run the ``ix`` command (and the library functions) on a root.
type CommandsOn = Callable[[Root], Root]


@pytest.fixture
def commands_on() -> Iterator[CommandsOn]:
    with ExitStack() as stack:
        yield lambda root: stack.enter_context(using(root))


def _settings(**fields: Any) -> matrix.Config[IxSettings]:
    return matrix.Config[IxSettings](client=IxSettings(**fields))


class Scripted:
    """A runtime a third party registers: its replies come from ``answer(task)``."""

    answer: Callable[[str], matrix.AgentResponse]

    def check(self, definition: matrix.AgentDefinition) -> None:
        return None

    async def run(self, definition: matrix.AgentDefinition, task: str) -> matrix.AgentResponse:
        return self.answer(task)


class ScriptedConfig(BaseModel):
    """What the scripted runtime takes: a third party's config, declared as matrix's are. Its
    sessions find the skills they are given in ``acme/skills`` and fire one through a tool
    named ``Skill``, and it says so."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: str = "scripted"

    def capabilities(self) -> matrix.AgentCapabilities:
        return matrix.AgentCapabilities(skill_tool="Skill", skills_dir="acme/skills")


def _registry(answer: Callable[[str], matrix.AgentResponse]) -> matrix.Registry:
    """ix's adapters, matrix's built-ins, and ``acme.v1.agent-runtime.scripted``: an agent
    runtime that is no stand-in, so its trials are measured, not simulated."""
    registry = matrix.default_registry(discover=False)
    register(registry)

    def build(config: ScriptedConfig) -> Scripted:
        runtime = Scripted()
        runtime.answer = answer
        return runtime

    registry.register("agent-runtime", SCRIPTED, build, config=ScriptedConfig)
    return registry


SCRIPTED = "acme.v1.agent-runtime.scripted"


def _routing(tmp_path: Path, **changes: Any) -> Path:
    """An activation experiment on the scripted runtime: two asks and a decoy."""
    data: dict[str, Any] = {
        "name": "routing",
        "question": "Does the right skill fire?",
        "genre": "evaluate",
        "subjects": {"catalog": {"kind": "agent-session", "runtime": SCRIPTED}},
        "primary": {"metric": "activated_correctly"},
        "threshold": {"op": ">=", "value": 0.5},
        "metrics": ["cost"],
        "repeats": 5,
        **changes,
    }
    folder = tmp_path / "routing"
    (folder / "probes").mkdir(parents=True)
    (folder / "experiment.yaml").write_text(yaml.safe_dump(data))
    probes = {
        "review.md": ("must_trigger", "review", "Review this diff."),
        "harden.md": ("must_trigger", "harden", "Make onboarding better."),
        "decoy.md": ("should_not_trigger", "review", "Rename load_cfg."),
    }
    for name, (expectation, skill, prompt) in probes.items():
        (folder / "probes" / name).write_text(
            f"---\nexpectation: {expectation}\nexpected_skill: {skill}\n---\n{prompt}\n"
        )
    return folder


def _fires(cost: float | None = 0.1) -> Callable[[str], matrix.AgentResponse]:
    """Reviews fire ``review``; the vague ask fires ``review`` too (a routing mistake); the
    decoy fires nothing."""

    def answer(task: str) -> matrix.AgentResponse:
        calls = () if "Rename" in task else ({"name": "Skill", "input": {"skill": "x:review"}},)
        return matrix.AgentResponse(
            content="ok", tool_calls=calls, num_turns=1, stop="completed", cost_usd=cost,
            family="scripted", model="s-1",
        )  # fmt: skip

    return answer


class TestTheHarnessIntegrityCheck:
    """The earlier ix's sensor-integrity: canned answers through the real sensors on
    matrix's mock runtime, each grade held to what it must be."""

    def test_the_canned_answers_grade_as_they_must(self, tmp_path: Path) -> None:
        lab = tmp_path / "harness-integrity"
        shutil.copytree(EXAMPLE, lab)
        report = Root(settings=_settings()).run(lab, exploratory=True)
        assert report.status == "completed" and report.simulated == "all"
        assert report.verdict is None and report.indication is None
        assert report.no_verdict == "Dry run on mock replies: checks the harness, not the agent"
        assert report.exit_code == 0
        record = Root(settings=_settings()).report(lab)
        readings = [
            json.loads(line)
            for line in Path(record.tables["ix.v1.reading"]).read_text().splitlines()
        ]
        trials = {
            row["seq"]: row["probe"]
            for row in map(json.loads, Path(record.tables["ix.v1.trial"]).read_text().splitlines())
        }
        by_probe: dict[str, dict[str, Any]] = {}
        for reading in readings:
            by_probe.setdefault(trials[reading["seq"]], {})[reading["metric"]] = reading
        expected = {  # probe: (tests_score, passed of run, outcome_score)
            "correct-solution": (1.0, (3, 3), 1.0),
            "partially-correct": (0.5, (1, 3), 1.0),
            "raising-solution": (0.0, (0, 2), 1.0),
            "no-code": (0.0, (0, 1), 1.0),
        }
        for probe, (score, (passed, run), outcome) in expected.items():
            seen = by_probe[probe]
            assert seen["tests_score"]["value"] == pytest.approx(score), probe
            share = seen["tests_pass_share"]["statistics"]
            assert (share["events"], share["units"]) == (passed, run), probe
            assert seen["outcome_score"]["value"] == pytest.approx(outcome), probe
            assert all(reading["failure"] is None for reading in seen.values()), probe
        raised = by_probe["raising-solution"]["tests_score"]["detail"]["failures"]
        assert raised and all("raised RuntimeError" in failure for failure in raised)
        assert by_probe["no-code"]["outcome_score"]["detail"]["graded_by"] == "expected_facts"
        # Changed requirement: the probe screen is left out of a run a stand-in answered.
        # Every probe of canned replies reads at its floor or its ceiling, which says nothing
        # about whether the probe discriminates (tests/test_summaries.py holds the screen).
        assert report.screen == () and record.screen == ()

    def test_a_registered_run_a_stand_in_answered_is_no_gate_and_never_exits_0(
        self, tmp_path: Path
    ) -> None:
        """Regression: an experiment registered with ``runtime: mock`` written in ran held to
        its registration, gave no verdict (a stand-in answered) and exited 0, the code that
        says "holds": a gate reading the exit code passed a run in which nothing under test
        was measured. It is not gated, and it exits ``simulated``."""
        lab = tmp_path / "harness-integrity"
        shutil.copytree(EXAMPLE, lab)
        root = Root(settings=_settings())
        root.register(lab)
        report = root.run(lab)
        assert report.simulated == "all" and report.verdict is None
        assert report.gated is False and report.exit_kind == "simulated"
        assert report.exit_code == ix.EXIT_CODES["simulated"] == 16
        assert root.report(lab).exit_code == 16
        (listed,) = root.runs(lab).runs
        assert (listed.mode, listed.gated) == ("dry", False)

    def test_a_dry_run_asked_for_is_no_gate_and_exits_0(self, tmp_path: Path) -> None:
        """A dry run is asked for, as an exploratory run is: it gives no gate result and
        none was wanted."""
        lab = tmp_path / "harness-integrity"
        shutil.copytree(EXAMPLE, lab)
        root = Root(settings=_settings())
        root.register(lab)
        report = root.run(lab, dry_run=True)
        assert report.gated is False and report.exit_code == 0


def _rows(report: ix.Report, table: str) -> list[dict[str, Any]]:
    """The rows of one of a run's tables, as written."""
    return [json.loads(line) for line in Path(report.tables[table]).read_text().splitlines()]


class TestAnActivationRun:
    def test_the_confusion_table_and_the_reported_cost(self, tmp_path: Path) -> None:
        root = Root(registry=_registry(_fires()), settings=_settings())
        report = root.run(_routing(tmp_path), exploratory=True)
        assert report.status == "completed" and report.simulated == "none"
        (table,) = report.confusion
        assert table.counts == {
            "review": {"review": 5}, "harden": {"review": 5}, "none": {"none": 5},
        }  # fmt: skip
        assert (table.true_positive, table.false_positive) == (5, 5)
        assert (table.false_negative, table.true_negative) == (5, 5)
        assert (table.precision, table.recall) == (0.5, 0.5)
        review = next(label for label in table.labels if label.label == "review")
        assert (review.precision, review.recall) == (0.5, 1.0)
        # What each session's runtime reported it cost is a fact about the session, read by
        # the usage sensor like its tokens: nothing adds it up or holds a run to it.
        (cost,) = [e for e in report.analysis.estimates if e.metric == "cost"]
        assert cost.value == pytest.approx(0.1)
        assert "spend" not in report.document() and "halt" not in report.document()

    @pytest.mark.parametrize("cost", [0.4, None])
    def test_a_run_carries_out_its_whole_schedule_whatever_its_sessions_cost(
        self, tmp_path: Path, cost: float | None
    ) -> None:
        """Changed requirement: spend is removed. A run was stopped once the next trial
        could take it past a ceiling, and at the first trial whose cost no runtime
        reported; now cost never stops a run, and what a runtime reported is kept on the
        session's record (its ``usage``), not on the trial."""
        root = Root(registry=_registry(_fires(cost=cost)), settings=_settings())
        report = root.run(_routing(tmp_path, metrics=[]), exploratory=True)
        assert report.status == "completed" and report.exit_code == 0
        assert report.trials.carried_out == report.trials.planned == 15
        rows = _rows(report, "ix.v1.interaction")
        assert [row["observations"]["usage"]["cost_usd"] for row in rows] == [cost] * 15
        assert not any("cost_usd" in row for row in rows)
        assert "ix.v1.halt" not in report.tables

    def test_spend_is_refused_in_an_experiment_and_in_ixs_settings(self, tmp_path: Path) -> None:
        """Changed requirement: spend is removed, so a ``spend`` section and ix's
        ``spend_cap`` are keys that do not exist, and a plan states no spend."""
        root = Root(registry=_registry(_fires()), settings=_settings())
        (problem,) = root.validate(_routing(tmp_path / "spend", spend={"ceiling": 1.0})).problems
        assert (problem.path, problem.kind) == ("spend", "unknown_key")
        with pytest.raises(ValidationError, match="spend_cap"):
            IxSettings(spend_cap=1.0)  # type: ignore[call-arg]
        assert "spend" not in root.plan(_routing(tmp_path / "plain")).document()

    def test_the_settings_say_where_runs_are_kept(self, tmp_path: Path) -> None:
        kept = tmp_path / "every-run"
        root = Root(registry=_registry(_fires()), settings=_settings(runs=str(kept)))
        report = root.run(_routing(tmp_path), exploratory=True)
        assert Path(report.location).parent == kept
        assert [run.run for run in root.runs(_routing(tmp_path / "again")).runs] == [report.run]
        assert "matrix.v1.event" in report.tables  # the run's events, kept beside its tables


DRY_RUN = "Dry run on mock replies: checks the harness, not the agent"

SKILL = """---
name: review
description: Reviews a diff for correctness. Use when asked to review a change.
---
Read the diff and say what is wrong with it.
"""


def _skill_lab(tmp_path: Path, **subject: Any) -> Path:
    """A skill under evaluation, as an agent that read only ``--skill`` would write one: the
    skill's folder beside the experiment, given to a claude-sdk session."""
    folder = tmp_path / "skill"
    (folder / "skills" / "review").mkdir(parents=True)
    (folder / "skills" / "review" / "SKILL.md").write_text(SKILL)
    data = {
        "name": "review-fires",
        "question": "Does the review skill fire when asked for a review?",
        "genre": "evaluate",
        "subjects": {
            "agent": {
                "kind": "agent-session",
                "runtime": "claude-sdk",
                "skills": ["skills/review"],
                **subject,
            }
        },
        "probes": [
            {
                "id": "ask",
                "kind": "prompt",
                "prompt": "Review this diff before I merge it.",
                "mock_response": "I would review it with the review skill.",
                "truth": {"expectation": "must_trigger", "expected_skill": "review"},
            }
        ],
        "primary": {"metric": "activated_correctly"},
        "threshold": {"op": ">=", "value": 0.5},
        "repeats": 18,
    }
    (folder / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return folder


class TestTheHarnessCannotHideTheAgent:
    """Regression: a session given skills and ``tools: []`` ran; the Skill tool was gone,
    so the skill could never fire, and the registered run said fails, 0 of 18, with no
    warning. Now validate refuses it, and so does every command that would run it."""

    def test_a_skill_with_no_skill_tool_is_refused_before_any_trial(self, tmp_path: Path) -> None:
        root = Root(settings=_settings())
        lab = _skill_lab(tmp_path, tools=[])
        found = root.validate(lab)
        assert not found.valid
        (problem,) = found.problems
        assert (problem.path, problem.kind) == ("subjects.agent.tools", "unobservable")
        assert "Skill" in problem.fix
        with pytest.raises(ix.InvalidExperimentError):
            root.register(lab)
        with pytest.raises(ix.InvalidExperimentError):
            root.run(lab, exploratory=True)
        assert not (lab / ".ix").exists()
        assert root.validate(_skill_lab(tmp_path / "fixed", tools=["Read", "Skill"])).valid

    def test_an_option_the_runtime_does_not_declare_is_a_problem_validate_reports(
        self, tmp_path: Path
    ) -> None:
        """Regression: validate said valid; only plan and run refused, with a generic fix.
        What the runtime takes is read from its registration's config, not its name."""
        lab = _skill_lab(
            tmp_path, runtime="mock", runtime_options={"permission_mode": "bypassPermissions"}
        )
        found = Root(settings=_settings()).validate(lab)
        assert {(p.path, p.kind) for p in found.problems} == {
            ("subjects.agent.runtime_options.permission_mode", "option_unknown"),
        }
        (problem,) = found.problems
        assert "default" in problem.allowed and "permission_mode" not in problem.allowed


DEFINITION = """---
name: reviewer
tools: [Read, Skill]
---
You review diffs before they are merged.
"""

MOUNTING = "acme.v1.agent-runtime.mounting"


class MountingConfig(BaseModel):
    """A third party's agent runtime that mounts plugins, as claude-sdk does, and fires a
    skill through ``Skill``, as the scripted runtime does."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plugins: tuple[dict[str, Any], ...] = ()

    def capabilities(self) -> matrix.AgentCapabilities:
        return matrix.AgentCapabilities(skill_tool="Skill", skills_dir="acme/skills")


def _mounting() -> matrix.Registry:
    registry = _registry(_fires())

    def build(config: MountingConfig) -> Scripted:
        runtime = Scripted()
        runtime.answer = _fires()
        return runtime

    registry.register("agent-runtime", MOUNTING, build, config=MountingConfig)
    return registry


def _subject_lab(tmp_path: Path) -> Path:
    """The skill lab with everything a subject can be made of: a skill, a plugin found by a
    ``*``, a fixture and an agent definition, each a file beside the experiment."""
    lab = _skill_lab(
        tmp_path,
        runtime=MOUNTING,
        plugins=["plugins/*"],
        fixtures=["fixtures/diff.patch"],
        definition="agents/reviewer.md",
    )
    (lab / "plugins" / "lint").mkdir(parents=True)
    (lab / "plugins" / "lint" / "plugin.json").write_text('{"name": "lint"}\n')
    (lab / "fixtures").mkdir()
    (lab / "fixtures" / "diff.patch").write_text("- total = a\n+ total = a + b\n")
    (lab / "agents").mkdir()
    (lab / "agents" / "reviewer.md").write_text(DEFINITION)
    return lab


def _git(lab: Path, *arguments: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(lab), *arguments], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _committed(lab: Path) -> str:
    """``lab`` as a git repository with everything in it but its runs committed: the
    commit."""
    (lab / ".gitignore").write_text(".ix/\n")
    _git(lab, "init", "--quiet")
    _git(lab, "add", ".")
    _git(lab, "-c", "user.name=ix", "-c", "user.email=ix@example.invalid",
         "commit", "--quiet", "-m", "the lab")  # fmt: skip
    return _git(lab, "rev-parse", "HEAD")


class TestAVerdictIsTiedToWhatItJudged:
    """Regression: the experiment's digest covered its document, which names the
    skills, plugins, fixtures and agent definition by path. Nothing hashed what was at
    those paths, so a registered run whose skill had been edited since was still gated, and
    no record said which skill, commit or versions a verdict was about."""

    @pytest.mark.parametrize(
        ("edited", "field", "message"),
        [
            ("skills/review/SKILL.md", "subjects.agent.skills[skills/review]",
             "skill review changed after registration"),
            ("plugins/lint/plugin.json", "subjects.agent.plugins[plugins/lint]",
             "plugin lint changed after registration"),
            ("fixtures/diff.patch", "subjects.agent.fixtures[fixtures/diff.patch]",
             "fixture diff.patch changed after registration"),
            ("agents/reviewer.md", "subjects.agent.definition[agents/reviewer.md]",
             "definition reviewer.md changed after registration"),
        ],
    )  # fmt: skip
    def test_a_subject_edited_after_registration_is_a_deviation_and_its_run_is_not_gated(
        self, tmp_path: Path, edited: str, field: str, message: str
    ) -> None:
        root = Root(registry=_mounting(), settings=_settings())
        lab = _subject_lab(tmp_path)
        root.register(lab)
        with (lab / edited).open("a") as file:
            file.write("\nOne more line.\n")
        (deviation,) = root.deviations(lab).deviations
        assert (deviation.field, deviation.message) == (field, message)
        assert deviation.registered and deviation.current
        assert deviation.registered != deviation.current
        report = root.run(lab)
        assert report.status == "completed" and report.gated is False
        assert report.verdict is None and report.indication is not None
        assert [(d.field, d.message) for d in report.deviations] == [(field, message)]
        assert report.exit_code == ix.EXIT_CODES["deviations"]

    def test_an_unchanged_subject_runs_gated_and_its_digests_are_recorded(
        self, tmp_path: Path
    ) -> None:
        root = Root(registry=_mounting(), settings=_settings())
        lab = _subject_lab(tmp_path)
        frozen = root.register(lab).registration
        report = root.run(lab)
        assert report.gated is True and report.deviations == ()
        assert [(c.field, c.label) for c in report.contents] == [
            ("subjects.agent.definition[agents/reviewer.md]", "definition reviewer.md"),
            ("subjects.agent.plugins[plugins/lint]", "plugin lint"),
            ("subjects.agent.skills[skills/review]", "skill review"),
            ("subjects.agent.fixtures[fixtures/diff.patch]", "fixture diff.patch"),
        ]
        assert [c.digest for c in report.contents] == [c.digest for c in frozen.contents]
        assert all(len(c.digest) == 64 for c in report.contents)
        (manifest,) = _rows(report, "ix.v1.run")
        assert [c["digest"] for c in manifest["contents"]] == [c.digest for c in frozen.contents]
        # Paths and digests are recorded; what the files hold is not.
        registration = (lab / "experiment.registration.json").read_text()
        for written in (registration, json.dumps(manifest)):
            assert "Read the diff and say what is wrong" not in written
            assert "You review diffs" not in written and "total = a + b" not in written

    def test_a_folder_is_its_files_and_their_paths_not_their_times(self, tmp_path: Path) -> None:
        root = Root(registry=_mounting(), settings=_settings())
        lab = _subject_lab(tmp_path)
        root.register(lab)
        skill = lab / "skills" / "review"
        os.utime(skill / "SKILL.md", (0, 0))
        (skill / "__pycache__").mkdir()
        (skill / "__pycache__" / "x.pyc").write_bytes(b"\0")
        assert root.deviations(lab).deviations == ()
        (skill / "SKILL.md").rename(skill / "README.md")
        (deviation,) = root.deviations(lab).deviations
        assert deviation.message == "skill review changed after registration"
        shutil.rmtree(skill)
        (deviation,) = root.deviations(lab).deviations
        assert (deviation.message, deviation.current) == (
            "skill review was deleted after registration", None,
        )  # fmt: skip

    def test_a_plugin_that_a_star_newly_matches_is_a_deviation(self, tmp_path: Path) -> None:
        root = Root(registry=_mounting(), settings=_settings())
        lab = _subject_lab(tmp_path)
        root.register(lab)
        (lab / "plugins" / "format").mkdir()
        (deviation,) = root.deviations(lab).deviations
        assert (deviation.field, deviation.message) == (
            "subjects.agent.plugins[plugins/format]",
            "plugin format was added to the experiment after registration",
        )

    def test_a_sensors_file_edited_after_registration_is_a_deviation(self, tmp_path: Path) -> None:
        lab = tmp_path / "harness-integrity"
        shutil.copytree(EXAMPLE, lab)
        root = Root(settings=_settings())
        root.register(lab)
        with (lab / "graders.py").open("a") as file:
            file.write("\n# graded more kindly\n")
        (deviation,) = root.deviations(lab).deviations
        assert (deviation.field, deviation.message) == (
            "sensors.0.graders[graders.py]", "graders graders.py changed after registration",
        )  # fmt: skip

    def test_the_run_records_its_commit_and_the_versions_that_ran_it(self, tmp_path: Path) -> None:
        root = Root(registry=_mounting(), settings=_settings())
        lab = _subject_lab(tmp_path)
        outside = root.run(lab, exploratory=True).provenance
        assert outside is not None
        assert (outside.commit, outside.no_commit) == ("", "not a repository")
        commit = _committed(lab)
        clean = root.run(lab, exploratory=True).provenance
        assert clean is not None and (clean.commit, clean.dirty) == (commit, False)
        (lab / "fixtures" / "diff.patch").write_text("changed\n")
        dirty = root.run(lab, exploratory=True).provenance
        assert dirty is not None and (dirty.commit, dirty.dirty) == (commit, True)
        assert dirty.checkout() == f"commit {commit[:12]} (dirty)"
        assert set(dirty.versions) == {"ix", "matrix", "hardline"}
        assert dirty.versions["matrix"] == importlib.metadata.version("matrix")
        assert MOUNTING in {runtime.type_url for runtime in dirty.runtimes}

    def test_a_runtime_is_recorded_with_the_distribution_that_registered_it(
        self, tmp_path: Path
    ) -> None:
        lab = tmp_path / "harness-integrity"
        shutil.copytree(EXAMPLE, lab)
        provenance = Root(settings=_settings()).run(lab, exploratory=True).provenance
        assert provenance is not None
        mock = next(r for r in provenance.runtimes if r.type_url == "matrix.v1.agent-runtime.mock")
        assert (mock.distribution, mock.version) == ("matrix", importlib.metadata.version("matrix"))

    def test_a_runtime_is_recorded_with_the_version_of_what_it_drives(self, tmp_path: Path) -> None:
        """A runtime declares the distributions it drives (matrix's ``Entry.drives``): the
        manifest records each with its installed version (claude-sdk's SDK, beside the
        matrix that registered it), and each session row on it names them."""
        registry = _registry(_fires())
        driving = "acme.v1.agent-runtime.over-pydantic"

        def build(config: ScriptedConfig) -> Scripted:
            runtime = Scripted()
            runtime.answer = _fires()
            return runtime

        registry.register(
            "agent-runtime", driving, build, config=ScriptedConfig, drives=("pydantic", "acme-gone")
        )
        lab = _routing(
            tmp_path, subjects={"catalog": {"kind": "agent-session", "runtime": driving}}
        )
        report = Root(registry=registry, settings=_settings()).run(lab, exploratory=True)
        sessions = _session_rows(report)
        assert sessions and all(row["drives"] == ["pydantic", "acme-gone"] for row in sessions)
        provenance = report.provenance
        assert provenance is not None
        by_url = {runtime.type_url: runtime for runtime in provenance.runtimes}
        assert by_url[driving].drives == {
            "pydantic": importlib.metadata.version("pydantic"),
            "acme-gone": "not installed",
        }
        sdk = by_url["matrix.v1.agent-runtime.claude-sdk"]
        assert set(sdk.drives) == {"claude-agent-sdk"}
        assert by_url[SCRIPTED].drives == {}

    def test_report_and_runs_show_the_subjects_digests_and_commit(
        self, tmp_path: Path, commands_on: CommandsOn
    ) -> None:
        root = Root(registry=_mounting(), settings=_settings())
        commands_on(root)
        lab = _subject_lab(tmp_path)
        commit = _committed(lab)
        report = root.run(lab, exploratory=True)
        runner = CliRunner(env={"COLUMNS": "200"})
        text = runner.invoke(main, ["report", str(lab)]).stdout
        assert f"commit {commit[:12]}" in text
        for content in report.contents:
            assert f"{content.label}  {content.digest[:12]}" in text
        document = json.loads(runner.invoke(main, ["report", str(lab), "--json"]).stdout)
        assert document["provenance"]["commit"] == commit
        assert [c["digest"] for c in document["contents"]] == [c.digest for c in report.contents]
        assert f"commit {commit[:12]}" in runner.invoke(main, ["runs", str(lab)]).stdout
        (listed,) = json.loads(runner.invoke(main, ["runs", str(lab), "--json"]).stdout)["runs"]
        assert (listed["commit"], listed["dirty"]) == (commit, False)


class Oneshot:
    """A model runtime a third party registers: one call, answered by ``answer(prompt)``."""

    answer: Callable[[str], str]

    def check(self, model: str | None) -> None:
        if model not in (None, "one-1"):
            raise matrix.ConfigError(f"oneshot has no model {model!r}", fix="model: one-1")

    async def call(self, request: matrix.ModelRequest) -> matrix.ModelCall:
        return matrix.ModelCall(
            text=self.answer(request.prompt), name="one-1", model="one-1", family="acme",
            usage=matrix.TokenUsage(input_tokens=12, output_tokens=3), request_id="r-1",
            latency_ms=40,
        )  # fmt: skip


ONESHOT = "acme.v1.model-runtime.oneshot"


def _third_parties() -> matrix.Registry:
    """ix's adapters, matrix's built-ins, an agent runtime and a model runtime registered
    under ``acme``, as a third party registers them: neither name means anything to ix."""
    fires = _fires()

    def judges(task: str) -> matrix.AgentResponse:
        if "Rubric:" not in task:
            return fires(task)
        return matrix.AgentResponse(content='{"score": 0.6}', num_turns=1, family="scripted")

    registry = _registry(judges)

    def build() -> Oneshot:
        runtime = Oneshot()
        runtime.answer = lambda prompt: (
            '{"score": 0.7}' if "Rubric:" in prompt else "BANANA" if "fruit" in prompt else "no"
        )
        return runtime

    registry.register("model-runtime", ONESHOT, build)
    return registry


def _either(
    tmp_path: Path, subject: dict[str, Any], *, routing: bool = False, **changes: Any
) -> Path:
    """A text check put to one subject; with ``routing``, the probes carry what an
    activation sensor reads too."""
    fired = {"expectation": "must_trigger", "expected_skill": "review"} if routing else {}
    quiet = {"expectation": "should_not_trigger"} if routing else {}
    data: dict[str, Any] = {
        "name": "either",
        "question": "Does the reply start with BANANA?",
        "genre": "evaluate",
        "subjects": {"it": subject},
        "probes": [
            {"id": "fruit", "kind": "prompt", "prompt": "Name a fruit.",
             "truth": {"starts_with": "BANANA", **fired}},
            {"id": "other", "kind": "prompt", "prompt": "Name a colour.",
             "truth": {"starts_with": "BANANA", **quiet}},
        ],
        "primary": {"metric": "text_matched"},
        "threshold": {"op": ">=", "value": 0.5},
        "metrics": ["tokens_total"],
        "repeats": 5,
        **changes,
    }  # fmt: skip
    folder = tmp_path / "either"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return folder


class TestThirdPartyRuntimes:
    """Regression: ix told an agent runtime from a model runtime by name, so a
    third party's model runtime escaped the no-tools review, and a third party's agent
    runtime lost the session reviews. Each now gets the review of the port it is
    registered at, whatever it is called."""

    def test_a_model_runtime_is_a_model_call_and_its_tool_sensors_are_refused(
        self, tmp_path: Path
    ) -> None:
        root = Root(registry=_third_parties(), settings=_settings())
        call = {"kind": "model-call", "runtime": ONESHOT}
        lab = _either(tmp_path / "tools", call, routing=True, sensors=[{"kind": "activation"}])
        found = root.validate(lab)
        assert {(p.path, p.kind) for p in found.problems} == {("subjects.it.kind", "unobservable")}
        assert "agent-session" in (found.problems[0].fix or "")
        # A runtime option its config does not declare: it declares none.
        lab = _either(tmp_path / "options", {**call, "runtime_options": {"temperature": 0}})
        (problem,) = root.validate(lab).problems
        assert (problem.path, problem.kind) == ("subjects.it.runtime_options.temperature",
                                                "option_unknown")  # fmt: skip

    def test_a_model_call_runs_one_call_per_trial_and_keeps_its_facts(self, tmp_path: Path) -> None:
        root = Root(registry=_third_parties(), settings=_settings())
        lab = _either(tmp_path, {"kind": "model-call", "runtime": ONESHOT, "model": "one-1"})
        assert root.validate(lab).valid and root.plan(lab).runnable
        report = root.run(lab, exploratory=True)
        assert report.status == "completed" and report.simulated == "none"
        (estimate,) = [e for e in report.analysis.estimates if e.metric == "text_matched"]
        assert estimate.value == pytest.approx(0.5) and estimate.n == 10
        usage = [row["observations"]["usage"] for row in _rows(report, "ix.v1.interaction")]
        assert {row["runtime"] for row in usage} == {ONESHOT}
        assert {(row["request_id"], row["tokens_total"], row["family"]) for row in usage} == {
            ("r-1", 15, "acme")
        }
        assert not any("turns" in row for row in usage)
        # The model is resolved before anything starts: an unknown one cannot start.
        unknown = _either(tmp_path / "unknown", {"kind": "model-call", "runtime": ONESHOT,
                                                 "model": "two-2"})  # fmt: skip
        assert any("oneshot has no model 'two-2'" in r.why for r in root.plan(unknown).unmet)

    def test_each_model_call_ix_makes_is_a_matrix_model_span_as_a_model_steps_is(
        self, tmp_path: Path
    ) -> None:
        """Regression: ix made its direct model calls (a ``model-call`` trial, a judge on a model
        runtime) beside matrix's event path, so none was a ``matrix.model`` span and a model
        runtime's own spans (hardline's ``chat``) had no call span to nest under. Each now
        goes through matrix's public call path, as a model step's call does."""
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        registry = _third_parties()
        spans = "acme.v1.observer.spans"
        registry.register("observer", spans, lambda: OtelObserver(tracer_provider=provider))
        settings = matrix.Config[IxSettings](
            client=IxSettings(), matrix=matrix.MatrixConfig(observers=(spans,))
        )
        lab = _either(
            tmp_path, {"kind": "model-call", "runtime": ONESHOT, "model": "one-1"},
            sensors=[{"kind": "judge", "rubric": "Helpful.", "model_runtime": ONESHOT}],
            metrics=["judged_score"],
        )  # fmt: skip
        report = Root(registry=registry, settings=settings).run(lab, exploratory=True)
        assert report.status == "completed"
        calls = [span for span in exporter.get_finished_spans() if span.name == "matrix.model"]
        assert len(calls) == 20  # ten trials, and the judge's reading of each
        for call in calls:
            attributes = dict(call.attributes or {})
            assert attributes["matrix.runtime"] == ONESHOT
            assert attributes["matrix.status"] == "completed"
            assert attributes["gen_ai.response.model"] == "one-1"
            assert call.parent is not None  # under the component whose work made it
        # Each is still a row of the session table, as before, and the rows name no fewer.
        assert len(_session_rows(report)) == 20

    def test_an_agent_runtime_gets_the_session_reviews(self, tmp_path: Path) -> None:
        root = Root(registry=_third_parties(), settings=_settings())
        (tmp_path / "skills" / "review").mkdir(parents=True)
        session = {"kind": "agent-session", "runtime": SCRIPTED, "skills": ["skills/review"]}
        lab = _either(tmp_path, {**session, "tools": []}, routing=True,
                      sensors=[{"kind": "activation"}])  # fmt: skip
        assert {(p.path, p.kind) for p in root.validate(lab).problems} == {
            ("subjects.it.tools", "unobservable"),
        }
        # An option its config refuses: a config refused has no capabilities to ask of.
        planning = {**session, "runtime_options": {"permission_mode": "plan"}}
        lab = _either(tmp_path / "options", planning, routing=True,
                      sensors=[{"kind": "activation"}])  # fmt: skip
        assert {(p.path, p.kind) for p in root.validate(lab).problems} == {
            ("subjects.it.runtime_options.permission_mode", "option_unknown"),
        }
        fixed = _either(tmp_path, session, routing=True, sensors=[{"kind": "activation"}])
        assert root.validate(fixed).valid

    def test_each_subject_takes_runtimes_of_its_own_port_only(self, tmp_path: Path) -> None:
        root = Root(registry=_third_parties(), settings=_settings())
        on_a_model = _either(tmp_path / "a", {"kind": "agent-session", "runtime": ONESHOT})
        (said,) = root.plan(on_a_model).unmet
        assert said.needed_by == ("subjects.it",)
        assert said.why.startswith(f"no agent runtime {ONESHOT!r}")
        on_an_agent = _either(tmp_path / "m", {"kind": "model-call", "runtime": SCRIPTED})
        (said,) = root.plan(on_an_agent).unmet
        assert said.why.startswith(f"no model runtime {SCRIPTED!r}")

    def test_a_judge_uses_the_port_its_option_names(self, tmp_path: Path) -> None:
        root = Root(registry=_third_parties(), settings=_settings())

        def judged(**judge: str) -> list[dict[str, Any]]:
            lab = _either(
                tmp_path / "-".join(judge), {"kind": "model-call", "runtime": ONESHOT},
                sensors=[{"kind": "judge", "rubric": "Helpful.", **judge}],
                metrics=["judged_score"],
            )  # fmt: skip
            report = root.run(lab, exploratory=True)
            readings = _rows(report, "ix.v1.reading")
            return [r["detail"]["judge"] for r in readings if r["metric"] == "judged_score"]

        on_a_call = judged(model_runtime=ONESHOT)
        assert on_a_call and {(j["port"], j["runtime"], j["family"]) for j in on_a_call} == {
            ("model-runtime", ONESHOT, "acme")
        }
        on_a_session = judged(agent_runtime=SCRIPTED)
        assert on_a_session and {j["port"] for j in on_a_session} == {"agent-runtime"}
        assert {j["runtime"] for j in on_a_session} == {SCRIPTED}


class _Pooled:
    """What a runtime holding connections per event loop (hardline's clients) is asked: the
    loop each call or session ran in, then the loop each ``aclose()`` ran in."""

    def __init__(self, log: list[list[tuple[str, object]]]) -> None:
        self.events: list[tuple[str, object]] = []
        log.append(self.events)

    def used(self) -> None:
        self.events.append(("used", asyncio.get_running_loop()))

    async def aclose(self) -> None:
        self.events.append(("closed", asyncio.get_running_loop()))


class PooledModel(Oneshot, _Pooled):
    async def call(self, request: matrix.ModelRequest) -> matrix.ModelCall:
        self.used()
        return await super().call(request)


class PooledAgent(Scripted, _Pooled):
    async def run(self, definition: matrix.AgentDefinition, task: str) -> matrix.AgentResponse:
        self.used()
        return await super().run(definition, task)


POOLED_MODEL = "acme.v1.model-runtime.pooled"
POOLED_AGENT = "acme.v1.agent-runtime.pooled"


def _pooled(log: list[list[tuple[str, object]]]) -> matrix.Registry:
    registry = _third_parties()

    def model() -> PooledModel:
        runtime = PooledModel(log)
        runtime.answer = lambda prompt: '{"score": 0.7}' if "Rubric:" in prompt else "BANANA"
        return runtime

    def agent(config: ScriptedConfig) -> PooledAgent:
        runtime = PooledAgent(log)
        runtime.answer = lambda task: matrix.AgentResponse(
            content='{"score": 0.6}' if "Rubric:" in task else "BANANA", num_turns=1
        )
        return runtime

    registry.register("model-runtime", POOLED_MODEL, model)
    registry.register("agent-runtime", POOLED_AGENT, agent, config=ScriptedConfig)
    return registry


class TestEachRuntimeIsClosedInTheLoopThatUsedIt:
    """Regression (ix's half): a ``model-call`` trial, a model judge's reading and
    each agent round build a runtime and never closed it. hardline keeps a client per event
    loop and can close it only in that loop, while it runs, so every one was left open
    until garbage collection, and hardline warned of it when the loop ended."""

    @pytest.mark.parametrize(
        ("subject", "judge"),
        [
            ({"kind": "model-call", "runtime": POOLED_MODEL}, {"model_runtime": POOLED_MODEL}),
            ({"kind": "agent-session", "runtime": POOLED_AGENT}, {"agent_runtime": POOLED_AGENT}),
        ],
    )
    def test_each_built_runtime_is_closed_after_its_calls_in_their_loop(
        self, tmp_path: Path, subject: dict[str, Any], judge: dict[str, str]
    ) -> None:
        log: list[list[tuple[str, object]]] = []
        lab = _either(
            tmp_path, subject, sensors=[{"kind": "judge", "rubric": "Helpful.", **judge}],
            metrics=["judged_score"],
        )  # fmt: skip
        report = Root(registry=_pooled(log), settings=_settings()).run(lab, exploratory=True)
        assert report.status == "completed"
        used = [events for events in log if events]
        assert len(used) >= 2 * 10  # a subject's and a judge's runtime for each trial
        for events in used:  # each closed once, after its calls, in the loop they ran in
            kinds = [kind for kind, _ in events]
            assert kinds == ["used"] * (len(kinds) - 1) + ["closed"], kinds
            assert len({loop for _, loop in events}) == 1


class SandboxConfig(BaseModel):
    """A third party's agent runtime config. It takes an option of claude-sdk's name with a
    meaning of its own (``setting_sources``: where it reads its sandbox's settings from,
    nothing to do with skills), and says what its sessions do."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    read_only: bool = False
    setting_sources: list[str] = []

    def capabilities(self) -> matrix.AgentCapabilities:
        return matrix.AgentCapabilities(
            settings_skills=True,
            project_skills=True,
            skill_tool="Skill",
            skills_dir="sandbox/skills",
            writes_files=not self.read_only,
            set_by={"writes_files": "read_only"},
        )


SANDBOXED = "acme.v1.agent-runtime.sandboxed"


def _sandboxed() -> matrix.Registry:
    """The third parties, and ``acme``'s sandboxed agent runtime, scripted as the rest."""
    registry = _third_parties()

    def build(config: SandboxConfig) -> Scripted:
        runtime = Scripted()
        runtime.answer = _fires()
        return runtime

    registry.register("agent-runtime", SANDBOXED, build, config=SandboxConfig)
    return registry


class TestCapabilitiesAreAsked:
    """Regression: ix read two of claude-sdk's option names under ``runtime_options``
    (``setting_sources``, ``permission_mode``) to tell whether a session would load its skills
    or could write files: Claude Code's knowledge inside ix, applied to any runtime given
    those names. ix now asks the runtime, which declares it as data
    (``matrix.AgentCapabilities``)."""

    def _validated(
        self, tmp_path: Path, subject: dict[str, Any], **changes: Any
    ) -> set[tuple[str, str]]:
        (tmp_path / "skills" / "review").mkdir(parents=True, exist_ok=True)
        routing = any(sensor["kind"] == "activation" for sensor in changes.get("sensors", ()))
        lab = _either(tmp_path, subject, routing=routing, **changes)
        root = Root(registry=_sandboxed(), settings=_settings())
        return {(p.path, p.kind) for p in root.validate(lab).problems}

    def test_a_runtime_that_loads_its_skills_is_not_held_to_claude_sdks_option(
        self, tmp_path: Path
    ) -> None:
        """``acme``'s ``setting_sources: []`` means something of its own, and it says its
        sessions load their skills: nothing is wrong. Read by name, as before, it was
        "skills that can never fire"."""
        session = {"kind": "agent-session", "runtime": SANDBOXED, "skills": ["skills/review"],
                   "runtime_options": {"setting_sources": []}}  # fmt: skip
        assert self._validated(tmp_path, session, sensors=[{"kind": "activation"}]) == set()
        bare = {"kind": "agent-session", "runtime": SANDBOXED,
                "runtime_options": {"setting_sources": []}}  # fmt: skip
        assert self._validated(tmp_path, bare, sensors=[{"kind": "activation"}]) == set()

    def test_a_runtime_that_writes_nothing_leaves_nothing_to_collect(self, tmp_path: Path) -> None:
        """``acme`` says a ``read_only`` session writes no file, and which option decides
        it: files to collect from one are a problem at that option. Before, only claude-sdk's
        ``permission_mode: plan`` was known to write nothing."""
        collecting = [{"id": "fix", "kind": "prompt", "prompt": "Fix it.", "collect": ["a.py"],
                       "truth": {"starts_with": "BANANA"}}]  # fmt: skip
        session = {"kind": "agent-session", "runtime": SANDBOXED,
                   "runtime_options": {"read_only": True}}  # fmt: skip
        assert self._validated(tmp_path, session, probes=collecting) == {
            ("subjects.it.runtime_options.read_only", "unobservable")
        }
        writable = {**session, "runtime_options": {"read_only": False}}
        assert self._validated(tmp_path, writable, probes=collecting) == set()

    def test_the_pydantic_ai_runtime_loads_no_skills_and_says_so(self, tmp_path: Path) -> None:
        """pydantic-ai has no skills: skills given to a session on it can never fire, and an
        activation sensor on it has none to see, whatever its options are called."""
        session = {"kind": "agent-session", "runtime": "pydantic-ai"}
        skilled = {**session, "skills": ["skills/review"]}
        assert self._validated(tmp_path / "a", skilled) == {("subjects.it.skills", "unobservable")}
        assert self._validated(tmp_path / "b", session, sensors=[{"kind": "activation"}]) == {
            ("subjects.it.runtime", "unobservable")
        }


class SilentConfig(BaseModel):
    """A third party's agent runtime that declares nothing of what its sessions do."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ToollessConfig(BaseModel):
    """One that says its sessions load skills from settings, and names no tool that fires
    one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    def capabilities(self) -> matrix.AgentCapabilities:
        return matrix.AgentCapabilities(settings_skills=True)


class TestASkillToolNobodyNames:
    """Regression: ``activation`` on an agent runtime that names no tool through which a
    skill fires read every ``must_trigger`` probe as not fired, a confident ``fails`` and a
    perfect score on the decoys, and validate said nothing."""

    @pytest.mark.parametrize("config", [SilentConfig, ToollessConfig])
    def test_activation_is_unobservable_until_the_tool_is_named(
        self, tmp_path: Path, config: type[BaseModel]
    ) -> None:
        registry = _third_parties()
        url = "acme.v1.agent-runtime.quiet"
        registry.register("agent-runtime", url, lambda config: Scripted(), config=config)
        session = {"kind": "agent-session", "runtime": url}
        root = Root(registry=registry, settings=_settings())
        lab = _either(tmp_path / "a", session, routing=True, sensors=[{"kind": "activation"}])
        found = {(p.path, p.kind) for p in root.validate(lab).problems}
        assert found == {("subjects.it.runtime", "unobservable")}
        named = [{"kind": "activation", "tool": "Skill"}]
        lab = _either(tmp_path / "b", session, routing=True, sensors=named)
        assert root.validate(lab).problems == ()


class ElsewhereConfig(BaseModel):
    """A third party's agent runtime whose sessions find their skills somewhere of its own,
    and fire one through a tool of its own name: nothing of Claude Code's."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    def capabilities(self) -> matrix.AgentCapabilities:
        return matrix.AgentCapabilities(
            project_skills=True, skill_tool="use_skill", skills_dir="agent/skills"
        )


ELSEWHERE = "acme.v1.agent-runtime.elsewhere"


class Elsewhere:
    """``acme``'s runtime: a session fires each skill it finds in its ``agent/skills``, and
    keeps every file it saw in its working directory."""

    seen: list[list[str]]

    def __init__(self, cwd: str) -> None:
        self.cwd = Path(cwd)

    def check(self, definition: matrix.AgentDefinition) -> None:
        return None

    async def run(self, definition: matrix.AgentDefinition, task: str) -> matrix.AgentResponse:
        Elsewhere.seen.append(
            sorted(str(f.relative_to(self.cwd)) for f in self.cwd.rglob("*") if f.is_file())
        )
        skills = sorted(path.name for path in (self.cwd / "agent" / "skills").glob("*"))
        calls = tuple({"name": "use_skill", "input": {"skill": skill}} for skill in skills)
        return matrix.AgentResponse(content="ok", tool_calls=calls, num_turns=1, family="acme")


def _elsewhere() -> matrix.Registry:
    registry = _third_parties()

    def build(config: ElsewhereConfig, *, cwd: str | None = None) -> Elsewhere:
        return Elsewhere(cwd or ".")  # the preflight builds it with no directory

    registry.register("agent-runtime", ELSEWHERE, build, config=ElsewhereConfig, needs={"cwd"})
    return registry


class TestSkillsGoWhereTheRuntimeSays:
    """Regression: ix copied a session's skills into its ``.claude/skills`` and read a skill as
    fired when the session called ``Skill``, both Claude Code's answers, for every runtime.
    The runtime now declares both (``skills_dir``, ``skill_tool``), and ix asks it."""

    def test_skills_are_placed_and_read_as_fired_where_the_runtime_says(
        self, tmp_path: Path
    ) -> None:
        subject = {"kind": "agent-session", "runtime": ELSEWHERE, "skills": ["skills/review"]}
        lab = _either(tmp_path, subject, routing=True, sensors=[{"kind": "activation"}],
                      metrics=["activated_correctly"])  # fmt: skip
        (lab / "skills" / "review").mkdir(parents=True)
        (lab / "skills" / "review" / "SKILL.md").write_text(SKILL)
        root = Root(registry=_elsewhere(), settings=_settings())
        assert root.validate(lab).valid
        Elsewhere.seen = []
        report = root.run(lab, exploratory=True)
        assert Elsewhere.seen and all(
            files == ["agent/skills/review/SKILL.md"] for files in Elsewhere.seen
        )
        readings = [r for r in _rows(report, "ix.v1.reading")
                    if r["metric"] == "activated_correctly"]  # fmt: skip
        fired = {r["detail"]["fired"] for r in readings}
        assert fired == {"review"}  # every trial fired it, decoy and all
        responses = [row["observations"]["response"] for row in _rows(report, "ix.v1.interaction")]
        assert {response["skill_tool"] for response in responses} == {"use_skill"}

    def test_a_session_that_cannot_call_the_runtimes_skill_tool_is_a_problem(
        self, tmp_path: Path
    ) -> None:
        subject = {"kind": "agent-session", "runtime": ELSEWHERE, "skills": ["skills/review"]}
        root = Root(registry=_elsewhere(), settings=_settings())
        for tools, problems in [(["Read", "Skill"], 1), (["Read", "use_skill"], 0)]:
            lab = _either(tmp_path / str(problems), {**subject, "tools": tools}, routing=True,
                          sensors=[{"kind": "activation"}])  # fmt: skip
            found = root.validate(lab).problems
            assert [(p.path, p.kind) for p in found] == [("subjects.it.tools", "unobservable")][
                :problems
            ]
            assert all("use_skill" in p.message and "use_skill" in (p.fix or "") for p in found)


def lookup(word: str) -> str:
    """Find a word in the dictionary: the tool the pydantic-ai session below calls."""
    return {"banana": "a long yellow fruit"}.get(word, "not found")


PYDANTIC_AI = "matrix.v1.agent-runtime.pydantic-ai"


class TestASecondRealAgentRuntime:
    """matrix's ``pydantic-ai`` runtime is the second agent runtime that runs a model's loop,
    and ix names it in an ``agent-session`` subject as it names ``claude-sdk``: by
    its registered name, its options declared by its config. Here its model is hardline's
    mock backend, a stand-in row, and its tool a function in this module: no model is
    called, and the session's loop, tool call included, runs for real."""

    def test_an_agent_session_runs_on_it_with_its_tools_and_its_facts(self, tmp_path: Path) -> None:
        registry = matrix.default_registry(discover=False)
        register(registry)
        root = Root(registry=registry, settings=_settings())
        asks = {"tool_calls": [{"name": "lookup", "arguments": '{"word": "banana"}'}]}
        row = {"backend": "mock", "model": "m-1", "family": "stand",
               "options": {"script": [asks, "BANANA, a long yellow fruit"]}}  # fmt: skip
        session = {
            "kind": "agent-session",
            "runtime": "pydantic-ai",
            "runtime_options": {
                "models": {"models": {"q": row}},
                "default_model": "q",
                "tools": {"lookup": f"{__name__}:lookup"},
            },
        }
        lab = _either(tmp_path, session)
        assert root.validate(lab).valid and root.plan(lab).runnable
        report = root.run(lab, exploratory=True)
        assert report.status == "completed"
        (estimate,) = [e for e in report.analysis.estimates if e.metric == "text_matched"]
        assert estimate.value == pytest.approx(1.0)
        assert estimate.n == 10
        interactions = _rows(report, "ix.v1.interaction")
        calls = [row["observations"]["response"]["tool_calls"] for row in interactions]
        assert all([c["name"] for c in called] == ["lookup"] for called in calls)
        usage = [row["observations"]["usage"] for row in interactions]
        assert {(u["runtime"], u["family"]) for u in usage} == {(PYDANTIC_AI, "stand")}
        sessions = _session_rows(report)
        assert {(s["runtime"], s["turns"]) for s in sessions} == {(PYDANTIC_AI, 2)}
        assert {tuple(s["drives"]) for s in sessions} == {("pydantic-ai-slim", "hardline")}


class Hanging(Scripted):
    """A runtime whose agent never finishes a task that asks for something hard, and
    answers every other one at once."""

    async def run(self, definition: matrix.AgentDefinition, task: str) -> matrix.AgentResponse:
        if "hard" in task:
            await asyncio.sleep(60)
        return self.answer(task)


class TestASessionThatOverrunsItsBudget:
    def test_is_the_subjects_failure_not_missing_data(self, tmp_path: Path) -> None:
        """Regression: a session that ran past its probe's ``timeout`` was a harness timeout,
        so missing data: an agent that hung on its hard probes had exactly those trials
        left out, and its pass rate was computed over the probes it finished. Here it hangs
        on one probe of two and passes the other, and ``>= 0.8`` held."""
        registry = matrix.default_registry(discover=False)
        register(registry)

        def build() -> Hanging:
            runtime = Hanging()
            runtime.answer = lambda task: matrix.AgentResponse(
                content=task, num_turns=1, stop="completed", family="scripted", model="s-1"
            )
            return runtime

        registry.register("agent-runtime", "acme.v1.agent-runtime.hanging", build)
        data = {
            "name": "hangs",
            "question": "Does the agent answer each question?",
            "genre": "evaluate",
            "subjects": {
                "agent": {"kind": "agent-session", "runtime": "acme.v1.agent-runtime.hanging"}
            },
            "probes": [
                {"id": probe, "kind": "prompt", "prompt": f"An {probe} question: say ok.",
                 "timeout": "50ms", "truth": {"contains": "ok"}}
                for probe in ("easy", "hard")
            ],
            "primary": {"metric": "text_matched"},
            "threshold": {"op": ">=", "value": 0.8},
            "repeats": 20,
        }  # fmt: skip
        lab = tmp_path / "hangs"
        lab.mkdir()
        (lab / "experiment.yaml").write_text(yaml.safe_dump(data))
        report = Root(registry=registry, settings=_settings()).run(lab, exploratory=True)
        assert report.indication == "fails"
        assert [(f.owner, f.reason, f.count) for f in report.analysis.failures] == [
            ("subject", "overran", 20)
        ]
        (finding,) = report.analysis.findings
        assert finding.reason == "the subject failed 20 of 40 trial(s)"


class TestADryRun:
    """Regression: a dry run needed an edited copy of the experiment."""

    def test_the_experiment_as_written_runs_on_stand_ins(self, tmp_path: Path) -> None:
        lab = _skill_lab(
            tmp_path,
            runtime_options={
                "permission_mode": "bypassPermissions",
                "setting_sources": ["project"],
            },
        )
        data = yaml.safe_load((lab / "experiment.yaml").read_text())
        data["sensors"] = [
            {"kind": "judge", "model_runtime": "hardline", "rubric": "Did it review the diff?"}
        ]
        data["metrics"] = ["judged_score"]
        (lab / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
        written = (lab / "experiment.yaml").read_bytes()
        root = Root(settings=_settings())
        root.register(lab)
        registration = (lab / "experiment.registration.json").read_bytes()

        report = root.run(lab, dry_run=True)

        assert (lab / "experiment.yaml").read_bytes() == written
        assert (lab / "experiment.registration.json").read_bytes() == registration
        assert report.dry_run and report.simulated == "all" and not report.gated
        assert report.verdict is None and report.indication is None
        assert report.no_verdict == DRY_RUN and report.exit_code == 0
        assert report.trials.carried_out == 18
        assert report.stood_in == (
            "subjects.agent: runtime_options set aside: the mock runtime takes none of them",
            "sensors.0: judged on mock, which scores every reply 0.5: the readings say nothing",
        )

    def test_a_model_call_stands_in_on_matrixs_mock_model_runtime(self, tmp_path: Path) -> None:
        """matrix's own mock model runtime gives each probe's canned reply, at the
        model-runtime point: no hardline model is resolved or called."""
        lab = _either(
            tmp_path,
            {"kind": "model-call", "runtime": "hardline", "model": "nowhere",
             "runtime_options": {"temperature": 0}},
        )  # fmt: skip
        data = yaml.safe_load((lab / "experiment.yaml").read_text())
        for probe, reply in zip(data["probes"], ["BANANA split", "Plain"], strict=True):
            probe["mock_response"] = reply
        (lab / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
        report = Root(settings=_settings()).run(lab, dry_run=True)
        assert report.simulated == "all" and report.exit_code == 0
        assert report.stood_in == (
            "subjects.it: runtime_options set aside: the mock runtime takes none of them",
        )
        (estimate,) = [e for e in report.analysis.estimates if e.metric == "text_matched"]
        assert estimate.value == pytest.approx(0.5)
        usage = [row["observations"]["usage"] for row in _rows(report, "ix.v1.interaction")]
        assert {(row["runtime"], row["backend"], row["simulated"]) for row in usage} == {
            ("matrix.v1.model-runtime.mock", "mock", True)
        }

    def test_a_subject_with_no_stand_in_is_refused(
        self, compare_data: dict[str, Any], write_experiment: Any
    ) -> None:
        with pytest.raises(ix.ConfigError, match="subjects.buffered .kind process., "):
            Root(settings=_settings()).run(write_experiment(compare_data), dry_run=True)

    @pytest.mark.parametrize("dry_run", [True, False])
    def test_a_session_that_names_no_runtime_is_refused_before_any_session_starts(
        self, tmp_path: Path, dry_run: bool
    ) -> None:
        """Regression: ``runtime`` defaulted to claude-sdk, and a fixture that named none
        started paid sessions. A dry run does not paper over it: the file is wrong as
        written, and the fix says which runtime calls a model."""
        lab = _skill_lab(tmp_path, tools=["Read", "Skill"])
        data = yaml.safe_load((lab / "experiment.yaml").read_text())
        del data["subjects"]["agent"]["runtime"]
        (lab / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
        root = Root(settings=_settings())
        (problem,) = root.validate(lab).problems
        assert (problem.path, problem.kind) == ("subjects.agent.runtime", "option_missing")
        assert "mock (canned replies; calls no model)" in problem.fix
        with pytest.raises(ix.ConfigError):
            root.run(lab, dry_run=dry_run, exploratory=not dry_run)
        assert not (lab / ".ix").exists()

    def test_its_report_cannot_be_read_as_evidence(self, tmp_path: Path) -> None:
        lab = _skill_lab(tmp_path, runtime_options={"permission_mode": "bypassPermissions"})
        result = CliRunner(env={"COLUMNS": "200"}).invoke(main, ["run", str(lab), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert f"none: {DRY_RUN}" in result.stdout
        assert "Findings on mock replies (the harness, not the agent)" in result.stdout
        assert "Indication" not in result.stdout


class Listening(Scripted):
    """A scripted runtime that keeps, for each session, the system prompt and the task it
    was given, as the agent would read them."""

    heard: list[tuple[str | None, str]]

    async def run(self, definition: matrix.AgentDefinition, task: str) -> matrix.AgentResponse:
        self.heard.append((definition.system_prompt, task))
        return self.answer(task)


def _listening(heard: list[tuple[str | None, str]]) -> matrix.Registry:
    """``SCRIPTED`` as a :class:`Listening` runtime that appends to ``heard``."""
    registry = matrix.default_registry(discover=False)
    register(registry)

    def build(config: ScriptedConfig) -> Listening:
        runtime = Listening()
        runtime.answer, runtime.heard = _fires(), heard
        return runtime

    registry.register("agent-runtime", SCRIPTED, build, config=ScriptedConfig)
    return registry


class TestAPromptReachesTheAgentAsWritten:
    """Regression: every ``{{`` and ``}}`` in a subject's or a probe's options was un-doubled
    before the agent saw it, so a prompt holding nested JSON or a template was sent altered,
    while the registration digested the text as written, and nothing said so."""

    SYSTEM = 'Answer as {"verdict": {"ok": true}} only; {{x}} is the factor, here {x}.'
    PROMPT = (
        'Return JSON like {"a": {"b": 1}} and keep the line {{ user.name }} as it is.\n'
        "{% for item in items %}- {{ item }}{% endfor %}\n"
        "Write {{x}} literally; the level is {x}."
    )

    def test_nested_json_and_a_template_arrive_byte_for_byte(self, tmp_path: Path) -> None:
        data = {
            "name": "as-written",
            "question": "Does the prompt reach the agent as written?",
            "genre": "evaluate",
            "subjects": {
                "agent": {"kind": "agent-session", "runtime": SCRIPTED,
                          "system_prompt": self.SYSTEM},
            },
            "factors": {"x": ["one"]},
            "probes": [
                {"id": "ask", "kind": "prompt", "prompt": self.PROMPT,
                 "truth": {"expectation": "must_trigger", "expected_skill": "review"}},
            ],
            "primary": {"metric": "activated_correctly"},
            "threshold": {"op": ">=", "value": 0.5},
            "repeats": 5,
        }  # fmt: skip
        lab = tmp_path / "as-written"
        lab.mkdir()
        (lab / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
        heard: list[tuple[str | None, str]] = []
        root = Root(registry=_listening(heard), settings=_settings())
        found = root.validate(lab)
        assert found.valid and not found.problems, found.problems
        root.run(lab, exploratory=True)
        assert set(heard) == {
            (
                'Answer as {"verdict": {"ok": true}} only; {x} is the factor, here one.',
                'Return JSON like {"a": {"b": 1}} and keep the line {{ user.name }} as it is.\n'
                "{% for item in items %}- {{ item }}{% endfor %}\n"
                "Write {x} literally; the level is one.",
            )
        }

    def test_a_doubled_name_nothing_fills_is_sent_as_written(self, tmp_path: Path) -> None:
        """Regression: ``{{name}}`` became ``{name}`` for any name that could be a
        placeholder, so a Handlebars or Mustache line in a prompt (``{{user}}``) reached the
        agent altered. Only a name that would be filled there is an escape: ``{{size}}``,
        where ``size`` is a factor, is the text ``{size}``; ``{{user}}`` is sent as written."""
        lab = _routing(
            tmp_path,
            factors={"size": ["small"]},
            subjects={
                "catalog": {"kind": "agent-session", "runtime": SCRIPTED,
                            "system_prompt": "Hello {{user}}; {{size}} is {size}."},
            },
        )  # fmt: skip
        (lab / "probes" / "review.md").write_text(
            "---\nexpectation: must_trigger\nexpected_skill: review\n---\n"
            "Review {{user}}'s diff; {{size}} is {size}.\n"
        )
        heard: list[tuple[str | None, str]] = []
        Root(registry=_listening(heard), settings=_settings()).run(lab, exploratory=True)
        assert {system for system, _ in heard} == {"Hello {{user}}; {size} is small."}
        assert "Review {{user}}'s diff; {size} is small.\n" in {task for _, task in heard}

    def test_code_whose_braces_nothing_fills_is_sent_as_written(self, tmp_path: Path) -> None:
        """Regression: ``{user}`` in a prompt, where nothing fills ``user``, was refused as
        ``unbound_placeholder``, and ``{{user}}`` was sent doubled, so a prompt could not
        hold a Python f-string or a JS template literal at all. In prompt text a name nothing
        fills is sent as written, and validate warns of it rather than refusing."""
        code = 'Fix this: print(f"{user}") prints {user!r} and `${user}`.'
        lab = _routing(
            tmp_path,
            subjects={
                "catalog": {"kind": "agent-session", "runtime": SCRIPTED,
                            "system_prompt": f"Read code as code: {code}"},
            },
        )  # fmt: skip
        (lab / "probes" / "review.md").write_text(
            f"---\nexpectation: must_trigger\nexpected_skill: review\n---\n{code}\n"
        )
        heard: list[tuple[str | None, str]] = []
        root = Root(registry=_listening(heard), settings=_settings())
        found = root.validate(lab)
        assert found.valid, found.problems
        assert {(p.path, p.kind, p.got) for p in found.warnings} == {
            ("subjects.catalog.system_prompt", "unbound_placeholder", "user"),
            ("probes.review.prompt", "unbound_placeholder", "user"),
        }
        root.run(lab, exploratory=True)
        assert {system for system, _ in heard} == {f"Read code as code: {code}"}
        assert f"{code}\n" in {task for _, task in heard}

    def test_a_loops_doubled_placeholder_is_its_text_in_the_next_round(
        self, tmp_path: Path
    ) -> None:
        """A loop's prompt is filled twice, with the cell's levels and then by the session
        with the round's ``{response}``: ``{{response}}`` is un-doubled once, by the last,
        ``{{size}}`` by the first, and ``{{user}}``, which neither fills, not at all."""
        lab = _routing(
            tmp_path,
            factors={"size": ["small"]},
            subjects={
                "catalog": {
                    "kind": "agent-session",
                    "runtime": SCRIPTED,
                    "loop": {
                        "strategy": "self-review",
                        "rounds": 2,
                        "prompt": "You said {response}; quote {{response}} as "
                        '{"k": {"v": 1}} for {{user}} at {{size}}.',
                    },
                }
            },
        )
        heard: list[tuple[str | None, str]] = []
        Root(registry=_listening(heard), settings=_settings()).run(lab, exploratory=True)
        assert {task for _, task in heard[1::2]} == {
            'You said ok; quote {response} as {"k": {"v": 1}} for {{user}} at {size}.'
        }
        until = "{{response}} {{done}} {}"
        assert Loop(strategy="self-review", until=until).until == "{response} {{done}} {}"

    def test_a_factor_named_as_a_round_fills_leaves_the_rounds_escape_to_the_round(
        self, tmp_path: Path
    ) -> None:
        """A factor may share a name with what a round fills (``until``, swept as the stop
        word). ``{{until}}`` in the loop's prompt is then the text ``{until}``: the trial's
        filling leaves it doubled for the round's, which un-doubles it once. Un-doubled by
        the trial, the round would fill it with the stop word."""
        lab = _routing(
            tmp_path,
            factors={"until": ["DONE"]},
            subjects={
                "catalog": {
                    "kind": "agent-session",
                    "runtime": SCRIPTED,
                    "loop": {
                        "strategy": "self-review",
                        "rounds": 2,
                        "until": "{until}",
                        "prompt": "Write {{until}} literally, then {until} when done.",
                    },
                }
            },
        )
        heard: list[tuple[str | None, str]] = []
        Root(registry=_listening(heard), settings=_settings()).run(lab, exploratory=True)
        rounds = {task for _, task in heard[1::2]}
        assert rounds == {"Write {until} literally, then DONE when done."}

    def test_two_arms_are_never_put_different_prompts(self, tmp_path: Path) -> None:
        """Regression: ``{{model}}`` in a probe was the text ``{model}`` for the arm that sets
        ``model`` and sent doubled to the arm that does not, so a compare's two arms were put
        different prompts, and validate and the run said nothing. A name only some subjects
        fill is refused, doubled or not, before any session starts."""
        data = {
            "name": "arms",
            "question": "Does each arm get the same prompt?",
            "genre": "compare",
            "comparison": "superiority",
            "subjects": {
                "plain": {"kind": "agent-session", "runtime": SCRIPTED},
                "tuned": {"kind": "agent-session", "runtime": SCRIPTED, "model": "m-2"},
            },
            "primary": {"metric": "activated_correctly"},
            "repeats": 5,
        }
        lab = tmp_path / "arms"
        (lab / "probes").mkdir(parents=True)
        (lab / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
        (lab / "probes" / "ask.md").write_text(
            "---\ntruth: {expectation: must_trigger, expected_skill: review}\n---\n"
            'Write a Python line that prints print(f"{{model}}") and a Handlebars {{model}} tag.\n'
        )
        heard: list[tuple[str | None, str]] = []
        root = Root(registry=_listening(heard), settings=_settings())
        found = root.validate(lab)
        assert [(p.path, p.kind, p.got) for p in found.problems] == [
            ("probes.ask.prompt", "unbound_placeholder", "model")
        ]
        with pytest.raises(ix.ConfigError):
            root.run(lab, exploratory=True)
        assert heard == []

    def test_an_escape_in_an_option_that_is_not_prompt_text_is_its_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: ``{{HOME}}`` in a ``setup`` command reached the shell doubled, and every
        trial failed (``${{HOME}}: bad substitution``), though validate said valid. Outside
        prompt text a doubled name is the classic escape: ``{{name}}`` is the text
        ``{name}``, filled there or not."""
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        where = tmp_path / "where.txt"
        lab = _routing(
            tmp_path,
            subjects={
                "catalog": {"kind": "agent-session", "runtime": SCRIPTED,
                            "setup": ["sh", "-c", f"echo ${{{{HOME}}}} > {where}"]},
            },
        )  # fmt: skip
        heard: list[tuple[str | None, str]] = []
        root = Root(registry=_listening(heard), settings=_settings())
        assert root.validate(lab).valid
        report = root.run(lab, exploratory=True)
        assert report.status == "completed" and heard
        assert where.read_text() == f"{tmp_path / 'home'}\n"

    def test_a_warning_of_prompt_text_is_said_by_the_run_and_kept_in_its_record(
        self, tmp_path: Path
    ) -> None:
        """Regression: a typo in prompt text (``{sise}``) was warned of by validate and plan
        only; register and run said nothing, and the run's record kept no trace. The run's
        report and manifest carry it, and so does what register returns."""
        lab = _routing(tmp_path, factors={"size": ["small"]})
        (lab / "probes" / "review.md").write_text(
            "---\nexpectation: must_trigger\nexpected_skill: review\n---\n"
            "Review the {size} diff; {sise} is a typo.\n"
        )
        root = Root(registry=_listening([]), settings=_settings())
        typo = ("probes.review.prompt", "unbound_placeholder", "sise")
        registered = root.register(lab)
        assert typo in {(w.path, w.kind, w.got) for w in registered.warnings}
        report = root.run(lab, exploratory=True)
        # The report is read back from the run's record: its warnings are the manifest's.
        assert typo in {(w.path, w.kind, w.got) for w in report.warnings}
        assert typo in {(w.path, w.kind, w.got) for w in root.report(report.location).warnings}


class TestTheLiveGuardIsOnIxsPath:
    """The workspace guard replaces the calls where a session or a model call
    reaches a provider, and its own check reads only that the names are replaced. These
    hold that a run through ix calls those very names, so the guard is in ix's path too. In
    case one ever is not, each first points the provider at a closed port, away from this
    user's config and keys."""

    @pytest.fixture(autouse=True)
    def _nowhere(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            monkeypatch.delenv(name, raising=False)

    def test_a_claude_sdk_session_reaches_the_guard(
        self, tmp_path: Path, refused_live_calls: list[str]
    ) -> None:
        lab = _either(tmp_path, {"kind": "agent-session", "runtime": "claude-sdk",
                                 "runtime_options": {"setting_sources": []}})  # fmt: skip
        report = Root(settings=_settings()).run(lab, exploratory=True)
        refused = list(refused_live_calls)
        refused_live_calls.clear()  # before anything can fail: what is left fails the test
        assert report.trials.carried_out == 10
        assert refused and set(refused) == {"claude_agent_sdk.query"}

    def test_a_hardline_model_call_reaches_the_guard(
        self, tmp_path: Path, refused_live_calls: list[str]
    ) -> None:
        row = {"backend": "openai-compat", "model": "q-1", "family": "q",
               "base_url": "http://127.0.0.1:9/v1"}  # fmt: skip
        settings = matrix.Config[IxSettings](
            client=IxSettings(), matrix=matrix.MatrixConfig(models={"models": {"q": row}})
        )
        lab = _either(tmp_path, {"kind": "model-call", "runtime": "hardline", "model": "q"})
        report = Root(settings=settings).run(lab, exploratory=True)
        refused = list(refused_live_calls)
        refused_live_calls.clear()  # before anything can fail: what is left fails the test
        assert report.trials.carried_out == 10
        assert refused and set(refused) == {"hardline.adapters._out._http.network_transport"}


class TestATextCheck:
    """Regression: a rule like "the reply starts with BANANA" needed a graders module."""

    def test_a_reply_checked_with_no_code_of_ones_own(self, tmp_path: Path) -> None:
        data = {
            "name": "banana",
            "question": "Does the agent start its reply with BANANA?",
            "genre": "evaluate",
            "subjects": {"agent": {"kind": "agent-session", "runtime": "mock"}},
            "probes": [
                {"id": "yes", "kind": "prompt", "prompt": "Greet me.",
                 "mock_response": "BANANA, hello", "truth": {"starts_with": "BANANA"}},
                {"id": "no", "kind": "prompt", "prompt": "Greet me again.",
                 "mock_response": "Hello, banana", "truth": {"starts_with": "BANANA"}},
            ],
            "primary": {"metric": "text_matched"},
            "threshold": {"op": ">=", "value": 0.5},
            "repeats": 5,
        }  # fmt: skip
        lab = tmp_path / "banana"
        lab.mkdir()
        (lab / "experiment.yaml").write_text(yaml.safe_dump(data))
        root = Root(settings=_settings())
        assert root.validate(lab).valid
        report = root.run(lab, exploratory=True)
        (estimate,) = [e for e in report.analysis.estimates if e.metric == "text_matched"]
        assert estimate.value == pytest.approx(0.5) and estimate.n == 10


class TestThePlan:
    def test_a_threshold_out_of_reach_is_said_once(
        self, tmp_path: Path, commands_on: CommandsOn
    ) -> None:
        """Regression: plan printed the "This run cannot hold" line twice."""
        root = Root(registry=_registry(_fires()), settings=_settings())
        commands_on(root)
        lab = _routing(tmp_path, threshold={"op": ">=", "value": 0.9})
        runner = CliRunner(env={"COLUMNS": "80"})
        for _ in range(2):  # before any run, and with an earlier run's spread
            shown = runner.invoke(main, ["plan", str(lab)])
            assert shown.stdout.count("This run cannot hold") == 1, shown.stdout
            document = json.loads(runner.invoke(main, ["plan", str(lab), "--json"]).stdout)
            assert json.dumps(document).count("This run cannot hold") == 1
            root.run(lab, exploratory=True)


class TestTheRunsList:
    """Regression: ``ix runs`` gave a run's id and path, and nothing it found."""

    def test_each_run_says_how_it_stood_and_what_it_found(
        self, tmp_path: Path, commands_on: CommandsOn
    ) -> None:
        root = Root(registry=_registry(_fires()), settings=_settings())
        commands_on(root)
        lab = _routing(tmp_path)
        root.register(lab)
        gated = root.run(lab)
        exploratory = root.run(lab, exploratory=True)
        dry = root.run(lab, dry_run=True)
        listed = root.runs(lab).runs
        assert [run.run for run in listed] == [gated.run, exploratory.run, dry.run]
        assert [run.mode for run in listed] == ["gated", "exploratory", "dry"]
        assert [run.verdict for run in listed] == [gated.verdict, None, None]
        assert [run.indication for run in listed] == [None, exploratory.indication, None]
        assert all(run.started_at and run.ended_at for run in listed)
        runner = CliRunner(env={"COLUMNS": "80"})
        document = json.loads(runner.invoke(main, ["runs", str(lab), "--json"]).stdout)
        assert [run["mode"] for run in document["runs"]] == ["gated", "exploratory", "dry"]
        text = runner.invoke(main, ["runs", str(lab)]).stdout
        assert f"{gated.run}  " in text and f"  gated  completed  {gated.verdict}  " in text
        assert f"indication {exploratory.indication}" in text
        assert "dry  completed  the harness, not the agent  15/15 trials\n" in text
        assert "spent" not in text and all("spent" not in run for run in document["runs"])


class Throttled:
    """A model runtime whose every call is rate limited after one attempt that answered:
    what a provider out of quota looks like, with what the attempts cost."""

    def check(self, model: str | None) -> None:
        return None

    async def call(self, request: matrix.ModelRequest) -> matrix.ModelCall:
        spent = matrix.TokenUsage(input_tokens=9, output_tokens=2)
        raise matrix.ModelRuntimeError(
            "slow down",
            reason="rate_limited",
            call=matrix.ModelCall(
                usage=spent,
                attempt_log=(
                    matrix.CallAttempt(name="t", usage=spent, latency_ms=5),
                    matrix.CallAttempt(name="t", reason="rate_limit", latency_ms=1),
                ),
            ),
        )


def _session_rows(report: ix.Report) -> list[dict[str, Any]]:
    return _rows(report, matrix.SESSION)


def _judged_calls(tmp_path: Path) -> ix.Report:
    """A dry run of a model-call subject judged by a model judge: both on matrix's mock."""
    lab = _either(
        tmp_path,
        {"kind": "model-call", "runtime": "hardline", "model": "nowhere"},
        sensors=[{"kind": "judge", "model_runtime": "hardline", "rubric": "Helpful."}],
        metrics=["judged_score"],
    )
    return Root(settings=_settings()).run(lab, dry_run=True)


class TestAVerdictTracesToTheModelThatAnswered:
    """Regression: ix's own model calls (the model-call subject, the model judge)
    left no row in the run's session table, and no interaction or reading said which
    sessions were behind it, so a verdict could not be traced to the model that answered."""

    def test_a_reading_joins_its_interaction_and_each_to_its_session_row(
        self, tmp_path: Path
    ) -> None:
        report = _judged_calls(tmp_path)
        sessions = _session_rows(report)
        interactions = {row["seq"]: row for row in _rows(report, "ix.v1.interaction")}
        judged = [r for r in _rows(report, "ix.v1.reading") if r["metric"] == "judged_score"]
        assert judged and len(sessions) == 2 * len(interactions)  # a call and a judgement each
        mock = matrix.model_runtime_type_url("mock")
        for reading in judged:
            (by,) = reading["sessions"]  # the judge's call: the reading's own session
            judge = sessions[by]
            assert (judge["port"], judge["runtime"], judge["status"]) == (
                "model-runtime", mock, "completed",
            )  # fmt: skip
            interaction = interactions[reading["seq"]]
            (answered,) = interaction["sessions"]  # the subject's call, behind the trial
            subject = sessions[answered]
            assert (subject["runtime"], subject["family"], subject["finish_reason"]) == (
                mock, "mock", "stop",
            )  # fmt: skip
            prompt = interaction["observations"]["response"]["prompt"]
            assert subject["sent"] == matrix.digest(
                {"prompt": prompt, "system": None, "model": "nowhere", "output": None}
            )
            assert by != answered

    def test_an_agent_sessions_trial_names_the_sessions_its_rounds_ran(
        self, tmp_path: Path
    ) -> None:
        lab = _routing(
            tmp_path,
            subjects={
                "catalog": {
                    "kind": "agent-session",
                    "runtime": SCRIPTED,
                    "loop": {"strategy": "self-review", "rounds": 2},
                }
            },
        )
        report = Root(registry=_registry(_fires()), settings=_settings()).run(lab, exploratory=True)
        sessions = _session_rows(report)
        interactions = _rows(report, "ix.v1.interaction")
        named = [n for row in interactions for n in row["sessions"]]
        assert all(len(row["sessions"]) == 2 for row in interactions)  # one per round
        assert sorted(named) == list(range(len(sessions)))  # each session, once
        assert {(s["port"], s["runtime"], s["agent"]) for s in sessions} == {
            ("agent-runtime", SCRIPTED, "catalog")
        }

    def test_the_readmes_join_reads_a_readings_model_without_ix(self, tmp_path: Path) -> None:
        duckdb = pytest.importorskip("duckdb")
        readme = (PACKAGE / "README.md").read_text()
        found = re.search(r"<!-- reading-to-session -->\s*```sql\n(.*?)```", readme, re.S)
        assert found, "the README shows the join from a reading to its session"
        report = _judged_calls(tmp_path)
        run = str(Path(report.tables["ix.v1.reading"]).parent)
        rows = duckdb.sql(found.group(1).replace("RUN", run)).fetchall()
        assert len(rows) == 10
        assert {row[3:] for row in rows} == {
            ("matrix.v1.model-runtime.mock", "nowhere", "mock", "completed")
        }

    def test_a_call_that_failed_is_a_session_row_its_trial_names(self, tmp_path: Path) -> None:
        registry = _third_parties()
        registry.register("model-runtime", "acme.v1.model-runtime.throttled", Throttled)
        throttled = {"kind": "model-call", "runtime": "acme.v1.model-runtime.throttled"}
        lab = _either(tmp_path, throttled)
        report = Root(registry=registry, settings=_settings()).run(lab, exploratory=True)
        sessions = _session_rows(report)
        for interaction in _rows(report, "ix.v1.interaction"):
            assert interaction["failure"]["owner"] == "harness"
            (behind,) = interaction["sessions"]
            failed = sessions[behind]
            assert (failed["status"], failed["reason"]) == ("failed", "rate_limited")
            assert failed["usage"]["input_tokens"] == 9 and len(failed["attempt_log"]) == 2


class _RecorderConfig(BaseModel):
    """A stand-in runtime's config whose ``rounds`` keeps whatever type it is given."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rounds: Any = None
    default: str | None = None


class TestFactorsDriveAgentSettings:
    def test_a_sweep_over_a_loops_rounds_runs_each_level_as_an_integer(
        self, tmp_path: Path
    ) -> None:
        """Owner ruling 2026-10-22 (the rounds curve): ``characterize`` over the loop's ``rounds``
        (1, 2, 3) on a stand-in. Each level reaches the loop and the runtime as the integer it was
        written as, and each trial takes that many rounds; a loop of one round is the first answer
        alone."""
        configs: list[Any] = []
        asked: list[str] = []

        def build(config: _RecorderConfig) -> Scripted:
            configs.append(config.rounds)
            runtime = Scripted()

            def answer(task: str) -> matrix.AgentResponse:
                asked.append(task)
                return matrix.AgentResponse(content=config.default or "a draft", num_turns=1)

            runtime.answer = answer
            return runtime

        registry = _registry(_fires())
        recorder = "acme.v1.agent-runtime.recorder"
        registry.register("agent-runtime", recorder, build, config=_RecorderConfig, stand_in=True)
        data = {
            "name": "rounds",
            "question": "How do the rounds an agent takes bend with the loop's limit?",
            "genre": "characterize",
            "factors": {"rounds": [1, 2, 3]},
            "subjects": {
                "agent": {
                    "kind": "agent-session",
                    "runtime": recorder,
                    "loop": {"strategy": "self-review", "rounds": "{rounds}"},
                    "runtime_options": {"rounds": "{rounds}"},
                }
            },
            "probes": [{"id": "ask", "kind": "prompt", "prompt": "Write it.",
                        "mock_response": "a draft"}],
            "primary": {"metric": "rounds"},
            "repeats": 3,
        }  # fmt: skip
        lab = tmp_path / "rounds"
        lab.mkdir()
        (lab / "experiment.yaml").write_text(yaml.safe_dump(data))
        root = Root(registry=registry, settings=_settings())
        assert ix.validate(lab).valid  # validated through the default root: no adapter needed
        report = root.run(lab, exploratory=True)
        assert report.status == "completed" and report.simulated == "all"
        built = {type(level) for level in configs}
        assert built == {int} and set(configs) == {1, 2, 3}
        by_cell: dict[int, int] = {}
        for interaction in _rows(report, "ix.v1.interaction"):
            usage = interaction["observations"]["usage"]
            by_cell.setdefault(usage["rounds"], 0)
            by_cell[usage["rounds"]] += 1
        assert by_cell == {1: 3, 2: 3, 3: 3}
        assert len(asked) == 3 * (1 + 2 + 3)


class _GoesDown:
    """An environment that is lost after its second trial: the run fails before it senses."""

    def __init__(self) -> None:
        self.asked = 0

    async def up(self, environment: Any, *, base: str, scope: Any) -> dict[str, Any]:
        return {}

    async def lost(self, bindings: Any) -> str | None:
        self.asked += 1
        return "the service went away" if self.asked >= 2 else None


class TestAReportCallsNoModel:
    def test_a_run_that_ended_before_its_judge_read_is_reported_without_asking_it(
        self, tmp_path: Path
    ) -> None:
        """Regression: ``ix report`` of a run that ended before sensing derived its readings
        then, so every report asked the judge again: paid calls from a read command, and a
        stochastic judge's readings differing between two reports of one run."""
        judged: list[str] = []

        def answer(task: str) -> matrix.AgentResponse:
            if "Rubric:" in task:
                judged.append(task)
                return matrix.AgentResponse(content='{"score": 0.9, "reason": "fine"}')
            return _fires()(task)

        registry = _registry(answer)
        environment = "acme.v1.environment.goes-down"
        registry.register("ix.environment", environment, _GoesDown)
        lab = _routing(
            tmp_path,
            metrics=["judged_score"],
            sensors=[{"kind": "judge", "rubric": "Helpful.", "agent_runtime": SCRIPTED}],
            environment={"kind": environment},
        )
        (lab / "kinds").mkdir()
        (lab / "kinds" / "goes-down.yaml").write_text(
            yaml.safe_dump(
                {"schema": "ix.v1.kind", "name": environment, "summary": "Lost after a trial."}
            )
        )
        root = Root(registry=registry, settings=_settings())
        ran = root.run(lab, exploratory=True)
        assert ran.status == "failed" and ran.trials.carried_out == 2
        assert judged == []  # the run ended before its sensors read anything

        for _ in range(2):
            report = root.report(ran.location)
            assert report.readings == "none" and report.trials.carried_out == 2
            assert "judge" in report.unread and "ix run" in report.unread
        assert judged == []  # two reports, no call

    def test_a_report_runs_neither_the_agents_code_nor_the_graders(self, tmp_path: Path) -> None:
        """Regression: only sensors that call a model were kept out of a report; ``outcome``
        and ``function-test`` start processes (the experiment's graders, the agent's code),
        so ``ix report``, a read command, ran them on every report of a stopped run."""
        registry = _registry(_fires())
        environment = "acme.v1.environment.goes-down"
        registry.register("ix.environment", environment, _GoesDown)
        lab = _routing(
            tmp_path,
            metrics=["tests_score"],
            sensors=[{"kind": "outcome", "graders": "graders.py"}],
            environment={"kind": environment},
        )
        ran_graders = tmp_path / "graders-ran"
        (lab / "graders.py").write_text(
            f"open({str(ran_graders)!r}, 'a').write('ran\\n')\nGRADERS = {{}}\n"
        )
        (lab / "kinds").mkdir()
        (lab / "kinds" / "goes-down.yaml").write_text(
            yaml.safe_dump(
                {"schema": "ix.v1.kind", "name": environment, "summary": "Lost after a trial."}
            )
        )
        root = Root(registry=registry, settings=_settings())
        ran = root.run(lab, exploratory=True)
        assert ran.status == "failed" and ran.trials.carried_out == 2
        assert ran.readings == "none" and "outcome starts processes" in ran.unread
        for _ in range(2):
            report = root.report(ran.location)
            assert report.readings == "none"
            assert "outcome starts processes" in report.unread and "ix run" in report.unread
        assert not ran_graders.exists()  # neither the run's own report nor the two after

    def test_a_run_whose_sensors_call_no_model_is_still_read_at_report_time(
        self, tmp_path: Path
    ) -> None:
        registry = _registry(_fires())
        environment = "acme.v1.environment.goes-down"
        registry.register("ix.environment", environment, _GoesDown)
        lab = _routing(tmp_path, environment={"kind": environment})
        (lab / "kinds").mkdir()
        (lab / "kinds" / "goes-down.yaml").write_text(
            yaml.safe_dump(
                {"schema": "ix.v1.kind", "name": environment, "summary": "Lost after a trial."}
            )
        )
        root = Root(registry=registry, settings=_settings())
        ran = root.run(lab, exploratory=True)
        report = root.report(ran.location)
        assert report.readings == "derived" and report.unread == ""


class TestAJudgesFlags:
    def test_a_judge_of_the_subjects_family_is_flagged_and_what_it_cost_kept(
        self, tmp_path: Path
    ) -> None:
        def answer(task: str) -> matrix.AgentResponse:
            judging = "Rubric:" in task
            return matrix.AgentResponse(
                content='{"score": 0.9, "reason": "fine"}' if judging else "ok",
                num_turns=1, stop="completed", cost_usd=0.01 if judging else 0.1,
                family="scripted",
            )  # fmt: skip

        lab = _routing(
            tmp_path,
            metrics=["judged_score"],
            sensors=[{"kind": "judge", "rubric": "Helpful.", "agent_runtime": SCRIPTED}],
        )
        report = Root(registry=_registry(answer), settings=_settings()).run(lab, exploratory=True)
        (flag,) = report.flags
        assert flag.startswith("judge_in_family:") and "(15 of 15 trials)" in flag
        judged = [row for row in _rows(report, "ix.v1.reading") if row["sensor"].endswith("judge")]
        assert judged and all(row["detail"]["judge"]["cost_usd"] == 0.01 for row in judged)
        assert not any("cost_usd" in row for row in judged)


class TestTelemetry:
    def test_ix_traces_its_own_work_under_its_own_scope(self, tmp_path: Path) -> None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        root = Root(
            registry=_registry(_fires()),
            settings=_settings(),
            tracer=OtelTracer("test", tracer_provider=provider),
        )
        lab = _routing(tmp_path, repeats=5)
        root.plan(lab)
        report = root.run(lab, exploratory=True)
        spans = exporter.get_finished_spans()
        assert {getattr(span.instrumentation_scope, "name", None) for span in spans} == {"ix"}
        names = [span.name for span in spans]
        assert names.count("ix.trial") == 15 and names.count("ix.plan") == 1
        assert names.count("ix.run") == 1 and names.count("ix.sensor") == 30  # 2 sensors
        # Regression (ix-outer 20): bringing the environment up ran untraced.
        assert names.count("ix.environment") == 1
        run = next(span for span in spans if span.name == "ix.run")
        environment = next(span for span in spans if span.name == "ix.environment")
        assert environment.parent is not None
        assert environment.parent.span_id == run.context.span_id
        trial = next(span for span in spans if span.name == "ix.trial")
        assert trial.parent is not None and trial.parent.span_id == run.context.span_id
        # Bringing the environment up is ix's work too: one span, under the run.
        (environment,) = [span for span in spans if span.name == "ix.environment"]
        assert environment.parent is not None
        assert environment.parent.span_id == run.context.span_id
        assert (environment.attributes or {})["ix.environment.kind"] == "none"
        # Spend is removed: no span carries a cost of ix's own.
        assert not any("cost_usd" in key for span in spans for key in span.attributes or {})
        events = [e for span in spans if span.name == "ix.sensor" for e in span.events]
        labels = {
            (e.attributes or {}).get("gen_ai.evaluation.name")
            for e in events
            if e.name == "gen_ai.evaluation.result"
        }
        assert labels == {"activated_correctly", "cost"}
        # Each session row carries the trace it ran in: the run's, under its trial.
        sessions = _session_rows(report)
        assert sessions and {row["trace_id"] for row in sessions} == {
            f"{run.context.trace_id:032x}"
        }
        trials = {f"{span.context.span_id:016x}" for span in spans if span.name == "ix.trial"}
        assert {row["span_id"] for row in sessions} <= trials

    def test_the_tracer_speaks_the_conventions_the_run_path_does_not(self) -> None:
        """A failure is ``error.type``; a metric read is a ``gen_ai.evaluation.result``
        event, scored by value or label, or carrying why it could not be read."""
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        with OtelTracer("test", tracer_provider=provider).trace("ix.sensor") as span:
            span.failed("timeout")
            span.evaluated("latency", score=0.5)
            span.evaluated("fired", label="pass")
            span.evaluated("judged", failure="shape")
            span.evaluated("missing")
        (finished,) = exporter.get_finished_spans()
        assert (finished.attributes or {})["error.type"] == "timeout"
        assert [(e.name, dict(e.attributes or {})) for e in finished.events] == [
            ("gen_ai.evaluation.result",
             {"gen_ai.evaluation.name": "latency", "gen_ai.evaluation.score.value": 0.5}),
            ("gen_ai.evaluation.result",
             {"gen_ai.evaluation.name": "fired", "gen_ai.evaluation.score.label": "pass"}),
            ("gen_ai.evaluation.result",
             {"gen_ai.evaluation.name": "judged", "error.type": "shape"}),
            ("gen_ai.evaluation.result", {"gen_ai.evaluation.name": "missing"}),
        ]  # fmt: skip

    def test_a_telemetry_warning_travels_in_every_json_document(
        self, compare_data: dict[str, Any], write_experiment: Any
    ) -> None:
        file = str(write_experiment(compare_data))
        runner = CliRunner(env={"OTEL_TRACES_EXPORTER": "zipkin", "COLUMNS": "80"})
        for command in (["validate", file], ["plan", file], ["kinds"]):
            result = runner.invoke(main, [*command, "--json"])
            warned = [w for w in json.loads(result.stdout)["warnings"]]
            assert any(w["path"] == "OTEL_TRACES_EXPORTER" for w in warned), command


def test_matrixs_problems_reach_ixs_envelope() -> None:
    from ix.adapters._out.matrix_errors import carried_over

    problem = matrix.Problem(path="matrix.observers.0", kind="name_unknown", message="no such")
    error = matrix.ConfigError("bad config", fix="fix it")
    error.problems = (problem,)
    carried = carried_over("the run could not be carried out", error)
    document = ix.error_document(carried)["error"]
    assert [(p["path"], p["kind"]) for p in document["problems"]] == [
        ("matrix.observers.0", "name_unknown")
    ]


def test_matrixs_own_kinds_reach_ixs_envelope_by_their_names() -> None:
    """Owner ruling 2026-10-22: the warning for a dependency an install lacks stays
    ``dependency_missing`` (the rename from ``extra_missing`` is kept). ix carries matrix's
    own kinds by name, so one matrix has and ix does not carry would arrive as
    ``invalid_value``."""
    from matrix.domain.problems import MATRIX_PROBLEM_KINDS

    from ix.adapters._out.matrix_errors import carried_over
    from ix.domain.problem import CARRIED_KINDS

    assert set(MATRIX_PROBLEM_KINDS) == CARRIED_KINDS
    problem = matrix.Problem(path="matrix.observers.0", kind="dependency_missing", message="m")
    error = matrix.ConfigError("bad config", fix="fix it")
    error.problems = (problem,)
    document = ix.error_document(carried_over("not carried out", error))["error"]
    assert [p["kind"] for p in document["problems"]] == ["dependency_missing"]


# --- what a second drive from ``--skill`` alone found ------------------------------------


class Cautious(Scripted):
    """A scripted runtime that warns of its config as claude-sdk does of a permissive
    ``permission_mode``, and logs it, as claude-sdk does, on every build."""

    def __init__(self) -> None:
        import logging

        logging.getLogger(matrix.RUNTIME_LOGGER).warning("cautious runtime built")

    @property
    def warnings(self) -> tuple[matrix.Problem, ...]:
        return (
            matrix.Problem(
                path="permission_mode",
                kind="permissive",
                message="permission_mode 'bypassPermissions': agents may use tools without "
                "per-call confirmation",
                fix="keep it only for an unattended run in a sandbox",
                got="bypassPermissions",
            ),
        )


def _cautious_registry(answer: Callable[[str], matrix.AgentResponse]) -> matrix.Registry:
    registry = matrix.default_registry(discover=False)
    register(registry)

    def build(config: ScriptedConfig) -> Cautious:
        runtime = Cautious()
        runtime.answer = answer
        return runtime

    registry.register("agent-runtime", SCRIPTED, build, config=ScriptedConfig)
    return registry


def _pass_rate(tmp_path: Path, repeats: int) -> Path:
    """The design the drive met: evaluate, four probes, a boolean metric held to >= 0.9."""
    data = {
        "name": "pass-rate",
        "question": "Does the agent answer each question?",
        "genre": "evaluate",
        "subjects": {"agent": {"kind": "agent-session", "runtime": "mock", "tools": []}},
        "probes": [
            {"id": f"q{n}", "kind": "prompt", "prompt": f"Say {letter}.",
             "mock_response": letter, "truth": {"contains": letter}}
            for n, letter in enumerate("abcd", start=1)
        ],
        "primary": {"metric": "text_matched"},
        "threshold": {"op": ">=", "value": 0.9},
        "repeats": repeats,
    }  # fmt: skip
    lab = tmp_path / f"pass-rate-{repeats}"
    lab.mkdir()
    (lab / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return lab


class TestTheSecondDrive:
    """Regressions from a second agent driving ix and matrix from ``--skill`` alone."""

    def test_a_verdict_is_followed_by_its_exit_code_and_next_step(
        self, tmp_path: Path, commands_on: CommandsOn
    ) -> None:
        """The run's exit code was learned only through ``ix report``: nothing printed it,
        or a next step, after a run that did not hold."""
        root = Root(registry=_registry(_fires()), settings=_settings())
        commands_on(root)
        lab = _routing(tmp_path)
        root.register(lab)
        runner = CliRunner(env={"COLUMNS": "200"})
        shown = runner.invoke(main, ["run", str(lab)])
        assert shown.exit_code == ix.EXIT_CODES["inconclusive"], shown.output
        kind = "inconclusive"
        assert f"Exit 11 ({kind}): {ix.EXIT_MEANINGS[kind]}" in shown.stderr
        assert f"Next: {ix.EXIT_NEXT[kind]}" in shown.stderr
        document = json.loads(runner.invoke(main, ["report", str(lab), "--json"]).stdout)
        assert document["exit"] == {
            "code": 11, "kind": kind, "meaning": ix.EXIT_MEANINGS[kind],
            "next": ix.EXIT_NEXT[kind],
        }  # fmt: skip
        again = runner.invoke(main, ["report", str(lab)])
        assert again.exit_code == 11 and "Exit 11 (inconclusive)" in again.stderr

    def test_the_report_says_what_the_evidence_shows_in_plain_words(
        self, tmp_path: Path, commands_on: CommandsOn
    ) -> None:
        """The plan said "build30 is better than build20 on latency p50", the verdict was
        fails, and nothing said build30 was slower."""
        from ix.domain import Decision, Finding, Interval
        from ix.domain.report import shows

        lab = _routing(tmp_path, threshold={"op": ">=", "value": 0.5})
        evaluate = ix.resolve(lab)
        slower = Decision(
            part="primary", metric="latency", statistic="p50", estimator="mean",
            form="superiority", claim="better than build20", unit="%", better="lower",
            difference=29.6, interval=Interval(low=26.2, high=33.1), n=10, outcome="fails",
        )  # fmt: skip
        compare = evaluate.model_copy(update={"control": "build20"})
        (said,) = shows(compare, [Finding(subject="build30", outcome="fails", decisions=(slower,))])
        assert said == (
            "build30's latency p50 is higher than build20's by 29.6% [26.2%, 33.1%]; lower is "
            "better, so build30 is worse"
        )
        faster = slower.model_copy(
            update={"difference": -12.0, "interval": Interval(low=-15.0, high=-9.0)}
        )
        (said,) = shows(compare, [Finding(subject="build30", outcome="holds", decisions=(faster,))])
        assert said.startswith("build30's latency p50 is lower than build20's by 12% [9%, 15%]")
        assert said.endswith("so build30 is better")
        even = slower.model_copy(update={"difference": 1.0, "interval": Interval(low=-2, high=4)})
        (said,) = shows(compare, [Finding(subject="build30", outcome="inconclusive",
                                          decisions=(even,))])  # fmt: skip
        assert "the interval holds 0, so the run does not show which is higher" in said

        root = Root(registry=_registry(_fires()), settings=_settings())
        commands_on(root)
        report = root.run(lab, exploratory=True)
        (said,) = report.shows
        assert said.startswith("catalog's activated_correctly is 0.667 [")
        assert said.endswith("the interval holds 0.5, so the run does not show whether it "
                             "meets >= 0.5")  # fmt: skip
        assert report.indication == "inconclusive"  # the verdict's semantics are unchanged
        shown = CliRunner(env={"COLUMNS": "200"}).invoke(main, ["report", str(lab)])
        assert f"Shows:        {said}" in shown.stdout
        assert ix.run(lab, dry_run=True).shows == ()  # canned replies show nothing

    def test_a_pilot_runs_fewer_repeats_and_a_verdict_refuses_them(self, tmp_path: Path) -> None:
        """There was no ``--repeats``: the agent edited the registered file to pilot."""
        lab = _pass_rate(tmp_path, repeats=10)
        runner = CliRunner(env={"COLUMNS": "200"})
        written = (lab / "experiment.yaml").read_bytes()
        pilot = runner.invoke(main, ["run", str(lab), "--exploratory", "--repeats", "5", "--json"])
        assert pilot.exit_code == 0, pilot.output
        report = json.loads(pilot.stdout)
        assert report["trials"]["planned"] == 20
        assert report["overridden"] == ["repeats: 5 in place of 10 (--repeats: a pilot)"]
        assert (lab / "experiment.yaml").read_bytes() == written
        dry = runner.invoke(main, ["run", str(lab), "--dry-run", "--repeats", "5"])
        assert dry.exit_code == 0 and "Changed:      repeats: 5 in place of 10" in dry.stdout
        ix.register(lab)
        gated = runner.invoke(main, ["run", str(lab), "--repeats", "5", "--json"])
        assert gated.exit_code == ix.EXIT_CODES["usage"]
        assert json.loads(gated.stderr)["error"]["details"]["path"] == "--repeats"
        assert len(list((lab / ".ix" / "runs").iterdir())) == 2  # the pilot and the dry run
        low = runner.invoke(main, ["run", str(lab), "--exploratory", "--repeats", "2", "--json"])
        assert low.exit_code == ix.EXIT_CODES["config"]
        (found,) = json.loads(low.stderr)["error"]["problems"]
        assert (found["path"], found["kind"]) == ("repeats", "repeats_below_min")

    def test_a_run_too_small_to_hold_says_so_at_the_size_it_ran(self, tmp_path: Path) -> None:
        """Four probes, five repeats, a boolean at >= 0.9: even 20 of 20 shows only
        >= 0.839. The plan of the file at 10 repeats can hold, so it says nothing; the pilot
        at 5 came out inconclusive and nothing said why."""
        small = _pass_rate(tmp_path, repeats=5)
        assert ix.plan(small).sensitivity.text.startswith(
            "This run cannot hold: with 20 trials per cell, even every trial reading true "
            "shows only >= 0.839; it takes 35 trials per cell"
        )
        lab = _pass_rate(tmp_path, repeats=10)
        assert "cannot hold" not in ix.plan(lab).sensitivity.text
        pilot = ix.run(lab, exploratory=True, repeats=5)
        assert pilot.best_case.startswith("This run cannot hold: with 20 trials per cell")
        assert ix.run(lab, exploratory=True).best_case == ""
        shown = CliRunner(env={"COLUMNS": "200"}).invoke(
            main, ["run", str(lab), "--exploratory", "--repeats", "5"]
        )
        assert "Best case:    This run cannot hold: with 20 trials" in shown.stdout

    def test_a_runtime_warning_is_said_once_per_run(
        self, tmp_path: Path, commands_on: CommandsOn, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The claude-sdk runtime's bypassPermissions warning was printed once per trial:
        twenty times in a pilot of twenty."""
        root = Root(registry=_cautious_registry(_fires()), settings=_settings())
        commands_on(root)
        lab = _routing(tmp_path)
        with caplog.at_level("WARNING"):
            report = root.run(lab, exploratory=True)
            planned = root.plan(lab)
        assert report.trials.carried_out == 15
        (warning,) = report.warnings
        said = "subjects.catalog.runtime_options.permission_mode"
        assert (warning.path, warning.kind) == (said, "permissive")
        assert [w.path for w in planned.warnings] == [said]
        assert not [r for r in caplog.records if r.name == matrix.RUNTIME_LOGGER]
        runner = CliRunner(env={"COLUMNS": "200"})
        shown = runner.invoke(main, ["run", str(lab), "--exploratory"])
        assert shown.stderr.count("without per-call confirmation") == 1, shown.stderr
        quiet = runner.invoke(main, ["run", str(lab), "--exploratory", "--json"])
        assert quiet.stderr == ""
        assert [w["path"] for w in json.loads(quiet.stdout)["warnings"]] == [warning.path]

    def test_a_dry_run_report_has_no_probe_screen(self, tmp_path: Path) -> None:
        """Every probe read as "ceiling" on canned replies, which looked like a finding."""
        lab = _pass_rate(tmp_path, repeats=5)
        shown = CliRunner(env={"COLUMNS": "200"}).invoke(main, ["run", str(lab), "--dry-run"])
        assert shown.exit_code == 0, shown.output
        assert "cannot discriminate" not in shown.stdout and "Screen" not in shown.stdout
        assert ix.run(lab, dry_run=True).screen == ()
