"""ix's composition root: subjects → agents, engines, sensors, config tiers, probe truths."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from matrix import BoundAgent, MatrixConfig

from ix.composition import (
    IxConfig,
    build_registry,
    compose_matrix,
    create_engine,
    create_sensor,
    effective,
    load_ix_config,
    make_agent_factory,
    probe_problems,
    registered_runtimes,
    runtime_type,
    subject_definition,
    subject_spec,
)
from ix.domain.errors import ConfigError
from ix.domain.models import ExperimentConfig
from ix.domain.types import Probe, Subject

if TYPE_CHECKING:
    from pathlib import Path

MODELS = {
    "default": "q",
    "models": {"q": {"backend": "mock", "model": "qwen-8b", "family": "qwen"}},
}


def _subject(**config: object) -> Subject:
    return Subject(name="s", description="d", config=config)


def _container(
    matrix: dict[str, Any] | None = None, *, cwd: str | None = None, models: Any = None
) -> Any:
    experiment = ExperimentConfig(name="e", models=models)
    return compose_matrix(
        experiment, MatrixConfig.model_validate(matrix or {}), cwd=cwd, registry=build_registry()
    )


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

    def test_an_inline_subject_gets_one_turn_by_default(self):
        assert subject_definition(_subject(system_prompt="x")).max_turns == 1

    def test_deployment_key_at_definition_level_is_rejected_with_guidance(self):
        with pytest.raises(ConfigError, match="subject 's': permission_mode.*runtime:"):
            subject_spec(_subject(permission_mode="plan"))

    def test_definition_name_is_sanitised(self):
        assert subject_definition(Subject(name="catalog live!")).name == "catalog-live"

    def test_naming_an_agent_and_a_runtime_is_refused(self):
        with pytest.raises(ConfigError, match="brings its own runtime"):
            subject_spec(_subject(agent="reviewer", runtime={"type": "mock"}))


class TestConfiguredAgents:
    """A subject may name an agent defined in the shared matrix config."""

    MATRIX: dict[str, Any] = {
        "runtimes": {"m": {"type": "mock", "default": "configured"}},
        "agents": {"reviewer": {"runtime": "m", "system_prompt": "Review.", "max_turns": 4}},
    }

    async def test_a_subject_runs_a_configured_agent(self):
        factory = make_agent_factory(_container(self.MATRIX))
        agent = factory(_subject(agent="reviewer"), 0)
        assert (agent.definition.system_prompt, agent.definition.max_turns) == ("Review.", 4)
        assert (await agent.run("x")).content == "configured"

    def test_subject_fields_override_the_agents(self):
        factory = make_agent_factory(_container(self.MATRIX))
        agent = factory(_subject(agent="reviewer", max_turns=1, model="q"), 0)
        assert (agent.definition.max_turns, agent.definition.model) == (1, "q")
        assert agent.definition.system_prompt == "Review."

    def test_an_unknown_agent_names_the_configured_ones(self):
        factory = make_agent_factory(_container(self.MATRIX))
        with pytest.raises(ConfigError, match="Configured: reviewer"):
            factory(_subject(agent="ghost"), 0)

    def test_the_plan_reads_the_agents_runtime_type(self):
        matrix = MatrixConfig.model_validate(self.MATRIX)
        assert runtime_type(_subject(agent="reviewer"), matrix=matrix) == "mock"


class TestAgentFactory:
    async def test_model_runtime_subject_answers_with_its_family(self):
        factory = make_agent_factory(_container(models=MODELS))
        agent = factory(_subject(system_prompt="x", runtime={"type": "model"}), 0)
        assert isinstance(agent, BoundAgent)
        response = await agent.run("hi")
        assert (response.family, response.content) == ("qwen", "mock:qwen-8b:hi")

    def test_matrix_runtimes_are_built_once_per_subject(self):
        factory = make_agent_factory(_container(models=MODELS))
        subject = _subject(runtime={"type": "model"})
        assert factory(subject, 0).runtime is factory(subject, 4).runtime

    def test_simulate_flag_replaces_any_runtime(self):
        factory = make_agent_factory(_container(), simulate=True)
        agent = factory(_subject(runtime={"type": "claude-sdk", "permission_mode": "plan"}), 0)
        assert type(agent.runtime).__name__ == "SimulatedRuntime"

    def test_simulated_is_ix_and_mock_is_matrix_one_word_one_meaning(self):
        factory = make_agent_factory(_container())
        assert type(factory(_subject(runtime={"type": "simulated"}), 0).runtime).__name__ == (
            "SimulatedRuntime"
        )
        assert type(factory(_subject(runtime={"type": "mock"}), 0).runtime).__name__ == (
            "MockRuntime"
        )

    def test_claude_sdk_runtime_gets_the_experiment_dir_as_cwd(self, tmp_path: Path):
        factory = make_agent_factory(_container(cwd=str(tmp_path)))
        agent = factory(_subject(runtime={"type": "claude-sdk"}), 0)
        assert agent.runtime.config.cwd == str(tmp_path)

    def test_missing_runtime_type_names_the_legal_set(self):
        factory = make_agent_factory(_container())
        with pytest.raises(
            ConfigError, match="names no agent. Runtimes: claude-sdk, mock, model, simulated"
        ):
            factory(_subject(), 0)

    def test_unknown_runtime_type(self):
        factory = make_agent_factory(_container())
        with pytest.raises(ConfigError, match="runtime.type 'strands' is not registered"):
            factory(_subject(runtime={"type": "strands"}), 0)

    def test_runtime_option_typo_is_a_config_error(self):
        factory = make_agent_factory(_container())
        with pytest.raises(ConfigError, match="permision_mode"):
            factory(_subject(runtime={"type": "claude-sdk", "permision_mode": "plan"}), 0)

    async def test_seeded_repeats_draw_differently_and_reproducibly(self):
        """Regression: seeds depended on the trial only, so every repeat of a seeded run
        replayed identical draws and the noise floor across repeats was 0 by construction."""
        exp = ExperimentConfig(
            name="e", probes=(Probe(id="p", prompt="q", metadata={"expectation": "must_trigger"}),)
        )
        factory = make_agent_factory(_container(), exp, simulate=True, seed=42)
        subject = _subject(runtime={"type": "simulated"})

        async def draws(run_index: int) -> list[bool]:
            return [
                bool((await factory(subject, t, run_index).run("q")).tool_calls) for t in range(40)
            ]

        first, second = await draws(0), await draws(1)
        assert first != second
        assert first == await draws(0)


class TestEngines:
    def test_default_engine_is_native(self):
        assert create_engine(ExperimentConfig(name="e"), _container()).name == "native"

    def test_string_engine_normalised(self):
        assert ExperimentConfig(name="e", engine="inspect").engine == {"type": "inspect"}

    def test_inspect_log_dir_defaults_under_results(self, tmp_path: Path):
        pytest.importorskip("inspect_ai")
        engine = create_engine(
            ExperimentConfig(name="e", engine="inspect"), _container(), results_dir=tmp_path
        )
        assert engine.config.log_dir == str(tmp_path / "inspect")

    def test_override_drops_the_other_engines_options(self):
        exp = ExperimentConfig(name="e", engine={"type": "inspect", "max_samples": 4})
        assert create_engine(exp, _container(), override="native").name == "native"

    def test_unknown_engine_lists_the_registered_ones(self):
        with pytest.raises(ConfigError, match="unknown engine 'ray'. Engines: inspect, native"):
            create_engine(ExperimentConfig(name="e", engine="ray"), _container())

    def test_engine_option_typo(self):
        with pytest.raises(ConfigError, match="concurency"):
            create_engine(
                ExperimentConfig(name="e", engine={"type": "native", "concurency": 2}),
                _container(),
            )


class TestRegistry:
    def test_runtimes_listed_across_namespaces(self):
        assert registered_runtimes(build_registry()) == ["claude-sdk", "mock", "model", "simulated"]

    def test_sensors_and_engines_are_points_on_matrixs_registry(self):
        registry = build_registry()
        assert ("sensor", "ix.v1.sensor.activation") in registry
        assert ("engine", "ix.v1.engine.native") in registry
        assert registry.entry("sensor", "ix.v1.sensor.activation").origin == "ix"

    def test_an_extension_sensor_is_used_by_its_type_url(self, monkeypatch: pytest.MonkeyPatch):
        from ix.composition.builtins import register as ix_register
        from ix.eval.sensors import ActivationSensor

        class _Dist:
            name = "acme"

        def acme(registry: Any) -> None:
            registry.register(
                "sensor",
                "acme.v1.sensor.mine",
                lambda *, probes, judge: ActivationSensor(expected_skill="x"),
                needs={"probes", "judge"},
            )

        def ep(name: str, register: Any) -> Any:
            class _EP:
                value = f"{name}:register"
                dist = _Dist()

                def load(self) -> Any:
                    return register

            item = _EP()
            item.name = name  # type: ignore[attr-defined]
            return item

        # 'acme' sorts before 'ix': its sensor point does not exist yet when it loads.
        monkeypatch.setattr(
            "matrix.domain.registry.entry_points",
            lambda group: [ep("acme", acme), ep("ix", ix_register)],
        )
        experiment = ExperimentConfig(name="e", sensors=({"type": "acme.v1.sensor.mine"},))
        registry = build_registry()
        container = compose_matrix(experiment, MatrixConfig(), cwd=None, registry=registry)
        assert create_sensor(experiment, container).name == "activation"


class TestProbeTruths:
    """A probe key no configured sensor reads is a typo, refused at load."""

    def _experiment(self, **metadata: Any) -> ExperimentConfig:
        return ExperimentConfig(name="e", probes=(Probe(id="p", prompt="q", metadata=metadata),))

    def test_a_misspelt_key_is_refused_naming_what_is_read(self):
        (problem,) = probe_problems(self._experiment(expectaton="must_trigger"), build_registry())
        assert "probe 'p': key 'expectaton'" in problem and "expectation" in problem

    def test_keys_the_sensors_read_pass(self):
        exp = self._experiment(expectation="must_trigger", expected_skill="x", mock_response="y")
        assert probe_problems(exp, build_registry()) == []

    def test_x_prefixed_keys_are_notes(self):
        assert probe_problems(self._experiment(**{"x-why": "context"}), build_registry()) == []

    def test_a_key_another_sensor_reads_needs_that_sensor(self):
        exp = self._experiment(test_cases=[])  # function-test's key, but only activation runs
        assert probe_problems(exp, build_registry())


class TestIxConfig:
    def test_ix_section_fills_what_the_experiment_does_not_say(self, tmp_path: Path):
        ix_file = tmp_path / "ix.yaml"
        ix_file.write_text("ix:\n  trials: 9\n  repeats: 3\n  engine: inspect\n  lab: labs\n")
        config = load_ix_config([ix_file])
        assert config.client == IxConfig(trials=9, repeats=3, engine="inspect", lab="labs")
        silent = ExperimentConfig.model_validate({"name": "e"})
        filled = effective(silent, config.client)
        assert (filled.trials, filled.repeats, filled.engine) == (9, 3, {"type": "inspect"})
        explicit = ExperimentConfig.model_validate({"name": "e", "trials": 2})
        assert effective(explicit, config.client).trials == 2  # the experiment file wins

    def test_a_bad_ix_section_names_the_key_path(self, tmp_path: Path):
        ix_file = tmp_path / "ix.yaml"
        ix_file.write_text("ix:\n  trails: 9\n")
        with pytest.raises(ConfigError, match="ix.trails"):
            load_ix_config([ix_file])


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
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        runner = CliRunner()
        assert runner.invoke(main, ["lab", "init", "lab"]).exit_code == 0
        assert runner.invoke(main, ["experiment", "init", "e", "--lab", "lab"]).exit_code == 0
        (tmp_path / "lab" / "e" / "tasks" / "p.md").write_text(
            "---\nid: p\nexpectation: must_trigger\nexpected_skill: x\n---\nhello\n"
        )
        result = runner.invoke(main, ["run", "e", "--lab", "lab", "--trials", "1"])
        assert result.exit_code == 0, result.output
        assert "subject agent (simulated)" in result.output
