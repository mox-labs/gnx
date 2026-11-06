"""Each of matrix's conformance suites against each of matrix's own implementations of its
port. A test module that defines a stand-in for a port runs the suite against it there.

Nothing here leaves the process: the Claude Agent SDK's ``query`` is replaced, as in
``test_claude_runtime.py``, and the ``pydantic-ai`` runtime's model is a hardline row on its
mock backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from hardline import build_runtime
from hardline.adapters._out.mock import MockBackend
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from matrix import (
    AGENT_STEP,
    MODEL_STEP,
    PROMPT,
    TASK,
    AgentResponse,
    BoundAgent,
    Context,
    Document,
    Executor,
    JsonlConstructStore,
    MemoryConstructStore,
    RecordingObserver,
    RunContext,
    default_registry,
    wire,
)
from matrix.adapters._out.documents.files import FileSource
from matrix.adapters._out.observers.console import ConsoleObserver
from matrix.adapters._out.observers.jsonl import JsonlObserver
from matrix.adapters._out.observers.otel import OtelObserver
from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime, ClaudeSdkRuntimeConfig
from matrix.adapters._out.runtime.mock import (
    MockAgentRuntime,
    MockAgentRuntimeConfig,
    MockModelRuntime,
    MockModelRuntimeConfig,
)
from matrix.adapters._out.runtime.pydantic_ai import PydanticAiRuntime, PydanticAiRuntimeConfig
from matrix.domain.agent import AgentDefinition
from matrix.domain.agent_step import AgentStep, AgentStepConfig
from matrix.domain.construct import Construct
from matrix.domain.executor import Run
from matrix.domain.model_step import ModelStep, ModelStepConfig
from matrix.testing import (
    AgentRuntimeConformance,
    ComponentConformance,
    ConstructStoreConformance,
    ContextConformance,
    DocumentSourceConformance,
    ObserverConformance,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from matrix import AgentRuntime, Component, ConstructStore, DocumentSource, Observer


# --- agent runtimes ----------------------------------------------------------------------


class TestTheMockAgentRuntime(AgentRuntimeConformance):
    def runtime(self) -> AgentRuntime:
        return MockAgentRuntime(MockAgentRuntimeConfig(default="hello"))


class TestTheClaudeSdkRuntime(AgentRuntimeConformance):
    @pytest.fixture(autouse=True)
    def _echoing_sdk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sdk = pytest.importorskip("claude_agent_sdk")

        async def query(
            *, prompt: Any, options: Any = None, transport: Any = None
        ) -> AsyncIterator[Any]:
            yield sdk.AssistantMessage(content=[sdk.TextBlock("hello")], model="claude-x")
            yield sdk.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="conformance",
                result="hello",
                usage={"input_tokens": 1, "output_tokens": 1},
            )

        monkeypatch.setattr(sdk, "query", query)

    def runtime(self) -> AgentRuntime:
        return ClaudeSdkRuntime(ClaudeSdkRuntimeConfig(setting_sources=[]))


class TestThePermissiveClaudeSdkRuntime(TestTheClaudeSdkRuntime):
    """One with warnings, so its SupportsWarnings is checked too."""

    def runtime(self) -> AgentRuntime:
        return ClaudeSdkRuntime(
            ClaudeSdkRuntimeConfig(setting_sources=[], permission_mode="bypassPermissions")
        )


class TestThePydanticAiRuntime(AgentRuntimeConformance):
    def runtime(self) -> AgentRuntime:
        models = build_runtime(
            {"models": {"q": {"backend": "mock", "model": "m-1", "family": "x"}}},
            backends={"mock": MockBackend()},
        )
        return PydanticAiRuntime(models, PydanticAiRuntimeConfig(default_model="q"), tools={})


def test_every_built_in_agent_runtime_has_a_suite_here() -> None:
    registered = {e.type_url for e in default_registry(discover=False).entries("agent-runtime")}
    assert registered == {
        "matrix.v1.agent-runtime.claude-sdk",
        "matrix.v1.agent-runtime.mock",
        "matrix.v1.agent-runtime.pydantic-ai",
    }


# --- stores ------------------------------------------------------------------------------


class TestTheJsonlStore(ConstructStoreConformance):
    @pytest.fixture(autouse=True)
    def _root(self, tmp_path: Path) -> None:
        self.root = tmp_path / "runs"

    def store(self) -> ConstructStore:
        return JsonlConstructStore(self.root)


class TestTheMemoryStore(ConstructStoreConformance):
    def store(self) -> ConstructStore:
        return MemoryConstructStore()


def test_every_built_in_store_has_a_suite_here() -> None:
    registered = {e.type_url for e in default_registry(discover=False).entries("store")}
    assert registered == {"matrix.v1.store.jsonl", "matrix.v1.store.memory"}


# --- observers ---------------------------------------------------------------------------


class TestTheConsoleObserver(ObserverConformance):
    def observer(self) -> Observer:
        return ConsoleObserver()


class TestTheJsonlObserverOnAFile(ObserverConformance):
    @pytest.fixture(autouse=True)
    def _path(self, tmp_path: Path) -> None:
        self.path = tmp_path / "events" / "run.jsonl"

    def observer(self) -> Observer:
        return JsonlObserver(self.path)


class TestTheJsonlObserverOnStderr(ObserverConformance):
    def observer(self) -> Observer:
        return JsonlObserver()


class TestTheOtelObserver(ObserverConformance):
    def observer(self) -> Observer:
        return OtelObserver(tracer_provider=TracerProvider(), meter_provider=MeterProvider())


class TestTheOtelObserverWithContent(ObserverConformance):
    def observer(self) -> Observer:
        return OtelObserver(
            tracer_provider=TracerProvider(), meter_provider=MeterProvider(), capture_content=True
        )


class TestTheRecordingObserver(ObserverConformance):
    def observer(self) -> Observer:
        return RecordingObserver()


class TestTheJsonlStoresEventLog(ObserverConformance):
    """What a JSONL store's writer keeps the run's events in."""

    @pytest.fixture(autouse=True)
    def _root(self, tmp_path: Path) -> None:
        self.root = tmp_path / "runs"
        self.opened = 0

    def observer(self) -> Observer:
        self.opened += 1
        run = Run(run_id=f"events-{self.opened}", construct=Construct())
        writer = JsonlConstructStore(self.root).open(run)
        sink = writer.events()
        writer.finish(run)
        assert sink is not None
        return sink


def test_every_built_in_observer_has_a_suite_here() -> None:
    registered = {e.type_url for e in default_registry(discover=False).entries("observer")}
    assert registered == {
        "matrix.v1.observer.console",
        "matrix.v1.observer.jsonl",
        "matrix.v1.observer.otel",
    }


# --- document sources --------------------------------------------------------------------


class TestTheFileSource(DocumentSourceConformance):
    @pytest.fixture(autouse=True)
    def _folder(self, tmp_path: Path) -> None:
        self.folder = tmp_path / "docs"
        self.folder.mkdir()
        (self.folder / "a.yaml").write_text("name: a\n")
        (self.folder / "b.md").write_text("---\nname: b\n---\nThe body.\n")
        self.nowhere = tmp_path / "nowhere"

    def source(self) -> DocumentSource:
        return FileSource()

    def present(self) -> Any:
        return self.folder / "a.yaml"

    def absent(self) -> Any:
        return self.nowhere


class TestTheFileSourceOnAFolder(TestTheFileSource):
    def present(self) -> Any:
        return self.folder / "b.md"


# --- component contexts ------------------------------------------------------------------


class TestADetachedContext(ContextConformance):
    async def within(self, check: Callable[[Context], Awaitable[None]]) -> None:
        await check(RunContext("conformance", "alone"))


class TestTheExecutorsContext(ContextConformance):
    async def within(self, check: Callable[[Context], Awaitable[None]]) -> None:
        raised: list[BaseException] = []

        class _Checks:
            requires: frozenset[str] = frozenset()
            provides: frozenset[str] = frozenset()

            async def run(self, inputs: object, ctx: Context) -> None:
                try:
                    await check(ctx)
                except BaseException as error:  # handed back to the test as it was raised
                    raised.append(error)

        await Executor().run(wire({"checks": _Checks()}))
        if raised:
            raise raised[0]


# --- components --------------------------------------------------------------------------


class TestTheAgentStep(ComponentConformance):
    def component(self) -> Component:
        runtime = MockAgentRuntime(MockAgentRuntimeConfig(default="reviewed"))
        agent = BoundAgent(AgentDefinition(name="reviewer"), runtime)
        return AgentStep(AgentStepConfig(agent="reviewer"), agents={"reviewer": agent})

    def inputs(self) -> dict[str, Any]:
        return {TASK: "Review it."}


class TestTheModelStep(ComponentConformance):
    def component(self) -> Component:
        runtime = MockModelRuntime(MockModelRuntimeConfig(default="hello"))
        return ModelStep(ModelStepConfig(runtime="m"), model_runtimes={"m": runtime}, schemas={})

    def inputs(self) -> dict[str, Any]:
        return {PROMPT: "Say hello."}


def test_every_built_in_component_has_a_suite_here() -> None:
    registered = {e.type_url for e in default_registry(discover=False).entries("component")}
    assert registered == {AGENT_STEP, MODEL_STEP}


# --- the suites refuse what breaks a port ------------------------------------------------
# A suite that every implementation passes proves nothing: each of these breaks one promise,
# and the suite's test for it fails.


class _Silent:
    """A session that ends without saying why."""

    def check(self, definition: AgentDefinition) -> None:
        return None

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        return AgentResponse(content="hello")


class _ListedWarnings(_Silent):
    """Warnings as a list of strings, which no reader of a Problem can use."""

    warnings = ["permissive"]


class _BrokenRuntimes(AgentRuntimeConformance):
    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime

    def runtime(self) -> AgentRuntime:
        return self._runtime


def test_the_agent_runtime_suite_refuses_a_session_that_does_not_say_why_it_ended() -> None:
    with pytest.raises(AssertionError, match="stop"):
        _BrokenRuntimes(
            _Silent()
        ).test_a_session_ends_in_a_response_saying_why_or_in_a_typed_failure()


def test_the_agent_runtime_suite_refuses_warnings_that_are_not_problems() -> None:
    with pytest.raises(AssertionError, match="tuple of Problem"):
        _BrokenRuntimes(_ListedWarnings()).test_each_optional_member_it_has_keeps_its_protocol()


class _Reopening(MemoryConstructStore):
    def open(self, run: Run) -> Any:
        self._runs.pop(run.run_id, None)
        return super().open(run)


class _BrokenStores(ConstructStoreConformance):
    def store(self) -> ConstructStore:
        return _Reopening()


def test_the_store_suite_refuses_a_store_that_reopens_a_record() -> None:
    with pytest.raises(AssertionError, match="StoreError"):
        _BrokenStores().test_a_record_is_never_reopened()


class _Raising:
    def on_event(self, event: Any) -> None:
        raise RuntimeError("this observer fails on every event")


class _BrokenObservers(ObserverConformance):
    def observer(self) -> Observer:
        return _Raising()


def test_the_observer_suite_refuses_an_observer_that_raises() -> None:
    with pytest.raises(AssertionError, match="every event"):
        _BrokenObservers().test_it_sees_a_run_that_completes_and_one_that_fails_without_failing()


class _Inventing:
    """A source that finds a document where there is none."""

    def document(self, location: Any) -> Document | None:
        return Document(source=str(location), stem="x", data={})

    def documents(self, location: Any, suffixes: Any) -> tuple[Document, ...] | None:
        return (Document(source=str(location), stem="x", data={}),)

    def beside(self, document: Document, path: str) -> str:
        return path


class _BrokenSources(DocumentSourceConformance):
    def source(self) -> DocumentSource:
        return _Inventing()

    def present(self) -> Any:
        return "here"

    def absent(self) -> Any:
        return "nowhere"


def test_the_document_source_suite_refuses_a_document_where_there_is_none() -> None:
    with pytest.raises(AssertionError, match="None"):
        _BrokenSources().test_nothing_there_is_none()


class _Permissive(RunContext):
    """A context that takes any event name."""

    def emit(self, name: str, /, **fields: Any) -> None:
        return None


class _BrokenContexts(ContextConformance):
    async def within(self, check: Callable[[Context], Awaitable[None]]) -> None:
        await check(_Permissive("conformance", "lax"))


def test_the_context_suite_refuses_a_context_that_takes_an_unnamespaced_event() -> None:
    with pytest.raises(AssertionError, match="unnamespaced event"):
        _BrokenContexts().test_it_refuses_what_a_run_refuses()


class _Strays:
    requires: frozenset[str] = frozenset()
    provides = frozenset({"conformance.v1.mine"})

    async def run(self, inputs: object, ctx: Context) -> dict[str, Any]:
        return {"conformance.v1.theirs": 1}


class _BrokenComponents(ComponentConformance):
    def component(self) -> Component:
        return _Strays()


def test_the_component_suite_refuses_a_row_of_a_kind_it_does_not_provide() -> None:
    with pytest.raises(AssertionError, match="completes"):
        _BrokenComponents().test_it_runs_to_completion_handing_over_only_what_it_provides()
