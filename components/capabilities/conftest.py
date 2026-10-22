"""The capabilities workspace's guard: no test in any package runs a live model session, or
reaches any host but this machine.

A live session spends money and needs credentials, so no test opens one. The guard refuses
each call where a session reaches a model, beneath every runtime and backend that makes it:

* the Claude Agent SDK's ``query`` and ``ClaudeSDKClient.connect``, which start the
  ``claude`` CLI (matrix's ``claude-sdk`` runtime, and so an ix ``agent-session``);
* hardline's network transport, which its provider backends open for every request they
  send (matrix's ``hardline`` model runtime and ``pydantic-ai`` agent runtime, whose model
  calls hardline makes, and an ix ``judge``). hardline speaks to providers over
  HTTP itself, with no provider SDK.

A test that fakes one of these is untouched: a fake SDK module, or a backend handed an httpx2
``MockTransport``, is not the object refused. Refusing the runtimes' own ``run`` instead would
refuse matrix's tests of those runtimes, which run them over fakes.

Beneath those, the network itself: a socket may connect to loopback alone (``127.0.0.0/8``,
``::1``, ``localhost``, the unspecified address) or a Unix socket. That refuses every other
route out of the test process whatever client takes it: pydantic-ai's provider clients (httpx
or httpx2), fastmcp's remote transport, recon's requester, and any HTTP client a dependency
brings later. A load tool is a process of its own, which no socket guard reaches, so starting
``k6`` is refused when a URL or address it is given, in its arguments or in the environment
its caller added, is not loopback. A test that reaches third-party hosts on purpose (recon's
live tests, run by ``just recon-live``) is marked ``network``, and only the socket guard lets
it by.

A refused call raises, and the code under test may catch that and carry on (ix records it
as a trial the harness failed). So the test also fails at teardown whenever a call was
refused, whatever it caught.

The guard replaces module and class attributes, so it holds only for code that looks those
names up when it calls them. The socket guard sees ``socket.socket``'s ``connect`` and
``connect_ex``, through which asyncio, ssl and every pure-Python client connect; a C extension
that opens its own sockets is not seen, and neither is a URL in a file ``k6`` reads. A test that
proves a call site does so reaches the guard on purpose: it reads ``refused_live_calls`` and
clears what it expected to find there (``_testing/test_guard.py`` does, for each route).

It also holds the suites' one skip gate. Under ``--strict-skips`` (``just capabilities-test``,
and so CI) a skip whose reason is not in ``EXPECTED_SKIPS`` fails the run: a gate test that
began to skip, on a tool the runner stopped installing, would otherwise pass unseen.

pytest reads this file for every test under this directory: the members set no pytest
configuration of their own, so this workspace is the rootdir wherever pytest starts in it.
"""

from __future__ import annotations

import importlib
import ipaddress
import os
import re
import socket
import subprocess
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

#: The skips a full run is expected to show, by reason; each is opt-in by design.
EXPECTED_SKIPS: frozenset[str] = frozenset(
    {
        # ix's calibration across every scenario: minutes of simulation, run by hand.
        "the full simulation: IX_SLOW=1 pytest -m slow",
        # recon against third-party APIs, which a gate must not depend on: `just recon-live`.
        "calls live APIs; set RECON_LIVE=1 to run",
        # dao's check of its parser against the real catalog, which lives outside this repo.
        "the dao corpus is not on this machine",
    }
)

#: Where a session reaches a model: (module, attribute) of each call refused.
LIVE_CALLS: tuple[tuple[str, str], ...] = (
    ("claude_agent_sdk", "query"),
    ("claude_agent_sdk", "ClaudeSDKClient.connect"),
    ("hardline.adapters._out._http", "network_transport"),
)


#: The programs that send load of their own, past the socket guard, by executable name.
LOAD_TOOLS: frozenset[str] = frozenset({"k6"})

_URL = re.compile(r"""https?://[^\s"'<>]+""")
#: A ``host:port`` address given to a load tool (``--address``), outside a URL.
_ADDRESS = re.compile(r"^\[?([0-9A-Za-z.:-]+?)\]?:\d+$")


def loopback(host: str | None) -> bool:
    """Whether ``host`` names this machine: ``localhost``, a loopback address, or the
    unspecified address (which connects to this machine)."""
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False  # a name other than localhost may resolve anywhere
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_unspecified


def _remote_targets(argv: Any, env: Any) -> list[str]:
    """The URLs and addresses a process is given, in its arguments and in the environment
    its caller added to this one's, that are not loopback."""
    args = [str(a) for a in ([argv] if isinstance(argv, str | bytes) else argv)]
    added = [str(value) for key, value in (env or {}).items() if os.environ.get(key) != value]
    found = []
    for text in args + added:
        for url in _URL.findall(text):
            if not loopback(urlsplit(url).hostname):
                found.append(url)
    for arg in args:
        address = _ADDRESS.match(arg)
        if address and not _URL.match(arg) and not loopback(address.group(1)):
            found.append(arg)
    return found


def _guard_the_network(
    monkeypatch: pytest.MonkeyPatch, refused: list[str], *, sockets: bool
) -> None:
    def refuse(where: str) -> AssertionError:
        refused.append(where)
        return AssertionError(f"a test reached {where}: only loopback is allowed")

    def checked(original: Any) -> Any:
        def connect(self: socket.socket, address: Any) -> Any:
            if self.family in (socket.AF_INET, socket.AF_INET6) and not loopback(address[0]):
                raise refuse(f"{address[0]}:{address[1]}")
            return original(self, address)

        return connect

    if sockets:
        for name in ("connect", "connect_ex"):
            monkeypatch.setattr(socket.socket, name, checked(getattr(socket.socket, name)))

    started = subprocess.Popen.__init__

    def start(self: Any, args: Any, *rest: Any, **options: Any) -> None:
        program = args if isinstance(args, str | bytes) else next(iter(args), "")
        if os.path.basename(os.fsdecode(program)) in LOAD_TOOLS:
            remote = _remote_targets(args, options.get("env"))
            if remote:
                raise refuse(", ".join(remote))
        started(self, args, *rest, **options)

    monkeypatch.setattr(subprocess.Popen, "__init__", start)


@pytest.fixture(autouse=True)
def _no_live_model_session(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[list[str]]:
    refused: list[str] = []
    marked = request.node.get_closest_marker("network") is not None
    _guard_the_network(monkeypatch, refused, sockets=not marked)
    for module_name, attribute in LIVE_CALLS:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue  # not installed here, so not callable either
        *owners, name = attribute.split(".")
        owner: Any = module
        for part in owners:
            owner = getattr(owner, part)
        where = f"{module_name}.{attribute}"

        def refuse(*args: Any, _where: str = where, **kwargs: Any) -> Any:
            refused.append(_where)
            raise AssertionError(f"a test called {_where}: a live model session")

        monkeypatch.setattr(owner, name, refuse)
    yield refused
    if refused:
        pytest.fail(f"a test reached a live model session or the network: {', '.join(refused)}")


@pytest.fixture
def refused_live_calls(_no_live_model_session: list[str]) -> list[str]:
    """The calls the guard has refused in this test so far, each as ``module.attribute``.
    Whatever is left in it at teardown fails the test."""
    return _no_live_model_session


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--strict-skips",
        action="store_true",
        help="fail the run on a skip whose reason is not in EXPECTED_SKIPS (conftest.py)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "network: reaches third-party hosts on purpose, past the socket guard"
    )
    if config.getoption("--strict-skips"):
        config.pluginmanager.register(_SkipGate(), "strict-skips")


class _SkipGate:
    """Fails the run on each skip ``EXPECTED_SKIPS`` does not name, and lists them."""

    def __init__(self) -> None:
        self.unexpected: list[str] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self._check(report)

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        self._check(report)

    def _check(self, report: pytest.TestReport | pytest.CollectReport) -> None:
        if not report.skipped or hasattr(report, "wasxfail"):
            return
        if isinstance(report.longrepr, tuple):
            path, line, message = report.longrepr
            where, reason = f"{path}:{line}", message.removeprefix("Skipped: ")
        else:
            where, reason = report.nodeid, str(report.longrepr)
        if reason not in EXPECTED_SKIPS:
            self.unexpected.append(f"{where}: {reason}")

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.unexpected and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if not self.unexpected:
            return
        terminalreporter.section("unexpected skips", red=True)
        for line in self.unexpected:
            terminalreporter.write_line(line)
        terminalreporter.write_line(
            "Each is a test that did not run. Make it run here, or add its reason to "
            "EXPECTED_SKIPS in components/capabilities/conftest.py, saying why it is expected."
        )
