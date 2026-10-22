"""The normalize language: path resolution, specs, and the built-in transforms."""

import pytest

from recon.domain.errors import ConfigError
from recon.domain.normalize import (
    BUILTIN_TRANSFORMS,
    apply_normalize,
    first,
    html2text,
    inverted_index,
    join,
    nested_records,
    resolve_path,
    spec_problems,
)


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


class TestABuiltInGivenTheWrongTypeGivesNull:
    """Regression: a built-in given a value of a type it does not take raised (``'list'
    object has no attribute 'items'``), and one record failed the whole table as
    ``unknown``. A field of an unexpected type is a field the record lacks: null, as an
    unresolved path is, and the rest of the record and the table are kept."""

    @pytest.mark.parametrize(
        ("transform", "value"),
        [
            (html2text, ["<p>a</p>"]),
            (html2text, {"p": "a"}),
            (html2text, 42),
            (inverted_index, ["the", "cat"]),
            (inverted_index, "the cat"),
            (inverted_index, {"the": 0, "cat": [1]}),
            (inverted_index, {"the": ["0"]}),
            (join, 5),
            (join, "abc"),
            (join, {"a": 1}),
            (first, "abc"),
            (first, {"a": 1}),
            (first, 5),
        ],
    )
    def test_the_field_is_null(self, transform, value):
        assert transform(value) is None

    def test_one_record_of_the_wrong_type_does_not_fail_the_others(self):
        spec = {"id": "id", "abstract": "abstract_inverted_index|$inverted_index"}
        records = [
            {"id": 1, "abstract_inverted_index": {"a": [0], "cat": [1]}},
            {"id": 2, "abstract_inverted_index": ["a", "cat"]},
        ]
        assert [apply_normalize(r, spec) for r in records] == [
            {"id": 1, "abstract": "a cat"},
            {"id": 2, "abstract": None},
        ]


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

    def test_a_list_index(self):
        data = {"authors": [{"name": "Alice"}, {"name": "Bob"}]}
        assert resolve_path(data, "authors.1.name") == "Bob"
        assert resolve_path(data, "authors.2.name") is None

    def test_a_digit_is_a_key_in_an_object(self):
        assert resolve_path({"a": {"1": "one"}}, "a.1") == "one"

    def test_a_bare_star_is_the_list_or_the_object_s_values(self):
        assert resolve_path({"items": [1, 2]}, "items.*") == [1, 2]
        assert resolve_path({"x": {"y": 2}}, "*") == [{"y": 2}]
        assert resolve_path({"items": "text"}, "items.*") is None

    def test_a_list_map_with_an_item_missing_the_rest_is_none(self):
        """Pinned semantics: one item lacking the rest of the path makes the whole column
        None, not a list with a hole."""
        assert resolve_path({"a": [{"n": 1}, {}]}, "a.*.n") is None

    def test_a_second_star_is_refused(self):
        with pytest.raises(ValueError, match="Nested list-maps"):
            resolve_path({}, "a.*.b.*.c")


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
    images (OCR), audio. Tests cover the roundtrip, the best-effort failure
    contract (a missing file returns an empty string, does not raise), and the
    confinement: the value comes from collected data, so it may name only a file under
    the directories the mission declares, and never a URL.
    """

    @pytest.fixture
    def dirs(self, tmp_path):
        declared = tmp_path / "declared"
        outside = tmp_path / "outside"
        declared.mkdir()
        outside.mkdir()
        (declared / "data.csv").write_text("name,city\nAlice,Paris\nBob,Berlin\n")
        (outside / "secret.csv").write_text("key,value\ntoken,hunter2\n")
        return declared, outside

    def test_roundtrip_csv(self, dirs):
        """Create a simple CSV, convert to markdown via $markitdown."""
        from recon.adapters._out.markitdown_converter import markitdown_path

        declared, _outside = dirs
        text = markitdown_path(str(declared / "data.csv"), roots=[declared])
        # markitdown converts CSV to a markdown table — check for cell values
        assert "Alice" in text
        assert "Paris" in text

    def test_a_missing_file_fails_the_table_naming_it(self, dirs):
        """Changed requirement: a file $markitdown cannot read is an error, not an empty
        string, because an empty column reads as an empty document."""
        from recon.adapters._out.markitdown_converter import markitdown_path
        from recon.domain.errors import CollectionError

        declared, _outside = dirs
        with pytest.raises(CollectionError) as excinfo:
            markitdown_path(str(declared / "nonexistent.pdf"), roots=[declared])
        assert excinfo.value.kind == "run_failed"
        assert "nonexistent.pdf" in str(excinfo.value) and excinfo.value.fix

    def test_a_file_markitdown_cannot_convert_fails_the_table(self, dirs):
        from recon.adapters._out.markitdown_converter import markitdown_path
        from recon.domain.errors import CollectionError

        declared, _outside = dirs
        (declared / "blob.bin").write_bytes(b"\x00\x01\x02\xff\xfe")
        with pytest.raises(CollectionError) as excinfo:
            markitdown_path(str(declared / "blob.bin"), roots=[declared])
        assert excinfo.value.kind == "run_failed" and excinfo.value.fix

    def test_a_missing_converter_dependency_is_a_config_error(self, dirs, monkeypatch):
        import markitdown

        from recon.adapters._out.markitdown_converter import markitdown_path
        from recon.domain.errors import CollectionError

        def missing(self, path):
            raise markitdown.MissingDependencyException("pdfminer is not installed")

        monkeypatch.setattr(markitdown.MarkItDown, "convert_local", missing)
        declared, _outside = dirs
        with pytest.raises(CollectionError) as excinfo:
            markitdown_path(str(declared / "data.csv"), roots=[declared])
        assert excinfo.value.kind == "config" and "[all]" in excinfo.value.fix

    def test_a_defect_inside_markitdown_is_not_classified(self, dirs, monkeypatch):
        """Only markitdown's own errors and an unreadable file are classified; a bug
        propagates as itself."""
        import markitdown

        from recon.adapters._out.markitdown_converter import markitdown_path

        def broken(self, path):
            raise ZeroDivisionError

        monkeypatch.setattr(markitdown.MarkItDown, "convert_local", broken)
        with pytest.raises(ZeroDivisionError):
            markitdown_path(str(dirs[0] / "data.csv"), roots=[dirs[0]])

    @pytest.mark.parametrize("value", [None, "", 42, ["a.pdf"]])
    def test_a_value_that_is_not_a_path_returns_empty(self, dirs, value):
        from recon.adapters._out.markitdown_converter import markitdown_path

        assert markitdown_path(value, roots=[dirs[0]]) == ""

    def _refused(self, value, roots):
        from recon.adapters._out.markitdown_converter import markitdown_path
        from recon.domain.errors import CollectionError

        with pytest.raises(CollectionError) as excinfo:
            markitdown_path(value, roots=roots)
        assert excinfo.value.kind == "config"
        assert "hunter2" not in str(excinfo.value)
        return excinfo.value

    def test_a_file_outside_the_declared_directories_is_refused(self, dirs):
        declared, outside = dirs
        error = self._refused(str(outside / "secret.csv"), [declared])
        assert "local" in error.fix

    def test_dot_dot_does_not_escape(self, dirs):
        declared, _outside = dirs
        self._refused(str(declared / ".." / "outside" / "secret.csv"), [declared])

    def test_a_symlink_does_not_escape(self, dirs):
        declared, outside = dirs
        (declared / "link.csv").symlink_to(outside / "secret.csv")
        self._refused(str(declared / "link.csv"), [declared])

    def test_with_no_declared_directory_nothing_is_read(self, dirs):
        declared, _outside = dirs
        self._refused(str(declared / "data.csv"), [])

    @pytest.mark.parametrize(
        "uri",
        [
            "http://127.0.0.1:9/doc.html",
            "https://127.0.0.1:9/doc.pdf",
            "HTTPS://127.0.0.1:9/doc.pdf",
            "data:text/plain;base64,aHVudGVyMg==",
        ],
    )
    def test_a_uri_is_refused_not_fetched(self, dirs, uri):
        error = self._refused(uri, [dirs[0]])
        assert "web" in error.fix

    def test_a_relative_path_is_relative_to_the_directory_given_as_base(self, dirs, tmp_path):
        """A path a command printed (``rg --files``, ``find .``) is relative to where the
        command ran, which a ``cli`` collector binds as ``base``; without one it is the
        working directory's."""
        from recon.adapters._out.markitdown_converter import markitdown_path

        declared, _outside = dirs
        assert "Alice" in markitdown_path("data.csv", roots=[declared], base=declared)
        self._refused("../outside/secret.csv", [declared])

    def test_a_file_uri_is_refused_even_inside_a_declared_directory(self, dirs):
        declared, _outside = dirs
        self._refused((declared / "data.csv").as_uri(), [declared])


class TestAConvertedDocument:
    """The ``web`` collector's converter: bytes, the content type and the URL the server
    gave, in; markdown out."""

    def test_the_content_type_the_server_gave_is_honoured(self):
        """Regression: the converter ignored ``content_type`` and let markitdown guess from
        the bytes, so a server's ``text/plain`` reply that looked like CSV came back as a
        table."""
        from recon.adapters._out.markitdown_converter import MarkitdownConverter

        body = b"name,city\nAlice,Paris\n"
        converter = MarkitdownConverter()
        as_text = converter.convert(body, "text/plain", "https://x.test/data")
        as_csv = converter.convert(body, "text/csv", "https://x.test/data")
        assert as_text.text.strip() == "name,city\nAlice,Paris"
        assert "| name | city |" in as_csv.text


class TestNestedRecords:
    """What a survey looks for in a table's one record: the list of records it holds."""

    def test_the_longest_list_of_objects_wins(self):
        record = {"feed": {"links": [{"h": 1}, {"h": 2}], "entry": [{"i": 1}, {"i": 2}, {"i": 3}]}}
        assert nested_records(record) == ("feed.entry", 3)

    def test_a_list_of_scalars_or_of_one_object_is_not_records(self):
        assert nested_records({"tags": ["a", "b", "c"], "one": [{"a": 1}]}) is None

    def test_lists_are_not_searched_inside_and_depth_is_bounded(self):
        assert nested_records({"rows": [{"inner": [{"a": 1}, {"a": 2}]}]}) is None
        deep = {"a": {"b": {"c": {"d": {"e": [{"x": 1}, {"x": 2}]}}}}}
        assert nested_records(deep) is None
        assert nested_records(deep, depth=5) == ("a.b.c.d.e", 2)
