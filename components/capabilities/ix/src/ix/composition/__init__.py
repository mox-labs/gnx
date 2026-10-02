"""Composition root — the only module that names ix's concrete adapters.

One registry, keyed by type URL, holds everything an experiment is composed from:

* matrix's agent runtimes      ``matrix.v1/runtime.{claude-sdk,model,mock}``
* ix's simulated runtime        ``ix.v1/runtime.mock`` (shadows matrix's for ix subjects)
* ix's sensors                  ``ix.v1/sensor.<type>``
* ix's engines                  ``ix.v1/engine.{native,inspect}``
* extensions                    every ``matrix.components`` and ``ix.components`` entry point

A subject becomes an agent by composition: its config splits into an **AgentDefinition**
(``system_prompt``, ``model``, ``tools``, ``max_turns``) and a **runtime** (``runtime.type`` +
that runtime's deployment options), bound into a matrix ``BoundAgent``. The same subject can
move between the Claude SDK, a local model and the simulator by changing ``runtime.type``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from matrix import AgentDefinition, BoundAgent, ComponentRegistry, default_registry
from matrix import ConfigError as MatrixConfigError
from matrix import runtime_type_url as matrix_runtime_url
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ix.adapters._out.engines.inspect_engine import InspectEngine, InspectEngineConfig
from ix.adapters._out.engines.native import NativeEngine
from ix.adapters._out.filesystem_store import FilesystemStore
from ix.adapters._out.mock_runtime import SimulatedRuntime
from ix.config.settings import find_lab
from ix.domain import type_urls
from ix.domain.errors import ConfigError
from ix.eval.experiment import Experiment
from ix.eval.models import ACCEPTABLE, MUST_TRIGGER, ExperimentConfig
from ix.eval.sensors import (
    ActivationSensor,
    ActivationSensorConfig,
    CompositeSensor,
    FunctionTestSensor,
    FunctionTestSensorConfig,
    OutcomeSensor,
    OutcomeSensorConfig,
    ToolUsageSensor,
    ToolUsageSensorConfig,
)
from ix.eval.sensors_deepeval import DeepEvalSensor, DeepEvalSensorConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from hardline import ModelRuntime
    from matrix import Agent, AgentRuntime

    from ix.domain.ports import AgentFactory, Engine, Sensor, SensorClass
    from ix.domain.types import Probe, Subject

EXTENSION_GROUP = "ix.components"

_SENSOR_TYPES: dict[str, tuple[SensorClass, type[BaseModel]]] = {
    "activation": (ActivationSensor, ActivationSensorConfig),
    "function-test": (FunctionTestSensor, FunctionTestSensorConfig),
    "deepeval": (DeepEvalSensor, DeepEvalSensorConfig),
    "tool-usage": (ToolUsageSensor, ToolUsageSensorConfig),
    "outcome": (OutcomeSensor, OutcomeSensorConfig),
}


# --- Subjects ------------------------------------------------------------------------


class SubjectSpec(BaseModel):
    """A subject's config: its agent definition fields plus the runtime that plays it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    system_prompt: str = ""
    model: str | None = None
    tools: tuple[str, ...] | None = None
    max_turns: int = Field(default=1, ge=1)
    runtime: dict[str, Any] = {}

    @field_validator("tools", mode="before")
    @classmethod
    def _comma_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(t.strip() for t in value.split(",") if t.strip())
        return value


def subject_spec(subject: Subject) -> SubjectSpec:
    try:
        return SubjectSpec.model_validate(subject.config)
    except ValidationError as e:
        issues = "; ".join(f"{'.'.join(map(str, i['loc']))}: {i['msg']}" for i in e.errors())
        raise ConfigError(
            f"subject {subject.name!r}: {issues}. Definition keys: system_prompt, model, tools, "
            "max_turns; deployment goes under runtime: {type: ..., <options>}"
        ) from None


def subject_definition(subject: Subject, spec: SubjectSpec | None = None) -> AgentDefinition:
    s = spec or subject_spec(subject)
    return AgentDefinition(
        name=_definition_name(subject.name),
        description=subject.description,
        system_prompt=s.system_prompt,
        model=s.model,
        tools=s.tools,
        max_turns=s.max_turns,
    )


def _definition_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._-" else "-" for c in name).strip("-._")
    return cleaned or "subject"


def make_agent_factory(
    registry: ComponentRegistry,
    *,
    mock: bool = False,
    experiment_cwd: str | None = None,
    model_section: Callable[[], dict[str, Any] | None] = lambda: None,
) -> AgentFactory:
    """Subject + trial index → a BoundAgent.

    ``mock`` replaces every subject's runtime with ix's simulator (the ``--mock`` flag).
    Matrix runtimes are built once per subject and reused across trials; ix's simulated
    runtime is built per trial so each trial is seeded independently.
    """
    cache: dict[str, AgentRuntime] = {}

    def build(subject: Subject, trial_index: int, run_index: int = 0) -> Agent:
        spec = subject_spec(subject)
        definition = subject_definition(subject, spec)
        options = dict(spec.runtime)
        runtime_type = "mock" if mock else options.pop("type", None)
        if mock:
            options = {}
        if not runtime_type:
            raise ConfigError(
                f"subject {subject.name!r} has no runtime.type. Registered: "
                f"{', '.join(registered_runtimes(registry))}. For a dry run use --mock."
            )
        return BoundAgent(
            definition, _runtime(subject, str(runtime_type), options, trial_index, run_index)
        )

    def _runtime(
        subject: Subject,
        runtime_type: str,
        options: dict[str, Any],
        trial_index: int,
        run_index: int,
    ) -> AgentRuntime:
        ix_url = type_urls.runtime(runtime_type)
        if ix_url in registry:
            runtime: AgentRuntime = registry.create(
                ix_url,
                {**options, "trial_index": trial_index, "run_index": run_index},
                source=f"subject {subject.name}",
            )
            return runtime
        url = matrix_runtime_url(runtime_type)
        if url not in registry:
            raise ConfigError(
                f"subject {subject.name!r}: runtime.type {runtime_type!r} is not registered. "
                f"Registered: {', '.join(registered_runtimes(registry))}"
            )
        cached = cache.get(subject.name)
        if cached is not None:
            return cached
        if runtime_type == "claude-sdk" and experiment_cwd and "cwd" not in options:
            options["cwd"] = experiment_cwd
        if runtime_type == "model" and "models" not in options:
            section = model_section()
            if section is not None:
                options["models"] = section
        try:
            built: AgentRuntime = registry.create(
                url, options, source=f"subject {subject.name}: runtime"
            )
        except MatrixConfigError as e:
            raise ConfigError(str(e)) from None
        cache[subject.name] = built
        return built

    return build


def registered_runtimes(registry: ComponentRegistry) -> list[str]:
    names = set()
    for url in registry.types():
        for prefix in ("matrix.v1/runtime.", f"{type_urls.NAMESPACE}/runtime."):
            if url.startswith(prefix):
                names.add(url.removeprefix(prefix))
    return sorted(names)


# --- Registry --------------------------------------------------------------------------


def _sensor_factory(sensor_cls: SensorClass, config_cls: type[BaseModel]) -> Callable[..., Sensor]:
    def factory(
        *,
        probes: tuple[Probe, ...] = (),
        models: Callable[[], ModelRuntime] | None = None,
        **raw: Any,
    ) -> Sensor:
        try:
            config = config_cls.model_validate(raw)
        except ValidationError as e:
            issues = "; ".join(f"{'.'.join(map(str, i['loc']))}: {i['msg']}" for i in e.errors())
            raise ConfigError(f"sensor {raw.get('type', '?')!r}: {issues}") from None
        return sensor_cls.from_config(config, probes, models=models)

    return factory


def _simulated_factory(
    expected_skill: str,
    base_seed: int | None,
    expectations: dict[str, bool],
    skill_map: dict[str, str],
    responses: dict[str, str],
) -> Callable[..., SimulatedRuntime]:
    def factory(*, trial_index: int = 0, run_index: int = 0) -> SimulatedRuntime:
        # Distinct per (repeat, trial): repeats of a seeded run must differ, or the noise
        # floor across them is 0 by construction rather than by measurement.
        seed = (
            base_seed * 1_000_000 + run_index * 1000 + trial_index
            if base_seed is not None
            else None
        )
        return SimulatedRuntime(
            expected_skill=expected_skill,
            seed=seed,
            expectations=expectations,
            skill_map=skill_map,
            responses=responses,
        )

    return factory


class _NoOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _inspect_engine(config: InspectEngineConfig) -> Engine:
    return InspectEngine(config)


def build_registry(
    *,
    skill: str = "build-eval",
    seed: int | None = None,
    experiment: ExperimentConfig | None = None,
    discover: bool = True,
) -> ComponentRegistry:
    """matrix runtimes + ix sensors, engines, simulated runtime + discovered extensions."""
    registry = default_registry(discover=discover)

    for kind, (cls, config_cls) in _SENSOR_TYPES.items():
        registry.register(type_urls.sensor(kind), _sensor_factory(cls, config_cls))

    registry.register_typed(type_urls.engine("native"), _NoOptions, lambda _: NativeEngine())
    registry.register_typed(type_urls.engine("inspect"), InspectEngineConfig, _inspect_engine)

    registry.register(
        type_urls.runtime("mock"),
        _simulated_factory(
            expected_skill=skill,
            base_seed=seed,
            expectations=_build_expectations(experiment) if experiment else {},
            skill_map=_build_skill_map(experiment) if experiment else {},
            responses=_build_mock_responses(experiment) if experiment else {},
        ),
    )
    if discover:
        registry.discover(EXTENSION_GROUP)
    return registry


# --- Sensors and engines ---------------------------------------------------------------


def create_sensor(
    experiment: ExperimentConfig,
    registry: ComponentRegistry,
    experiment_cwd: str | None = None,
    models: Callable[[], ModelRuntime] | None = None,
) -> Sensor:
    """Build the experiment's sensor(s). Several are wrapped in a CompositeSensor."""
    sensors = [
        _build_one_sensor(sc, experiment.probes, registry, experiment_cwd, models)
        for sc in experiment.sensors
    ]
    return sensors[0] if len(sensors) == 1 else CompositeSensor(sensors)


def _build_one_sensor(
    sensor_config: dict[str, Any],
    probes: tuple[Probe, ...],
    registry: ComponentRegistry,
    experiment_cwd: str | None,
    models: Callable[[], ModelRuntime] | None,
) -> Sensor:
    config = dict(sensor_config)
    kind = config.get("type", "activation")
    url = type_urls.sensor(kind)
    if url not in registry:
        valid = sorted(
            type_urls.short(t, "sensor")
            for t in registry.types()
            if t.startswith(f"{type_urls.NAMESPACE}/sensor.")
        )
        raise ConfigError(f"Unknown sensor type: {kind!r}. Valid types: {', '.join(valid)}")
    if "graders_module" in config and experiment_cwd:
        config["graders_module"] = str((Path(experiment_cwd) / config["graders_module"]).resolve())
    sensor: Sensor = registry.create(url, {**config, "probes": probes, "models": models})
    return sensor


def create_engine(
    experiment: ExperimentConfig,
    registry: ComponentRegistry,
    *,
    override: str | None = None,
    results_dir: Path | None = None,
) -> Engine:
    options = dict(experiment.engine)
    kind = override or options.pop("type", "native")
    if override:
        options.pop("type", None)
        if override != experiment.engine.get("type"):
            options = {}  # another engine's options do not transfer
    url = type_urls.engine(kind)
    if url not in registry:
        valid = sorted(
            type_urls.short(t, "engine")
            for t in registry.types()
            if t.startswith(f"{type_urls.NAMESPACE}/engine.")
        )
        raise ConfigError(f"Unknown engine: {kind!r}. Valid engines: {', '.join(valid)}")
    if kind == "inspect" and "log_dir" not in options and results_dir is not None:
        options["log_dir"] = str(results_dir / "inspect")
    try:
        engine: Engine = registry.create(url, options, source=f"{experiment.name}: engine")
    except MatrixConfigError as e:
        raise ConfigError(str(e)) from None
    return engine


def _models_provider(experiment: ExperimentConfig | None) -> Callable[[], ModelRuntime]:
    built: list[ModelRuntime] = []

    def provide() -> ModelRuntime:
        if not built:
            from hardline import build_runtime

            section = experiment.models if experiment else None
            built.append(
                build_runtime(config=section, label=f"{experiment.name}: models")
                if section is not None and experiment is not None
                else build_runtime()
            )
        return built[0]

    return provide


# --- Service ---------------------------------------------------------------------------


def create_service(
    mock: bool = False,
    skill: str = "build-eval",
    lab: Path | None = None,
    seed: int | None = None,
    experiment: ExperimentConfig | None = None,
    experiment_cwd: str | None = None,
    engine: str | None = None,
) -> Experiment:
    """Wire an Experiment from config: registry → sensor, engine, agent factory, store."""
    registry = build_registry(skill=skill, seed=seed, experiment=experiment)
    models = _models_provider(experiment)
    sensor = (
        create_sensor(experiment, registry, experiment_cwd, models)
        if experiment
        else ActivationSensor(expected_skill=skill)
    )
    workspace = lab or find_lab()
    results_dir = workspace / experiment.name / "results" if experiment else None
    chosen = create_engine(
        experiment or ExperimentConfig(name="default"),
        registry,
        override=engine,
        results_dir=results_dir,
    )
    agents = make_agent_factory(
        registry,
        mock=mock,
        experiment_cwd=experiment_cwd,
        model_section=lambda: experiment.models if experiment else None,
    )
    return Experiment(sensor=sensor, store=FilesystemStore(workspace), engine=chosen, agents=agents)


def create_store(lab: Path | None = None) -> FilesystemStore:
    """Direct store access for CLI commands that only need persistence."""
    return FilesystemStore(lab or find_lab())


def _build_expectations(experiment: ExperimentConfig) -> dict[str, bool]:
    """Map probe prompts to activation expectations for the simulated runtime."""
    return {
        probe.prompt: probe.metadata.get("expectation") == MUST_TRIGGER
        for probe in experiment.probes
        if probe.metadata.get("expectation") != ACCEPTABLE
    }


def _build_skill_map(experiment: ExperimentConfig) -> dict[str, str]:
    return {
        probe.prompt: probe.metadata["expected_skill"]
        for probe in experiment.probes
        if "expected_skill" in probe.metadata
    }


def _build_mock_responses(experiment: ExperimentConfig) -> dict[str, str]:
    return {
        probe.prompt: str(probe.metadata["mock_response"])
        for probe in experiment.probes
        if "mock_response" in probe.metadata
    }


__all__ = [
    "SubjectSpec",
    "build_registry",
    "create_engine",
    "create_sensor",
    "create_service",
    "create_store",
    "make_agent_factory",
    "registered_runtimes",
    "subject_definition",
    "subject_spec",
]
