"""Agent sessions over matrix's agent runtimes: the ``agent-session`` subject, and the
``prompt`` probe, which puts a prompt to whatever answers it.

**A trial is one fresh session in a working directory of its own.** Reaching an
``agent-session`` subject makes a new temporary directory outside the experiment, copies the
subject's ``fixtures`` and ``skills`` into it, runs its ``setup`` command there, and opens a
:class:`Session`: an agent definition and the runtime that will play it, nothing started yet.
The target carries the session's handle (``respondent``) and its directory (``workdir``),
never the session itself; the ``prompt`` probe looks it up by its handle in the
:class:`Sessions` both were built with. Leaving the subject closes the session and removes
the directory, however the trial ended. A ``model-call`` subject
(:mod:`ix.adapters._out.matrix_calls`) opens a call under a handle the same way, and the
probe asks either alike: each is a :class:`Respondent`.

**What runs the agent is matrix's.** A session builds its runtime from the registry's
``agent-runtime`` point by name (``claude-sdk``, ``mock``, or any agent runtime installed).
The subject must name it: there is no default, because a runtime may call a model provider,
which is never implicit. The runtime takes the subject's ``runtime_options`` as its config,
as written, and the working directory as its ``cwd``; ix maps no option by a runtime's name,
and the runtime's own config and ``check`` refuse what it cannot take. The session runs its
definition through a :class:`matrix.BoundAgent`, so matrix reports each session as
``invoke_agent`` and each tool call it timed as ``execute_tool``, and records it as a row of
the run's session table under its runtime's type URL, failed or not; ix adds no span of its
own for them, and the trial's interaction names the rows (``sessions``). A runtime
registered as a stand-in (``stand_in``: matrix's ``mock``) gives canned replies: its trials
are marked ``simulated`` and measure the harness, not the subject.

**Rounds.** Without a ``loop`` a session is one round: the prompt, and what the agent said
and did. A ``self-review`` loop asks again with the previous answer until the answer carries
``until`` (``SOLUTION_VERIFIED``) or the rounds run out; a ``test-feedback`` loop runs the
probe's ``feedback`` tests on the answer's code and asks again with what failed, until
nothing does. Each round is a fresh session in the same working directory, so a file the
agent wrote is there for the next; tokens, turns and cost are added up over the rounds.

**What validation learns from it.** As a :class:`~ix.domain.ports.Review` the subject
tells ``validate`` what only it knows: a runtime option the runtime does not declare,
plugins for a runtime that mounts none, and a session that leaves its sensors nothing to
observe (a skill that cannot fire because the activation sensor's tool is not among its
``tools``, files to collect that no tool can write). Whether a session loads its skills or
can write files is the runtime's to say, as its configured options make it
(:class:`matrix.AgentCapabilities`): ix asks, and reads none of its options by name. As a
:class:`~ix.domain.ports.StandIn` it puts the session on ``mock`` for a dry run, setting the
runtime's own options aside.

**Whose failure.** A session the runtime could not run for reasons that are not the
agent's (``unavailable``, ``rate_limited``, ``timeout``, ``auth``, ``incapable``: the port's
infrastructure reasons) is the harness's; one that ran and broke, or that the model refused,
is the subject's. So is one that runs past its probe's ``timeout`` (``overran``), as a
subject not up in time is: the limit is the experiment's, set on the agent, and an agent
that hangs on its hard probes must not have exactly those trials left out. A fixture that
cannot be copied, or a setup command that fails, is the harness's: the apparatus failed,
not the agent.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

import matrix
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ix.adapters._out.processes import startable
from ix.adapters._out.python_checks import code_in, run_cases
from ix.domain.errors import IxError
from ix.domain.kind import fill, placeholders, seconds
from ix.domain.ports import Requirement, StoodIn
from ix.domain.problem import Problem, problem
from ix.domain.run import HarnessError, SubjectError, Target

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence

    from ix.adapters._out.processes import Launcher
    from ix.domain.experiment import SubjectConfig
    from ix.domain.ports import Surroundings

#: How long one session (every round of it) may take when the probe does not say.
SESSION_TIMEOUT_S = 600.0

#: How long past a session's own limit the run waits for the probe to give up, before it
#: records a harness timeout: a session that does not stop when its time is up.
BACKSTOP_S = 30.0

#: How long a subject's ``setup`` command may take.
SETUP_TIMEOUT_S = 300.0

#: The largest file a probe collects from the working directory, in bytes.
COLLECT_LIMIT = 1_000_000

#: The runtime ports' reasons that say the agent or model never had a fair attempt.
HARNESS_REASONS = frozenset({"unavailable", "rate_limited", "timeout", "auth", "incapable"})

#: matrix's two runtime points: an agent session (a loop and tools) and one model call.
type Point = Literal["agent-runtime", "model-runtime"]
AGENT_RUNTIME: Final = "agent-runtime"
MODEL_RUNTIME: Final = "model-runtime"

#: The runtime a dry run puts a subject on, at its own point: matrix's ``mock``, canned
#: replies, no model called.
STAND_IN = "mock"

#: The config key under which a stand-in runtime is handed a probe's ``mock_response``: the
#: reply matrix's mocks give an unmatched task or prompt.
CANNED = "default"

#: The config key under which a runtime that mounts plugins takes them.
PLUGINS = "plugins"

#: What a subject's options become in its runtime's config, beside ``runtime_options``.
RUNTIME_CONFIG = ("runtime_options", PLUGINS)

#: The facet a subject offers its session or call under: the handle a ``prompt`` probe
#: looks it up by.
RESPONDENT = "respondent"

#: The tool through which a Claude Code session fires a skill.
SKILL_TOOL = "Skill"

#: The sensors whose readings rest on a tool call, by kind.
ACTIVATION = "ix.v1.sensor.activation"
TOOL_USAGE = "ix.v1.sensor.tool-usage"

#: What a self-review loop looks for in an answer that it should stop at.
VERIFIED = "SOLUTION_VERIFIED"

SELF_REVIEW = (
    "Here is a task and your previous answer to it. Review the answer. If it is correct and "
    "complete, repeat it and end with the line {until}. Otherwise give the corrected answer "
    "in full.\n\nTask:\n{prompt}\n\nYour previous answer:\n{response}"
)

TEST_FEEDBACK = (
    "Here is a task and your previous answer to it. Your answer's code failed these tests:"
    "\n{failures}\n\nGive the corrected answer in full.\n\nTask:\n{prompt}\n\n"
    "Your previous answer:\n{response}"
)


class Runtimes(Protocol):
    """matrix's runtimes, at its two points, as the composition root hands them over. A
    ``name`` is a runtime's short name at that point (``claude-sdk``) or its type URL."""

    def build(
        self, point: Point, name: str, config: Mapping[str, Any], *, cwd: str | None = None
    ) -> Any:
        """The runtime registered at ``point`` as ``name``, built with ``config``; ``cwd`` is
        where an agent runtime runs its sessions. Raises ``matrix.MatrixError``, or ix's
        ``NotFoundError`` for a name nothing registered there."""
        ...

    def stand_in(self, point: Point, name: str) -> bool:
        """Whether the runtime declares itself a stand-in (``Entry.stand_in``): its replies
        are canned, not a model's. Raises as :meth:`build` does for an unknown name."""
        ...

    def takes(self, point: Point, name: str) -> frozenset[str] | None:
        """The keys the runtime's config declares; an empty set for one that takes no
        config, None for a name nothing registered there."""
        ...

    def drives(self, point: Point, name: str) -> dict[str, str]:
        """Each distribution the runtime drives (``claude-agent-sdk`` for ``claude-sdk``), as
        its registration declares, with its installed version (``not installed`` for one
        that is not); none for a runtime that declares none. Raises as :meth:`build` does
        for an unknown name."""
        ...

    def capabilities(
        self, point: Point, name: str, config: Mapping[str, Any]
    ) -> matrix.AgentCapabilities | None:
        """What sessions on the runtime do when it is built with ``config``, as the runtime
        declares (whether they load skills, whether their tools can write): None for one that
        declares nothing, a config it refuses, or a name nothing registered there. Asks
        nothing of the host."""
        ...


class Respondent(Protocol):
    """What a ``prompt`` probe puts its prompt to, by the handle a subject opened it under:
    an agent :class:`Session`, or a model call."""

    #: Whether a stand-in answers: canned replies, not a model's.
    simulated: bool

    async def answer(
        self, prompt: str, options: Mapping[str, Any]
    ) -> Mapping[str, Mapping[str, Any]]:
        """Put ``prompt`` and return what was left behind, by facet: ``response`` and
        ``usage``. ``options`` are the probe's. Raises the runtime's error as it stands."""
        ...


class Loop(BaseModel):
    """A subject's ``loop``: the rounds after the first, and when they stop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["self-review", "test-feedback"]
    #: Every round, the first included: 1 is the first answer alone, so a sweep over the
    #: rounds can start from no loop at all.
    rounds: int = Field(default=3, ge=1)
    #: The next round's task: ``{prompt}``, ``{response}`` and, for test-feedback,
    #: ``{failures}`` are filled in.
    prompt: str | None = None
    #: Self-review stops when an answer contains this.
    until: str = VERIFIED


class SessionOptions(BaseModel):
    """An ``agent-session`` subject's options. The kind's declaration has checked the keys
    and their types already; this reads them, and the shapes inside them."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    #: Required, with no default: a runtime may call a model provider, so a subject names
    #: its own.
    runtime: str
    definition: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    tools: tuple[str, ...] | None = None
    max_turns: int | None = Field(default=None, ge=1)
    plugins: tuple[str | dict[str, Any], ...] = ()
    skills: tuple[str, ...] = ()
    fixtures: tuple[str, ...] = ()
    setup: tuple[str, ...] = ()
    loop: Loop | None = None
    #: The runtime's own config, passed through as written.
    runtime_options: dict[str, Any] = {}


def _within(base: str, path: str) -> Path:
    """``path`` relative to the directory ``base``; an absolute one as it stands."""
    return Path(base, path).expanduser()


def _plugins(options: SessionOptions, base: str) -> list[dict[str, Any]]:
    """The SDK's plugin configs, every path absolute: a session runs in its own directory,
    so a path relative to the experiment would mean nothing there."""
    found: list[dict[str, Any]] = []
    for plugin in options.plugins:
        entry = {"type": "local", "path": plugin} if isinstance(plugin, str) else dict(plugin)
        path = entry.get("path")
        if not isinstance(path, str):
            found.append(entry)
            continue
        matched = sorted(glob(str(_within(base, path)))) if "*" in path else [path]
        found += [{**entry, "path": str(_within(base, item).resolve())} for item in matched]
    return found


def _definition(name: str, options: SessionOptions, base: str) -> matrix.AgentDefinition:
    """The agent the subject is: its ``definition`` file, with the options written beside it
    in place of what the file says."""
    fields: dict[str, Any] = {}
    if options.definition is not None:
        loaded = matrix.load(matrix.AgentDefinition, _within(base, options.definition))
        fields = loaded.model_dump(exclude={"name", "metadata"}, exclude_none=True)
    for key in ("model", "system_prompt", "tools", "max_turns"):
        value = getattr(options, key)
        if value is not None:
            fields[key] = value
    return matrix.AgentDefinition(name=name, **fields)


def _runtime_config(
    options: SessionOptions, base: str, *, canned: str | None = None
) -> dict[str, Any]:
    """The runtime's own config: ``runtime_options`` as written, the ``plugins`` beside
    them with every path absolute, and for a stand-in the probe's canned reply. The
    runtime's config schema, and its ``check``, refuse what it cannot take."""
    config = dict(options.runtime_options)
    if options.plugins:
        config[PLUGINS] = [*config.get(PLUGINS, ()), *_plugins(options, base)]
    if canned is not None:
        config[CANNED] = canned
    return config


@dataclass
class Round:
    """One round of a session: the task it was given, what came back, and what the loop
    told the agent next (failed tests), if anything."""

    number: int
    task: str
    response: matrix.AgentResponse
    feedback: list[str] = field(default_factory=list)


def _cost(rounds: Sequence[Round]) -> float | None:
    """What ``rounds`` cost, as their runtime reported it; None when none reported a cost."""
    known = [r.response.cost_usd for r in rounds if r.response.cost_usd is not None]
    return round(sum(known), 6) if known else None


@dataclass
class Session:
    """One trial's agent: what it is, where it runs, and how it is asked. A
    :class:`Respondent`."""

    name: str
    runtime: str
    definition: matrix.AgentDefinition
    workdir: Path
    options: SessionOptions
    base: str
    simulated: bool
    runtimes: Runtimes
    launcher: Launcher

    async def _round(self, task: str, canned: str | None) -> matrix.AgentResponse:
        config = _runtime_config(self.options, self.base, canned=canned if self.simulated else None)
        with built():
            runtime = self.runtimes.build(
                AGENT_RUNTIME, self.runtime, config, cwd=str(self.workdir)
            )
            url = matrix.agent_runtime_type_url(self.runtime)
            drives = tuple(self.runtimes.drives(AGENT_RUNTIME, self.runtime))
            bound = matrix.BoundAgent(self.definition, runtime, runtime_url=url, drives=drives)
        response: matrix.AgentResponse = await bound.run(task)
        return response

    async def _failures(self, text: str, feedback: Mapping[str, Any]) -> list[str]:
        cases = feedback.get("test_cases")
        if not feedback.get("function_name") or not isinstance(cases, list) or not cases:
            raise HarnessError(
                "no_feedback",
                "a test-feedback loop needs the probe's feedback: {function_name, test_cases}",
            )
        run = await run_cases(self.launcher, code_in(text), str(feedback["function_name"]), cases)
        return [
            f"- {case.get('description') or f'case {index}'}: {result.message}"
            for index, (case, result) in enumerate(zip(cases, run.results, strict=True))
            if not result.passed
        ]

    async def ask(
        self,
        prompt: str,
        *,
        canned: str | None = None,
        feedback: Mapping[str, Any] | None = None,
    ) -> list[Round]:
        """Put ``prompt`` to the agent, and again as the loop says. ``canned`` is the reply
        a stand-in runtime gives; ``feedback`` the tests a test-feedback loop runs."""
        loop = self.options.loop
        rounds: list[Round] = []
        task = prompt
        for number in range(1, (loop.rounds if loop else 1) + 1):
            response = await self._round(task, canned)
            rounds.append(Round(number, task, response))
            if loop is None or number == loop.rounds:
                break
            values = {"prompt": prompt, "response": response.content, "until": loop.until}
            if loop.strategy == "self-review":
                if loop.until in response.content:
                    break
                task = fill(loop.prompt or SELF_REVIEW, values)
            else:
                failures = await self._failures(response.content, feedback or {})
                if not failures:
                    break
                rounds[-1].feedback = failures
                task = fill(
                    loop.prompt or TEST_FEEDBACK, {**values, "failures": "\n".join(failures)}
                )
        return rounds

    async def answer(
        self, prompt: str, options: Mapping[str, Any]
    ) -> Mapping[str, Mapping[str, Any]]:
        """``response`` (the prompt, the final text, every tool call, the transcript of
        every round, how it stopped, the files collected) and ``usage`` (tokens, turns, tool
        calls, duration and cost added over the rounds, the model and family that answered,
        and whether a stand-in did)."""
        canned = options.get("mock_response")
        rounds = await self.ask(
            prompt,
            canned=None if canned is None else str(canned),
            feedback=options.get("feedback"),
        )
        last = rounds[-1].response
        calls = [call for r in rounds for call in r.response.tool_calls]
        response = {
            "prompt": prompt,
            "text": last.content,
            "stop": last.stop,
            "tool_calls": calls,
            "transcript": _trace(rounds),
            "files": _collected(self.workdir, options.get("collect")),
        }
        # A count a runtime did not report adds nothing, as ix has always summed it.
        tokens_in = sum(r.response.tokens_input or 0 for r in rounds)
        tokens_out = sum(r.response.tokens_output or 0 for r in rounds)
        usage = {
            "runtime": matrix.agent_runtime_type_url(self.runtime),
            "model": last.model,
            "family": last.family,
            "provider": last.provider,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "tokens_total": tokens_in + tokens_out,
            "cost_usd": _cost(rounds),
            "turns": sum(r.response.num_turns or 0 for r in rounds),
            "tool_calls": len(calls),
            "duration_ms": sum(r.response.duration_ms or 0 for r in rounds),
            "rounds": len(rounds),
            "stop": last.stop,
            "simulated": self.simulated,
        }
        return {"response": response, "usage": usage}


class Sessions:
    """What is open now, by handle: the agent sessions and model calls subjects opened, so
    that a subject and a probe built over the same table share a live one while a target
    carries only its handle."""

    def __init__(self) -> None:
        self._open: dict[str, Respondent] = {}

    def open(self, respondent: Respondent) -> str:
        handle = uuid.uuid4().hex
        self._open[handle] = respondent
        return handle

    def close(self, handle: str) -> None:
        self._open.pop(handle, None)

    def __getitem__(self, handle: str) -> Respondent:
        try:
            return self._open[handle]
        except KeyError:
            raise HarnessError(
                "no_session", f"no session is open under {handle!r}: the subject was released"
            ) from None


def runtime_requirement(need: str, attempt: Callable[[], object]) -> Requirement:
    """``need``, holding when ``attempt`` (building a runtime, checking it can play what it
    will be given) raises nothing it would raise for a config that does not work here."""
    try:
        attempt()
    except (matrix.MatrixError, IxError, ValidationError, ValueError) as error:
        return Requirement(
            need=need, holds=False, why=str(error), fix=str(getattr(error, "fix", "") or "")
        )
    return Requirement(need=need, holds=True)


def carried_out(error: ValidationError) -> Requirement:
    """The requirement that an entry's options are ones its adapter can carry out, which
    ``error`` says they are not: its first problem, at its option's path."""
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    return Requirement(
        need="options the adapter can carry out",
        holds=False,
        why=f"{where}: {first['msg']}",
        fix=f"correct {where}",
    )


def _files_there(options: SessionOptions, base: str) -> Requirement:
    """That every file the session names is there: its fixtures, skills, plugins and
    definition. A plugin path with a ``*`` must match something, or the session would run
    without the plugins the experiment meant to measure."""
    named = [*options.fixtures, *options.skills, *filter(None, [options.definition])]
    named += [p if isinstance(p, str) else str(p.get("path", "")) for p in options.plugins]
    need = "the files the session names are there (fixtures, skills, plugins, definition)"
    for path in named:
        if "*" in path:
            if not glob(str(_within(base, path))):
                return Requirement(
                    need=need,
                    holds=False,
                    why=f"{path!r} matches nothing in {base}",
                    fix="correct the path; it is relative to the experiment's directory",
                )
        elif not _within(base, path).exists():
            return Requirement(
                need=need,
                holds=False,
                why=f"{path!r} is not at {_within(base, path)}",
                fix="correct the path; it is relative to the experiment's directory",
            )
    return Requirement(need=need, holds=True)


def _options(subject: SubjectConfig) -> SessionOptions:
    return SessionOptions.model_validate(subject.options)


async def _setup(launcher: Launcher, command: Sequence[str], workdir: Path) -> None:
    """Run the subject's setup command in the working directory. Raises
    :class:`HarnessError` when it cannot start, fails or overruns."""
    try:
        process = await launcher.start(
            [str(part) for part in command],
            cwd=str(workdir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        message = f"setup {command[0]!r} could not start: {error}"
        raise HarnessError("setup_failed", message) from error
    try:
        _, said = await asyncio.wait_for(process.communicate(), SETUP_TIMEOUT_S)
    except TimeoutError:
        raise HarnessError(
            "setup_failed", f"setup {command[0]!r} overran {SETUP_TIMEOUT_S:g}s"
        ) from None
    finally:
        await launcher.stop(process, grace_s=1.0)
    if process.returncode != 0:
        tail = said.decode("utf-8", errors="replace").strip()[-400:]
        raise HarnessError(
            "setup_failed",
            f"setup {command[0]!r} exited with code {process.returncode}"
            + (f": {tail}" if tail else ""),
        )


def _copy(source: Path, into: Path) -> None:
    target = into / source.name
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)


def _seed(workdir: Path, options: SessionOptions, base: str) -> None:
    """Copy the fixtures into the working directory, and the skills into its
    ``.claude/skills``. Raises :class:`HarnessError` for one that is not there."""
    skills = workdir / ".claude" / "skills"
    for listed, into in [(options.fixtures, workdir), (options.skills, skills)]:
        for path in listed:
            source = _within(base, path)
            if not source.exists():
                raise HarnessError("fixture_missing", f"{path} is not at {source}")
            into.mkdir(parents=True, exist_ok=True)
            _copy(source, into)


def _undeclared(options: SessionOptions, takes: frozenset[str] | None, path: str) -> list[Problem]:
    """A runtime option the runtime's config does not declare, and plugins for a runtime
    that mounts none: building it would refuse each. ``takes`` is what the runtime's config
    declares; None for a runtime nothing registered, which the preflight reports."""
    if takes is None:
        return []
    declared = ", ".join(sorted(takes)) or "none"
    found = [
        problem(
            f"{path}.runtime_options.{key}",
            kind="option_unknown",
            message=f"agent runtime {options.runtime} takes no option {key!r}; its config "
            f"takes {declared}",
            fix=f"remove {key!r} or correct its spelling; `matrix describe "
            f"{matrix.agent_runtime_type_url(options.runtime)}` shows the runtime's config",
            allowed=tuple(sorted(takes)),
            got=key,
        )
        for key in options.runtime_options
        if key not in takes
    ]
    if options.plugins and PLUGINS not in takes:
        found.append(
            problem(
                f"{path}.{PLUGINS}",
                kind="option_inapplicable",
                message=f"agent runtime {options.runtime} mounts no plugins: its config takes "
                f"no {PLUGINS!r}",
                fix="remove 'plugins', or run the subject on a runtime whose config takes them",
                got=options.runtime,
            )
        )
    return found


def _tools(options: SessionOptions, base: str) -> tuple[tuple[str, ...] | None, str]:
    """The tools the session may call (None: every tool) and the option that says so:
    ``tools``, or else the ``definition`` file's. A definition that cannot be read says
    nothing here; the preflight reports it."""
    if options.tools is not None or options.definition is None:
        return options.tools, "tools"
    try:
        loaded = matrix.load(matrix.AgentDefinition, _within(base, options.definition))
    except (matrix.MatrixError, ValidationError, OSError):
        return None, "tools"
    return (None if loaded.tools is None else tuple(loaded.tools)), "definition"


def _unobservable(
    options: SessionOptions, around: Surroundings, does: matrix.AgentCapabilities | None
) -> list[Problem]:
    """A session written so that what the sensors read can never happen: a run of it would
    report a confident verdict on a harness that could not see the agent. ``does`` is what
    the runtime declares its sessions do, as configured; an answer it does not give (or no
    declaration) is assumed nothing of."""
    path = around.path
    sensors = {sensor.kind: sensor.options for sensor in around.sensors}
    activation = ACTIVATION in sensors
    tools, where = _tools(options, around.base)
    does = does or matrix.AgentCapabilities()
    url = matrix.agent_runtime_type_url(options.runtime)
    problems: list[Problem] = []

    def unobservable(key: str, message: str, fix: str) -> None:
        problems.append(problem(f"{path}.{key}", kind="unobservable", message=message, fix=fix))

    def decided_by(answer: str, otherwise: str) -> tuple[str, str]:
        """Where a problem with the runtime's ``answer`` is, and how to change it: the
        option the runtime says decides it, or ``otherwise`` when it names none."""
        key = does.set_by.get(answer)
        if key is None:
            return otherwise, ""
        return f"runtime_options.{key}", (
            f"; or change {key} under runtime_options (`matrix describe {url}` shows what it takes)"
        )

    skill_tool = str((sensors.get(ACTIVATION) or {}).get("tool") or SKILL_TOOL)
    if (activation or options.skills) and tools is not None and skill_tool not in tools:
        observed = (
            "the activation sensor would read every must_trigger probe as not fired"
            if activation
            else "the skills it is given can never run"
        )
        unobservable(
            where,
            f"the session may call {', '.join(tools) or 'no tool'}, and a skill fires only "
            f"through the {skill_tool} tool: {observed}",
            f"add {skill_tool} to tools: [{', '.join([*tools, skill_tool])}], or leave "
            "'tools' out for every tool",
        )
    if activation and not options.skills and not options.plugins and does.settings_skills is False:
        at, change = decided_by("settings_skills", "runtime")
        unobservable(
            at,
            f"agent runtime {options.runtime}, as configured, loads no skills from settings, "
            "and the session is given no skills or plugins, so there is no skill for the "
            "activation sensor to see fire",
            "give the skills under test (skills: [<folder holding SKILL.md>] or plugins: "
            f"[<plugin folder>]){change}",
        )
    if options.skills and does.project_skills is False:
        at, change = decided_by("project_skills", "skills")
        unobservable(
            at,
            "the skills are copied into the session's .claude/skills, and agent runtime "
            f"{options.runtime}, as configured, loads no skills from its working directory: "
            "they can never fire",
            f"run the session on a runtime that loads them{change}",
        )
    used = str((sensors.get(TOOL_USAGE) or {}).get("tool") or "")
    if used and tools is not None and used not in tools and "Bash" not in tools:
        unobservable(
            where,
            f"sensor tool-usage looks for {used}, called by name or run through Bash, and the "
            f"session may call {', '.join(tools) or 'no tool'}",
            f"add {used} (or Bash) to tools, or leave 'tools' out for every tool",
        )
    collecting = [at for at, probe in around.probes if probe.options.get("collect")]
    if collecting and (tools == () or does.writes_files is False):
        if tools == ():
            at, cannot, change = where, "may call no tool", ""
        else:
            at, change = decided_by("writes_files", "runtime")
            cannot = f"runs on agent runtime {options.runtime}, which as configured writes no file"
        unobservable(
            at,
            f"{', '.join(collecting)} collect files the agent writes, and the session "
            f"{cannot}: nothing would be collected",
            f"let the session write: give it tools that write files{change}",
        )
    return problems


class AgentSessionSubject:
    """A fresh agent session per trial, in a working directory of its own: see the module
    docstring. Offers ``respondent`` (the session's handle) and ``workdir``. A
    :class:`~ix.domain.ports.Review`, a :class:`~ix.domain.ports.Preflight` and a
    :class:`~ix.domain.ports.StandIn`."""

    def __init__(self, sessions: Sessions, runtimes: Runtimes, launcher: Launcher) -> None:
        self._sessions = sessions
        self._runtimes = runtimes
        self._launcher = launcher

    def review(self, options: Mapping[str, Any], around: Surroundings) -> list[Problem]:
        """A runtime option the runtime does not declare, and a session that leaves its
        sensors nothing to observe; nothing for options of the wrong shape. ``validate``
        asks it once for each cell's options."""
        try:
            parsed = SessionOptions.model_validate(options)
        except ValidationError:
            return []
        takes = self._runtimes.takes(AGENT_RUNTIME, parsed.runtime)
        does = self._runtimes.capabilities(AGENT_RUNTIME, parsed.runtime, parsed.runtime_options)
        return [*_undeclared(parsed, takes, around.path), *_unobservable(parsed, around, does)]

    def stand_in(self, options: Mapping[str, Any]) -> StoodIn:
        """The session on ``mock``: what would be the real runtime's config (its
        ``runtime_options`` and ``plugins``) set aside, the rest kept, so the fixtures,
        skills, setup, loop and sensors run as they would."""
        dropped = [key for key in RUNTIME_CONFIG if options.get(key) not in (None, [], (), {})]
        kept = {key: value for key, value in options.items() if key not in dropped}
        notes = (
            [f"{', '.join(dropped)} set aside: the {STAND_IN} runtime takes none of them"]
            if dropped
            else []
        )
        return StoodIn(options={**kept, "runtime": STAND_IN}, notes=tuple(notes))

    def requirements(self, options: Mapping[str, Any], *, base: str) -> list[Requirement]:
        """What a session needs here: options it can carry out, the files they name, its
        setup program, and its runtime, built, able to play the definition. Nothing while
        an option holds a placeholder only the trial fills (an environment's binding)."""
        if any(placeholders(value) for value in options.values()):
            return []  # told when the trial starts
        try:
            parsed = SessionOptions.model_validate(options)
        except ValidationError as error:
            return [carried_out(error)]
        found = [_files_there(parsed, base)]
        if parsed.setup:
            setup = startable(parsed.setup[0], base=base)
            if setup is not None:
                found.append(setup)

        def plays() -> None:
            definition = _definition("preflight", parsed, base)
            runtime = self._runtimes.build(
                AGENT_RUNTIME, parsed.runtime, _runtime_config(parsed, base)
            )
            runtime.check(definition)

        found.append(
            runtime_requirement(
                f"agent runtime {parsed.runtime!r} builds and can play the session", plays
            )
        )
        return found

    @asynccontextmanager
    async def reach(self, name: str, subject: SubjectConfig, *, base: str) -> AsyncIterator[Target]:
        try:
            options = _options(subject)
            definition = _definition(name, options, base)
        except (matrix.MatrixError, ValidationError) as error:
            raise HarnessError("misconfigured", f"{name}: {error}") from error
        try:
            stand_in = self._runtimes.stand_in(AGENT_RUNTIME, options.runtime)
        except IxError as error:
            raise HarnessError("misconfigured", f"{name}: {error}") from error
        workdir = Path(tempfile.mkdtemp(prefix="ix-trial-"))
        try:
            _seed(workdir, options, base)
            if options.setup:
                await _setup(self._launcher, options.setup, workdir)
            session = Session(
                name=name,
                runtime=options.runtime,
                definition=definition,
                workdir=workdir,
                options=options,
                base=base,
                simulated=stand_in,
                runtimes=self._runtimes,
                launcher=self._launcher,
            )
            handle = self._sessions.open(session)
            try:
                yield Target(
                    subject=name,
                    facets={RESPONDENT: handle, "workdir": str(workdir)},
                    simulated=session.simulated,
                )
            finally:
                self._sessions.close(handle)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def _collected(workdir: Path, names: Sequence[str] | None) -> dict[str, str]:
    """The files ``names`` the agent left in its working directory, as text. One that is not
    there, is not a file, lies outside the directory or is too large is left out."""
    found: dict[str, str] = {}
    root = workdir.resolve()
    for name in names or ():
        file = (workdir / str(name)).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            continue
        if file.stat().st_size <= COLLECT_LIMIT:
            found[str(name)] = file.read_text(encoding="utf-8", errors="replace")
    return found


def _trace(rounds: Sequence[Round]) -> list[dict[str, Any]]:
    return [
        {
            "round": r.number,
            "task": r.task,
            "text": r.response.content,
            "tool_calls": list(r.response.tool_calls),
            "stop": r.response.stop,
            "turns": r.response.num_turns,
            "feedback": r.feedback,
        }
        for r in rounds
    ]


class PromptProbe:
    """Puts the probe's prompt to what the subject opened (an agent session, a model call),
    by its ``respondent`` handle, and leaves behind what that left: ``response`` and
    ``usage`` (see :meth:`Session.answer`, and the model call's). The probe's ``timeout``
    bounds the whole of it."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    def budget_s(self, options: Mapping[str, Any]) -> float | None:
        return _limit(options) + BACKSTOP_S

    def stand_in(self, options: Mapping[str, Any]) -> StoodIn:
        """The probe as it is: a stand-in replies with its ``mock_response``."""
        if options.get("mock_response") is not None:
            return StoodIn(options=options)
        note = "no mock_response, so the mock runtime replies with an echo of the prompt"
        return StoodIn(options=options, notes=(note,))

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        respondent = self._sessions[str(target.facets[RESPONDENT])]
        limit = _limit(options)
        deadline = asyncio.timeout(limit)
        try:
            async with deadline:
                return await respondent.answer(str(options["prompt"]), options)
        except TimeoutError as error:
            if not deadline.expired():
                raise _owned(error) from error
            raise SubjectError("overran", f"it ran past its time limit of {limit:g}s") from error
        except Exception as error:
            raise _owned(error) from error


def _limit(options: Mapping[str, Any]) -> float:
    """How long one session (every round of it), or one call, may take: the probe's
    ``timeout``."""
    return seconds(options.get("timeout")) or SESSION_TIMEOUT_S


@contextmanager
def built() -> Iterator[None]:
    """Where a session's or a call's runtime is built and bound: what fails here is the
    harness's ``runtime`` failure (an option the runtime refuses, a name nothing
    registered), told apart from what fails while the session or the call runs."""
    try:
        yield
    except (matrix.AgentRuntimeError, matrix.ModelRuntimeError):
        raise
    except (matrix.MatrixError, IxError, ValueError) as error:
        raise HarnessError("runtime", f"the runtime could not be built: {error}") from error


def _owned(error: BaseException) -> Exception:
    """The trial failure ``error`` is: the harness's for an infrastructure reason or an
    apparatus fault, the subject's for a session or call that ran and broke or was
    refused. Anything else is the harness's, named by its type: a runtime that could not be
    built has already said so (:func:`built`)."""
    if isinstance(error, HarnessError | SubjectError):
        failure: HarnessError | SubjectError = error
    elif isinstance(error, matrix.AgentRuntimeError | matrix.ModelRuntimeError):
        whose = HarnessError if error.reason in HARNESS_REASONS else SubjectError
        failure = whose(error.reason, str(error))
    else:
        failure = HarnessError("error", f"{type(error).__name__}: {error}")
    return failure
