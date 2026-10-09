"""The capture log (recon.v1.capture): what it writes, what it refuses to trust on read.

The redaction tests carry forward the R-2 regression (SECURITY.md): a capture must not
persist a live credential. The log is a file people commit, attach to tickets, and hand
to colleagues.
"""

import hashlib
import json
from pathlib import Path

import pytest

from recon.application.captures import (
    REDACTED,
    FilesystemCaptureLog,
    read_captures,
    record_hook_payload,
    redact_headers,
)
from recon.domain.exceptions import CollectionError, ConfigError


def _record(log: FilesystemCaptureLog, body: bytes = b"{}", **overrides) -> dict:
    kwargs = {
        "collector": "src",
        "source": "s",
        "kind": "http",
        "request": {"method": "GET", "url": "https://example.test/v1"},
        "status": 200,
        "content_type": "application/json",
        "headers": {},
        "body": body,
    }
    kwargs.update(overrides)
    return log.record(**kwargs)


# --- R-2: credential-bearing response headers ---


def test_set_cookie_value_never_reaches_disk(tmp_path: Path) -> None:
    log = FilesystemCaptureLog(tmp_path)
    line = _record(log, headers={"Set-Cookie": "session=SECRET-TOKEN; HttpOnly"})

    assert "SECRET-TOKEN" not in log.log_path.read_text()
    assert line["headers"]["Set-Cookie"] == REDACTED


def test_redaction_keeps_the_key_so_the_capture_stays_evidence(tmp_path: Path) -> None:
    """Dropping the header would lose the fact that the server set one at all."""
    line = _record(
        FilesystemCaptureLog(tmp_path),
        headers={"Set-Cookie": "s=1", "Content-Type": "application/json"},
    )

    assert set(line["headers"]) == {"Set-Cookie", "Content-Type"}
    assert line["headers"]["Content-Type"] == "application/json"


def test_header_matching_is_case_insensitive() -> None:
    """HTTP header names are case-insensitive; clients normalise them differently."""
    out = redact_headers({"SET-COOKIE": "a", "x-api-key": "b", "X-Api-Key": "c"})

    assert list(out.values()) == [REDACTED, REDACTED, REDACTED]


def test_ordinary_headers_pass_through_untouched() -> None:
    headers = {"Content-Type": "text/html", "ETag": 'W/"abc"', "Server": "nginx"}

    assert redact_headers(headers) == headers


# --- the line format ---


def test_a_line_carries_every_documented_field(tmp_path: Path) -> None:
    log = FilesystemCaptureLog(tmp_path)
    line = _record(log, body=b'{"a": 1}')
    on_disk = json.loads(log.log_path.read_text())

    sha = hashlib.sha256(b'{"a": 1}').hexdigest()
    assert on_disk == line
    assert set(line) >= {
        "schema",
        "id",
        "source",
        "collector",
        "kind",
        "request",
        "status",
        "content_type",
        "headers",
        "body",
        "sha256",
        "captured_at",
    }
    assert line["schema"] == "recon.v1.capture"
    assert line["body"] == f"raw/{sha}" and line["sha256"] == sha
    assert (tmp_path / "raw" / sha).read_bytes() == b'{"a": 1}'


def test_identical_bodies_are_stored_once_and_logged_twice(tmp_path: Path) -> None:
    log = FilesystemCaptureLog(tmp_path)
    first = _record(log, body=b"same")
    second = _record(log, body=b"same")

    assert first["body"] == second["body"]
    assert first["id"] != second["id"]
    assert len(log.log_path.read_text().splitlines()) == 2
    assert [p.name for p in (tmp_path / "raw").iterdir()] == [first["sha256"]]


def test_append_only_across_log_instances(tmp_path: Path) -> None:
    _record(FilesystemCaptureLog(tmp_path), body=b"1")
    _record(FilesystemCaptureLog(tmp_path), body=b"2")

    bodies = [body for _line, body in read_captures(tmp_path / "captures.jsonl")]
    assert bodies == [b"1", b"2"]


def test_a_stream_is_hashed_and_moved_on_finalize(tmp_path: Path) -> None:
    log = FilesystemCaptureLog(tmp_path)
    path = log.stream_path("cmd")
    path.write_text("line 1\nline 2\n")
    line = log.finalize_stream(
        "cmd", source=None, kind="cli", request={}, status=0, content_type="text/plain"
    )

    assert line is not None
    assert not path.exists(), "the temp stream file is moved under its hash"
    assert (tmp_path / line["body"]).read_text() == "line 1\nline 2\n"
    assert (
        log.finalize_stream(
            "cmd", source=None, kind="cli", request={}, status=0, content_type="text/plain"
        )
        is None
    ), "a second finalize for the same collector logs nothing"


# --- reading: the format is written by other tools, so the reader verifies ---


def _write_log(tmp_path: Path, line: dict, body: bytes | None = b"x") -> Path:
    if body is not None:
        (tmp_path / "raw").mkdir(exist_ok=True)
        (tmp_path / "raw" / hashlib.sha256(body).hexdigest()).write_bytes(body)
    log = tmp_path / "captures.jsonl"
    log.write_text(json.dumps(line) + "\n")
    return log


def _line(content: bytes = b"x", **overrides) -> dict:
    sha = hashlib.sha256(content).hexdigest()
    line = {
        "schema": "recon.v1.capture",
        "id": "1",
        "collector": "c",
        "kind": "external",
        "body": f"raw/{sha}",
        "sha256": sha,
    }
    line.update(overrides)
    return line


def test_reader_ignores_unknown_fields(tmp_path: Path) -> None:
    log = _write_log(tmp_path, _line(future_field={"x": 1}))
    assert [body for _l, body in read_captures(log)] == [b"x"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"schema": "recon.v2.capture"}, "major version"),
        ({"schema": "other.v1.thing"}, "is not recon.v1.capture"),
        ({"body": "../../etc/passwd"}, "body must be 'raw/<sha256>'"),
        ({"body": "/etc/passwd"}, "body must be 'raw/<sha256>'"),
        ({"sha256": "0" * 64}, "does not match its sha256"),
    ],
)
def test_reader_refuses_what_it_cannot_trust(tmp_path: Path, override, message) -> None:
    log = _write_log(tmp_path, _line(**override))
    with pytest.raises(CollectionError, match=message):
        list(read_captures(log))


def test_reader_refuses_a_tampered_body(tmp_path: Path) -> None:
    line = _line(b"original")
    log = _write_log(tmp_path, line, body=None)
    (tmp_path / "raw").mkdir()
    (tmp_path / line["body"]).write_bytes(b"tampered")
    with pytest.raises(CollectionError, match="does not match"):
        list(read_captures(log))


def test_reader_names_the_file_and_line_of_bad_json(tmp_path: Path) -> None:
    log = tmp_path / "captures.jsonl"
    log.write_text("\n{not json\n")
    with pytest.raises(CollectionError, match=r"captures\.jsonl:2: not JSON"):
        list(read_captures(log))


# --- Claude Code PostToolUse payloads ---


def _payload(**overrides) -> dict:
    payload = {
        "session_id": "abc123",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": "/repo",
        "permission_mode": "default",
        "hook_event_name": "PostToolUse",
        "tool_name": "mcp__exa__web_search_exa",
        "tool_input": {"query": "state space models"},
        "tool_response": {"results": [{"title": "Mamba", "url": "https://x"}]},
        "tool_use_id": "toolu_01",
        "duration_ms": 12,
    }
    payload.update(overrides)
    return payload


def test_hook_payload_becomes_an_external_capture(tmp_path: Path) -> None:
    log = FilesystemCaptureLog(tmp_path)
    line = record_hook_payload(log, _payload())

    assert line["kind"] == "external"
    assert line["collector"] == "mcp__exa__web_search_exa"
    assert line["source"] == "exa", "the server, parsed from mcp__<server>__<tool>"
    assert line["status"] is None
    assert line["content_type"] == "application/json"
    assert line["request"]["tool_input"] == {"query": "state space models"}
    assert line["request"]["tool_use_id"] == "toolu_01"
    assert "tool_response" not in line["request"]
    [(_l, body)] = list(read_captures(log.log_path))
    assert json.loads(body) == {"results": [{"title": "Mamba", "url": "https://x"}]}


def test_hook_payload_prefers_the_mcp_server_object(tmp_path: Path) -> None:
    payload = _payload(mcp_server={"name": "plugin_research_exa", "source": "plugin"})
    line = record_hook_payload(FilesystemCaptureLog(tmp_path), payload)
    assert line["source"] == "plugin_research_exa"


def test_hook_string_response_is_stored_as_text(tmp_path: Path) -> None:
    line = record_hook_payload(FilesystemCaptureLog(tmp_path), _payload(tool_response="plain"))
    assert line["content_type"] == "text/plain"
    assert (tmp_path / line["body"]).read_bytes() == b"plain"


@pytest.mark.parametrize("payload", [[], "x", {"tool_name": "t"}, {"tool_response": {}}])
def test_hook_payload_without_required_fields_is_a_config_error(tmp_path: Path, payload) -> None:
    with pytest.raises(ConfigError):
        record_hook_payload(FilesystemCaptureLog(tmp_path), payload)
    assert not (tmp_path / "captures.jsonl").exists()
