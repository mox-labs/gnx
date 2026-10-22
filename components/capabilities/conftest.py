"""The capabilities workspace's guard: no test in any package runs a live model session.

A live session spends money and needs credentials, so no test opens one. The guard refuses
each call where a session reaches a model, beneath every runtime and backend that makes it:

* the Claude Agent SDK's ``query`` and ``ClaudeSDKClient.connect``, which start the
  ``claude`` CLI (matrix's ``claude-sdk`` runtime, and so an ix ``agent-session``);
* hardline's network transport, which its provider backends open for every request they
  send (matrix's ``model`` runtime, and an ix ``judge``). hardline speaks to providers over
  HTTP itself, with no provider SDK.

A test that fakes one of these is untouched: a fake SDK module, or a backend handed an httpx2
``MockTransport``, is not the object refused. Refusing the runtimes' own ``run`` instead would
refuse matrix's tests of those runtimes, which run them over fakes.

A refused call raises, and the code under test may catch that and carry on (ix records it
as a trial the harness failed). So the test also fails at teardown whenever a call was
refused, whatever it caught.

The guard replaces module and class attributes, so it holds only for code that looks those
names up when it calls them. A test that proves a call site does so reaches the guard on
purpose: it reads ``refused_live_calls`` and clears what it expected to find there.

It also holds the suites' one skip gate. Under ``--strict-skips`` (``just capabilities-test``,
and so CI) a skip whose reason is not in ``EXPECTED_SKIPS`` fails the run: a gate test that
began to skip, on a tool the runner stopped installing, would otherwise pass unseen.

pytest reads this file for every test under this directory: the members set no pytest
configuration of their own, so this workspace is the rootdir wherever pytest starts in it.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

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
    }
)

#: Where a session reaches a model: (module, attribute) of each call refused.
LIVE_CALLS: tuple[tuple[str, str], ...] = (
    ("claude_agent_sdk", "query"),
    ("claude_agent_sdk", "ClaudeSDKClient.connect"),
    ("hardline.adapters._out._http", "network_transport"),
)


@pytest.fixture(autouse=True)
def _no_live_model_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    refused: list[str] = []
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
        pytest.fail(f"a test reached a live model session: {', '.join(refused)}")


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
