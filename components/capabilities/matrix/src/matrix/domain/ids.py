"""Type URLs — the names everything is registered and typed under.

The grammar is slick's (SD-13, GEP-0001): dot-separated lowercase kebab segments, compared by
equality and never dereferenced. A slick kind name (``capability``, ``skill``, ``flow``,
``agent``) is never a whole segment, and ``slick.*`` is reserved for slick's own primitives::

    matrix.v1.runtime.claude-sdk     ix.v1.sensor.activation     matrix.v1.agent-response

matrix carries its own copy of the check until it depends on slick; the tests run slick's
grammar cases so the two cannot drift. Names matrix *mints* also carry one version segment
(``v1``, ``v1alpha1``, ``v2beta1``) after at least one namespace segment, so a reader can tell
the namespace from the resource. This is the only module that builds or splits a type URL;
everyone else compares strings.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from matrix.domain.errors import ConfigError

_SEGMENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^v[1-9][0-9]*(?:(?:alpha|beta)[1-9][0-9]*)?$")
KIND_SEGMENTS = frozenset({"capability", "skill", "flow", "agent"})


class TypeUrl(NamedTuple):
    """A type URL split at its version segment: ``matrix`` . ``v1`` . ``runtime.claude-sdk``."""

    namespace: str
    version: str
    resource: str

    def __str__(self) -> str:
        return f"{self.namespace}.{self.version}.{self.resource}"


def problem(url: str) -> str | None:
    """Why ``url`` is not a type URL under slick's grammar, or ``None`` if it is one."""
    if not isinstance(url, str) or not url:
        return "a type URL is a non-empty string"
    if "/" in url:
        return "type URLs are dot-separated; '/' is not part of the grammar"
    for segment in url.split("."):
        if not _SEGMENT.match(segment):
            return f"segment {segment!r} is not lowercase kebab-case ([a-z0-9]+(-[a-z0-9]+)*)"
        if segment in KIND_SEGMENTS:
            return f"segment {segment!r} is a component kind, which never appears in a type URL"
    return None


def is_type_url(url: str) -> bool:
    """True if ``url`` is valid under slick's grammar (what a matrix consumer may receive)."""
    return problem(url) is None


def check_type_url(url: str, *, where: str = "") -> str:
    """Return ``url`` if it is a valid type URL; otherwise raise ConfigError saying why."""
    reason = problem(url)
    if reason is not None:
        prefix = f"{where}: " if where else ""
        raise ConfigError(
            f"{prefix}{url!r} is not a type URL: {reason}. "
            "Expected e.g. 'matrix.v1.runtime.claude-sdk'",
            fix="use dot-separated lowercase kebab segments with a version, as in "
            "'<namespace>.v1.<resource>'",
        )
    return url


def parse_type_url(url: str) -> TypeUrl:
    """Split a minted type URL at its version segment. Raises ConfigError if it has none."""
    check_type_url(url)
    segments = url.split(".")
    for i, segment in enumerate(segments):
        if _VERSION.match(segment) and 0 < i < len(segments) - 1:
            return TypeUrl(".".join(segments[:i]), segment, ".".join(segments[i + 1 :]))
    raise ConfigError(
        f"{url!r} has no version segment between its namespace and its resource",
        fix="mint as '<namespace>.v<N>.<resource>', e.g. 'ix.v1.sensor.activation'",
    )


def type_url(namespace: str, version: str | int, resource: str) -> str:
    """Build and validate a type URL: ``type_url("ix", 1, "sensor.activation")``.

    Refuses to mint into ``slick.*`` (SD-13: reserved for slick's primitives).
    """
    ver = f"v{version}" if isinstance(version, int) else version
    if namespace == "slick" or namespace.startswith("slick."):
        raise ConfigError(
            f"cannot mint {namespace!r}: the slick namespace is reserved for slick's primitives",
            fix="mint under your own namespace root",
        )
    url = f"{namespace}.{ver}.{resource}"
    parse_type_url(url)
    return url
