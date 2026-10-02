"""Orchestrator — compile a component graph, run it batch by batch, ledger every result.

The scheduler yields batches whose members are independent of each other. With
``concurrency=1`` (the default) a batch runs one component at a time; above that, up to
``concurrency`` members of a batch run at once. Either way the ledger order is the batch
order, so a run's Construct reads the same however it was scheduled.

A component that raises becomes a :class:`ComponentError` carrying the component's name and
the Construct as it stood — the artifacts produced before the failure are kept, not lost
with the stack. Matrix's own contract errors pass through unwrapped.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

from .compiler import DagCompiler
from .errors import ComponentError, ConfigError, ContractError, MatrixError
from .scheduler import DagScheduler
from .types import Artifact, Construct, ConstructView

if TYPE_CHECKING:
    from .types import Component, TypedStruct

_tracer = trace.get_tracer("matrix")
logger = logging.getLogger("matrix.orchestrator")

#: ``(component_name, event)`` where event is ``"start"`` or ``"done"``.
NodeCallback = Callable[[str, str], None]


class Orchestrator:
    """Compiles and executes a DAG of components."""

    def __init__(
        self,
        components: Sequence[Component],
        on_node: NodeCallback | None = None,
        *,
        concurrency: int = 1,
        timeout_s: float | None = None,
    ) -> None:
        if concurrency < 1:
            raise ConfigError(f"concurrency must be >= 1, got {concurrency}")
        if timeout_s is not None and timeout_s <= 0:
            raise ConfigError(f"timeout_s must be > 0, got {timeout_s}")
        self._registry, self._edges = DagCompiler.compile(list(components))
        self._on_node = on_node
        self._concurrency = concurrency
        self._timeout_s = timeout_s

    async def run(self) -> Construct:
        """Execute the DAG. Returns the Construct with every result."""
        with _tracer.start_as_current_span("matrix.dag.run") as dag_span:
            construct = Construct()
            for batch in DagScheduler(self._registry, self._edges).batches():
                await self._run_batch(batch, construct)
            dag_span.set_attribute("matrix.dag.artifact_count", len(construct))
            return construct

    async def _run_batch(self, batch: tuple[Any, ...], construct: Construct) -> None:
        if self._concurrency == 1 or len(batch) == 1:
            for component in batch:
                self._append(component, await self._invoke(component, construct), construct)
            return

        gate = asyncio.Semaphore(self._concurrency)
        results: dict[str, TypedStruct] = {}

        async def one(component: Any) -> None:
            async with gate:
                results[component.name] = await self._invoke(component, construct)

        failure: BaseException | None = None
        try:
            async with asyncio.TaskGroup() as group:
                for component in batch:
                    group.create_task(one(component))
        except BaseExceptionGroup as group_error:
            failure = group_error.exceptions[0]
        # Siblings that finished before the failure are ledgered, in batch order.
        for component in batch:
            if component.name in results:
                self._append(component, results[component.name], construct)
        if failure is not None:
            raise failure

    async def _invoke(self, component: Any, construct: Construct) -> TypedStruct:
        """Run one component against its restricted view; wrap foreign failures."""
        name = component.name
        if self._on_node:
            self._on_node(name, "start")
        logger.debug("Running %s", name)
        with _tracer.start_as_current_span(
            "matrix.component.run",
            attributes={
                "matrix.component.name": name,
                "matrix.component.provides": component.provides,
            },
        ) as span:
            # Each component sees only the kinds it declared — the compiler's edges are
            # then the true data dependencies, not a claim about them.
            view = ConstructView(construct, reader=name, allowed=component.requires)
            try:
                async with asyncio.timeout(self._timeout_s):
                    result: TypedStruct = await component.run(view)
            except MatrixError as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR, str(e))
                raise
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR, str(e))
                reason = (
                    f"timed out after {self._timeout_s}s"
                    if isinstance(e, TimeoutError)
                    else f"{type(e).__name__}: {e}"
                )
                raise ComponentError(
                    f"component {name!r} failed: {reason}", component=name, construct=construct
                ) from e

            if result.type_url != component.provides:
                error = ContractError(
                    f"{name!r} declared provides={component.provides!r} "
                    f"but returned type_url={result.type_url!r}"
                )
                span.record_exception(error)
                span.set_status(trace.StatusCode.ERROR, str(error))
                raise error
        return result

    def _append(self, component: Any, result: TypedStruct, construct: Construct) -> None:
        construct.append(
            Artifact.create(type_url=result.type_url, producer=component.name, data=result.value)
        )
        if self._on_node:
            self._on_node(component.name, "done")
