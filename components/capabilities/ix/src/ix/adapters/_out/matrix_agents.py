"""Agent sessions over matrix's agent runtimes: the ``agent-session`` subject and the
``prompt`` probe.

**A trial is one fresh session in a working directory of its own.** Reaching an
``agent-session`` subject makes a new temporary directory outside the experiment, copies the
subject's ``fixtures`` and ``skills`` into it, runs its ``setup`` command there, and opens a
:class:`Session`: an agent definition and the runtime that will play it, nothing started yet.
The target carries the session's handle (``agent``) and its directory (``workdir``), never
the session itself; the ``prompt`` probe looks the session up by its handle in the
:class:`Sessions` both were built with. Leaving the subject closes the session and removes
the directory, however the trial ended.

**What runs the agent is matrix's.** A session builds its runtime from the registry by name
(``claude-sdk``, ``model``, ``mock``, or any runtime installed). The subject must name it:
there is no default, because two of them spend money and spending is never implicit. The
runtime takes the subject's options as its config and the working directory as its ``cwd``,
and the session runs its definition through a :class:`matrix.BoundAgent`, so matrix reports
each session as ``invoke_agent`` and each tool call it timed as ``execute_tool``; ix adds no
span of its own for them. A runtime that declares it touches no model (matrix's ``mock``)
is a stand-in: its trials are marked ``simulated`` and measure the harness, not the subject.

**Rounds.** Without a ``loop`` a session is one round: the prompt, and what the agent said
and did. A ``self-review`` loop asks again with the previous answer until the answer carries
``until`` (``SOLUTION_VERIFIED``) or the rounds run out; a ``test-feedback`` loop runs the
probe's ``feedback`` tests on the answer's code and asks again with what failed, until
nothing does. Each round is a fresh session in the same working directory, so a file the
agent wrote is there for the next; tokens, turns and cost are added up over the rounds.

**What validation learns from it.** As a :class:`~ix.domain.ports.Review` the subject
tells ``validate`` what only it knows: an SDK option written for a subject on another
runtime, and a session that leaves its sensors nothing to observe (a skill that cannot fire
because the ``Skill`` tool is not among its ``tools``, a ``model`` runtime asked for tool
calls, files to collect that no tool can write). As a :class:`~ix.domain.ports.StandIn` it
puts the session on ``mock`` for a dry run, setting the SDK's options aside.

**Whose failure.** A session the runtime could not run for reasons that are not the
agent's (``unavailable``, ``rate_limited``, ``timeout``, ``auth``, ``incapable``: the port's
infrastructure reasons) is the harness's; one that ran and broke, or that the model refused,
is the subject's. A fixture that cannot be copied, or a setup command that fails, is the
harness's: the apparatus failed, not the agent.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import matrix
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ix.adapters._out.python_checks import code_in, run_cases
from ix.domain.kind import fill, placeholders, seconds
from ix.domain.ports import StoodIn
from ix.domain.problem import Problem, problem
from ix.domain.run import COST, HarnessError, SubjectError, Target

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from ix.adapters._out.processes import Launcher
    from ix.domain.experiment import SubjectConfig
    from ix.domain.ports import Surroundings

#: How long one session (every round of it) may take when the probe does not say.
SESSION_TIMEOUT_S = 600.0

#: How long a subject's ``setup`` command may take.
SETUP_TIMEOUT_S = 300.0

#: The largest file a probe collects from the working directory, in bytes.
COLLECT_LIMIT = 1_000_000

#: The runtime port's reasons that say the agent never had a fair attempt.
HARNESS_REASONS = frozenset({"unavailable", "rate_limited", "timeout", "auth", "incapable"})

#: Options that configure the Claude Agent SDK runtime and mean nothing to another.
_SDK_OPTIONS = ("permission_mode", "setting_sources", "plugins", "skills")

#: The runtime a dry run puts a session on: canned replies, nothing spent.
STAND_IN = "mock"

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
    """matrix's agent runtimes, as the composition root hands them over."""

    def build(self, name: str, config: Mapping[str, Any], *, cwd: str | None) -> Any:
        """The runtime registered as ``name`` (``claude-sdk``, or a type URL), built with
        ``config``; ``cwd`` is where it runs its sessions. Raises ``matrix.MatrixError``."""
        ...

    def simulated(self, name: str) -> bool:
        """Whether the runtime ``name`` declares it touches no model: a stand-in."""
        ...


class Loop(BaseModel):
    """A subject's ``loop``: the rounds after the first, and when they stop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["self-review", "test-feedback"]
    #: Every round, the first included.
    rounds: int = Field(default=3, ge=2)
    #: The next round's task: ``{prompt}``, ``{response}`` and, for test-feedback,
    #: ``{failures}`` are filled in.
    prompt: str | None = None
    #: Self-review stops when an answer contains this.
    until: str = VERIFIED


class SessionOptions(BaseModel):
    """An ``agent-session`` subject's options. The kind's declaration has checked the keys
    and their types already; this reads them, and the shapes inside them."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    #: Required, with no default: two runtimes spend money, so a subject names its own.
    runtime: str
    definition: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    tools: tuple[str, ...] | None = None
    max_turns: int | None = Field(default=None, ge=1)
    permission_mode: str | None = None
    setting_sources: tuple[str, ...] | None = None
    plugins: tuple[str | dict[str, Any], ...] = ()
    skills: tuple[str, ...] = ()
    fixtures: tuple[str, ...] = ()
    setup: tuple[str, ...] = ()
    loop: Loop | None = None
    runtime_options: dict[str, Any] = {}


def _short(runtime: str) -> str:
    return runtime.removeprefix("matrix.v1.runtime.")


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
    """The runtime's own config: ``runtime_options``, and for ``claude-sdk`` the SDK's
    options written beside it. Raises ValueError for an SDK option on another runtime."""
    config = dict(options.runtime_options)
    runtime = _short(options.runtime)
    if runtime == "claude-sdk":
        if options.permission_mode is not None:
            config["permission_mode"] = options.permission_mode
        sources = None if options.setting_sources is None else list(options.setting_sources)
        if options.skills:
            # The session finds its skills in its own .claude/skills, which it reads as
            # project settings: nothing else is there to read.
            sources = sorted({*(sources or ()), "project"})
        if sources is not None:
            config["setting_sources"] = sources
        if options.plugins:
            config["plugins"] = _plugins(options, base)
    else:
        given = [key for key in _SDK_OPTIONS if getattr(options, key) not in (None, ())]
        if given:
            raise ValueError(
                f"{', '.join(given)} {'is an option' if len(given) == 1 else 'are options'} of "
                f"the claude-sdk runtime, and this subject runs on {runtime}"
            )
    if runtime == "mock" and canned is not None:
        config["default"] = canned
    return config


@dataclass
class Round:
    """One round of a session: the task it was given, what came back, and what the loop
    told the agent next (failed tests), if anything."""

    number: int
    task: str
    response: matrix.AgentResponse
    feedback: list[str] = field(default_factory=list)


class RoundsError(Exception):
    """A session that failed after some rounds had run: what those rounds spent."""

    def __init__(self, error: BaseException, cost_usd: float | None) -> None:
        super().__init__(str(error))
        self.error = error
        self.cost_usd = cost_usd


def _cost(rounds: Sequence[Round]) -> float | None:
    """What ``rounds`` cost, as their runtime reported it; None when none reported a cost."""
    known = [r.response.cost_usd for r in rounds if r.response.cost_usd is not None]
    return round(sum(known), 6) if known else None


@dataclass
class Session:
    """One trial's agent: what it is, where it runs, and how it is asked."""

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
        config = _runtime_config(self.options, self.base, canned=canned)
        runtime = self.runtimes.build(self.runtime, config, cwd=str(self.workdir))
        response: matrix.AgentResponse = await matrix.BoundAgent(self.definition, runtime).run(task)
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
        the ``mock`` runtime gives; ``feedback`` the tests a test-feedback loop runs.

        Raises :class:`RoundsError` holding the runtime's error and what the rounds before
        it spent."""
        loop = self.options.loop
        rounds: list[Round] = []
        task = prompt
        try:
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
        except Exception as error:
            raise RoundsError(error, _cost(rounds)) from error
        return rounds


class Sessions:
    """The sessions open now, by handle: how a subject and a probe built over the same table
    share a live session while a target carries only its handle."""

    def __init__(self) -> None:
        self._open: dict[str, Session] = {}

    def open(self, session: Session) -> str:
        handle = uuid.uuid4().hex
        self._open[handle] = session
        return handle

    def close(self, handle: str) -> None:
        self._open.pop(handle, None)

    def __getitem__(self, handle: str) -> Session:
        try:
            return self._open[handle]
        except KeyError:
            raise HarnessError(
                "no_session", f"no session is open under {handle!r}: the subject was released"
            ) from None


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


def _inapplicable(options: SessionOptions, runtime: str, path: str) -> list[Problem]:
    """An SDK option on a subject that runs on another runtime, which would refuse it."""
    if runtime == "claude-sdk":
        return []
    given = [key for key in _SDK_OPTIONS if getattr(options, key) not in (None, ())]
    return [
        problem(
            f"{path}.{key}",
            kind="option_inapplicable",
            message=f"{key} is an option of the claude-sdk runtime, and this subject runs on "
            f"{runtime}, which would refuse it",
            fix=f"remove {' and '.join(repr(name) for name in given)}, or run the subject "
            "on claude-sdk (runtime: claude-sdk)",
            allowed=("claude-sdk",),
            got=runtime,
        )
        for key in given
    ]


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


def _unobservable(options: SessionOptions, runtime: str, around: Surroundings) -> list[Problem]:
    """A session written so that what the sensors read can never happen: a run of it would
    report a confident verdict on a harness that could not see the agent."""
    path = around.path
    sensors = {sensor.kind: sensor.options for sensor in around.sensors}
    activation = ACTIVATION in sensors
    tools, where = _tools(options, around.base)
    problems: list[Problem] = []

    def unobservable(key: str, message: str, fix: str) -> None:
        problems.append(problem(f"{path}.{key}", kind="unobservable", message=message, fix=fix))

    if runtime == "model":
        for kind, what in [(ACTIVATION, "a skill fires"), (TOOL_USAGE, "a tool is called")]:
            if kind in sensors:
                unobservable(
                    "runtime",
                    f"the model runtime makes one call with no tools, and sensor "
                    f"{kind.rsplit('.', 1)[-1]} reads whether {what}: it never can",
                    "run the subject on claude-sdk (runtime: claude-sdk)",
                )
    if runtime != "claude-sdk":
        return problems
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
    if activation and not options.skills and not options.plugins and options.setting_sources == ():
        unobservable(
            "setting_sources",
            "the session reads no settings and is given no skills or plugins, so there is "
            "no skill for the activation sensor to see fire",
            "give the skills under test (skills: [<folder holding SKILL.md>] or plugins: "
            "[<plugin folder>]), or the settings that hold them (setting_sources: [project])",
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
    if collecting and (tools == () or options.permission_mode == "plan"):
        cannot = "may call no tool" if tools == () else "runs in plan mode, which writes nothing"
        unobservable(
            where if tools == () else "permission_mode",
            f"{', '.join(collecting)} collect files the agent writes, and the session "
            f"{cannot}: nothing would be collected",
            "let the session write: give it the tools that write files (Write, Edit, Bash) "
            "and a permission_mode that allows them (acceptEdits, bypassPermissions)",
        )
    return problems


class AgentSessionSubject:
    """A fresh agent session per trial, in a working directory of its own: see the module
    docstring. Offers ``agent`` (the session's handle) and ``workdir``. A
    :class:`~ix.domain.ports.Review`, a :class:`~ix.domain.ports.Preflight` and a
    :class:`~ix.domain.ports.StandIn`."""

    def __init__(self, sessions: Sessions, runtimes: Runtimes, launcher: Launcher) -> None:
        self._sessions = sessions
        self._runtimes = runtimes
        self._launcher = launcher

    def review(self, options: Mapping[str, Any], around: Surroundings) -> list[Problem]:
        """An option of another runtime, and a session that leaves its sensors nothing to
        observe; nothing for options of the wrong shape, or filled in per cell."""
        if any(placeholders(value) for value in options.values()):
            return []
        try:
            parsed = SessionOptions.model_validate(options)
        except ValidationError:
            return []
        runtime = _short(parsed.runtime)
        return [
            *_inapplicable(parsed, runtime, around.path),
            *_unobservable(parsed, runtime, around),
        ]

    def stand_in(self, options: Mapping[str, Any]) -> StoodIn:
        """The session on ``mock``: the runtime's own options set aside, the rest kept, so
        the fixtures, setup, loop and sensors run as they would."""
        dropped = [
            key
            for key in (*_SDK_OPTIONS, "runtime_options")
            if options.get(key) not in (None, [], (), {})
        ]
        kept = {key: value for key, value in options.items() if key not in dropped}
        notes = (
            [f"{', '.join(dropped)} set aside: the {STAND_IN} runtime takes none of them"]
            if dropped
            else []
        )
        return StoodIn(options={**kept, "runtime": STAND_IN}, notes=tuple(notes))

    def unstartable(self, subject: SubjectConfig, *, base: str) -> str | None:
        """What can be told before anything starts: the options' shapes, the files they
        name, the runtime and whether it can play the definition, the setup program."""
        try:
            options = _options(subject)
        except ValidationError as error:
            first = error.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            return f"{where}: {first['msg']}"
        if any("{" in str(value) for value in subject.options.values()):
            return None  # filled per cell: told when the trial starts
        named = [*options.fixtures, *options.skills, *filter(None, [options.definition])]
        named += [p if isinstance(p, str) else str(p.get("path", "")) for p in options.plugins]
        for path in named:
            if "*" in path:
                # Only a plugin path may match several: one that matches none would run the
                # session without the plugins the experiment meant to measure.
                if not glob(str(_within(base, path))):
                    return f"{path!r} matches nothing in {base}"
            elif not _within(base, path).exists():
                return f"{path!r} is not at {_within(base, path)}"
        if (
            options.setup
            and shutil.which(options.setup[0]) is None
            and not _within(base, options.setup[0]).is_file()
        ):
            return f"setup program {options.setup[0]!r} is not on PATH"
        try:
            definition = _definition("preflight", options, base)
            runtime = self._runtimes.build(
                options.runtime, _runtime_config(options, base), cwd=None
            )
            runtime.check(definition)
        except (matrix.MatrixError, ValidationError, ValueError) as error:
            return str(error)
        return None

    @asynccontextmanager
    async def reach(self, name: str, subject: SubjectConfig, *, base: str) -> AsyncIterator[Target]:
        try:
            options = _options(subject)
            definition = _definition(name, options, base)
        except (matrix.MatrixError, ValidationError) as error:
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
                simulated=self._runtimes.simulated(options.runtime),
                runtimes=self._runtimes,
                launcher=self._launcher,
            )
            handle = self._sessions.open(session)
            try:
                yield Target(
                    subject=name,
                    facets={"agent": handle, "workdir": str(workdir)},
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
    """Puts the probe's prompt to the session the subject opened, and leaves behind
    ``response`` (the prompt, the final text, every tool call, the transcript of every
    round, how it stopped, the files collected) and ``usage`` (tokens, turns, tool calls,
    duration and cost added over the rounds, the model and family that answered, and
    whether a stand-in did)."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    def budget_s(self, options: Mapping[str, Any]) -> float | None:
        return seconds(options.get("timeout")) or SESSION_TIMEOUT_S

    def stand_in(self, options: Mapping[str, Any]) -> StoodIn:
        """The probe as it is: a stand-in replies with its ``mock_response``."""
        if options.get("mock_response") is not None:
            return StoodIn(options=options)
        note = "no mock_response, so the mock runtime replies mock:<subject>:<prompt>"
        return StoodIn(options=options, notes=(note,))

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        session = self._sessions[str(target.facets["agent"])]
        prompt = str(options["prompt"])
        canned = options.get("mock_response")
        try:
            rounds = await session.ask(
                prompt,
                canned=None if canned is None else str(canned),
                feedback=options.get("feedback"),
            )
        except RoundsError as failed:
            raise _owned(failed.error, failed.cost_usd) from failed.error
        last = rounds[-1].response
        calls = [call for r in rounds for call in r.response.tool_calls]
        response = {
            "prompt": prompt,
            "text": last.content,
            "stop": last.stop,
            "tool_calls": calls,
            "transcript": _trace(rounds),
            "files": _collected(session.workdir, options.get("collect")),
        }
        tokens_in = sum(r.response.tokens_input for r in rounds)
        tokens_out = sum(r.response.tokens_output for r in rounds)
        usage = {
            "runtime": _short(session.runtime),
            "model": last.model,
            "family": last.family,
            "provider": last.provider,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "tokens_total": tokens_in + tokens_out,
            COST: _cost(rounds),
            "turns": sum(r.response.num_turns for r in rounds),
            "tool_calls": len(calls),
            "duration_ms": sum(r.response.duration_ms for r in rounds),
            "rounds": len(rounds),
            "stop": last.stop,
            "simulated": session.simulated,
        }
        return {"response": response, "usage": usage}


def _owned(error: BaseException, cost_usd: float | None) -> Exception:
    """The trial failure ``error`` is: the harness's for an infrastructure reason or an
    apparatus fault, the subject's for a session that ran and broke or was refused."""
    if isinstance(error, HarnessError | SubjectError):
        failure: HarnessError | SubjectError = error
    elif isinstance(error, matrix.AgentRuntimeError):
        whose = HarnessError if error.reason in HARNESS_REASONS else SubjectError
        failure = whose(error.reason, str(error))
    elif isinstance(error, matrix.MatrixError | ValueError):
        failure = HarnessError("runtime", f"the session's runtime could not be built: {error}")
    else:
        failure = HarnessError("error", f"{type(error).__name__}: {error}")
    failure.cost_usd = cost_usd
    return failure
