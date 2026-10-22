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
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix
import pytest
import yaml
from click.testing import CliRunner
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel, ConfigDict, ValidationError

import ix
from ix.adapters._in.cli import main
from ix.adapters._out.otel_tracing import OtelTracer
from ix.composition import Root
from ix.composition.extension import register
from ix.composition.settings import IxSettings

if TYPE_CHECKING:
    from collections.abc import Callable

EXAMPLE = Path(ix.__file__).resolve().parents[2] / "examples" / "harness-integrity"


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
    """What the scripted runtime takes: a third party's config, declared as matrix's are."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: str = "scripted"


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
    """A third party's agent runtime that mounts plugins, as claude-sdk does."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plugins: tuple[dict[str, Any], ...] = ()


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
    """Regression (W3.6): the experiment's digest covered its document, which names the
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

    def test_report_and_runs_show_the_subjects_digests_and_commit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ix.composition

        root = Root(registry=_mounting(), settings=_settings())
        monkeypatch.setattr(ix.composition, "_DEFAULT", root)
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
    """Regression (W2.4, W2.5): ix told an agent runtime from a model runtime by name, so a
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
        assert any("oneshot has no model 'two-2'" in r for r in root.plan(unknown).unstartable)

    def test_an_agent_runtime_gets_the_session_reviews(self, tmp_path: Path) -> None:
        root = Root(registry=_third_parties(), settings=_settings())
        (tmp_path / "skills" / "review").mkdir(parents=True)
        session = {"kind": "agent-session", "runtime": SCRIPTED, "skills": ["skills/review"]}
        lab = _either(
            tmp_path, {**session, "tools": [], "runtime_options": {"permission_mode": "plan"}},
            routing=True, sensors=[{"kind": "activation"}],
        )  # fmt: skip
        assert {(p.path, p.kind) for p in root.validate(lab).problems} == {
            ("subjects.it.tools", "unobservable"),
            ("subjects.it.runtime_options.permission_mode", "option_unknown"),
        }
        fixed = _either(tmp_path, session, routing=True, sensors=[{"kind": "activation"}])
        assert root.validate(fixed).valid

    def test_each_subject_takes_runtimes_of_its_own_port_only(self, tmp_path: Path) -> None:
        root = Root(registry=_third_parties(), settings=_settings())
        on_a_model = _either(tmp_path / "a", {"kind": "agent-session", "runtime": ONESHOT})
        (said,) = root.plan(on_a_model).unstartable
        assert said.startswith(f"subjects.it: no agent runtime {ONESHOT!r}")
        on_an_agent = _either(tmp_path / "m", {"kind": "model-call", "runtime": SCRIPTED})
        (said,) = root.plan(on_an_agent).unstartable
        assert said.startswith(f"subjects.it: no model runtime {SCRIPTED!r}")

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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: plan printed the "This run cannot hold" line twice."""
        import ix.composition

        root = Root(registry=_registry(_fires()), settings=_settings())
        monkeypatch.setattr(ix.composition, "_DEFAULT", root)
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ix.composition

        root = Root(registry=_registry(_fires()), settings=_settings())
        monkeypatch.setattr(ix.composition, "_DEFAULT", root)
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
        root = Root(registry=_registry(_fires()), settings=_settings())
        root._tracer = OtelTracer("test", tracer_provider=provider)
        lab = _routing(tmp_path, repeats=5)
        root.plan(lab)
        root.run(lab, exploratory=True)
        spans = exporter.get_finished_spans()
        assert {span.instrumentation_scope.name for span in spans} == {"ix"}
        names = [span.name for span in spans]
        assert names.count("ix.trial") == 15 and names.count("ix.plan") == 1
        assert names.count("ix.run") == 1 and names.count("ix.sensor") == 30  # 2 sensors
        run = next(span for span in spans if span.name == "ix.run")
        trial = next(span for span in spans if span.name == "ix.trial")
        assert trial.parent is not None and trial.parent.span_id == run.context.span_id
        # Spend is removed: no span carries a cost of ix's own.
        assert not any("cost_usd" in key for span in spans for key in span.attributes or {})
        events = [e for span in spans if span.name == "ix.sensor" for e in span.events]
        labels = {
            (e.attributes or {}).get("gen_ai.evaluation.name")
            for e in events
            if e.name == "gen_ai.evaluation.result"
        }
        assert labels == {"activated_correctly", "cost"}

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

    def build() -> Cautious:
        runtime = Cautious()
        runtime.answer = answer
        return runtime

    registry.register("agent-runtime", SCRIPTED, build)
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The run's exit code was learned only through ``ix report``: nothing printed it,
        or a next step, after a run that did not hold."""
        import ix.composition

        root = Root(registry=_registry(_fires()), settings=_settings())
        monkeypatch.setattr(ix.composition, "_DEFAULT", root)
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The plan said "build30 is better than build20 on latency p50", the verdict was
        fails, and nothing said build30 was slower."""
        import ix.composition
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
        monkeypatch.setattr(ix.composition, "_DEFAULT", root)
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The claude-sdk runtime's bypassPermissions warning was printed once per trial:
        twenty times in a pilot of twenty."""
        import ix.composition

        root = Root(registry=_cautious_registry(_fires()), settings=_settings())
        monkeypatch.setattr(ix.composition, "_DEFAULT", root)
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
