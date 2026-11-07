"""Every built-in collector type passes the conformance kit (``recon.testing``), as a third
party's must (review C7, migration M5): each is checked by its behaviour, through the needs an
act hands it, against a source that reaches nothing outside this host (a scripted requester, a
scripted shell, a capture log, a stdio MCP server, a file, a loopback event stream)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from recon.adapters._out.capture_files import FilesystemCaptureLog, MissionCaptureLogs
from recon.adapters._out.credentials import Credentials
from recon.adapters._out.http_requester import HttpRequester
from recon.adapters._out.system_clock import SystemClock
from recon.core.domain.ratelimit import RateLimiter
from recon.testing import CollectorConformance
from tests.recon_fakes import FixedClock, ScriptedRequester, ScriptedShell
from tests.sse_server import SseServer

if TYPE_CHECKING:
    from collections.abc import Iterator

MCP_SERVER = str(Path(__file__).parent / "mcp_server.py")


class TestApiConforms(CollectorConformance):
    type_name = "api"
    BODY: ClassVar[str] = json.dumps({"data": [{"id": 1}, {"id": 2}], "meta": {"next": None}})

    def entry(self) -> dict[str, Any]:
        return {"name": "q", "type": "api", "source": "s", "endpoint": "/q", "extract": "data"}

    def source(self) -> dict[str, Any] | None:
        return {"name": "s", "url": "https://api.example.test"}

    def needs(self) -> dict[str, Any]:
        requester = ScriptedRequester({"https://api.example.test/q": self.BODY})
        return {"requester": requester, "credentials": Credentials({})}

    def paged_entry(self) -> dict[str, Any] | None:
        return {**self.entry(), "paginate": {"type": "cursor", "next": "meta.next",
                                             "param": "cursor"}}  # fmt: skip


class TestCliConforms(CollectorConformance):
    type_name = "cli"

    def entry(self) -> dict[str, Any]:
        return {"name": "c", "type": "cli", "run": "listing"}

    def needs(self) -> dict[str, Any]:
        return {"shell": ScriptedShell({"listing": ['{"a": 1}', "plain text"]})}


class TestCaptureConforms(CollectorConformance):
    type_name = "capture"

    @pytest.fixture(autouse=True)
    def _logged(self, tmp_path: Path) -> None:
        log = FilesystemCaptureLog(tmp_path / "captures", FixedClock())
        log.record(collector="tool", source="here", kind="external", request={}, status=None,
                   content_type="application/json", headers={},
                   body=json.dumps({"results": [{"id": 1}]}).encode())  # fmt: skip
        self._mission = tmp_path

    def entry(self) -> dict[str, Any]:
        return {"name": "again", "type": "capture", "extract": "results"}

    def needs(self) -> dict[str, Any]:
        return {"capture_logs": MissionCaptureLogs(self._mission)}


class TestMcpConforms(CollectorConformance):
    type_name = "mcp"

    def entry(self) -> dict[str, Any]:
        return {"name": "who", "type": "mcp", "source": "fixture", "tool": "whoami"}

    def source(self) -> dict[str, Any] | None:
        return {"name": "fixture", "type": "mcp", "command": sys.executable,
                "args": [MCP_SERVER], "rate_limit": {"rps": 100, "burst": 10}}  # fmt: skip

    def needs(self) -> dict[str, Any]:
        return {"credentials": Credentials({})}


class TestTailConforms(CollectorConformance):
    type_name = "tail"

    @pytest.fixture(autouse=True)
    def _file(self, tmp_path: Path) -> None:
        self._log = tmp_path / "app.log"
        self._log.write_text('{"n": 1}\n{"n": 2}\nplain\n')

    def entry(self) -> dict[str, Any]:
        return {"name": "log", "type": "tail", "source": "app", "start": "beginning"}

    def source(self) -> dict[str, Any] | None:
        return {"name": "app", "type": "file", "url": str(self._log)}


class TestSseConforms(CollectorConformance):
    type_name = "sse"

    @pytest.fixture(autouse=True)
    def _server(self) -> Iterator[None]:
        with SseServer([("1", '{"a": 1}'), ("2", '{"a": 2}'), ("3", '{"a": 3}')]) as server:
            self._url = server.url
            yield

    def entry(self) -> dict[str, Any]:
        return {"name": "events", "type": "sse", "source": "feed", "endpoint": "/events"}

    def source(self) -> dict[str, Any] | None:
        return {"name": "feed", "url": self._url, "rate_limit": {"rps": 100, "burst": 10}}

    def needs(self) -> dict[str, Any]:
        clock = SystemClock()
        return {"requester": HttpRequester(RateLimiter(clock), clock),
                "credentials": Credentials({})}  # fmt: skip
