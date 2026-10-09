"""Composition root: the only module that joins ix's domain to concrete adapters.

**Configuration** follows matrix's convention, so ix shares runtimes, models and agents with
every other tool. From lowest to highest priority:

1. ix's schema defaults (``trials: 5``, ``repeats: 1``, ``engine: native``);
2. the ``ix:`` section of ``~/.matrix/config.yaml``, ``~/.ix/config.yaml``, ``./matrix.yaml``,
   ``./ix.yaml``, ``$MATRIX_CONFIG``, ``$IX_CONFIG`` (see :class:`IxConfig`);
3. the experiment's own ``experiment.yaml``;
4. command-line flags.

The ``matrix:`` section of the same files supplies runtimes, models and agents. A subject
may name one of those agents (``agent: reviewer``) or define itself inline (prompt, model,
tools, ``runtime: {type: ...}``).

**Extensions.** Sensors and engines are entries at matrix registry points ``sensor`` and
``engine`` (see :mod:`ix.composition.builtins`); config names a built-in by its short name
and anyone else's by type URL. Nothing here lists the built-ins.

**Probe truths.** Each sensor declares the probe keys it reads. A probe key that no configured
sensor reads is refused when the experiment loads (a typo like ``expectaton`` must not pass
silently), unless it is one of ix's own keys or starts with ``x-``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from matrix import (
    AgentDefinition,
    BoundAgent,
    Config,
    MatrixConfig,
    MatrixError,
    compose,
    default_registry,
    load_config,
    runtime_type_url,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ix.adapters._out.filesystem_store import FilesystemStore
from ix.adapters._out.simulated_runtime import SimulatedRuntime
from ix.composition.builtins import register as register_builtins
from ix.config.settings import find_lab
from ix.domain import type_urls
from ix.domain.errors import ConfigError, IxError
from ix.eval.activation import expected_skill_of, should_activate
from ix.eval.experiment import Experiment
from ix.eval.sensors import CompositeSensor

if TYPE_CHECKING:
    from matrix import Agent, AgentRuntime, Container, Registry

    from ix.domain.models import ExperimentConfig
    from ix.domain.ports import AgentFactory, Engine, Sensor
    from ix.domain.types import Probe, Subject

SIMULATED = "simulated"
#: Runtimes that call no model and spend nothing: ix's simulator and matrix's mock. Every
#: other runtime is *live*: it may spend money and, on claude-sdk, run tools.
OFFLINE_RUNTIMES = frozenset({SIMULATED, "mock"})
#: The suffix a simulated run of a non-simulated subject is saved under.
SIMULATED_SUFFIX = "@simulated"
#: Probe keys ix itself reads, whatever the sensors: the simulator's canned reply, and notes.
RESERVED_PROBE_KEYS = frozenset({"mock_response", "tags", "notes", "title", "description"})
#: How many trials a subject's session may take by default: one turn, as a routing eval needs.
DEFAULT_SUBJECT_TURNS = 1


# --- Configuration ---------------------------------------------------------------------


class IxConfig(BaseModel):
    """The ``ix:`` section: your defaults for every experiment. Each field may be absent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A lab directory, relative to where ix runs, used when --lab is not given.
    lab: str | None = None
    trials: int | None = Field(default=None, ge=1)
    repeats: int | None = Field(default=None, ge=1)
    #: ``native`` or ``inspect`` (or an extension's type URL), or ``{type, <options>}``.
    engine: str | dict[str, Any] | None = None


def load_ix_config(sources: list[Path] | None = None) -> Config[IxConfig]:
    """ix's settings and the shared ``matrix:`` section, through matrix's config tiers."""
    try:
        return load_config(IxConfig, "ix", sources)
    except MatrixError as e:
        raise ConfigError(e.message) from None


def effective(experiment: ExperimentConfig, ix: IxConfig) -> ExperimentConfig:
    """The experiment with your ``ix:`` defaults filled in where its file is silent."""
    update: dict[str, Any] = {}
    explicit = experiment.model_fields_set
    if ix.trials is not None and "trials" not in explicit:
        update["trials"] = ix.trials
    if ix.repeats is not None and "repeats" not in explicit:
        update["repeats"] = ix.repeats
    if ix.engine is not None and "engine" not in explicit:
        update["engine"] = {"type": ix.engine} if isinstance(ix.engine, str) else dict(ix.engine)
    return experiment.model_copy(update=update) if update else experiment


# --- Registry --------------------------------------------------------------------------


def build_registry(*, discover: bool = True) -> Registry:
    """matrix's registry with ix's points and built-ins, plus every installed extension."""
    registry = default_registry(discover=discover)
    if "sensor" not in registry.points:  # ix not installed with metadata (a source tree)
        register_builtins(registry)
    return registry


def _entry_names(registry: Registry, point: str) -> list[str]:
    return sorted(type_urls.short(e.type_url) for e in registry.entries(point))


# --- Subjects --------------------------------------------------------------------------


class SubjectSpec(BaseModel):
    """A subject's config: a configured agent by name, or a definition plus its runtime.

    With ``agent``, the definition and runtime come from ``matrix.agents.<name>``, and any
    definition field given here overrides that agent's. Without it, the subject is defined
    inline and ``runtime: {type: ..., <options>}`` says what plays it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    tools: tuple[str, ...] | None = None
    max_turns: int | None = Field(default=None, ge=1)
    runtime: dict[str, Any] = {}

    @field_validator("tools", mode="before")
    @classmethod
    def _comma_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(t.strip() for t in value.split(",") if t.strip())
        return value


def subject_spec(subject: Subject) -> SubjectSpec:
    try:
        spec = SubjectSpec.model_validate(subject.config)
    except ValidationError as e:
        issues = "; ".join(f"{'.'.join(map(str, i['loc']))}: {i['msg']}" for i in e.errors())
        raise ConfigError(
            f"subject {subject.name!r}: {issues}. A subject names a configured agent "
            "(agent: <name>) or defines one inline: system_prompt, model, tools, max_turns, "
            "runtime: {type: ..., <options>}"
        ) from None
    if spec.agent and spec.runtime:
        raise ConfigError(
            f"subject {subject.name!r} names agent {spec.agent!r} and also a runtime; the "
            "agent brings its own runtime. Drop one."
        )
    return spec


def runtime_type(
    subject: Subject | None, *, simulate: bool = False, matrix: MatrixConfig | None = None
) -> str:
    """The runtime that will play ``subject``: ``simulated`` under ``--simulate``, the named
    agent's runtime type, its own ``runtime.type``, or ``unset``. Nothing is built."""
    if simulate:
        return SIMULATED
    if subject is None:
        return "unset"
    config = subject.config
    agent = config.get("agent")
    if agent and matrix is not None:
        spec = matrix.agents.get(agent)
        if spec is not None and spec.runtime in matrix.runtimes:
            return matrix.runtimes[spec.runtime].type
    runtime = config.get("runtime") or {}
    return str(runtime.get("type") or "unset") if isinstance(runtime, dict) else "unset"


def is_live(runtime: str) -> bool:
    """Whether a runtime may call a real model. ``unset`` counts as live: assume the cost."""
    return runtime not in OFFLINE_RUNTIMES


def results_name(subject: Subject | None, *, simulate: bool) -> str:
    """The name a run's results are saved under.

    A simulated run of a subject whose own runtime is not the simulator is saved as
    ``<name>@simulated``, so a harness check never replaces that subject's measured results.
    """
    name = subject.name if subject else "default"
    if simulate and runtime_type(subject) != SIMULATED and not name.endswith(SIMULATED_SUFFIX):
        return name + SIMULATED_SUFFIX
    return name


def _definition_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._-" else "-" for c in name).strip("-._")
    return cleaned or "subject"


def subject_definition(
    subject: Subject, spec: SubjectSpec | None = None, base: AgentDefinition | None = None
) -> AgentDefinition:
    """The definition a subject runs: its agent's (when it names one), overridden by its own
    fields. An inline subject gets one turn unless it says otherwise."""
    s = spec or subject_spec(subject)
    fields: dict[str, Any] = base.model_dump() if base is not None else {}
    fields.update(name=_definition_name(subject.name), description=subject.description)
    for key in ("system_prompt", "model", "tools", "max_turns"):
        value = getattr(s, key)
        if value is not None:
            fields[key] = value
    if base is None:
        fields.setdefault("max_turns", DEFAULT_SUBJECT_TURNS)
    return AgentDefinition.model_validate(fields)


def compose_matrix(
    experiment: ExperimentConfig, matrix: MatrixConfig, *, cwd: str | None, registry: Registry
) -> Container:
    """matrix's runtimes, models and agents for this experiment; its ``models`` win."""
    if experiment.models is not None:
        matrix = matrix.model_copy(update={"models": experiment.models})
    try:
        return compose(matrix, registry=registry, context={"cwd": cwd}, source="matrix")
    except MatrixError as e:
        raise ConfigError(e.message) from None


def make_agent_factory(
    container: Container,
    experiment: ExperimentConfig | None = None,
    *,
    simulate: bool = False,
    seed: int | None = None,
) -> AgentFactory:
    """Subject + trial → a bound agent.

    ``simulate`` puts every subject on ix's simulator (the ``--simulate`` flag), seeded per
    repeat and trial so repeats differ. Any other runtime is built once per subject and
    reused across its trials.
    """
    cache: dict[str, AgentRuntime] = {}
    registry = container.registry

    def build(subject: Subject, trial_index: int, run_index: int = 0) -> Agent:
        spec = subject_spec(subject)
        base = None
        if spec.agent:
            try:
                configured = container.agent(spec.agent)
            except MatrixError as e:
                raise ConfigError(f"subject {subject.name!r}: {e.message}") from None
            base = configured.definition
        definition = subject_definition(subject, spec, base)
        options = dict(spec.runtime)
        kind = str(options.pop("type", "") or "")
        if simulate or kind == SIMULATED:
            runtime: AgentRuntime = _simulator(experiment, seed, trial_index, run_index)
        elif spec.agent:
            runtime = container.agent(spec.agent).runtime
        elif not kind:
            raise ConfigError(
                f"subject {subject.name!r} has no runtime.type and names no agent. Runtimes: "
                f"{', '.join(registered_runtimes(registry))}. For a dry run use --simulate."
            )
        else:
            runtime = cache.get(subject.name) or _runtime(subject, kind, options)
            cache[subject.name] = runtime
        try:
            return BoundAgent(definition, runtime, observers=container.observers)
        except MatrixError as e:
            raise ConfigError(f"subject {subject.name!r}: {e.message}") from None

    def _runtime(subject: Subject, kind: str, options: dict[str, Any]) -> AgentRuntime:
        url = runtime_type_url(kind)
        if ("runtime", url) not in registry:
            raise ConfigError(
                f"subject {subject.name!r}: runtime.type {kind!r} is not registered. "
                f"Registered: {', '.join(registered_runtimes(registry))}"
            )
        try:
            built: AgentRuntime = registry.create(
                "runtime", url, options, needs=container.needs(), where=f"subject {subject.name}"
            )
        except MatrixError as e:
            raise ConfigError(e.message) from None
        return built

    return build


def _simulator(
    experiment: ExperimentConfig | None, seed: int | None, trial_index: int, run_index: int
) -> SimulatedRuntime:
    # Distinct per (repeat, trial): repeats of a seeded run must differ, or the noise floor
    # across them is 0 by construction rather than by measurement.
    trial_seed = seed * 1_000_000 + run_index * 1000 + trial_index if seed is not None else None
    return SimulatedRuntime(
        expected_skill=default_skill(experiment) if experiment else None,
        seed=trial_seed,
        expectations=_expectations(experiment) if experiment else {},
        skill_map=_skill_map(experiment) if experiment else {},
        responses=_mock_responses(experiment) if experiment else {},
    )


def registered_runtimes(registry: Registry) -> list[str]:
    names = {e.type_url.removeprefix("matrix.v1.runtime.") for e in registry.entries("runtime")}
    return sorted(names | {SIMULATED})


# --- Sensors and engines ---------------------------------------------------------------


def judge_factory(container: Container) -> Any:
    """``judge(name)``: the configured agent of that name, else that model on the model runtime."""

    def judge(name: str) -> Agent:
        if name in container.agents:
            return container.agent(name)
        runtime = container.registry.create(
            "runtime",
            runtime_type_url("model"),
            {"default_model": name},
            needs=container.needs(),
            where=f"judge {name}",
        )
        return BoundAgent(
            AgentDefinition(name="ix-judge", tools=()), runtime, observers=container.observers
        )

    return judge


def create_sensor(
    experiment: ExperimentConfig,
    container: Container,
    experiment_cwd: str | None = None,
) -> Sensor:
    """Build the experiment's sensor(s). Several are wrapped in a CompositeSensor."""
    sensors = [
        _build_one_sensor(sc, experiment.probes, container, experiment_cwd)
        for sc in experiment.sensors
    ]
    return sensors[0] if len(sensors) == 1 else CompositeSensor(sensors)


def _build_one_sensor(
    sensor_config: dict[str, Any],
    probes: tuple[Probe, ...],
    container: Container,
    experiment_cwd: str | None,
) -> Sensor:
    config = dict(sensor_config)
    kind = str(config.pop("type", "activation"))
    url = type_urls.sensor(kind)
    registry = container.registry
    if ("sensor", url) not in registry:
        raise ConfigError(
            f"unknown sensor type {kind!r}. Sensors: {', '.join(_entry_names(registry, 'sensor'))}"
        )
    if "graders_module" in config and experiment_cwd:
        config["graders_module"] = str((Path(experiment_cwd) / config["graders_module"]).resolve())
    try:
        sensor: Sensor = registry.create(
            "sensor",
            url,
            config,
            needs={"probes": probes, "judge": judge_factory(container)},
            where=f"sensor {kind}",
        )
    except MatrixError as e:
        raise ConfigError(e.message) from None
    return sensor


def probe_problems(experiment: ExperimentConfig, registry: Registry) -> list[str]:
    """Probe keys no configured sensor reads (typos), one problem per probe and key."""
    readable: set[str] = set(RESERVED_PROBE_KEYS)
    for sensor_config in experiment.sensors:
        url = type_urls.sensor(str(sensor_config.get("type", "activation")))
        if ("sensor", url) not in registry:
            return []  # the unknown sensor is reported on its own
        keys = getattr(registry.entry("sensor", url).build, "truth_keys", None)
        if keys is None:
            return []  # a sensor that does not say what it reads may read anything
        readable |= set(keys)
    problems = []
    for probe in experiment.probes:
        for key in sorted(probe.metadata):
            if key not in readable and not key.startswith("x-"):
                problems.append(
                    f"probe {probe.id!r}: key {key!r} is read by none of this experiment's "
                    f"sensors (they read: {', '.join(sorted(readable))}); fix the name, or "
                    f"prefix it x- if it is a note"
                )
    return problems


def create_engine(
    experiment: ExperimentConfig,
    container: Container,
    *,
    override: str | None = None,
    results_dir: Path | None = None,
) -> Engine:
    options = dict(experiment.engine)
    kind = str(override or options.pop("type", "native"))
    if override:
        options.pop("type", None)
        if override != experiment.engine.get("type"):
            options = {}  # another engine's options do not transfer
    url = type_urls.engine(kind)
    registry = container.registry
    if ("engine", url) not in registry:
        raise ConfigError(
            f"unknown engine {kind!r}. Engines: {', '.join(_entry_names(registry, 'engine'))}"
        )
    if kind == "inspect" and "log_dir" not in options and results_dir is not None:
        options["log_dir"] = str(results_dir / "inspect")
    try:
        engine: Engine = registry.create(
            "engine",
            url,
            options,
            needs={"observers": container.observers},
            where=f"{experiment.name}: engine",
        )
    except MatrixError as e:
        raise ConfigError(e.message) from None
    return engine


# --- Service ---------------------------------------------------------------------------


def create_service(
    experiment: ExperimentConfig,
    *,
    simulate: bool = False,
    lab: Path | None = None,
    seed: int | None = None,
    experiment_cwd: str | None = None,
    engine: str | None = None,
    matrix: MatrixConfig | None = None,
    registry: Registry | None = None,
) -> Experiment:
    """Wire an Experiment from config: sensors, engine, agent factory, store."""
    reg = registry or build_registry()
    problems = probe_problems(experiment, reg)
    if problems:
        raise ConfigError("probe keys no sensor reads:\n  " + "\n  ".join(problems))
    container = compose_matrix(
        experiment, matrix or MatrixConfig(), cwd=experiment_cwd, registry=reg
    )
    sensor = create_sensor(experiment, container, experiment_cwd)
    workspace = lab or find_lab()
    chosen = create_engine(
        experiment,
        container,
        override=engine,
        results_dir=workspace / experiment.name / "results",
    )
    agents = make_agent_factory(container, experiment, simulate=simulate, seed=seed)
    return Experiment(
        sensor=sensor,
        store=FilesystemStore(workspace),
        engine=chosen,
        agents=agents,
        seed=seed,
        simulated=simulate,
    )


def validate_experiment(
    experiment: ExperimentConfig,
    *,
    experiment_cwd: str | None = None,
    engine: str | None = None,
    matrix: MatrixConfig | None = None,
    registry: Registry | None = None,
) -> list[str]:
    """Compose everything a run would (sensors, engine, every subject's agent) and run nothing.

    Returns every problem found, not just the first. Building a runtime constructs its
    adapter (an SDK client, a model registry) but makes no call.
    """
    problems: list[str] = []
    if not experiment.probes:
        problems.append("no probes: add tasks/*.md")
    reg = registry or build_registry()
    problems.extend(probe_problems(experiment, reg))
    try:
        container = compose_matrix(
            experiment, matrix or MatrixConfig(), cwd=experiment_cwd, registry=reg
        )
    except IxError as e:
        return [*problems, f"matrix: {e}"]
    try:
        create_sensor(experiment, container, experiment_cwd)
    except (IxError, MatrixError, ImportError) as e:
        problems.append(f"sensors: {e}")
    try:
        create_engine(experiment, container, override=engine)
    except (IxError, MatrixError, ImportError) as e:
        problems.append(f"engine: {e}")
    factory = make_agent_factory(container, experiment)
    for subject in experiment.subjects:
        try:
            factory(subject, 0)
        except (IxError, MatrixError, ImportError) as e:
            problems.append(f"subject {subject.name!r}: {e}")
        problems.extend(_missing_plugins(subject, experiment_cwd))
    return problems


def _missing_plugins(subject: Subject, experiment_cwd: str | None) -> list[str]:
    """Local plugin paths (``runtime.plugins: [{type: local, path}]``) that do not exist.

    Resolved the way the claude-sdk runtime resolves them: against ``runtime.cwd``, which
    defaults to the experiment directory. The runtime itself never checks, so a dangling
    path would otherwise surface only inside a live session.
    """
    runtime = subject.config.get("runtime")
    if not isinstance(runtime, dict) or not isinstance(runtime.get("plugins"), list):
        return []
    base = Path(str(runtime.get("cwd") or experiment_cwd or "."))
    problems = []
    for i, plugin in enumerate(runtime["plugins"]):
        if not isinstance(plugin, dict) or plugin.get("type", "local") != "local":
            continue
        path = plugin.get("path")
        if not isinstance(path, str):
            continue
        resolved = (base / path).resolve()
        if not resolved.exists():
            problems.append(
                f"subject {subject.name!r}: runtime.plugins[{i}].path {path!r} does not exist "
                f"(resolved against {base}: {resolved})"
            )
    return problems


def create_store(lab: Path | None = None) -> FilesystemStore:
    """Direct store access for CLI commands that only need persistence."""
    return FilesystemStore(lab or find_lab())


# --- Simulator inputs ------------------------------------------------------------------


def default_skill(experiment: ExperimentConfig) -> str | None:
    """The skill an activation sensor in this experiment expects, if one names it."""
    for sensor in experiment.sensors:
        if sensor.get("type", "activation") == "activation" and sensor.get("expected_skill"):
            return str(sensor["expected_skill"])
    return None


def _expectations(experiment: ExperimentConfig) -> dict[str, bool]:
    """Probe prompt → should it activate, read the way the sensor reads it (one module)."""
    return {
        probe.prompt: expected
        for probe in experiment.probes
        if (expected := should_activate(probe)) is not None
    }


def _skill_map(experiment: ExperimentConfig) -> dict[str, str]:
    default = default_skill(experiment)
    return {
        probe.prompt: skill
        for probe in experiment.probes
        if (skill := expected_skill_of(probe, default)) is not None
    }


def _mock_responses(experiment: ExperimentConfig) -> dict[str, str]:
    return {
        probe.prompt: str(probe.metadata["mock_response"])
        for probe in experiment.probes
        if "mock_response" in probe.metadata
    }


__all__ = [
    "OFFLINE_RUNTIMES",
    "RESERVED_PROBE_KEYS",
    "SIMULATED",
    "SIMULATED_SUFFIX",
    "IxConfig",
    "SubjectSpec",
    "build_registry",
    "compose_matrix",
    "create_engine",
    "create_sensor",
    "create_service",
    "create_store",
    "default_skill",
    "effective",
    "is_live",
    "judge_factory",
    "load_ix_config",
    "make_agent_factory",
    "probe_problems",
    "registered_runtimes",
    "results_name",
    "runtime_type",
    "subject_definition",
    "subject_spec",
    "validate_experiment",
]
