"""ix type URLs, in slick's dotted grammar (minted through matrix's builder).

Registry keys for ix's own extension points (sensors, engines, the simulator), the payload
types of the native engine's trial flow, and the schema names of ix's JSON documents. Config
names a built-in by its short form (``type: activation``); a third party's by its full type
URL (``type: acme.v1.sensor.rubric``).
"""

from __future__ import annotations

from matrix import type_url

NAMESPACE = "ix"

PROBE = type_url(NAMESPACE, 1, "probe")
TRIAL_INDEX = type_url(NAMESPACE, 1, "trial-index")
TRIAL = type_url(NAMESPACE, 1, "trial")

SENSOR_PREFIX = f"{NAMESPACE}.v1.sensor."
ENGINE_PREFIX = f"{NAMESPACE}.v1.engine."


def sensor(kind: str) -> str:
    """``activation`` → ``ix.v1.sensor.activation``; a full type URL passes through."""
    return kind if "." in kind else type_url(NAMESPACE, 1, f"sensor.{kind}")


def engine(kind: str) -> str:
    """``native`` → ``ix.v1.engine.native``; a full type URL passes through."""
    return kind if "." in kind else type_url(NAMESPACE, 1, f"engine.{kind}")


def schema(document: str) -> str:
    """The ``schema`` field of an ix JSON document: ``results`` → ``ix.v1.results``."""
    return type_url(NAMESPACE, 1, document)


def short(url: str) -> str:
    """A built-in's short name for display; a third party's full type URL as it stands."""
    for prefix in (SENSOR_PREFIX, ENGINE_PREFIX):
        if url.startswith(prefix):
            return url.removeprefix(prefix)
    return url
