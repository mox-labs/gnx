"""ix's extension points and built-ins, registered through matrix's one discovery path.

``pyproject.toml`` declares ``[project.entry-points."matrix.extensions"] ix = ...:register``.
That declares two extension points on matrix's registry, ``sensor`` and ``engine``, and
registers ix's own sensors and engines there, exactly as a third party registers theirs::

    def register(registry):
        registry.register(
            "sensor", "acme.v1.sensor.rubric", build, config=RubricConfig,
            needs={"probes", "judge"}, effects={"model"},
        )

A sensor's ``build(config, *, probes, judge)`` returns the sensor; ``judge(name)`` is a matrix
agent for a model or agent name. ``build.truth_keys`` (when set) names the probe keys the
sensor reads, so ix can refuse probe keys no configured sensor reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ix.adapters._out.engines.inspect_engine import InspectEngine, InspectEngineConfig
from ix.adapters._out.engines.native import NativeEngine, NativeEngineConfig
from ix.domain import type_urls
from ix.domain.types import Probe, Trial
from ix.eval.sensors import (
    ActivationSensor,
    FunctionTestSensor,
    OutcomeSensor,
    ToolUsageSensor,
)
from ix.eval.sensors_deepeval import DeepEvalSensor

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from matrix import Observer, Registry

    from ix.domain.ports import SensorClass

_SENSORS: dict[str, tuple[Any, frozenset[str] | None, str]] = {
    "activation": (ActivationSensor, frozenset(), "Did the agent activate the expected skill?"),
    # Runs the subject's generated code: what that code touches is not knowable, so unknown.
    "function-test": (
        FunctionTestSensor,
        None,
        "Runs the generated function against the probe's test cases (untrusted code)",
    ),
    "tool-usage": (ToolUsageSensor, frozenset(), "Did the agent call the expected tool?"),
    "outcome": (OutcomeSensor, None, "Did the answer contain the expected facts?"),
    "deepeval": (
        DeepEvalSensor,
        frozenset({"model", "network"}),
        "A DeepEval metric, judged by a model (requires ix[deepeval])",
    ),
}


def sensor_builder(cls: SensorClass) -> Callable[..., Any]:
    """Wrap a sensor class's ``from_config`` as a registry build function."""

    def build(config: Any, *, probes: Any, judge: Any) -> Any:
        return cls.from_config(config, probes, judge=judge)

    build.truth_keys = cls.truth_keys  # type: ignore[attr-defined]
    return build


def _native(config: NativeEngineConfig, *, observers: Sequence[Observer]) -> NativeEngine:
    return NativeEngine(config, observers=observers)


def register(registry: Registry) -> None:
    registry.add_point("sensor").add_point("engine")
    registry.register_payload(type_urls.PROBE, Probe, "A stimulus put to a subject")
    registry.register_payload(type_urls.TRIAL, Trial, "One execution of a probe by a subject")
    for kind, (cls, effects, summary) in _SENSORS.items():
        registry.register(
            "sensor",
            type_urls.sensor(kind),
            sensor_builder(cls),
            config=cls.Config,
            needs={"probes", "judge"},
            effects=effects,
            summary=summary,
        )
    registry.register(
        "engine",
        type_urls.engine("native"),
        _native,
        config=NativeEngineConfig,
        needs={"observers"},
        summary="Each trial as one run of a matrix flow, compiled once per repeat",
    )
    registry.register(
        "engine",
        type_urls.engine("inspect"),
        InspectEngine,
        config=InspectEngineConfig,
        needs={"results_dir"},
        effects={"filesystem"},
        summary="Each repeat as an Inspect AI task, with its .eval log (requires ix[inspect])",
    )
