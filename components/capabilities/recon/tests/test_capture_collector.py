"""The capture collector normalizes bodies another process fetched; it fetches nothing."""

import json
import tempfile
from pathlib import Path

import pytest

from recon.adapters._out.capture_collector import CaptureCollector, body_format
from recon.adapters._out.capture_files import FilesystemCaptureLog
from recon.adapters._out.cli_collector import CliCollector
from recon.adapters._out.system_clock import SystemClock
from recon.application.captures import record_hook_payload
from recon.domain.errors import CollectionError, ConfigError
from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
from tests.recon_fakes import run_survey as run


def _log(mission: Path) -> FilesystemCaptureLog:
    return FilesystemCaptureLog(mission / "captures", SystemClock())


def _external(log, tool, response):
    return record_hook_payload(
        log, {"tool_name": tool, "tool_input": {"q": "x"}, "tool_response": response}
    )


def _collect(mission, **entry):
    collector = CaptureCollector(mission)
    return list(collector.collect(CollectorEntry(name="c", type="capture", **entry), None))


def test_reads_the_missions_external_log_by_default(tmp_path):
    _external(_log(tmp_path), "mcp__exa__search", {"results": [{"title": "A"}, {"title": "B"}]})
    records = _collect(tmp_path, extract="results", normalize={"t": "title"})
    assert records == [{"t": "A"}, {"t": "B"}]


def test_normalize_can_name_the_capture_line_but_raw_records_stay_raw(tmp_path):
    _external(_log(tmp_path), "mcp__exa__search", {"title": "A"})
    spec = {"t": "title", "tool": "_capture.collector", "q": "_capture.request.tool_input.q"}
    assert _collect(tmp_path, normalize=spec) == [{"t": "A", "tool": "mcp__exa__search", "q": "x"}]
    assert _collect(tmp_path) == [{"title": "A"}], "no normalize: nothing injected"


def test_match_filters_by_glob(tmp_path):
    log = _log(tmp_path)
    _external(log, "mcp__exa__search", {"title": "exa"})
    _external(log, "mcp__github__issues", {"title": "gh"})
    _external(log, "mcp__exa__crawl", {"title": "exa-crawl"})

    titles = [r["title"] for r in _collect(tmp_path, match={"collector": "mcp__exa__*"})]
    assert titles == ["exa", "exa-crawl"]
    assert [r["title"] for r in _collect(tmp_path, match={"source": "github"})] == ["gh"]
    assert _collect(tmp_path, match={"kind": "http"}) == []


def test_text_bodies_parse_as_lines(tmp_path):
    _external(_log(tmp_path), "t", 'line one\n{"a": 1}\n')
    assert _collect(tmp_path) == [{"line_number": 1, "line": "line one"}, {"a": 1}]


def test_explicit_response_format_overrides_content_type(tmp_path):
    _external(_log(tmp_path), "t", '{"items": [{"k": 1}]}')  # a JSON string, stored as text
    assert _collect(tmp_path, response_format="json", extract="items") == [{"k": 1}]


def test_xml_bodies_parse_by_content_type(tmp_path):
    log = _log(tmp_path)
    log.record(
        collector="feed",
        source=None,
        kind="http",
        request={},
        status=200,
        content_type="application/atom+xml",
        headers={},
        body=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title></entry></feed>',
    )
    assert _collect(tmp_path, extract="feed.entry", normalize={"t": "title"}) == [{"t": "T"}]


_ARXIV_FEED = b"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <title>Mamba</title>
    <arxiv:primary_category term="cs.LG"/>
  </entry>
</feed>"""


def test_arxiv_namespaced_keys_have_one_colon(tmp_path):
    """Regression: the prefixes ended in a colon and xmltodict adds its own, so keys came
    out ``arxiv::primary_category`` and the documented path matched nothing."""
    _log(tmp_path).record(
        collector="arxiv",
        source=None,
        kind="http",
        request={},
        status=200,
        content_type="application/atom+xml",
        headers={},
        body=_ARXIV_FEED,
    )
    spec = {"title": "title", "venue": "arxiv:primary_category.@term"}
    assert _collect(tmp_path, extract="feed.entry", normalize=spec) == [
        {"title": "Mamba", "venue": "cs.LG"}
    ]
    [feed] = _collect(tmp_path, extract="feed")
    assert feed["opensearch:totalResults"] == "1"


def test_a_capture_line_without_an_id_names_its_file_and_line(tmp_path):
    """Regression: a line without ``id`` raised KeyError, so the table failed as kind
    ``unknown`` with the message ``'id'``."""
    _external(_log(tmp_path), "t", {"title": "A"})
    log = tmp_path / "captures" / "captures.jsonl"
    line = json.loads(log.read_text())
    del line["id"]
    log.write_text(json.dumps(line) + "\n")

    with pytest.raises(CollectionError) as excinfo:
        _collect(tmp_path)
    error = excinfo.value
    assert error.kind == "run_failed"
    assert f"{log}:1" in str(error) and "'id'" in str(error)
    assert error.fix


@pytest.mark.parametrize(
    ("ct", "fmt"),
    [
        ("application/json", "json"),
        ("application/vnd.api+json; charset=utf-8", "json"),
        ("text/xml", "xml"),
        ("text/plain", "text"),
        ("", "text"),
    ],
)
def test_body_format(ct, fmt):
    assert body_format(ct) == fmt


def test_fan_out_keeps_each_sources_captures(tmp_path):
    log = _log(tmp_path)
    _external(log, "mcp__exa__search", {"title": "exa"})
    _external(log, "mcp__github__issues", {"title": "gh"})
    collector = CaptureCollector(tmp_path)
    entry = CollectorEntry(name="c", type="capture")
    source = SourceEntry(name="github", type="local", url=str(tmp_path))
    assert [r["title"] for r in collector.collect(entry, source)] == ["gh"]


def test_glob_over_archive_logs(tmp_path):
    for ts in ("2026-01-01", "2026-01-02"):
        log = FilesystemCaptureLog(tmp_path / "archive" / ts, SystemClock())
        _external(log, "t", {"day": ts})
    records = _collect(tmp_path, path="archive/*/captures.jsonl")
    assert [r["day"] for r in records] == ["2026-01-01", "2026-01-02"]


def test_no_log_is_an_error_not_an_empty_table(tmp_path):
    with pytest.raises(CollectionError, match="no capture log matches"):
        _collect(tmp_path)


def test_unknown_transform_raises(tmp_path):
    _external(_log(tmp_path), "t", {"a": 1})
    with pytest.raises(ConfigError):
        _collect(tmp_path, normalize={"a": "a|$nope"})


def test_renormalize_a_survey_without_refetching():
    """A survey with preserve_raw, then a capture collector over its log: same records,
    reshaped by a new spec, with no command run the second time."""
    with tempfile.TemporaryDirectory() as tmp:
        mission = Path(tmp)
        first = ReconConfig.model_validate(
            {
                "preserve_raw": True,
                "collectors": [
                    {"name": "src", "type": "cli", "run": 'printf \'{"v": 1}\\n{"v": 2}\\n\''}
                ],
            }
        )
        archive, _ = run(first, {"cli": CliCollector()}, mission)
        again = ReconConfig.model_validate(
            {
                "collectors": [
                    {
                        "name": "renorm",
                        "type": "capture",
                        "path": f"archive/{archive.name}/captures.jsonl",
                        "match": {"kind": "cli"},
                        "normalize": {"value": "v"},
                    }
                ]
            }
        )
        second, results = run(again, {"capture": CaptureCollector(mission)}, mission)
        lines = (second / "renorm.jsonl").read_text().splitlines()
    assert results[0].status == "ok"
    assert [json.loads(x) for x in lines] == [{"value": 1}, {"value": 2}]
