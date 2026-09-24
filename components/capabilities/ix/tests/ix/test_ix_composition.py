"""ix's composition root: subjects → definitions + runtimes, engines, sensors, config errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from matrix import BoundAgent

from ix.composition import (
    build_registry,
    create_engine,
    make_agent_factory,
    registered_runtimes,
    subject_definition,
    subject_spec,
)
from ix.domain.errors import ConfigError
from ix.domain.types import Probe, Subject
from ix.eval.models import ExperimentConfig

if TYPE_CHECKING:
    from pathlib import Path

MODELS = {
    "default": "q",
    "models": {"q": {"backend": "mock", "model": "qwen-8b", "family": "qwen"}},
}


def _subject(**config: object) -> Subject:
    return Subject(name="s", description="d", config=config)


class TestSubjects:
    def test_subject_splits_into_definition_and_runtime(self):
        subject = _subject(
            system_prompt="Be brief.",
            model="q",
            tools="Read, Grep",
            max_turns=2,
            runtime={"type": "model"},
        )
        definition = subject_definition(subject)
        assert (definition.system_prompt, definition.model) == ("Be brief.", "q")
        assert (definition.tools, definition.max_turns) == (("Read", "Grep"), 2)
        assert subject_spec(subject).runtime == {"type": "model"}

    def test_deployment_key_at_definition_level_is_rejected_with_guidance(self):
        with pytest.raises(ConfigError, match="subject 's': permission_mode.*runtime:"):
            subject_spec(_subject(permission_mode="plan"))

    def test_definition_name_is_sanitised(self):
        assert subject_definition(Subject(name="catalog live!")).name == "catalog-live"


class TestAgentFactory:
    async def test_model_runtime_subject_answers_with_its_family(self):
        factory = make_agent_factory(build_registry(), model_section=lambda: MODELS)
        agent = factory(_subject(system_prompt="x", runtime={"type": "model"}), 0)
        assert isinstance(agent, BoundAgent)
        response = await agent.run("hi")
        assert (response.family, response.content) == ("qwen", "mock:qwen-8b:hi")

    def test_matrix_runtimes_are_built_once_per_subject(self):
        factory = make_agent_factory(build_registry(), model_section=lambda: MODELS)
        subject = _subject(runtime={"type": "model"})
        assert factory(subject, 0).runtime is factory(subject, 4).runtime

    def test_mock_flag_replaces_any_runtime(self):
        factory = make_agent_factory(build_registry(), mock=True)
        agent = factory(_subject(runtime={"type": "claude-sdk", "permission_mode": "plan"}), 0)
        assert type(agent.runtime).__name__ == "SimulatedRuntime"

    def test_ix_mock_shadows_matrix_mock(self):
        factory = make_agent_factory(build_registry())
        assert type(factory(_subject(runtime={"type": "mock"}), 0).runtime).__name__ == (
            "SimulatedRuntime"
        )

    def test_claude_sdk_runtime_gets_the_experiment_dir_as_cwd(self, tmp_path: Path):
        factory = make_agent_factory(build_registry(), experiment_cwd=str(tmp_path))
        agent = factory(_subject(runtime={"type": "claude-sdk"}), 0)
        assert agent.runtime.config.cwd == str(tmp_path)

    def test_missing_runtime_type_names_the_legal_set(self):
        factory = make_agent_factory(build_registry())
        with pytest.raises(
            ConfigError, match="no runtime.type. Registered: claude-sdk, mock, model"
        ):
            factory(_subject(), 0)

    def test_unknown_runtime_type(self):
        factory = make_agent_factory(build_registry())
        with pytest.raises(ConfigError, match="runtime.type 'strands' is not registered"):
            factory(_subject(runtime={"type": "strands"}), 0)

    def test_runtime_option_typo_is_a_config_error(self):
        factory = make_agent_factory(build_registry())
        with pytest.raises(ConfigError, match="permision_mode"):
            factory(_subject(runtime={"type": "claude-sdk", "permision_mode": "plan"}), 0)

    async def test_seeded_repeats_draw_differently_and_reproducibly(self):
        """Regression: seeds depended on the trial only, so every repeat of a seeded run
        replayed identical draws and the noise floor across repeats was 0 by construction."""
        exp = ExperimentConfig(
            name="e", probes=(Probe(id="p", prompt="q", metadata={"expectation": "must_trigger"}),)
        )
        factory = make_agent_factory(build_registry(seed=42, experiment=exp), mock=True)
        subject = _subject(runtime={"type": "mock"})

        async def draws(run_index: int) -> list[bool]:
            return [
                bool((await factory(subject, t, run_index).run("q")).tool_calls) for t in range(40)
            ]

        first, second = await draws(0), await draws(1)
        assert first != second
        assert first == await draws(0)


class TestEngines:
    def test_default_engine_is_native(self):
        assert create_engine(ExperimentConfig(name="e"), build_registry()).name == "native"

    def test_string_engine_normalised(self):
        assert ExperimentConfig(name="e", engine="inspect").engine == {"type": "inspect"}

    def test_inspect_log_dir_defaults_under_results(self, tmp_path: Path):
        engine = create_engine(
            ExperimentConfig(name="e", engine="inspect"), build_registry(), results_dir=tmp_path
        )
        assert engine.config.log_dir == str(tmp_path / "inspect")

    def test_override_drops_the_other_engines_options(self):
        exp = ExperimentConfig(name="e", engine={"type": "inspect", "max_samples": 4})
        assert create_engine(exp, build_registry(), override="native").name == "native"

    def test_unknown_engine_lists_valid(self):
        with pytest.raises(
            ConfigError, match="Unknown engine: 'ray'. Valid engines: inspect, native"
        ):
            create_engine(ExperimentConfig(name="e", engine="ray"), build_registry())

    def test_engine_option_typo(self):
        with pytest.raises(ConfigError, match="max_sample"):
            create_engine(
                ExperimentConfig(name="e", engine={"type": "inspect", "max_sample": 2}),
                build_registry(),
            )


class TestRegistry:
    def test_runtimes_listed_across_namespaces(self):
        assert registered_runtimes(build_registry()) == ["claude-sdk", "mock", "model"]

    def test_sensor_type_urls_are_versioned(self):
        assert "ix.v1/sensor.activation" in build_registry()

    def test_extension_registers_a_sensor(self, monkeypatch: pytest.MonkeyPatch):
        from ix.eval.sensors import ActivationSensor

        class _EP:
            name, value = "mine", "pkg:register"

            @staticmethod
            def load():
                return lambda registry: registry.register(
                    "ix.v1/sensor.mine", lambda **kw: ActivationSensor(expected_skill="x")
                )

        monkeypatch.setattr(
            "matrix.domain.registry.entry_points",
            lambda group: [_EP()] if group == "ix.components" else [],
        )
        assert "ix.v1/sensor.mine" in build_registry()


class TestExperimentFiles:
    def test_unknown_experiment_key_names_the_file(self, tmp_path: Path):
        from ix.adapters._out.filesystem_store import FilesystemStore

        exp = tmp_path / "e"
        exp.mkdir()
        (exp / "experiment.yaml").write_text("name: e\nagent: {model: sonnet}\n")
        with pytest.raises(ConfigError, match=r"experiment.yaml: unknown key\(s\) \['agent'\]"):
            FilesystemStore(tmp_path).load_experiment(exp)


    def test_empty_subjects_dir_does_not_discard_yaml_subjects(self, tmp_path: Path):
        """Regression: an empty subjects/ took precedence and silently dropped YAML subjects."""
        from ix.adapters._out.filesystem_store import FilesystemStore

        exp = tmp_path / "e"
        (exp / "subjects").mkdir(parents=True)
        (exp / "experiment.yaml").write_text(
            "name: e\nsubjects:\n  - name: s\n    config: {runtime: {type: mock}}\n"
        )
        loaded = FilesystemStore(tmp_path).load_experiment(exp)
        assert [s.name for s in loaded.subjects] == ["s"]


class TestInit:
    def test_new_experiment_runs_on_the_starter_subject(self, tmp_path: Path, monkeypatch):
        from click.testing import CliRunner

        from ix.adapters._in.cli import main

        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        assert runner.invoke(main, ["lab", "init", "lab"]).exit_code == 0
        assert runner.invoke(main, ["experiment", "init", "e", "--lab", "lab"]).exit_code == 0
        (tmp_path / "lab" / "e" / "tasks" / "p.md").write_text(
            "---\nid: p\nexpectation: must_trigger\nexpected_skill: x\n---\nhello\n"
        )
        result = runner.invoke(main, ["run", "e", "--lab", "lab", "--trials", "1"])
        assert result.exit_code == 0, result.output
        assert "subject=agent" in result.output
