"""Model calls over matrix's model runtimes: the ``model-call`` subject.

**A trial is one model call.** A model call is not an agent session: it has no loop, no
tools, no turns and no working directory. Reaching a ``model-call`` subject opens a
:class:`Call` under a handle (``respondent``) in the
:class:`~ix.adapters._out.matrix_agents.Sessions` the ``prompt`` probe shares; the probe puts
its prompt, and the call makes one request through :meth:`matrix.ModelRuntime.call`.
Leaving the subject closes the handle.

**What makes the call is matrix's.** The call builds its runtime from the registry's
``model-runtime`` point by name (``hardline``, ``mock``, or any model runtime installed).
The subject must name it: there is no default, because a runtime may call a model provider,
which is never implicit. The runtime takes the subject's ``runtime_options`` as its config,
as written, and its ``check`` resolves the subject's ``model`` before anything starts. A
runtime registered as a stand-in (``stand_in``: matrix's ``mock``) gives canned replies:
its trials are marked ``simulated``.

**What is recorded.** Each call is a row of the run's session table (``matrix.v1.session``),
as a model step's is, failed or not: :func:`recorded_call`. The trial's interaction names it.

**What is kept.** ``response`` holds the prompt, the reply's text, the validated ``value``
when there is one, and the model's ``refusal``. ``usage`` holds what the runtime reported of
the call (:class:`matrix.ModelCall`): the runtime, the model that answered and its family,
provider and backend, tokens with the cache's, the request id, attempts, retries, the model
a fallback answered for, the finish reason and the latency as ``duration_ms``. A fact the
runtime did not report is None; nothing stands in for one, so a call has no ``turns``, no
``tool_calls`` and no ``rounds``.

**What applies.** The ``prompt`` probe applies, without ``collect`` (a call writes no files).
Sensors that read tool calls (``activation``, ``tool-usage``) never can, nor does
``function-test`` read a ``file``; the subject's review refuses each, so ``validate`` says
so before any trial runs. Every other built-in sensor reads the reply or its usage.

**Whose failure.** As for an agent session: a call the runtime could not make for an
infrastructure reason is the harness's, one the model refused or that failed is the
subject's.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import matrix
from pydantic import BaseModel, ConfigDict, ValidationError

from ix.adapters._out.matrix_agents import (
    ACTIVATION,
    CANNED,
    MODEL_RUNTIME,
    RESPONDENT,
    STAND_IN,
    TOOL_USAGE,
    Runtimes,
    Sessions,
    carried_out,
    runtime_requirement,
)
from ix.domain.errors import IxError
from ix.domain.kind import placeholders
from ix.domain.ports import Requirement, StoodIn
from ix.domain.problem import Problem, problem
from ix.domain.run import HarnessError, Target

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from ix.domain.experiment import SubjectConfig
    from ix.domain.ports import Surroundings

FUNCTION_TEST = "ix.v1.sensor.function-test"

#: What a call carries of how it was made (what was sent, each attempt, the registry row,
#: its session row) that no sensor reads: the usage facet keeps the facts sensors measure and
#: report.
_RECORD = frozenset(
    {"native_finish_reason", "params", "attempt_log", "turns_digest", "row", "session"}
)

#: The sensors a model call leaves nothing to read for, and what each would read.
NO_TOOLS = {ACTIVATION: "whether a skill fires", TOOL_USAGE: "whether a tool is called"}


class CallOptions(BaseModel):
    """A ``model-call`` subject's options. The kind's declaration has checked the keys and
    their types already; this reads them."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    #: Required, with no default: a runtime may call a model provider, so a subject names
    #: its own.
    runtime: str
    #: A name the runtime resolves (a hardline registry name); None: its default.
    model: str | None = None
    system_prompt: str | None = None
    #: The runtime's own config, passed through as written.
    runtime_options: dict[str, Any] = {}


@dataclass
class Call:
    """One trial's model call: the runtime it is made on, and how it is asked. A
    :class:`~ix.adapters._out.matrix_agents.Respondent`."""

    options: CallOptions
    simulated: bool
    runtimes: Runtimes

    async def answer(
        self, prompt: str, options: Mapping[str, Any]
    ) -> Mapping[str, Mapping[str, Any]]:
        """``response`` and ``usage``: see the module docstring."""
        config = dict(self.options.runtime_options)
        canned = options.get("mock_response")
        if self.simulated and canned is not None:
            config[CANNED] = str(canned)
        runtime = self.runtimes.build(MODEL_RUNTIME, self.options.runtime, config)
        runtime.check(self.options.model)
        request = matrix.ModelRequest(
            prompt=prompt, model=self.options.model, system=self.options.system_prompt
        )
        url = matrix.model_runtime_type_url(self.options.runtime)
        call = await recorded_call(runtime, request, url=url)
        response: dict[str, Any] = {"prompt": prompt, "text": call.text, "refusal": call.refusal}
        if call.value is not None:
            response["value"] = call.value
        # A runtime that reports no usage or latency counts as none, as ix has read it.
        tokens = call.usage or matrix.TokenUsage()
        usage = {
            "runtime": url,
            **call.model_dump(mode="json", exclude={"text", "value", "prompt", "usage", *_RECORD}),
            "tokens_input": tokens.input_tokens,
            "tokens_output": tokens.output_tokens,
            "tokens_total": tokens.input_tokens + tokens.output_tokens,
            "cache_read_tokens": tokens.cache_read_tokens,
            "cache_write_tokens": tokens.cache_write_tokens,
            "duration_ms": call.latency_ms or 0,
            "simulated": self.simulated,
        }
        return {"response": response, "usage": usage}


async def recorded_call(
    runtime: matrix.ModelRuntime, request: matrix.ModelRequest, *, url: str
) -> matrix.ModelCall:
    """One call on ``runtime`` (whose type URL is ``url``), recorded as a row of the run's
    session table, as matrix's model step records its own: whether it replied or failed, and
    for a failure what the runtime's error carried of it. Outside a run nothing is recorded.
    The call returned points at its row (``session``); a failure is raised as it stands."""
    sent = matrix.call_sent(request)
    try:
        call: matrix.ModelCall = await runtime.call(request)
    except Exception as error:
        known = error.call if isinstance(error, matrix.ModelRuntimeError) else None
        matrix.record_session(matrix.call_session(known, sent=sent, runtime=url, error=error))
        raise
    row = matrix.record_session(matrix.call_session(call, sent=sent, runtime=url))
    return call if row is None else call.model_copy(update={"session": row})


def _options(subject: SubjectConfig) -> CallOptions:
    return CallOptions.model_validate(subject.options)


def _unobservable(around: Surroundings) -> list[Problem]:
    """What a model call leaves its sensors and probes nothing to read: tool calls, and
    files."""
    found: list[Problem] = []
    for sensor in around.sensors:
        if sensor.kind in NO_TOOLS:
            name = sensor.kind.rsplit(".", 1)[-1]
            found.append(
                problem(
                    f"{around.path}.kind",
                    kind="unobservable",
                    message=f"a model call has no tools, and sensor {name} reads "
                    f"{NO_TOOLS[sensor.kind]}: it never can",
                    fix=f"measure {name} on an agent-session subject, or leave the sensor out",
                    got=name,
                )
            )
        if sensor.kind == FUNCTION_TEST and sensor.options.get("file"):
            found.append(
                problem(
                    f"{around.path}.kind",
                    kind="unobservable",
                    message="a model call writes no files, and sensor function-test is told "
                    f"to test the file {sensor.options['file']!r}: it is never there",
                    fix="leave 'file' out to test the reply's code, or measure an "
                    "agent-session subject",
                )
            )
    for at, probe in around.probes:
        if probe.options.get("collect"):
            found.append(
                problem(
                    f"{at}.collect",
                    kind="unobservable",
                    message="a model call writes no files, so there is nothing to collect",
                    fix="leave 'collect' out, or measure an agent-session subject",
                )
            )
    return found


class ModelCallSubject:
    """One model call per trial: see the module docstring. Offers ``respondent`` (the call's
    handle). A :class:`~ix.domain.ports.Review`, a :class:`~ix.domain.ports.Preflight` and
    a :class:`~ix.domain.ports.StandIn`."""

    def __init__(self, sessions: Sessions, runtimes: Runtimes) -> None:
        self._sessions = sessions
        self._runtimes = runtimes

    def review(self, options: Mapping[str, Any], around: Surroundings) -> list[Problem]:
        """A runtime option the runtime does not declare, and a sensor or probe that would
        read what a call never leaves; nothing for options of the wrong shape, or filled in
        per cell."""
        found = _unobservable(around)
        if any(placeholders(value) for value in options.values()):
            return found
        try:
            parsed = CallOptions.model_validate(options)
        except ValidationError:
            return found
        takes = self._runtimes.takes(MODEL_RUNTIME, parsed.runtime)
        if takes is None:
            return found
        declared = ", ".join(sorted(takes)) or "none"
        return [
            *found,
            *(
                problem(
                    f"{around.path}.runtime_options.{key}",
                    kind="option_unknown",
                    message=f"model runtime {parsed.runtime} takes no option {key!r}; its "
                    f"config takes {declared}",
                    fix=f"remove {key!r} or correct its spelling; `matrix describe "
                    f"{matrix.model_runtime_type_url(parsed.runtime)}` shows the runtime's "
                    "config",
                    allowed=tuple(sorted(takes)),
                    got=key,
                )
                for key in parsed.runtime_options
                if key not in takes
            ),
        ]

    def stand_in(self, options: Mapping[str, Any]) -> StoodIn:
        """The call on ``mock``: the real runtime's ``runtime_options`` set aside, the
        rest kept."""
        kept = {key: value for key, value in options.items() if key != "runtime_options"}
        notes = (
            (f"runtime_options set aside: the {STAND_IN} runtime takes none of them",)
            if options.get("runtime_options")
            else ()
        )
        return StoodIn(options={**kept, "runtime": STAND_IN}, notes=notes)

    def requirements(self, options: Mapping[str, Any], *, base: str) -> list[Requirement]:
        """Options it can carry out, and its runtime, built, resolving the model. Nothing
        for options filled in per cell."""
        if any(placeholders(value) for value in options.values()):
            return []  # filled per cell: told when the trial starts
        try:
            parsed = CallOptions.model_validate(options)
        except ValidationError as error:
            return [carried_out(error)]

        def resolves() -> None:
            runtime = self._runtimes.build(MODEL_RUNTIME, parsed.runtime, parsed.runtime_options)
            runtime.check(parsed.model)

        model = parsed.model or "its default model"
        return [
            runtime_requirement(
                f"model runtime {parsed.runtime!r} builds and resolves {model}", resolves
            )
        ]

    @asynccontextmanager
    async def reach(self, name: str, subject: SubjectConfig, *, base: str) -> AsyncIterator[Target]:
        try:
            options = _options(subject)
            simulated = self._runtimes.stand_in(MODEL_RUNTIME, options.runtime)
        except (ValidationError, IxError) as error:
            raise HarnessError("misconfigured", f"{name}: {error}") from error
        handle = self._sessions.open(Call(options, simulated, self._runtimes))
        try:
            yield Target(subject=name, facets={RESPONDENT: handle}, simulated=simulated)
        finally:
            self._sessions.close(handle)
