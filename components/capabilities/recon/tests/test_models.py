"""Tests for domain models — validation, defaults, edge cases."""

from typing import Any

import pytest
from pydantic import ValidationError

from recon.domain.models import (
    AuthConfig,
    CollectorEntry,
    RateLimitConfig,
    ReconConfig,
    SourceEntry,
)
from tests.recon_fakes import collector_entry


class TestAuthConfig:
    def test_defaults(self) -> None:
        auth = AuthConfig()
        assert auth.header == ""
        assert auth.param == ""
        assert auth.env == ""

    def test_header_auth(self) -> None:
        auth = AuthConfig(header="x-api-key", env="S2_API_KEY")
        assert auth.header == "x-api-key"

    def test_param_auth(self) -> None:
        auth = AuthConfig(param="api_key", env="OPENALEX_API_KEY")
        assert auth.param == "api_key"


class TestSourceEntry:
    def test_minimal(self) -> None:
        src = SourceEntry(name="test", url="https://example.com")
        assert src.type == "api"
        assert src.auth == AuthConfig()
        assert src.rate_limit.rps == 10.0

    def test_full(self) -> None:
        src = SourceEntry(
            name="s2",
            type="api",
            url="https://api.semanticscholar.org",
            auth=AuthConfig(header="x-api-key", env="S2_API_KEY"),
            rate_limit=RateLimitConfig(rps=0.33, burst=1),
            user_agent="test/1.0",
        )
        assert src.rate_limit.rps == 0.33
        assert src.user_agent == "test/1.0"

    def test_local_type(self) -> None:
        src = SourceEntry(name="tokio", type="local", url="/Users/dev/oss/tokio")
        assert src.type == "local"

    @pytest.mark.parametrize(
        ("fields", "named"),
        [
            ({"rate_limit": {"rps": 0}}, "rps"),
            ({"rate_limit": {"rps": -1}}, "rps"),
            ({"rate_limit": {"burst": 0}}, "burst"),
            ({"rate_limit": {"burst": -2}}, "burst"),
            ({"timeout": 0}, "timeout"),
            ({"timeout": -5}, "timeout"),
        ],
    )
    def test_a_bound_that_cannot_be_kept_is_refused(
        self, fields: dict[str, Any], named: str
    ) -> None:
        """Regression: ``rps: 0`` divided by zero on a request, ``burst: 0`` waited forever
        for a token no bucket that holds none can give, and a negative timeout was accepted."""
        with pytest.raises(ValidationError, match=named):
            SourceEntry(name="s", url="https://x", **fields)


class TestCollectorEntry:
    def test_cli(self) -> None:
        entry = collector_entry(
            name="grep",
            type="cli",
            run="rg --json '{pattern}'",
            patterns=["fixme", "todo"],
        )
        assert entry.type == "cli"
        assert entry.source is None

    def test_api(self) -> None:
        entry = collector_entry(
            name="search",
            type="api",
            source="s2",
            endpoint="/paper/search",
            params={"query": "transformer attention", "limit": "20"},
            extract="data",
            normalize={"title": "title", "authors": "authors.*.name"},
        )
        assert entry.source == "s2"
        assert entry.normalize is not None
        assert len(entry.normalize) == 2

    def test_type_is_an_open_string(self) -> None:
        """Collector types are extensions: whether one is installed is a plan-time check
        against the entry-point group (test_plan.py), not a closed Literal here."""
        assert CollectorEntry(name="x", type="sql").type == "sql"

    def test_type_url_defaults_to_records(self) -> None:
        assert CollectorEntry(name="x", type="cli").type_url == "recon.v1.records"

    @pytest.mark.parametrize(
        "value", ["acme.papers.v2.search-hit", "recon.v1.records", "x.v10.a-b-c"]
    )
    def test_type_url_accepts_the_dotted_grammar(self, value: str) -> None:
        assert CollectorEntry(name="x", type="cli", type_url=value).type_url == value

    @pytest.mark.parametrize(
        "value",
        [
            "recon.v1/records",  # slash form
            "recon/v1/records",
            "recon.records",  # no version
            "v1.records",  # empty namespace
            "recon.v1",  # version-terminal
            "recon.v1.a.b",  # two resource segments
            "Recon.v1.records",  # uppercase namespace
            "recon.v1.Records",  # resource not kebab
            "recon.v1.search_hit",  # underscore
        ],
    )
    def test_type_url_rejects_everything_else(self, value: str) -> None:
        with pytest.raises(ValidationError, match="not a type_url"):
            CollectorEntry(name="x", type="cli", type_url=value)


class TestReconConfig:
    def test_minimal(self) -> None:
        config = ReconConfig(
            collectors=[
                collector_entry(name="test", type="cli", run="echo hi"),
            ]
        )
        assert config.catalog == []
        assert len(config.collectors) == 1

    def test_from_yaml_dict(self) -> None:
        """Simulates loading from YAML."""
        raw = {
            "catalog": [
                {"name": "s2", "url": "https://api.semanticscholar.org/graph/v1"},
            ],
            "collectors": [
                {
                    "name": "s2-search",
                    "type": "api",
                    "source": "s2",
                    "endpoint": "/paper/search",
                    "params": {"query": "test"},
                    "extract": "data",
                    "normalize": {"title": "title"},
                },
            ],
        }
        config = ReconConfig.model_validate(raw)
        assert config.catalog[0].name == "s2"
        assert config.collectors[0].normalize == {"title": "title"}

    def test_no_collectors_fails(self) -> None:
        with pytest.raises(ValidationError):
            ReconConfig.model_validate({"catalog": []})

    def test_collectors_only(self) -> None:
        """No catalog needed for CLI collectors."""
        config = ReconConfig.model_validate(
            {
                "collectors": [
                    {"name": "local", "type": "cli", "run": "ls"},
                ],
            }
        )
        assert config.catalog == []

    def test_local_sources(self) -> None:
        """Local sources for CLI fan-out."""
        config = ReconConfig.model_validate(
            {
                "catalog": [
                    {"name": "repo-a", "type": "local", "url": "/tmp/a"},
                    {"name": "repo-b", "type": "local", "url": "/tmp/b"},
                ],
                "collectors": [
                    {"name": "scan", "type": "cli", "run": "echo hello"},
                ],
            }
        )
        assert len(config.catalog) == 2
        assert config.catalog[0].type == "local"


class TestJsonRecordMapping:
    """`recon query --json` maps columns onto rows via zip.

    Regression for the B905 finding: the auto-fixer chose `strict=False`, which preserves
    silent truncation. Both sides come from one query execution, so a width mismatch is a
    bug — and emitting JSON records with fields silently dropped is worse than failing,
    because the caller gets plausible output that is missing data.
    """

    def test_equal_widths_map_cleanly(self) -> None:
        columns = ["a", "b", "c"]
        rows = [(1, 2, 3), (4, 5, 6)]
        records = [dict(zip(columns, row, strict=True)) for row in rows]
        assert records == [{"a": 1, "b": 2, "c": 3}, {"a": 4, "b": 5, "c": 6}]

    def test_row_narrower_than_columns_raises_rather_than_dropping(self) -> None:
        import pytest

        columns = ["a", "b", "c"]
        with pytest.raises(ValueError):
            [dict(zip(columns, row, strict=True)) for row in [(1, 2)]]
        # strict=False is what the auto-fix produced: it silently loses column "c".
        assert dict(zip(columns, (1, 2), strict=False)) == {"a": 1, "b": 2}

    def test_row_wider_than_columns_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            [dict(zip(["a"], row, strict=True)) for row in [(1, 2)]]
