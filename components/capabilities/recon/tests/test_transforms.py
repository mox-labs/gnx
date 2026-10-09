"""Tests for transforms — glom-based path resolution + normalize specs."""

import pytest

from recon.application.transforms import (
    BUILTIN_TRANSFORMS,
    apply_normalize,
    first,
    html2text,
    inverted_index,
    join,
    resolve_path,
    spec_problems,
)
from recon.domain.exceptions import ConfigError


class TestHtml2Text:
    def test_basic(self):
        assert html2text("<p>Hello</p>") == "Hello"

    def test_strips_scripts(self):
        html = "<p>Keep</p><script>remove();</script><p>Also keep</p>"
        result = html2text(html)
        assert "Keep" in result
        assert "Also keep" in result
        assert "remove" not in result

    def test_empty(self):
        assert html2text("") == ""
        assert html2text(None) == ""


class TestInvertedIndex:
    def test_basic(self):
        idx = {"hello": [0], "world": [1]}
        assert inverted_index(idx) == "hello world"

    def test_repeated_words(self):
        idx = {"the": [0, 3], "cat": [1], "sat": [2]}
        assert inverted_index(idx) == "the cat sat the"

    def test_empty(self):
        assert inverted_index({}) == ""
        assert inverted_index(None) == ""


class TestJoin:
    def test_basic(self):
        assert join(["a", "b", "c"]) == "a, b, c"

    def test_empty(self):
        assert join([]) == ""
        assert join(None) == ""


class TestFirst:
    def test_basic(self):
        assert first([1, 2, 3]) == 1

    def test_empty(self):
        assert first([]) is None
        assert first(None) is None


class TestResolvePath:
    def test_direct(self):
        assert resolve_path({"title": "Test"}, "title") == "Test"

    def test_nested(self):
        data = {"openAccessPdf": {"url": "https://example.com/paper.pdf"}}
        assert resolve_path(data, "openAccessPdf.url") == "https://example.com/paper.pdf"

    def test_list_map(self):
        data = {"authors": [{"name": "Alice"}, {"name": "Bob"}]}
        assert resolve_path(data, "authors.*.name") == ["Alice", "Bob"]

    def test_deep_list_map(self):
        data = {
            "authorships": [
                {"author": {"display_name": "Alice"}},
                {"author": {"display_name": "Bob"}},
            ]
        }
        assert resolve_path(data, "authorships.*.author.display_name") == ["Alice", "Bob"]

    def test_missing_field(self):
        assert resolve_path({"a": 1}, "b") is None
        assert resolve_path({"a": 1}, "a.b") is None

    def test_none_data(self):
        assert resolve_path(None, "x") is None


class TestApplyNormalize:
    def test_s2_shape(self):
        """Simulates Semantic Scholar API response normalization."""
        raw = {
            "title": "Attention Is All You Need",
            "abstract": "The dominant...",
            "authors": [{"name": "Vaswani"}, {"name": "Shazeer"}],
            "year": 2017,
            "citationCount": 90000,
            "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
            "venue": "NeurIPS",
        }
        spec = {
            "title": "title",
            "abstract": "abstract",
            "authors": "authors.*.name",
            "year": "year",
            "citations": "citationCount",
            "pdf_url": "openAccessPdf.url",
            "venue": "venue",
        }
        result = apply_normalize(raw, spec)
        assert result["title"] == "Attention Is All You Need"
        assert result["authors"] == ["Vaswani", "Shazeer"]
        assert result["citations"] == 90000
        assert result["pdf_url"] == "https://arxiv.org/pdf/1706.03762"

    def test_openalex_shape(self):
        """Simulates OpenAlex API response normalization with $inverted_index."""
        raw = {
            "display_name": "Test Paper",
            "abstract_inverted_index": {"hello": [0], "world": [1]},
            "authorships": [{"author": {"display_name": "Alice"}}],
            "publication_year": 2024,
            "cited_by_count": 42,
        }
        spec = {
            "title": "display_name",
            "abstract": "abstract_inverted_index|$inverted_index",
            "authors": "authorships.*.author.display_name",
            "year": "publication_year",
            "citations": "cited_by_count",
        }
        result = apply_normalize(raw, spec)
        assert result["title"] == "Test Paper"
        assert result["abstract"] == "hello world"
        assert result["authors"] == ["Alice"]

    def test_zenodo_shape(self):
        """Simulates Zenodo normalization with $html2text."""
        raw = {
            "metadata": {
                "title": "Dataset",
                "description": "<p>A <b>dataset</b> for testing.</p>",
                "creators": [{"name": "Bob"}],
            },
        }
        spec = {
            "title": "metadata.title",
            "abstract": "metadata.description|$html2text",
            "authors": "metadata.creators.*.name",
        }
        result = apply_normalize(raw, spec)
        assert result["title"] == "Dataset"
        assert result["abstract"] == "A dataset for testing."
        assert result["authors"] == ["Bob"]

    def test_missing_transform_raises(self):
        """An unknown transform is a config error, never a silently skipped step (it was
        skipped before 0.9.0, so a typo produced untransformed data with no signal)."""
        with pytest.raises(ConfigError, match=r"normalize\.x: unknown transform \$nonexistent"):
            apply_normalize({"x": "hello"}, {"x": "x|$nonexistent"})

    def test_uses_the_transforms_it_is_given(self):
        result = apply_normalize({"x": "a"}, {"x": "x|$shout"}, {"shout": str.upper})
        assert result["x"] == "A"


class TestSpecProblems:
    def test_sound_spec_has_none(self):
        spec = {"t": "title", "a": "authors.*.name|$join", "b": "x|$html2text"}
        assert spec_problems(spec, BUILTIN_TRANSFORMS) == {}

    def test_every_problem_is_reported_by_column(self):
        spec = {
            "ok": "title",
            "typo": "abstract|$html2txt",
            "nested": "a.*.b.*.c",
            "no_dollar": "x|join",
            "not_a_string": 3,
        }
        problems = spec_problems(spec, BUILTIN_TRANSFORMS)
        assert set(problems) == {"typo", "nested", "no_dollar", "not_a_string"}
        assert "unknown transform $html2txt" in problems["typo"]
        assert "$html2text" in problems["typo"], "the message lists what is installed"
        assert "Nested list-maps" in problems["nested"]


class TestMarkitdown:
    """Test $markitdown transform via markitdown (registered under recon.transforms).

    markitdown handles PDF, DOCX, PPTX, XLSX, EPub, CSV, JSON, XML, ZIP, HTML,
    images (OCR), audio. Tests cover the roundtrip + the best-effort failure
    contract (missing file returns empty string, does not raise).
    """

    def test_roundtrip_csv(self, tmp_path):
        """Create a simple CSV, convert to markdown via $markitdown."""
        from recon.adapters._out.markitdown_converter import markitdown_path

        csv_path = tmp_path / "data.csv"
        csv_path.write_text("name,city\nAlice,Paris\nBob,Berlin\n")

        text = markitdown_path(str(csv_path))
        # markitdown converts CSV to a markdown table — check for cell values
        assert "Alice" in text
        assert "Paris" in text

    def test_missing_file_returns_empty(self):
        """Non-existent file returns empty string (best-effort contract)."""
        from recon.adapters._out.markitdown_converter import markitdown_path

        assert markitdown_path("/nonexistent/file.pdf") == ""
