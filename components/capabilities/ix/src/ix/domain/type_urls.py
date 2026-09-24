"""ix type URLs — the matrix convention ``<namespace>.v<version>/<resource>``.

Registry keys (sensors, engines, ix's own runtimes) and the kinds ix's DAG nodes append to
the Construct. A composition root keys on these; config names the short form
(``type: activation``) and composition expands it.
"""

from __future__ import annotations

NAMESPACE = "ix.v1"

# Construct kinds — what the four-node trial DAG reads and writes.
PROBE = f"{NAMESPACE}/probe.stimulus"
SUBJECT = f"{NAMESPACE}/subject"
TRIAL = f"{NAMESPACE}/trial.observation"
READINGS = f"{NAMESPACE}/sensor.readings"


def sensor(kind: str) -> str:
    return f"{NAMESPACE}/sensor.{kind}"


def engine(kind: str) -> str:
    return f"{NAMESPACE}/engine.{kind}"


def runtime(kind: str) -> str:
    """ix-owned agent runtimes. Resolved before matrix's for the same short name."""
    return f"{NAMESPACE}/runtime.{kind}"


def short(type_url: str, prefix: str) -> str:
    return type_url.removeprefix(f"{NAMESPACE}/{prefix}.")
