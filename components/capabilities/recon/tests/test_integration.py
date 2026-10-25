"""Integration tests against live APIs and real filesystems.

The live-API classes call third-party services, so they run only when asked:
``RECON_LIVE=1 pytest tests/test_integration.py``. The filesystem ones always run.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from recon.adapters._out.api_collector import ApiCollector
from recon.adapters._out.cli_collector import CliCollector
from recon.adapters._out.credentials import Credentials
from recon.adapters._out.http_requester import HttpRequester
from recon.adapters._out.markitdown_converter import MarkitdownConverter
from recon.adapters._out.system_clock import SystemClock
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.adapters._out.web_collector import WebCollector
from recon.domain.collector import Collector
from recon.domain.errors import CollectionError
from recon.domain.models import CollectorEntry, RateLimitConfig, ReconConfig, SourceEntry
from recon.domain.ratelimit import RateLimiter
from tests.recon_fakes import collector_entry
from tests.recon_fakes import run_survey as run

_asked = pytest.mark.skipif(
    not os.environ.get("RECON_LIVE"), reason="calls live APIs; set RECON_LIVE=1 to run"
)


def live[T: type](tests: T) -> T:
    """Calls live APIs: runs only when asked, and is let past the workspace's network guard."""
    return pytest.mark.network(_asked(tests))


def _requester() -> HttpRequester:
    clock = SystemClock()
    return HttpRequester(RateLimiter(clock), clock)


def _collectors() -> dict[str, Collector]:
    req = _requester()
    conv = MarkitdownConverter()
    return {
        "cli": CliCollector(),
        "api": ApiCollector(req, Credentials({}), DEFAULT_USER_AGENT),
        "web": WebCollector(req, conv, DEFAULT_USER_AGENT),
    }


def _api_records(entry: CollectorEntry, source: SourceEntry | None) -> list[dict[str, Any]]:
    """Helper — materializes the streaming iterator for test assertions."""
    return list(
        ApiCollector(_requester(), Credentials({}), DEFAULT_USER_AGENT).collect(entry, source)
    )


# --- OpenAlex (no auth, fast) ---


@live
class TestOpenAlexLive:
    def test_search(self) -> None:
        source = SourceEntry(
            name="openalex",
            url="https://api.openalex.org",
            user_agent="recon-tests (mailto:test@example.com)",
        )
        entry = collector_entry(
            name="test",
            type="api",
            source="openalex",
            endpoint="/works",
            params={
                "search": "collaborative intelligence",
                "per_page": "3",
                "select": "id,display_name,publication_year,cited_by_count",
            },
            extract="results",
            normalize={
                "title": "display_name",
                "year": "publication_year",
                "citations": "cited_by_count",
            },
        )
        records = _api_records(entry, source)
        assert len(records) == 3
        assert all(r["title"] is not None for r in records)
        assert all(isinstance(r["year"], int) for r in records)

    def test_inverted_index_transform(self) -> None:
        """OpenAlex abstract_inverted_index → $inverted_index → text."""
        source = SourceEntry(
            name="openalex",
            url="https://api.openalex.org",
            user_agent="recon-tests (mailto:test@example.com)",
        )
        entry = collector_entry(
            name="test",
            type="api",
            source="openalex",
            endpoint="/works",
            params={
                "search": "attention mechanism transformer",
                "per_page": "3",
                "select": "display_name,abstract_inverted_index",
            },
            extract="results",
            normalize={
                "title": "display_name",
                "abstract": "abstract_inverted_index|$inverted_index",
            },
        )
        records = _api_records(entry, source)
        assert len(records) == 3
        # At least one paper should have an abstract (some don't)
        abstracts = [r["abstract"] for r in records if r["abstract"]]
        assert len(abstracts) >= 1
        # Reconstructed abstract is a string, not a dict
        assert isinstance(abstracts[0], str)
        assert len(abstracts[0]) > 20

    def test_full_survey_to_jsonl(self) -> None:
        config = ReconConfig.model_validate(
            {
                "catalog": [
                    {
                        "name": "openalex",
                        "url": "https://api.openalex.org",
                        "user_agent": "recon-tests (mailto:test@example.com)",
                    }
                ],
                "collectors": [
                    {
                        "name": "oa-search",
                        "type": "api",
                        "source": "openalex",
                        "endpoint": "/works",
                        "params": {
                            "search": "attention mechanism",
                            "per_page": "2",
                            "select": "id,display_name,publication_year",
                        },
                        "extract": "results",
                        "normalize": {"title": "display_name", "year": "publication_year"},
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            archive, _ = run(config, Path(tmp))
            jsonl = archive / "oa-search.jsonl"
            assert jsonl.exists()
            records = [json.loads(line) for line in jsonl.read_text().splitlines()]
            assert len(records) == 2
            assert records[0]["title"] is not None


# --- Semantic Scholar (rate limited without API key) ---


@live
class TestS2Live:
    def test_search_or_rate_limited(self) -> None:
        """S2 works if under rate limit, raises CollectionError if 429."""
        source = SourceEntry(
            name="s2",
            url="https://api.semanticscholar.org/graph/v1",
            rate_limit=RateLimitConfig(rps=0.33, burst=1),
        )
        entry = collector_entry(
            name="test",
            type="api",
            source="s2",
            endpoint="/paper/search",
            params={
                "query": "attention mechanism",
                "limit": "2",
                "fields": "title,year",
            },
            extract="data",
            normalize={"title": "title", "year": "year"},
        )
        try:
            records = _api_records(entry, source)
        except CollectionError as exc:
            # Without a key, Semantic Scholar may refuse with 429: that, and nothing else.
            assert exc.kind == "transient" and "HTTP 429" in str(exc), exc
            return
        assert len(records) == 2
        assert all(r["title"] is not None for r in records)


# --- arXiv (XML, can be slow) ---


@live
class TestArxivLive:
    def test_xml_search(self) -> None:
        """arXiv returns XML, parsed via xmltodict. Skips on timeout."""
        source = SourceEntry(name="arxiv", url="https://export.arxiv.org/api")
        entry = collector_entry(
            name="test",
            type="api",
            source="arxiv",
            endpoint="/query",
            params={"search_query": "attention mechanism", "max_results": "2"},
            response_format="xml",
            extract="feed.entry",
            normalize={
                "title": "title",
                "authors": "author.*.name",
                "published": "published",
            },
        )
        try:
            records = _api_records(entry, source)
            assert len(records) == 2
            assert all(r["title"] is not None for r in records)
            # Authors should be a list
            assert isinstance(records[0]["authors"], list)
        except CollectionError:
            pytest.skip("arXiv API timed out (known to be slow)")


# --- Zenodo (HTML descriptions) ---


@live
class TestZenodoLive:
    def test_html2text_transform(self) -> None:
        """Zenodo descriptions are HTML, $html2text strips tags."""
        source = SourceEntry(
            name="zenodo",
            url="https://zenodo.org/api",
            rate_limit=RateLimitConfig(rps=1, burst=2),
            user_agent=DEFAULT_USER_AGENT,
        )
        entry = collector_entry(
            name="test",
            type="api",
            source="zenodo",
            endpoint="/records",
            params={"q": "machine learning", "size": "2", "sort": "bestmatch"},
            extract="hits.hits",
            normalize={
                "title": "metadata.title",
                "abstract": "metadata.description|$html2text",
                "authors": "metadata.creators.*.name",
            },
        )
        records = _api_records(entry, source)
        assert len(records) == 2
        assert all(r["title"] is not None for r in records)
        # Abstract should be plain text (no HTML tags)
        for r in records:
            if r["abstract"]:
                assert "<p>" not in r["abstract"]
                assert "</" not in r["abstract"]


# --- Fan-out with real local directories ---


class TestFanOutOverLocalDirectories:
    def test_cli_fan_out_with_cwd(self, tmp_path: Path) -> None:
        """Two local sources, one collector, fan-out verifies cwd."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        repo_a.mkdir()
        repo_b.mkdir()
        (repo_a / "hello.txt").write_text("from repo a")
        (repo_b / "hello.txt").write_text("from repo b")

        config = ReconConfig.model_validate(
            {
                "catalog": [
                    {"name": "a", "type": "local", "url": str(repo_a)},
                    {"name": "b", "type": "local", "url": str(repo_b)},
                ],
                "collectors": [
                    {"name": "read", "type": "cli", "run": "cat hello.txt"},
                ],
            }
        )
        archive, _ = run(config, tmp_path)

        a_lines = (archive / "read-a.jsonl").read_text().splitlines()
        b_lines = (archive / "read-b.jsonl").read_text().splitlines()
        a_records = [json.loads(line) for line in a_lines]
        b_records = [json.loads(line) for line in b_lines]
        assert "from repo a" in a_records[0]["line"]
        assert "from repo b" in b_records[0]["line"]

    def test_mixed_survey_with_errors(self, tmp_path: Path) -> None:
        """One source succeeds, another uses a command that hard-fails."""
        good = tmp_path / "good"
        good.mkdir()
        (good / "data.txt").write_text("found")

        config = ReconConfig.model_validate(
            {
                "catalog": [
                    {"name": "good", "type": "local", "url": str(good)},
                    {"name": "bad", "type": "local", "url": str(good)},
                ],
                "collectors": [
                    {"name": "ok", "type": "cli", "run": "cat data.txt", "source": "good"},
                    {"name": "fail", "type": "cli", "run": "exit 2", "source": "bad"},
                ],
            }
        )
        archive, _ = run(config, tmp_path)

        assert (archive / "ok.jsonl").exists()
        import yaml

        meta = yaml.safe_load((archive / "meta.yaml").read_text())
        statuses = {t["name"]: t["status"] for t in meta["tables"]}
        assert statuses["ok"] == "ok"
        assert statuses["fail"] == "error"
