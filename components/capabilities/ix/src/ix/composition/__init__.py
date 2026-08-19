"""Composition root — unified registry for all component types.

One registry, one pattern: type_url → factory → Component.
Sensors, agents, probes, trials — all resolve the same way.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from matrix import ComponentRegistry
from matrix import Orchestrator as _Orchestrator

from ix.adapters._out.components import ProbeNode, SensorNode, SubjectNode, TrialNode
from ix.adapters._out.filesystem_store import FilesystemStore
from ix.adapters._out.mock_runtime import MockAgent
from ix.config.settings import find_lab
from ix.eval.experiment import Experiment
from ix.eval.models import ACCEPTABLE, MUST_TRIGGER
from ix.eval.sensors import (
    ActivationSensor,
    ActivationSensorConfig,
    FunctionTestSensor,
    FunctionTestSensorConfig,
    OutcomeSensor,
    OutcomeSensorConfig,
    ToolUsageSensor,
    ToolUsageSensorConfig,
)
from ix.eval.sensors_deepeval import DeepEvalSensor, DeepEvalSensorConfig

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from matrix import Agent
    from pydantic import BaseModel

    from ix.domain.ports import Sensor, SensorClass
    from ix.domain.ports import Sensor as _Sensor
    from ix.domain.types import Probe, Reading, Subject
    from ix.eval.models import ExperimentConfig

    # What `make_run_trial` hands the service layer. Named because the service layer
    # depends on this signature and nothing else about the DAG.
    RunTrial = Callable[
        [Probe, Subject, _Sensor, ComponentRegistry, int], Awaitable[list[Reading]]
    ]


# --- Sensor Types ---

_SENSOR_TYPES: dict[str, tuple[SensorClass, type[BaseModel]]] = {
    "ix.sensor.activation": (ActivationSensor, ActivationSensorConfig),
    "ix.sensor.function-test": (FunctionTestSensor, FunctionTestSensorConfig),
    "ix.sensor.deepeval": (DeepEvalSensor, DeepEvalSensorConfig),
    "ix.sensor.tool-usage": (ToolUsageSensor, ToolUsageSensorConfig),
    "ix.sensor.outcome": (OutcomeSensor, OutcomeSensorConfig),
}


def _make_sensor_factory(
    sensor_cls: SensorClass, config_cls: type[BaseModel]
) -> Callable[..., Sensor]:
    """Registry-compatible factory that validates config through Pydantic.

    Passes registry to from_config — each sensor resolves its own
    dependencies (e.g. DeepEval creates its judge agent from the registry).
    """

    def factory(
        *, probes: tuple[Probe, ...] = (), registry: Any = None, **raw_config: Any
    ) -> Sensor:
        config = config_cls.model_validate(raw_config)
        return sensor_cls.from_config(config, probes, registry=registry)

    return factory


# --- Agent Factories ---


def _anthropic_factory(*, system_prompt: str | None = None, **kw: Any) -> Agent:
    """Factory for AnthropicAgent — single-turn API calls."""
    from matrix.adapters._out.runtime.anthropic_agent import AnthropicAgent

    kw.pop("type", None)
    kw.pop("trial_index", None)
    return AnthropicAgent(system_prompt=system_prompt, **kw)


def _claude_factory(*, system_prompt: str | None = None, **kw: Any) -> Agent:
    """Factory for ClaudeAgent — Agent SDK, multi-turn."""
    from matrix.adapters._out.runtime.claude import ClaudeAgent

    kw.pop("type", None)
    kw.pop("trial_index", None)
    return ClaudeAgent(system_prompt=system_prompt, **kw)


def _make_mock_factory(
    expected_skill: str = "build-eval",
    base_seed: int | None = None,
    expectations: dict[str, bool] | None = None,
    skill_map: dict[str, str] | None = None,
    responses: dict[str, str] | None = None,
) -> Callable[..., Agent]:
    """Build a mock factory with captured test config.

    Derives per-trial seed from (base_seed, trial_index) so each trial
    is deterministic but independent. skill_map maps prompt -> skill name
    for multi-skill experiments.
    """

    def factory(*, trial_index: int = 0, **kw: Any) -> Agent:
        effective_seed = (base_seed * 1000 + trial_index) if base_seed is not None else None
        return MockAgent(
            expected_skill=expected_skill,
            seed=effective_seed,
            expectations=expectations or {},
            skill_map=skill_map or {},
            responses=responses or {},
        )

    return factory


# --- Unified Registry ---


def build_registry(
    *,
    mock: bool = False,
    skill: str = "build-eval",
    seed: int | None = None,
    experiment: ExperimentConfig | None = None,
) -> ComponentRegistry:
    """Build the unified ComponentRegistry — sensors + agent runtimes.

    All component types resolve through this single registry.
    Mock config is captured in the mock factory closure.
    """
    registry = ComponentRegistry()

    # Sensors
    for type_url, (cls, config_cls) in _SENSOR_TYPES.items():
        registry.register(type_url, _make_sensor_factory(cls, config_cls))

    # Agent runtimes
    registry.register("matrix.agent.anthropic", _anthropic_factory)
    registry.register("matrix.agent.claude", _claude_factory)

    expectations = _build_expectations(experiment) if experiment and mock else {}
    skill_map = _build_skill_map(experiment) if experiment and mock else {}
    responses = _build_mock_responses(experiment) if experiment and mock else {}
    registry.register(
        "matrix.agent.mock",
        _make_mock_factory(
            expected_skill=skill,
            base_seed=seed,
            expectations=expectations,
            skill_map=skill_map,
            responses=responses,
        ),
    )

    return registry


# --- Sensor Wiring ---


def _build_one_sensor(
    sensor_config: dict[str, Any],
    probes: tuple[Probe, ...],
    registry: ComponentRegistry,
    experiment_cwd: str | None = None,
) -> Sensor:
    """Build a single sensor from its config dict via registry."""
    sensor_config = dict(sensor_config)
    sensor_type = sensor_config.get("type", "activation")
    type_url = f"ix.sensor.{sensor_type}"

    # Resolve graders_module path relative to experiment directory
    if "graders_module" in sensor_config and experiment_cwd:
        graders_path = Path(experiment_cwd) / sensor_config["graders_module"]
        sensor_config["graders_module"] = str(graders_path.resolve())

    if type_url not in registry:
        valid = sorted(
            t.removeprefix("ix.sensor.") for t in registry.types() if t.startswith("ix.sensor.")
        )
        raise ValueError(f"Unknown sensor type: {sensor_type!r}. Valid types: {', '.join(valid)}")

    sensor: Sensor = registry.create(
        type_url,
        {**sensor_config, "probes": probes, "registry": registry},
    )
    return sensor


def create_sensor(
    experiment: ExperimentConfig,
    registry: ComponentRegistry,
    experiment_cwd: str | None = None,
) -> Sensor:
    """Build sensor(s) from experiment config via registry.

    Multiple sensors are wrapped in CompositeSensor.
    """
    from ix.eval.sensors import CompositeSensor

    sensors = [
        _build_one_sensor(sc, experiment.probes, registry, experiment_cwd)
        for sc in experiment.sensors
    ]

    if len(sensors) == 1:
        return sensors[0]
    return CompositeSensor(sensors)


# --- Trial Runner ---


def make_run_trial(experiment_cwd: str | None = None) -> RunTrial:
    """Build the default trial runner using concrete DAG Components.

    This is the composition root's job — wiring concrete implementations.
    The service layer only knows the callable signature, not the node classes.
    """

    async def _run(
        probe: Probe,
        subject: Subject,
        sensor: _Sensor,
        registry: ComponentRegistry,
        trial_index: int,
    ) -> list[Reading]:
        orchestrator = _Orchestrator(
            [
                ProbeNode(probe),
                SubjectNode(subject),
                TrialNode(
                    registry=registry, trial_index=trial_index, experiment_cwd=experiment_cwd
                ),
                SensorNode(sensor=sensor),
            ]
        )
        construct = await orchestrator.run()
        readings: list[Reading] = construct["sensor.reading"]
        return readings

    return _run


# --- Service Wiring ---


def create_service(
    mock: bool = False,
    skill: str = "build-eval",
    lab: Path | None = None,
    seed: int | None = None,
    experiment: ExperimentConfig | None = None,
    experiment_cwd: str | None = None,
) -> Experiment:
    """Wire up the experiment service with unified registry.

    Everything resolves through one ComponentRegistry.
    --mock overrides subject runtime to use MockAgent.
    """
    registry = build_registry(
        mock=mock,
        skill=skill,
        seed=seed,
        experiment=experiment,
    )
    sensor = (
        create_sensor(experiment, registry, experiment_cwd)
        if experiment
        else ActivationSensor(expected_skill=skill)
    )
    store = FilesystemStore(lab or find_lab())

    return Experiment(
        registry=registry,
        sensor=sensor,
        store=store,
        mock=mock,
        run_trial=make_run_trial(experiment_cwd=experiment_cwd),
    )


def create_store(lab: Path | None = None) -> FilesystemStore:
    """Direct store access for CLI commands that only need persistence."""
    return FilesystemStore(lab or find_lab())


def _build_expectations(experiment: ExperimentConfig) -> dict[str, bool]:
    """Map probe prompts to activation expectations for mock runtime."""
    return {
        probe.prompt: probe.metadata.get("expectation") == MUST_TRIGGER
        for probe in experiment.probes
        if probe.metadata.get("expectation") != ACCEPTABLE
    }


def _build_skill_map(experiment: ExperimentConfig) -> dict[str, str]:
    """Map probe prompts to expected skill names for multi-skill mock mode."""
    return {
        probe.prompt: probe.metadata["expected_skill"]
        for probe in experiment.probes
        if "expected_skill" in probe.metadata
    }


def _build_mock_responses(experiment: ExperimentConfig) -> dict[str, str]:
    """Map probe prompts to canned responses, for sensors that grade content."""
    return {
        probe.prompt: str(probe.metadata["mock_response"])
        for probe in experiment.probes
        if "mock_response" in probe.metadata
    }


__all__ = ["build_registry", "create_sensor", "create_service", "create_store", "make_run_trial"]
