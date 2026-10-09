"""What a probe's activation frontmatter means — one module, read by the sensor and the simulator.

``expectation`` is one of ``must_trigger`` (the default), ``should_not_trigger`` or
``acceptable``. Anything else is a ConfigError naming the probe: an unrecognised value used
to fall through to "acceptable" and pass every trial, and YAML reads ``expectation: no`` as
``False``. ``expected_skill`` on the probe overrides the sensor's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ix.domain.errors import ConfigError
from ix.domain.models import ACCEPTABLE, MUST_TRIGGER, SHOULD_NOT_TRIGGER

if TYPE_CHECKING:
    from ix.domain.types import Probe

EXPECTATIONS = (MUST_TRIGGER, SHOULD_NOT_TRIGGER, ACCEPTABLE)


def expectation_of(probe: Probe) -> str:
    """The probe's expectation; ``must_trigger`` when absent; ConfigError when not legal."""
    raw = probe.metadata.get("expectation", MUST_TRIGGER)
    if not isinstance(raw, str) or raw not in EXPECTATIONS:
        raise ConfigError(
            f"probe {probe.id!r}: expectation {raw!r} is not one of {', '.join(EXPECTATIONS)}"
            + (" (YAML reads an unquoted no/yes as a boolean)" if isinstance(raw, bool) else "")
        )
    return raw


def expected_skill_of(probe: Probe, default: str | None) -> str | None:
    """The skill this probe expects: its own ``expected_skill``, else the sensor's."""
    skill = probe.metadata.get("expected_skill") or default
    return str(skill) if skill else None


def should_activate(probe: Probe) -> bool | None:
    """For the simulator: True / False, or None when either outcome is acceptable."""
    expectation = expectation_of(probe)
    return None if expectation == ACCEPTABLE else expectation == MUST_TRIGGER
