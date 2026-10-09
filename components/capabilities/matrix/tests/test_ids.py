"""Type URLs follow slick's grammar (SD-13, GEP-0001), so matrix ids are valid slick ids.

The cases mirror slick's own (``src/manifest.rs`` tests and the ``kind-segment-type-url``
vector): dot-separated lowercase kebab segments, no kind segment, ``slick.*`` reserved.
"""

from __future__ import annotations

import pytest

from matrix import ConfigError, check_type_url, is_type_url, parse_type_url, type_url
from matrix.domain.ids import TypeUrl

VALID = [
    "matrix.v1.runtime.claude-sdk",
    "ix.v1.sensor.activation",
    "matrix.v1.agent-response",
    "gnx.dev.v1.source-cache",
    "acme.v2beta1.thing",
    "a.b",
]

INVALID = [
    ("matrix.v1/runtime.claude-sdk", "'/'"),  # the old slash form
    ("Matrix.v1.x", "kebab"),
    ("matrix.v1.Runtime", "kebab"),
    ("matrix..v1", "kebab"),
    ("matrix.v1.agent.response", "kind"),  # 'agent' is a kind, never a whole segment
    ("acme.flow", "kind"),
    ("acme.v1.skill", "kind"),
    ("acme.v1.capability.x", "kind"),
    ("acme_x.v1", "kebab"),
    ("-acme.v1", "kebab"),
    ("", "non-empty"),
]


@pytest.mark.parametrize("url", VALID)
def test_valid(url: str) -> None:
    assert is_type_url(url)
    assert check_type_url(url) == url


@pytest.mark.parametrize(("url", "reason"), INVALID)
def test_invalid_says_why(url: str, reason: str) -> None:
    assert not is_type_url(url)
    with pytest.raises(ConfigError, match=reason) as e:
        check_type_url(url, where="here")
    assert e.value.fix  # every grammar error says how to fix it


def test_parse_splits_at_the_version_segment() -> None:
    assert parse_type_url("matrix.v1.runtime.claude-sdk") == TypeUrl(
        "matrix", "v1", "runtime.claude-sdk"
    )
    assert str(parse_type_url("gnx.dev.v1.source-cache")) == "gnx.dev.v1.source-cache"


def test_parse_requires_a_version_between_namespace_and_resource() -> None:
    with pytest.raises(ConfigError, match="no version segment"):
        parse_type_url("acme.thing")
    with pytest.raises(ConfigError, match="no version segment"):
        parse_type_url("v1.thing")  # nothing before the version


def test_builder_mints_and_validates() -> None:
    assert type_url("ix", 1, "sensor.activation") == "ix.v1.sensor.activation"
    assert type_url("acme", "v1alpha1", "x") == "acme.v1alpha1.x"
    with pytest.raises(ConfigError):
        type_url("ix", 1, "Bad")


@pytest.mark.parametrize("root", ["slick", "slick.core"])
def test_slick_namespace_is_reserved(root: str) -> None:
    with pytest.raises(ConfigError, match="reserved"):
        type_url(root, 1, "thing")
    assert type_url("slickers", 1, "x") == "slickers.v1.x"  # only the exact root is fenced
