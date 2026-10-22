"""Test-only adapters: a subject, a probe and a sensor that start nothing and call nothing.

They are registered under the built-in kinds' type URLs (``process``, ``http-load``,
``timing``), so the experiments the tests write are ordinary ones and everything between the
command line and the record is the real path: the registry, the use cases, the three matrix
components, matrix's store.

A subject says how it behaves in its ``env`` option, which the ``process`` kind takes:

* ``LATENCY_MS``: the latency its trials report (default 50);
* ``FAIL``: ``subject`` (the probe finds it broken), ``harness`` (the probe breaks),
  ``sensor`` (the reading cannot be taken), ``dies`` (it dies during the probe, so the
  probe gets no answer and its release finds it gone), or ``never`` (default);
* ``FAIL_SEQS``: comma-separated trial positions the failure applies to (default: all);
* ``HOLD_S``: how long one probe takes (default 0), for runs that are stopped or killed.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from matrix import Registry, default_registry

from ix.adapters._out.environments import NoEnvironment
from ix.composition.extension import POINTS, register_run_path
from ix.domain import schema
from ix.domain.run import HarnessError, SubjectError, Target

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from ix.domain.experiment import SubjectConfig
    from ix.domain.ports import Measured
    from ix.domain.run import Interaction

#: What the fakes did in this process, in order: each subject reached, probed and released.
LOG: list[str] = []


class FakeSubject:
    def __init__(self) -> None:
        self.reached = 0

    @asynccontextmanager
    async def reach(self, name: str, subject: SubjectConfig, *, base: str) -> AsyncIterator[Target]:
        env = {str(k): str(v) for k, v in (subject.options.get("env") or {}).items()}
        seq = self.reached
        self.reached += 1
        LOG.append(f"reach {name}")
        try:
            yield Target(subject=name, facets={"url": subject.options.get("url", ""), "env": env})
        finally:
            LOG.append(f"release {name}")
        if _fails(env, "dies", seq):
            raise SubjectError("exited", f"{name} exited during the probe")


def _fails(env: Mapping[str, str], what: str, seq: int) -> bool:
    if env.get("FAIL", "never") != what:
        return False
    listed = env.get("FAIL_SEQS", "")
    return not listed or str(seq) in listed.split(",")


class FakeLoad:
    """Reports the latency the subject was told to have, with a small jitter fixed by the
    trial's seed, so two runs of one design measure the same thing."""

    def __init__(self) -> None:
        self.applied = 0

    def budget_s(self, options: Mapping[str, Any]) -> float | None:
        return 30.0

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        env = target.facets["env"]
        seq = self.applied
        self.applied += 1
        LOG.append(f"apply {target.subject}")
        if _fails(env, "subject", seq):
            raise SubjectError("exited", "the fake subject died")
        if _fails(env, "harness", seq):
            raise HarnessError("tool_failed", "the fake load tool broke")
        if _fails(env, "dies", seq):
            raise HarnessError("tool_failed", "the load tool gave up: nothing answered")
        await asyncio.sleep(float(env.get("HOLD_S", "0")))
        latency = float(env.get("LATENCY_MS", "50")) * (1 + ((seed % 1000) / 1000 - 0.5) * 0.01)
        spread = {
            name: latency for name in ("min", "mean", "p50", "p90", "p95", "p99", "p999", "max")
        }
        return {
            "load": {
                "tool": "fake",
                "requests": 100,
                "throughput": 1000.0 / latency,
                "sent": 100,
                "failed": 0,
                "error_rate": 0.0,
                "latency_ms": spread,
                "ttfb_ms": spread,
                "sensor_fails": _fails(env, "sensor", seq),
            }
        }


class FakeTiming:
    def watch(self, target: Target, options: Mapping[str, Any]) -> None:
        return None

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Mapping[str, Measured]:
        load = interaction.observations["load"]
        if load["sensor_fails"]:
            raise RuntimeError("the fake sensor could not read this")
        return {
            "latency": dict(load["latency_ms"]),
            "ttfb": dict(load["ttfb_ms"]),
            "throughput": float(load["throughput"]),
            "error_rate": {"events": int(load["failed"]), "units": int(load["sent"])},
        }


def fake_registry(**replacing: Any) -> Registry:
    """A registry of ix's run path with the fakes as its adapters: ``process`` subjects,
    ``http-load`` probes, the ``timing`` sensor and no environment. ``replacing`` gives
    another adapter for a family (``environment=Broken``), or None to leave the family
    without one."""
    registry = default_registry(discover=False)
    register_run_path(registry)
    adapters: dict[str, tuple[str, Any]] = {
        "subject": ("process", FakeSubject),
        "probe": ("http-load", FakeLoad),
        "sensor": ("timing", FakeTiming),
        "environment": ("none", NoEnvironment),
    }
    for family, (name, build) in adapters.items():
        chosen = replacing.get(family, build)
        if chosen is not None:
            registry.register(POINTS[family], schema.url(family, name), chosen)
    return registry


def experiment(variant_ms: float = 50.0, **changes: Any) -> dict[str, Any]:
    """A ``compare`` experiment the fakes can run: a control at 50 ms and one variant."""
    url = "http://fake"
    data: dict[str, Any] = {
        "name": "fake-compare",
        "question": "Is the candidate faster than the baseline?",
        "genre": "compare",
        "subjects": {
            "baseline": {"kind": "process", "command": ["fake"], "url": url, "env": {}},
            "candidate": {
                "kind": "process",
                "command": ["fake"],
                "url": url,
                "env": {"LATENCY_MS": str(variant_ms)},
            },
        },
        "probes": [
            {
                "id": "get",
                "kind": "http-load",
                "target": "{url}/x",
                "load_model": "closed",
                "duration": "1s",
            }
        ],
        "primary": {"metric": "latency", "statistic": "p50"},
        "comparison": "superiority",
        # The genre's minimum: it keeps a run's tables short.
        "repeats": 5,
    }
    data.update(changes)
    return data
