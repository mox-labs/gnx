"""The dotted type_url grammar (gnx GEP-0001), checked without depending on gnx.

    <namespace>.<v N>.<resource>      e.g. recon.v1.records, acme.papers.v2.search-hit

- namespace: one or more dot-separated lowercase segments (``[a-z][a-z0-9]*``)
- version: exactly one ``v<N>`` segment, the first one found, never first or last
- resource: exactly one kebab-case segment after the version
- a slash anywhere is rejected: the slash form is not adopted

Every JSON document recon prints names its shape with a type_url in ``schema``, and every
archive table declares the type_url of its records.
"""

from __future__ import annotations

import re

_VERSION = re.compile(r"^v[0-9]+$")
_NAMESPACE_SEGMENT = re.compile(r"^[a-z][a-z0-9]*$")
_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

DEFAULT_RECORDS_TYPE_URL = "recon.v1.records"


def type_url_problem(value: str) -> str | None:
    """Why ``value`` is not a type_url, or None when it is one."""
    if "/" in value:
        return "slash form is not a type_url: use dots (recon.v1.records)"
    segments = value.split(".")
    versions = [i for i, seg in enumerate(segments) if _VERSION.match(seg)]
    if not versions:
        return "no version segment (v1, v2, ...)"
    vi = versions[0]
    if vi == 0:
        return "namespace is empty: the version segment comes first"
    if vi != len(segments) - 2:
        return "exactly one resource segment must follow the version"
    for seg in segments[:vi]:
        if not _NAMESPACE_SEGMENT.match(seg):
            return f"namespace segment {seg!r} must be lowercase letters and digits"
    if not _KEBAB.match(segments[-1]):
        return f"resource {segments[-1]!r} must be lowercase kebab-case"
    return None


def is_type_url(value: str) -> bool:
    return type_url_problem(value) is None
