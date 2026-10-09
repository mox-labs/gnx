"""Type URLs — ``<namespace>.v<version>/<resource>``, the key everything is registered under.

The convention follows Envoy's TypedExtensionConfig: the type URL selects the extension and
names a versioned schema; an instance's name is only an identifier. Examples::

    matrix.v1/runtime.claude-sdk     ix.v1/sensor.activation     hardline.v1/completion

A version may carry a stability suffix (``v1alpha1``, ``v2beta1``), as Kubernetes API
versions do. Building and parsing live here so no caller concatenates strings.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from matrix.domain.errors import ConfigError

_PATTERN = re.compile(
    r"^(?P<namespace>[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*)"
    r"\.v(?P<version>[1-9][0-9]*(?:(?:alpha|beta)[1-9][0-9]*)?)"
    r"/(?P<resource>[a-z0-9][a-z0-9._-]*)$"
)


class TypeUrl(NamedTuple):
    namespace: str
    version: str
    resource: str

    def __str__(self) -> str:
        return f"{self.namespace}.v{self.version}/{self.resource}"


def type_url(namespace: str, version: str | int, resource: str) -> str:
    """Build and validate a type URL: ``type_url("ix", 1, "sensor.activation")``."""
    url = f"{namespace}.v{version}/{resource}"
    parse_type_url(url)
    return url


def parse_type_url(url: str) -> TypeUrl:
    """Split a type URL into its parts. Raises ConfigError naming the expected shape."""
    match = _PATTERN.match(url)
    if match is None:
        raise ConfigError(
            f"{url!r} is not a type URL. Expected <namespace>.v<version>/<resource>, "
            "e.g. 'matrix.v1/runtime.claude-sdk' (lowercase; version like 1, 1alpha1, 2beta1)"
        )
    return TypeUrl(match["namespace"], match["version"], match["resource"])
