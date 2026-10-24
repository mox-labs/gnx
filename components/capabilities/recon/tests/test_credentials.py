"""One credential grammar for every source (ruling A1, vector's REQ): an ``api`` (or ``sse``)
source's ``auth.token`` is ``env:NAME`` or ``file:PATH``, or its ``auth.headers_helper`` prints
its headers, as an ``mcp`` source's ``token`` and ``headers_helper`` are, through the same
code. A ``file:`` that its group or others can read is refused, for every kind. A credential
a helper gives goes to its source's origin only."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml
from pydantic import ValidationError

from recon.adapters._out.credentials import resolve_reference
from recon.adapters._out.http_requester import HttpRequester
from recon.application.plan import build_plan
from recon.composition import load_plugins, root
from recon.domain.errors import CollectionError
from recon.domain.models import AuthConfig, SourceEntry
from recon.domain.ratelimit import RateLimiter
from tests.recon_fakes import FixedClock
from tests.test_paginated_survey import BASE, PagedApi


def _secret(path: Path, text: str, mode: int = 0o600) -> Path:
    path.write_text(text + "\n")
    path.chmod(mode)
    return path


def _survey(
    project: Path, api: PagedApi, auth: dict[str, Any], env: dict[str, str], **top: Any
) -> Any:
    mission = {
        **top,
        "catalog": [{"name": "api", "url": BASE, "auth": auth}],
        "collectors": [
            {"name": "items", "type": "api", "source": "api", "endpoint": "/items",
             "extract": "items",
             "paginate": {"type": "cursor", "next": "meta.next", "param": "cursor"}}
        ],
    }  # fmt: skip
    (project / ".recon" / "m").mkdir(parents=True, exist_ok=True)
    (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))
    recon = root(env, project=project, requester=lambda telemetry, deadline, limiter: api)
    return recon.survey("m")


class TestTheGrammar:
    def test_a_token_is_a_reference(self) -> None:
        assert AuthConfig(token="env:K", header="x-api-key").token == "env:K"
        assert AuthConfig(token="file:key.txt", param="key").token == "file:key.txt"
        with pytest.raises(ValidationError, match="reference"):
            AuthConfig(token="sk-live-123", header="x-api-key")

    def test_a_token_or_a_helper_not_both(self) -> None:
        with pytest.raises(ValidationError, match="not both"):
            AuthConfig(token="env:K", header="x", headers_helper="print-headers")

    @pytest.mark.parametrize("key", ["header", "param", "prefix"])
    def test_a_helper_prints_its_own_headers(self, key: str) -> None:
        with pytest.raises(ValidationError, match=key):
            AuthConfig(headers_helper="print-headers", **{key: "x"})

    def test_the_old_env_key_is_unknown_and_the_plan_names_token(self) -> None:
        mission = {
            "catalog": [{"name": "s", "url": BASE, "auth": {"env": "K", "header": "x"}}],
            "collectors": [{"name": "c", "type": "api", "source": "s", "endpoint": "/q"}],
        }
        [problem] = build_plan(mission, load_plugins(), {}).problems
        assert (problem.path, problem.kind) == ("catalog.0.auth.env", "unknown_key")
        assert "token" in problem.allowed and "headers_helper" in problem.allowed

    def test_an_unset_variable_is_warned_at_the_token(self) -> None:
        mission = {
            "catalog": [{"name": "s", "url": BASE, "auth": {"token": "env:K", "header": "x"}}],
            "collectors": [{"name": "c", "type": "api", "source": "s", "endpoint": "/q"}],
        }
        plan = build_plan(mission, load_plugins(), {})
        [warning] = plan.warnings
        assert (warning.path, warning.kind, warning.got) == (
            "catalog.0.auth.token",
            "env_unset",
            "K",
        )

    def test_the_plan_shows_the_credential_redacted_and_the_helper_by_command(self) -> None:
        mission = {
            "catalog": [
                {"name": "a", "url": BASE, "auth": {"token": "file:k", "header": "x-api-key"}},
                {"name": "b", "url": BASE, "auth": {"headers_helper": "print-headers"}},
            ],
            "collectors": [{"name": "c", "type": "api", "endpoint": "/q"}],
        }
        runs = {r.output: r.document() for r in build_plan(mission, load_plugins(), {}).runs}
        assert runs["c-a"]["headers"]["x-api-key"] == "REDACTED"
        assert runs["c-b"]["headers_helper"] == "print-headers"


class TestFiles:
    def test_a_file_the_owner_alone_reads_gives_its_credential(self, tmp_path: Path) -> None:
        _secret(tmp_path / "key", "s3cret")
        assert resolve_reference("file:key", {}, tmp_path, "auth.token") == "s3cret"

    @pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
    def test_a_file_its_group_or_others_can_read_is_refused(
        self, tmp_path: Path, mode: int
    ) -> None:
        _secret(tmp_path / "key", "s3cret", mode)
        with pytest.raises(CollectionError) as refused:
            resolve_reference("file:key", {}, tmp_path, "auth.token")
        assert refused.value.kind == "config"
        assert f"chmod 600 {tmp_path / 'key'}" in (refused.value.fix or "")
        assert "s3cret" not in str(refused.value)

    def test_a_missing_file_is_a_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(CollectionError) as missing:
            resolve_reference("file:nowhere", {}, tmp_path, "auth.token")
        assert missing.value.kind == "config"

    def test_an_environment_variable_unset_gives_none(self, tmp_path: Path) -> None:
        assert resolve_reference("env:K", {}, tmp_path, "auth.token") is None
        assert resolve_reference("env:K", {"K": "v"}, tmp_path, "auth.token") == "v"


class TestAnApiSourceSendsIt:
    def test_a_token_from_a_file_goes_as_its_header_on_every_page(self, tmp_path: Path) -> None:
        mission_dir = tmp_path / ".recon" / "m"
        mission_dir.mkdir(parents=True)
        _secret(mission_dir / "key.txt", "fil3key")
        api = PagedApi("cursor", 3, 1)
        auth = {"token": "file:key.txt", "header": "Authorization", "prefix": "Bearer "}
        document = _survey(tmp_path, api, auth, {})

        assert document.status == "complete", document.tables
        assert [s["headers"]["Authorization"] for s in api.sent] == ["Bearer fil3key"] * 3

    def test_a_group_readable_file_fails_the_table_as_config(self, tmp_path: Path) -> None:
        mission_dir = tmp_path / ".recon" / "m"
        mission_dir.mkdir(parents=True)
        _secret(mission_dir / "key.txt", "fil3key", 0o640)
        api = PagedApi("cursor", 3, 1)
        document = _survey(tmp_path, api, {"token": "file:key.txt", "header": "x-api-key"}, {})

        [table] = document.tables
        assert table.status == "error" and table.error.kind == "config"
        assert api.sent == [], "nothing is sent without the credential the mission named"

    def test_a_helper_s_headers_go_with_every_page_and_it_runs_once(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        helper = tmp_path / "helper.sh"
        helper.write_text(
            f"#!/bin/sh\necho run >> {runs}\n"
            'echo \'{"Private-Token": "h3lper", "X-Org": "acme"}\'\n'
        )
        helper.chmod(0o755)
        api = PagedApi("cursor", 3, 1)
        document = _survey(tmp_path, api, {"headers_helper": str(helper)}, {})

        assert document.status == "complete", document.tables
        assert all(s["headers"]["Private-Token"] == "h3lper" for s in api.sent)
        assert all(s["headers"]["X-Org"] == "acme" for s in api.sent)
        assert runs.read_text().splitlines() == ["run"], "once a survey, for every page"

    def test_a_helper_that_fails_fails_the_table_as_auth(self, tmp_path: Path) -> None:
        api = PagedApi("cursor", 3, 1)
        document = _survey(tmp_path, api, {"headers_helper": "exit 3"}, {})
        [table] = document.tables
        assert table.status == "error" and table.error.kind == "auth"
        assert api.sent == []


def test_a_helper_s_header_does_not_follow_a_redirect_off_its_origin() -> None:
    """The requester drops every header it was given but its own (User-Agent, Accept, ...)
    on a hop to another origin: a helper's header names are its own, so none of them is
    known in advance, and each is a credential."""
    sent: list[tuple[str, dict[str, str]]] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        sent.append((str(request.url), dict(request.headers)))
        if request.url.path == "/start":
            return httpx2.Response(302, headers={"Location": "https://evil.example.net/x"})
        return httpx2.Response(200, json={"ok": True})

    clock = FixedClock()
    requester = HttpRequester(RateLimiter(clock), clock, transport=httpx2.MockTransport(answer))
    source = SourceEntry(name="s", url="https://api.example.test",
                         auth=AuthConfig(headers_helper="print-headers"))  # fmt: skip
    given = {"User-Agent": "recon-test", "Private-Token": "h3lper", "X-Org": "acme"}
    requester.request(source, "GET", "https://api.example.test/start", headers=given)

    assert sent[0][1]["private-token"] == "h3lper"
    assert "private-token" not in sent[1][1] and "x-org" not in sent[1][1]
    assert sent[1][1]["user-agent"] == "recon-test", "recon's own header goes on"


def test_an_mcp_token_file_is_held_to_the_same_rule(tmp_path: Path) -> None:
    """The one grammar is one code: an mcp source's ``token: file:`` is refused the same."""
    from recon.adapters._out import mcp_collector

    assert vars(mcp_collector)["resolve_reference"] is resolve_reference
    _secret(tmp_path / "t", "tok", 0o644)
    with pytest.raises(CollectionError):
        resolve_reference("file:t", {}, tmp_path, "token")


def test_no_secret_is_written_to_the_archive(tmp_path: Path) -> None:
    mission_dir = tmp_path / ".recon" / "m"
    mission_dir.mkdir(parents=True)
    _secret(mission_dir / "key.txt", "fil3keyZZ")
    api = PagedApi("cursor", 2, 1)
    auth = {"token": "file:key.txt", "param": "api_key"}
    document = _survey(tmp_path, api, auth, {}, preserve_raw=True)
    archive = Path(document.path)
    assert (archive / "captures.jsonl").is_file()
    for path in archive.rglob("*"):
        if path.is_file():
            assert b"fil3keyZZ" not in path.read_bytes(), path
    assert os.stat(mission_dir / "key.txt").st_mode & 0o077 == 0
    assert json.loads((archive / "items.pages.jsonl").read_text().splitlines()[0])["page"] == 0
