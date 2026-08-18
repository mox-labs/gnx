"""Core protocols — Sensor, SensorClass.

Composable building blocks of any experiment.
Each is a typing.Protocol: implement the methods, satisfy the contract.
A sensor measures a trial and produces readings — like instruments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ix.domain.types import Probe, Reading, Trial


@runtime_checkable
class Sensor(Protocol):
    """Measures a trial and produces readings.

    The sensor is an instrument — it observes a trial (probe_id + response).
    Ground truth (test cases, rubrics) is injected at construction by
    the service layer, not discovered from the probe.

    The trial gives the sensor a key (probe_id) to look up its
    pre-configured ground truth, and a response to measure.
    """

    @property
    def name(self) -> str: ...

    def measure(self, trial: Trial) -> list[Reading]: ...


@runtime_checkable
class SensorClass(Protocol):
    """A sensor *class* — the shape the composition root holds in its registry.

    Every ix sensor pairs a pydantic `Config` model with a `from_config` classmethod, and
    the registry resolves sensors through that pair and nothing else. Naming it as a port
    is what makes the sensor table typeable: a bare `type` erases `from_config`, so the
    composition root would have to either cast or go unchecked at the one place where a
    third party plugs a new sensor in.
    """

    def from_config(
        self,
        config: Any,
        probes: tuple[Probe, ...] = ...,
        **kwargs: Any,
    ) -> Sensor: ...
