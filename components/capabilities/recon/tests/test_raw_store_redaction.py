"""A raw capture must not persist a live credential.

`meta.yaml` exists to prove what the server said, and it is a file people commit, attach
to tickets, and hand to colleagues. Every test here fails against the pre-fix code, which
wrote response headers through verbatim.
"""

from pathlib import Path

import yaml

from recon.application.raw_store import REDACTED, FilesystemRawStore, redact_headers


def _meta(tmp_path: Path, headers: dict[str, str]) -> dict:
    store = FilesystemRawStore(tmp_path)
    store.save_http(
        "src",
        b"{}",
        status=200,
        url="https://example.test/v1",
        headers=headers,
        content_type="application/json",
    )
    return yaml.safe_load((tmp_path / "raw" / "src" / "meta.yaml").read_text())


def test_set_cookie_value_never_reaches_disk(tmp_path: Path) -> None:
    meta = _meta(tmp_path, {"Set-Cookie": "session=SECRET-TOKEN; HttpOnly"})

    assert "SECRET-TOKEN" not in (tmp_path / "raw" / "src" / "meta.yaml").read_text()
    assert meta["headers"]["Set-Cookie"] == REDACTED


def test_redaction_keeps_the_key_so_the_capture_stays_evidence(tmp_path: Path) -> None:
    """Dropping the header would lose the fact that the server set one at all."""
    meta = _meta(tmp_path, {"Set-Cookie": "s=1", "Content-Type": "application/json"})

    assert set(meta["headers"]) == {"Set-Cookie", "Content-Type"}
    assert meta["headers"]["Content-Type"] == "application/json"


def test_header_matching_is_case_insensitive() -> None:
    """HTTP header names are case-insensitive; clients normalise them differently."""
    out = redact_headers({"SET-COOKIE": "a", "x-api-key": "b", "X-Api-Key": "c"})

    assert list(out.values()) == [REDACTED, REDACTED, REDACTED]


def test_ordinary_headers_pass_through_untouched() -> None:
    headers = {"Content-Type": "text/html", "ETag": 'W/"abc"', "Server": "nginx"}

    assert redact_headers(headers) == headers
