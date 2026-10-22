"""Agent sessions over matrix's runtimes: the ``agent-session`` subject, the ``prompt``
probe, and the sensors that read what a session said and did.

The runtimes here are scripted test doubles of matrix's ``AgentRuntime`` port, handed over
as the composition root hands over matrix's: no test runs a model.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix
import pytest

from ix.adapters._out.agent_sensors import (
    ActivationSensor,
    FunctionTestSensor,
    OutcomeSensor,
    TextMatchSensor,
    ToolUsageSensor,
    UsageSensor,
)
from ix.adapters._out.matrix_agents import AgentSessionSubject, PromptProbe, Sessions
from ix.adapters._out.matrix_judge import STAND_IN_REPLY, JudgeSensor
from ix.adapters._out.matrix_process import MatrixLauncher
from ix.domain.design import SelectedSensor
from ix.domain.experiment import ProbeConfig, SubjectConfig
from ix.domain.ports import Sensed, Surroundings
from ix.domain.run import HarnessError, Interaction, SubjectError

if TYPE_CHECKING:
    from collections.abc import Mapping


def _reply(text: str = "", **fields: Any) -> matrix.AgentResponse:
    return matrix.AgentResponse(content=text, num_turns=1, stop="completed", **fields)


class Scripted:
    """A runtime that answers from a script, one reply a session, the last one repeated."""

    def __init__(self, *replies: matrix.AgentResponse | Exception) -> None:
        self.replies = list(replies)
        self.tasks: list[str] = []
        self.definitions: list[matrix.AgentDefinition] = []

    def check(self, definition: matrix.AgentDefinition) -> None:
        if definition.tools and self.refuses_tools:
            raise matrix.ConfigError("this runtime makes one call and takes no tools")

    refuses_tools = False

    async def run(self, definition: matrix.AgentDefinition, task: str) -> matrix.AgentResponse:
        self.tasks.append(task)
        self.definitions.append(definition)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


class Runtimes:
    """matrix's runtimes as the composition root hands them over, all one scripted one."""

    def __init__(self, runtime: Scripted, *, simulated: bool = False) -> None:
        self.runtime = runtime
        self._simulated = simulated
        self.built: list[tuple[str, dict[str, Any], str | None]] = []

    def build(self, name: str, config: Mapping[str, Any], *, cwd: str | None) -> Scripted:
        self.built.append((name, dict(config), cwd))
        return self.runtime

    def simulated(self, name: str) -> bool:
        return self._simulated


def _subject(runtime: str = "claude-sdk", **options: Any) -> SubjectConfig:
    """An ``agent-session`` subject. It names ``claude-sdk`` unless told otherwise, so the
    SDK's options apply; the scripted double plays it, and no model is called."""
    return SubjectConfig.model_validate({"kind": "agent-session", "runtime": runtime, **options})


async def _ask(
    runtimes: Runtimes,
    probe: Mapping[str, Any],
    *,
    base: Path,
    subject: SubjectConfig | None = None,
) -> tuple[Mapping[str, Mapping[str, Any]], Path, bool]:
    """Reach a subject, apply the prompt probe, release: what the probe left, the working
    directory the session had, and whether a stand-in answered."""
    sessions = Sessions()
    reached = AgentSessionSubject(sessions, runtimes, MatrixLauncher())
    async with reached.reach("agent", subject or _subject(), base=str(base)) as target:
        workdir = Path(target.facets["workdir"])
        seen = await PromptProbe(sessions).apply(probe, target, seed=0)
        assert workdir.is_dir()
    return seen, workdir, target.simulated


def _interaction(
    response: Mapping[str, Any] | None = None,
    usage: Mapping[str, Any] | None = None,
    probe: str = "ask",
) -> Interaction:
    return Interaction(
        seq=0,
        subject="agent",
        role="subject",
        probe=probe,
        started_ns=0,
        duration_ns=0,
        observations={"response": dict(response or {}), "usage": dict(usage or {})},
    )


def _calls(*calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {"tool_calls": [{"name": name, "input": given} for name, given in calls]}


# --- the session ---------------------------------------------------------------------------


class TestASessionPerTrial:
    async def test_a_trial_is_a_fresh_directory_seeded_and_removed(self, tmp_path: Path) -> None:
        (tmp_path / "corpus").mkdir()
        (tmp_path / "corpus" / "notes.txt").write_text("seed")
        (tmp_path / "skills" / "review").mkdir(parents=True)
        (tmp_path / "skills" / "review" / "SKILL.md").write_text("---\nname: review\n---\n")
        (tmp_path / "seed.py").write_text("open('seeded', 'w').write('yes')\n")
        subject = _subject(
            fixtures=["corpus", "seed.py"],
            skills=["skills/review"],
            setup=[sys.executable, "seed.py"],
        )
        runtimes = Runtimes(Scripted(_reply("done")))
        seen = {}

        class Peek(Scripted):
            async def run(self, definition: Any, task: str) -> matrix.AgentResponse:
                cwd = runtimes.built[-1][2]
                assert cwd is not None
                seen["files"] = sorted(
                    str(p.relative_to(cwd)) for p in Path(cwd).rglob("*") if p.is_file()
                )
                return _reply("done")

        runtimes.runtime = Peek()
        _, workdir, simulated = await _ask(
            runtimes, {"prompt": "Go."}, base=tmp_path, subject=subject
        )
        assert seen["files"] == [
            ".claude/skills/review/SKILL.md", "corpus/notes.txt", "seed.py", "seeded",
        ]  # fmt: skip
        assert not workdir.exists() and simulated is False
        assert not workdir.is_relative_to(tmp_path)  # outside the experiment, and its truth

    async def test_a_setup_that_fails_is_the_harnesss(self, tmp_path: Path) -> None:
        subject = _subject(setup=[sys.executable, "-c", "raise SystemExit('no corpus')"])
        with pytest.raises(HarnessError, match="no corpus") as failed:
            await _ask(Runtimes(Scripted(_reply())), {"prompt": "Go."}, base=tmp_path,
                       subject=subject)  # fmt: skip
        assert failed.value.reason == "setup_failed"

    async def test_the_definition_file_and_the_options_beside_it(self, tmp_path: Path) -> None:
        (tmp_path / "help-only.md").write_text(
            "---\nname: reviewer\ntools: Read, Bash\nmodel: haiku\nmaxTurns: 3\n---\n"
            "You only have --help.\n"
        )
        runtime = Scripted(_reply("ok"))
        subject = _subject(definition="help-only.md", model="sonnet", max_turns=10)
        await _ask(Runtimes(runtime), {"prompt": "Go."}, base=tmp_path, subject=subject)
        (definition,) = runtime.definitions
        assert definition.system_prompt == "You only have --help."
        assert (definition.tools, definition.model, definition.max_turns) == (
            ("Read", "Bash"), "sonnet", 10,
        )  # fmt: skip

    async def test_what_the_session_said_and_did_is_kept(self, tmp_path: Path) -> None:
        class Writes(Scripted):
            async def run(self, definition: Any, task: str) -> matrix.AgentResponse:
                Path(runtimes.built[-1][2] or ".", "solution.py").write_text("x = 1\n")
                return _reply(
                    "Wrote it.",
                    tool_calls=({"name": "Write", "input": {"file_path": "solution.py"}},),
                    tokens_input=100, tokens_output=20, cost_usd=0.03, duration_ms=900,
                    model="claude-x", family="claude",
                )  # fmt: skip

        runtimes = Runtimes(Writes())
        seen, _, _ = await _ask(
            runtimes, {"prompt": "Write it.", "collect": ["solution.py", "../escape"]},
            base=tmp_path,
        )  # fmt: skip
        response, usage = seen["response"], seen["usage"]
        assert (response["prompt"], response["text"], response["stop"]) == (
            "Write it.", "Wrote it.", "completed",
        )  # fmt: skip
        assert response["tool_calls"] == [{"name": "Write", "input": {"file_path": "solution.py"}}]
        assert response["files"] == {"solution.py": "x = 1\n"}
        assert [r["round"] for r in response["transcript"]] == [1]
        assert usage == {
            "runtime": "claude-sdk", "model": "claude-x", "family": "claude", "provider": None,
            "tokens_input": 100, "tokens_output": 20, "tokens_total": 120, "cost_usd": 0.03,
            "turns": 1, "tool_calls": 1, "duration_ms": 900, "rounds": 1,
            "stop": "completed", "simulated": False,
        }  # fmt: skip

    async def test_a_stand_in_runtime_is_marked_and_gives_the_canned_reply(
        self, tmp_path: Path
    ) -> None:
        runtimes = Runtimes(Scripted(_reply("canned")), simulated=True)
        subject = _subject(runtime="mock")
        seen, _, simulated = await _ask(
            runtimes, {"prompt": "Go.", "mock_response": "A canned answer."}, base=tmp_path,
            subject=subject,
        )  # fmt: skip
        assert simulated is True and seen["usage"]["simulated"] is True
        assert runtimes.built[-1][1] == {"default": "A canned answer."}

    async def test_the_sdks_options_reach_its_runtime_and_paths_are_absolute(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "plugins" / "a").mkdir(parents=True)
        (tmp_path / "plugins" / "b").mkdir(parents=True)
        (tmp_path / "skills" / "s").mkdir(parents=True)
        runtimes = Runtimes(Scripted(_reply()))
        subject = _subject(
            permission_mode="bypassPermissions", setting_sources=[], plugins=["plugins/*"],
            skills=["skills/s"], runtime_options={"fallback_model": "haiku"},
        )  # fmt: skip
        await _ask(runtimes, {"prompt": "Go."}, base=tmp_path, subject=subject)
        name, config, _ = runtimes.built[-1]
        assert name == "claude-sdk"
        assert config == {
            "fallback_model": "haiku",
            "permission_mode": "bypassPermissions",
            # Its skills are read from the session's own .claude, as project settings.
            "setting_sources": ["project"],
            "plugins": [
                {"type": "local", "path": str((tmp_path / "plugins" / p).resolve())}
                for p in ("a", "b")
            ],
        }


class TestRounds:
    async def test_self_review_asks_again_until_the_answer_is_verified(
        self, tmp_path: Path
    ) -> None:
        runtime = Scripted(
            _reply("draft", cost_usd=0.01, tokens_input=10),
            _reply("final\nSOLUTION_VERIFIED", cost_usd=0.02, tokens_input=30),
            _reply("never asked"),
        )
        subject = _subject(loop={"strategy": "self-review", "rounds": 5})
        seen, _, _ = await _ask(Runtimes(runtime), {"prompt": "Merge."}, base=tmp_path,
                                subject=subject)  # fmt: skip
        assert len(runtime.tasks) == 2 and "draft" in runtime.tasks[1]
        assert "Merge." in runtime.tasks[1] and "SOLUTION_VERIFIED" in runtime.tasks[1]
        usage = seen["usage"]
        assert (usage["rounds"], usage["cost_usd"], usage["tokens_input"]) == (2, 0.03, 40)
        assert seen["response"]["text"] == "final\nSOLUTION_VERIFIED"

    async def test_test_feedback_shows_the_failures_until_none_remain(self, tmp_path: Path) -> None:
        wrong = "```python\ndef double(x):\n    return x + 2\n```"
        right = "```python\ndef double(x):\n    return x * 2\n```"
        runtime = Scripted(_reply(wrong), _reply(right), _reply("never asked"))
        subject = _subject(loop={"strategy": "test-feedback", "rounds": 4})
        feedback = {
            "function_name": "double",
            "test_cases": [{"input": 3, "expected": 6, "description": "three"}],
        }
        seen, _, _ = await _ask(
            Runtimes(runtime), {"prompt": "Double it.", "feedback": feedback}, base=tmp_path,
            subject=subject,
        )  # fmt: skip
        assert len(runtime.tasks) == 2
        assert "three: got 5, expected 6" in runtime.tasks[1]
        rounds = seen["response"]["transcript"]
        assert (
            rounds[0]["feedback"] == ["- three: got 5, expected 6"] and rounds[1]["feedback"] == []
        )

    async def test_a_test_feedback_loop_without_tests_is_the_harnesss_failure(
        self, tmp_path: Path
    ) -> None:
        subject = _subject(loop={"strategy": "test-feedback"})
        with pytest.raises(HarnessError) as failed:
            await _ask(Runtimes(Scripted(_reply("x"))), {"prompt": "Go."}, base=tmp_path,
                       subject=subject)  # fmt: skip
        assert failed.value.reason == "no_feedback"


class TestWhoseFailure:
    @pytest.mark.parametrize(
        ("reason", "owner"),
        [("rate_limited", HarnessError), ("unavailable", HarnessError), ("auth", HarnessError),
         ("timeout", HarnessError), ("incapable", HarnessError), ("refused", SubjectError),
         ("failed", SubjectError)],
    )  # fmt: skip
    async def test_the_runtimes_reason_says_whose(
        self, reason: str, owner: type[Exception], tmp_path: Path
    ) -> None:
        error = matrix.AgentRuntimeError("it broke", reason=reason)  # type: ignore[arg-type]
        with pytest.raises(owner) as failed:
            await _ask(Runtimes(Scripted(error)), {"prompt": "Go."}, base=tmp_path)
        assert failed.value.reason == reason  # type: ignore[attr-defined]

    async def test_a_session_that_fails_after_a_round_is_owned_as_its_cause_says(
        self, tmp_path: Path
    ) -> None:
        """Changed requirement: a failure no longer carries what earlier rounds cost (spend
        is removed); it is owned by the runtime's reason, whatever ran before it."""
        error = matrix.AgentRuntimeError("it broke", reason="refused")
        runtime = Scripted(_reply("draft", cost_usd=0.05), error)
        subject = _subject(loop={"strategy": "self-review"})
        with pytest.raises(SubjectError) as failed:
            await _ask(Runtimes(runtime), {"prompt": "Go."}, base=tmp_path, subject=subject)
        assert (failed.value.reason, str(failed.value)) == ("refused", "it broke")


class TestPreflight:
    def _unstartable(self, tmp_path: Path, scripted: Scripted | None = None, **options: Any) -> Any:
        adapter = AgentSessionSubject(
            Sessions(), Runtimes(scripted or Scripted(_reply())), MatrixLauncher()
        )
        return adapter.unstartable(_subject(**options), base=str(tmp_path))

    def test_what_can_be_told_before_anything_starts(self, tmp_path: Path) -> None:
        assert self._unstartable(tmp_path) is None
        assert "is not at" in self._unstartable(tmp_path, fixtures=["corpus"])
        assert "is not at" in self._unstartable(tmp_path, definition="agents/x.md")
        # Regression: a plugin pattern that matched nothing passed, and the sessions ran
        # without the plugins the experiment was written to measure.
        assert "matches nothing" in self._unstartable(tmp_path, plugins=["plugins/*"])
        (tmp_path / "plugins" / "review").mkdir(parents=True)
        assert self._unstartable(tmp_path, plugins=["plugins/*"]) is None
        assert "not on PATH" in self._unstartable(tmp_path, setup=["no-such-program-anywhere"])
        assert "option of the claude-sdk runtime" in self._unstartable(
            tmp_path, runtime="model", permission_mode="default"
        )
        assert "strategy" in self._unstartable(tmp_path, loop={"strategy": "ralph"})

    def test_a_subject_that_names_no_runtime_never_starts_one(self, tmp_path: Path) -> None:
        """Regression: ``runtime`` defaulted to claude-sdk, and an experiment and a fixture
        that named none started paid sessions. Nothing is built for a subject without one."""
        runtimes = Runtimes(Scripted(_reply()))
        adapter = AgentSessionSubject(Sessions(), runtimes, MatrixLauncher())
        unnamed = SubjectConfig.model_validate({"kind": "agent-session", "max_turns": 1})
        said = adapter.unstartable(unnamed, base=str(tmp_path))
        assert said is not None and said.startswith("runtime:")
        assert runtimes.built == []

    def test_a_runtime_that_cannot_play_the_definition_says_so(self, tmp_path: Path) -> None:
        one_call = Scripted(_reply())
        one_call.refuses_tools = True
        said = self._unstartable(tmp_path, one_call, runtime="model", tools=["Bash"])
        assert said is not None and "no tools" in said


def _around(
    tmp_path: Path, *sensors: tuple[str, dict[str, Any]], probes: tuple[ProbeConfig, ...] = ()
) -> Surroundings:
    """The subject ``subjects.agent`` among ``sensors`` (each a short kind and its options)
    and ``probes``."""
    return Surroundings(
        path="subjects.agent",
        base=str(tmp_path),
        sensors=tuple(
            SelectedSensor(kind=f"ix.v1.sensor.{kind}", metrics=(), options=options)
            for kind, options in sensors
        ),
        probes=tuple((f"probes.{index}", probe) for index, probe in enumerate(probes)),
    )


class TestReview:
    """What validate learns from the subject: a session written so its sensors can see
    nothing is a problem before any trial runs, never a confident verdict after."""

    def _review(
        self, around: Surroundings, runtime: str = "claude-sdk", **options: Any
    ) -> set[tuple[str, str]]:
        adapter = AgentSessionSubject(Sessions(), Runtimes(Scripted(_reply())), MatrixLauncher())
        found = adapter.review({"runtime": runtime, **options}, around)
        return {(problem.path, problem.kind) for problem in found}

    def test_a_skill_cannot_fire_without_the_skill_tool(self, tmp_path: Path) -> None:
        """Regression: skills with tools: [] ran, the skill could never fire, and the run
        reported fails, 0 of 18, with no warning."""
        activation = _around(tmp_path, ("activation", {}))
        found = self._review(activation, skills=["skills/review"], tools=[])
        assert found == {("subjects.agent.tools", "unobservable")}
        assert self._review(activation, tools=["Read"]) == {
            ("subjects.agent.tools", "unobservable")
        }
        assert self._review(activation, tools=["Read", "Skill"]) == set()
        assert self._review(activation) == set()  # every tool
        # Skills with no sensor that reads them still cannot run without the tool.
        assert self._review(_around(tmp_path), skills=["s"], tools=["Read"]) == {
            ("subjects.agent.tools", "unobservable")
        }
        # The sensor's own tool is the one that must be there.
        named = _around(tmp_path, ("activation", {"tool": "Activate"}))
        assert self._review(named, tools=["Skill"]) == {("subjects.agent.tools", "unobservable")}

    def test_the_tools_a_definition_file_gives_count(self, tmp_path: Path) -> None:
        (tmp_path / "agent.md").write_text("---\nname: a\ntools: [Read]\n---\nBe brief.\n")
        found = self._review(_around(tmp_path, ("activation", {})), definition="agent.md")
        assert found == {("subjects.agent.definition", "unobservable")}

    def test_a_session_with_no_skill_anywhere(self, tmp_path: Path) -> None:
        activation = _around(tmp_path, ("activation", {}))
        assert self._review(activation, setting_sources=[]) == {
            ("subjects.agent.setting_sources", "unobservable")
        }
        assert self._review(activation, setting_sources=[], plugins=["plugins/review"]) == set()

    def test_tool_calls_on_a_runtime_that_makes_none(self, tmp_path: Path) -> None:
        for sensor in ("activation", "tool-usage"):
            around = _around(tmp_path, (sensor, {"tool": "memex"}))
            assert self._review(around, runtime="model") == {
                ("subjects.agent.runtime", "unobservable")
            }

    def test_a_tool_the_session_may_not_call(self, tmp_path: Path) -> None:
        around = _around(tmp_path, ("tool-usage", {"tool": "memex"}))
        assert self._review(around, tools=["Read"]) == {("subjects.agent.tools", "unobservable")}
        assert self._review(around, tools=["Bash"]) == set()  # run through Bash

    def test_files_to_collect_that_nothing_can_write(self, tmp_path: Path) -> None:
        probe = ProbeConfig(id="p", kind="prompt", options={"prompt": "x", "collect": ["a.py"]})
        around = _around(tmp_path, probes=(probe,))
        assert self._review(around, tools=[]) == {("subjects.agent.tools", "unobservable")}
        assert self._review(around, permission_mode="plan") == {
            ("subjects.agent.permission_mode", "unobservable")
        }
        assert self._review(around, permission_mode="acceptEdits") == set()

    def test_an_sdk_option_on_another_runtime_names_what_to_remove(self, tmp_path: Path) -> None:
        """Regression: validate said valid for runtime: mock with skills and permission_mode;
        only plan and run refused it, with a generic fix."""
        adapter = AgentSessionSubject(Sessions(), Runtimes(Scripted(_reply())), MatrixLauncher())
        found = adapter.review(
            {"runtime": "mock", "skills": ["s"], "permission_mode": "plan"}, _around(tmp_path)
        )
        assert {(p.path, p.kind) for p in found} == {
            ("subjects.agent.skills", "option_inapplicable"),
            ("subjects.agent.permission_mode", "option_inapplicable"),
        }
        assert all("remove 'permission_mode' and 'skills'" in p.fix for p in found)
        assert all("claude-sdk" in p.fix for p in found)

    def test_a_missing_runtime_is_left_to_the_declaration(self, tmp_path: Path) -> None:
        """The kind's declaration reports it (``option_missing``); the review adds no second
        problem for the same mistake."""
        adapter = AgentSessionSubject(Sessions(), Runtimes(Scripted(_reply())), MatrixLauncher())
        assert adapter.review({"tools": []}, _around(tmp_path, ("activation", {}))) == []

    def test_options_filled_per_cell_wait_for_the_cell(self, tmp_path: Path) -> None:
        assert self._review(_around(tmp_path, ("activation", {})), tools="{tools}") == set()


class TestStandIn:
    def test_a_session_stands_in_on_mock_with_the_sdk_options_set_aside(self) -> None:
        adapter = AgentSessionSubject(Sessions(), Runtimes(Scripted(_reply())), MatrixLauncher())
        stood = adapter.stand_in(
            {"skills": ["s"], "permission_mode": "plan", "tools": ["Read"], "max_turns": 2}
        )
        assert stood.options == {"tools": ["Read"], "max_turns": 2, "runtime": "mock"}
        assert stood.notes == (
            "permission_mode, skills set aside: the mock runtime takes none of them",
        )
        assert adapter.stand_in({"runtime": "mock"}).notes == ()

    def test_a_probe_without_a_canned_reply_says_what_the_mock_replies(self) -> None:
        probe = PromptProbe(Sessions())
        assert probe.stand_in({"prompt": "x", "mock_response": "y"}).notes == ()
        assert "no mock_response" in probe.stand_in({"prompt": "x"}).notes[0]

    def test_a_judge_stands_in_on_mock(self) -> None:
        stood = JudgeSensor(Runtimes(Scripted(_reply()))).stand_in({"rubric": "r", "model": "m"})
        assert stood.options["runtime"] == "mock"
        assert stood.options["runtime_options"] == {"default": STAND_IN_REPLY}
        assert stood.options["rubric"] == "r"


# --- the sensors ----------------------------------------------------------------------------


async def _sense(sensor: Any, interaction: Interaction, truth: Any, **options: Any) -> Sensed:
    found = await sensor.sense(interaction, truth=truth, options=options)
    return found if isinstance(found, Sensed) else Sensed(values=found)


class TestActivation:
    @pytest.mark.parametrize(
        ("expectation", "calls", "passed", "fired"),
        [("must_trigger", [("Skill", {"skill": "antifragile:aces"})], True, "aces"),
         ("must_trigger", [("Skill", {"skill": "dao:dao"})], False, "dao"),
         ("must_trigger", [], False, "none"),
         ("should_not_trigger", [], True, "none"),
         ("should_not_trigger", [("Skill", {"skill": "aces"})], False, "aces"),
         ("acceptable", [("Skill", {"skill": "x"})], True, "x")],
    )  # fmt: skip
    async def test_whether_the_expected_skill_fired(
        self, expectation: str, calls: list[Any], passed: bool, fired: str
    ) -> None:
        truth = {"expectation": expectation, "expected_skill": "aces"}
        found = await _sense(ActivationSensor(), _interaction(_calls(*calls)), truth)
        assert found.values == {"activated_correctly": passed}
        assert found.detail["fired"] == fired
        expected = {"must_trigger": "aces", "should_not_trigger": "none"}.get(expectation)
        assert found.detail.get("expected") == expected

    @pytest.mark.parametrize(
        ("expected", "called", "passed", "fired"),
        [("review", ["code-review-lite"], False, "code-review-lite"),
         ("review", ["reviewer"], False, "reviewer"),
         ("review", ["acme:review-lite"], False, "review-lite"),
         ("review", ["acme:review"], True, "review"),
         ("review", ["code-review-lite", "acme:review"], True, "review"),
         ("acme:review", ["acme:review"], True, "acme:review"),
         ("acme:review", ["other:review"], False, "other:review"),
         ("acme:review", ["review"], False, "review")],
    )  # fmt: skip
    async def test_a_near_miss_name_is_not_the_skill(
        self, expected: str, called: list[str], passed: bool, fired: str
    ) -> None:
        """Regression: the expected name was matched as a substring, so ``code-review-lite``
        firing read as ``review`` firing, and the detail recorded ``review`` as fired."""
        truth = {"expectation": "must_trigger", "expected_skill": expected}
        calls = [("Skill", {"skill": skill}) for skill in called]
        found = await _sense(ActivationSensor(), _interaction(_calls(*calls)), truth)
        assert found.values == {"activated_correctly": passed}
        assert found.detail["fired"] == fired
        assert found.detail["skills_called"] == called

    async def test_an_expectation_it_does_not_know_is_the_sensors_failure(self) -> None:
        with pytest.raises(ValueError, match="must_triger"):
            await _sense(ActivationSensor(), _interaction(), {"expectation": "must_triger"})


class TestToolUsage:
    @pytest.mark.parametrize(
        ("calls", "score", "used"),
        [([("memex", {"command": "dig", "query": "auth tokens"})], 1.0, "dig"),
         ([("Bash", {"command": 'cd corpus && memex dig "auth" --limit 3 | head'})], 1.0, "dig"),
         ([("Bash", {"command": "/usr/local/bin/memex dig sessions"})], 0.5, "dig"),
         ([("Bash", {"command": "memex search auth"})], 0.0, "search"),
         ([("Bash", {"command": "grep auth notes.md"})], 0.0, "none")],
    )  # fmt: skip
    async def test_the_command_and_its_arguments_with_partial_credit(
        self, calls: list[Any], score: float, used: str
    ) -> None:
        truth = {"expected_command": "dig", "expected_args": "auth"}
        found = await _sense(ToolUsageSensor(), _interaction(_calls(*calls)), truth, tool="memex")
        assert found.values == {"tool_usage_score": score, "tool_used_correctly": score >= 0.5}
        assert (found.detail["expected"], found.detail["used"]) == ("dig", used)

    @pytest.mark.parametrize(
        ("calls", "used"),
        [([("Bash", {"command": "memex digest auth"})], "digest"),
         ([("memex", {"command": "undig", "query": "auth"})], "undig")],
    )  # fmt: skip
    async def test_a_near_miss_command_is_not_the_command(
        self, calls: list[Any], used: str
    ) -> None:
        """Regression: ``expected_command: dig`` matched ``digest`` as a substring."""
        truth = {"expected_command": "dig", "expected_args": "auth"}
        found = await _sense(ToolUsageSensor(), _interaction(_calls(*calls)), truth, tool="memex")
        assert found.values == {"tool_usage_score": 0.0, "tool_used_correctly": False}
        assert (found.detail["expected"], found.detail["used"]) == ("dig", used)

    async def test_a_tool_used_where_it_should_not_be_scores_nothing(self) -> None:
        truth = {"expectation": "should_not_trigger"}
        used = _interaction(_calls(("Bash", {"command": "memex dig x"})))
        found = await _sense(ToolUsageSensor(), used, truth, tool="memex")
        assert found.values["tool_usage_score"] == 0.0
        quiet = await _sense(ToolUsageSensor(), _interaction(_calls()), truth, tool="memex")
        assert quiet.values["tool_usage_score"] == 1.0


_MERGE = {
    "function_name": "double",
    "test_cases": [
        {"input": 2, "expected": 4, "tier": "basic", "description": "two"},
        {"input": 0, "expected": 0, "tier": "edge", "description": "zero"},
        {"input": "x", "raises": "TypeError", "tier": "security", "description": "refuses"},
    ],
    "tiers": {"basic": 0.5, "edge": 0.3, "security": 0.2},
}


class TestFunctionTest:
    async def _run(self, text: str, truth: Any = None, **options: Any) -> Sensed:
        sensor = FunctionTestSensor(MatrixLauncher())
        return await _sense(sensor, _interaction({"text": text}), truth or _MERGE, **options)

    async def test_tiers_weigh_the_cases_and_a_case_may_require_a_raise(self) -> None:
        refuses = (
            "```python\ndef double(x):\n    if not isinstance(x, int):\n"
            "        raise TypeError\n    return 2 * x\n```"
        )
        found = await self._run(refuses)
        assert found.values == {
            "tests_passed": True, "tests_pass_share": {"events": 3, "units": 3},
            "tests_score": 1.0,
        }  # fmt: skip
        sloppy = "```python\ndef double(x):\n    return x * 2\n```"  # "x" * 2 does not raise
        found = await self._run(sloppy, pass_at=0.7)
        assert found.values["tests_score"] == pytest.approx(0.8)
        assert found.values["tests_passed"] is True
        assert found.detail["failures"] == [
            "refuses: returned 'xx' where TypeError was to be raised"
        ]

    async def test_code_that_raises_or_hangs_or_is_absent_scores_nothing(self) -> None:
        for text, said in [
            ("```python\nraise RuntimeError('boom')\n```", "did not load: RuntimeError: boom"),
            ("```python\ndef double(x):\n    while True: pass\n```", "overran"),
            ("I would double it.", "did not load"),
        ]:
            found = await self._run(text, timeout="2s")
            assert found.values["tests_score"] == 0.0 and found.values["tests_passed"] is False
            assert said in found.detail["failures"][0], found.detail

    async def test_a_file_the_agent_wrote_is_tested_instead_of_its_reply(self) -> None:
        sensor = FunctionTestSensor(MatrixLauncher())
        truth = {"function_name": "double", "test_cases": [{"input": 2, "expected": 4}]}
        wrote = _interaction(
            {"text": "Done.", "files": {"solution.py": "double = lambda x: 2 * x\n"}}
        )
        found = await _sense(sensor, wrote, truth, file="solution.py")
        assert found.values["tests_passed"] is True
        missing = await _sense(sensor, _interaction({"text": "Done."}), truth, file="solution.py")
        assert missing.detail["failures"] == ["solution.py was not written"]

    async def test_a_truth_it_cannot_read_is_the_sensors_failure(self) -> None:
        with pytest.raises(ValueError, match="function_name and test_cases"):
            await self._run("x", {"function_name": "double"})
        with pytest.raises(ValueError, match="tiers"):
            await self._run("x", {**_MERGE, "tiers": {"basic": 1}})

    async def test_the_code_sees_no_credential_of_ix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        leak = (
            "```python\nimport os\n"
            "def double(x):\n    return os.environ.get('ANTHROPIC_API_KEY')\n```"
        )
        truth = {"function_name": "double", "test_cases": [{"input": 1, "expected": None}]}
        assert (await self._run(leak, truth)).values["tests_passed"] is True


class TestOutcome:
    async def _run(
        self, tmp_path: Path, source: str, text: str, truth: Any = None, probe: str = "ask"
    ) -> Sensed:
        module = tmp_path / "graders.py"
        module.write_text(source)
        sensor = OutcomeSensor(MatrixLauncher())
        interaction = _interaction({"text": text, "files": {"a.txt": "A"}}, probe=probe)
        return await _sense(sensor, interaction, truth or {}, graders=str(module))

    async def test_the_experiments_grader_for_the_probe(self, tmp_path: Path) -> None:
        source = (
            "def by_text(text):\n    return 0.75 if 'yaml' in text else 0.0\n"
            "def with_trial(text, trial):\n"
            "    return 1.0 if trial['files']['a.txt'] == 'A' else 0\n"
            "GRADERS = {'ask': by_text, 'other': with_trial}\n"
        )
        found = await self._run(tmp_path, source, "here is yaml")
        assert found.values == {"outcome_score": 0.75, "outcome_passed": True}
        named = await self._run(tmp_path, source, "x", truth={"grader": "other"})
        assert named.values["outcome_score"] == 1.0

    async def test_expected_facts_grade_a_probe_with_no_grader(self, tmp_path: Path) -> None:
        found = await self._run(
            tmp_path, "GRADERS = {}\n", "Sort them, then join the Overlaps.",
            truth={"expected_facts": ["sort", "overlap", "stack"]},
        )  # fmt: skip
        assert found.values["outcome_score"] == pytest.approx(2 / 3)
        assert found.detail["missed"] == ["stack"]

    async def test_a_grader_that_cannot_grade_is_the_sensors_failure(self, tmp_path: Path) -> None:
        for source in ("GRADERS = {'ask': lambda t: 7}\n", "GRADERS = {'ask': lambda t: 1/0}\n",
                       "raise SystemExit\n"):  # fmt: skip
            with pytest.raises(ValueError, match="grader"):
                await self._run(tmp_path, source, "x")
        with pytest.raises(ValueError, match="no expected_facts"):
            await self._run(tmp_path, "GRADERS = {}\n", "x")


class TestTextMatch:
    """Regression: a rule like "the reply starts with BANANA" needed a graders module."""

    @pytest.mark.parametrize(
        ("text", "checks", "matched"),
        [("BANANA split", {"starts_with": "BANANA"}, True),
         ("  BANANA split\n", {"starts_with": "BANANA", "ends_with": "split"}, True),
         ("banana split", {"starts_with": "BANANA"}, False),
         ("banana split", {"starts_with": "BANANA", "ignore_case": True}, True),
         ("one two three", {"contains": ["one", "three"]}, True),
         ("one two three", {"contains": ["one", "four"]}, False),
         ("one two three", {"excludes": "four"}, True),
         ("one two three", {"excludes": ["two"]}, False),
         ("42", {"equals": "42"}, True),
         ("order 1234 shipped", {"matches": r"order \d{4}\b"}, True),
         ("ORDER 12 shipped", {"matches": r"^order \d+", "ignore_case": True}, True),
         ("  42  ", {"equals": "42", "strip": False}, False)],
    )  # fmt: skip
    async def test_each_check(self, text: str, checks: dict[str, Any], matched: bool) -> None:
        found = await _sense(TextMatchSensor(), _interaction({"text": text}), {}, **checks)
        assert found.values == {"text_matched": matched}

    async def test_a_probes_truth_replaces_the_option_of_the_same_name(self) -> None:
        reply = _interaction({"text": "BANANA split"})
        found = await _sense(TextMatchSensor(), reply, {"starts_with": "BANANA"}, starts_with="X")
        assert found.values == {"text_matched": True}
        both = await _sense(TextMatchSensor(), reply, {"starts_with": "BANANA"}, contains="pie")
        assert both.values == {"text_matched": False} and both.detail["failed"] == ["contains"]

    async def test_no_check_at_all_is_the_sensors_failure(self) -> None:
        with pytest.raises(ValueError, match="no check for probe ask"):
            await _sense(TextMatchSensor(), _interaction({"text": "x"}), {})

    def test_validate_hears_of_a_check_written_wrong_or_missing(self, tmp_path: Path) -> None:
        probes = (
            ProbeConfig(id="a", kind="prompt", truth={"starts_with": "B"}),
            ProbeConfig(id="b", kind="prompt", truth={"matches": "(open"}),
            ProbeConfig(id="c", kind="prompt"),
        )
        around = Surroundings(path="sensors.0", base=str(tmp_path), probes=tuple(
            (f"probes.{index}", probe) for index, probe in enumerate(probes)
        ))  # fmt: skip
        found = {(p.path, p.kind) for p in TextMatchSensor().review({}, around)}
        assert found == {
            ("probes.1.truth.matches", "option_invalid"),
            ("probes.2.truth", "option_missing"),
        }
        # A check the sensor gives every probe leaves none without one.
        found = {(p.path, p.kind) for p in TextMatchSensor().review({"contains": 3}, around)}
        assert found == {
            ("sensors.0.contains", "option_invalid"),
            ("probes.1.truth.matches", "option_invalid"),
        }


class TestUsage:
    async def test_what_the_runtime_reported(self) -> None:
        usage = {
            "tokens_total": 120, "tokens_input": 100, "tokens_output": 20, "turns": 3,
            "tool_calls": 2, "duration_ms": 900, "rounds": 1, "stop": "max_turns",
            "cost_usd": None,
        }  # fmt: skip
        found = await _sense(UsageSensor(), _interaction(usage=usage), {})
        assert "cost" not in found.values  # the runtime did not say
        assert found.values["hit_limit"] is True and found.values["turns"] == 3.0
        usage |= {"stop": "completed", "cost_usd": 0.02}
        found = await _sense(UsageSensor(), _interaction(usage=usage), {})
        assert found.values["cost"] == 0.02 and found.values["hit_limit"] is False


class TestJudge:
    async def _judge(self, *replies: matrix.AgentResponse, **options: Any) -> Sensed:
        """The judge on ``model``, played by the scripted double: no model is called."""
        interaction = _interaction({"prompt": "Merge.", "text": "def merge"}, {"family": "claude"})
        return await _sense(JudgeSensor(Runtimes(Scripted(*replies))), interaction, {},
                            rubric="Correct and clear.", runtime="model", **options)  # fmt: skip

    async def test_a_score_against_the_rubric_and_what_it_cost(self) -> None:
        reply = _reply('Here: {"score": 0.8, "reason": "clear"}', cost_usd=0.01, family="qwen")
        found = await self._judge(reply)
        assert found.values == {
            "judged_score": 0.8, "judged_pass": True, "judge_agreement": 1.0,
            "judge_inconsistent": False, "judge_in_family": False,
        }  # fmt: skip
        assert found.detail["reasons"] == ["clear"]
        # What the judge's runtime reported it cost is kept as a fact about the judge.
        assert found.detail["judge"]["cost_usd"] == 0.01

    async def test_repeats_check_the_judge_against_itself(self) -> None:
        found = await self._judge(
            _reply('{"score": 0.9}', family="claude"), _reply('{"score": 0.2}', family="claude"),
            _reply('{"score": 0.7}', family="claude"), repeats=3,
        )  # fmt: skip
        assert found.values["judged_score"] == pytest.approx(0.6)
        assert found.values["judge_agreement"] == pytest.approx(2 / 3)
        assert found.values["judge_inconsistent"] is True
        assert found.values["judge_in_family"] is True  # claude judging claude

    async def test_a_reply_with_no_score_is_the_sensors_failure(self) -> None:
        with pytest.raises(ValueError, match="no score"):
            await self._judge(_reply("I think it is good."))

    async def test_it_needs_a_rubric(self) -> None:
        sensor = JudgeSensor(Runtimes(Scripted(_reply('{"score": 1}'))))
        with pytest.raises(ValueError, match="rubric"):
            await sensor.sense(_interaction(), truth={}, options={"runtime": "model"})

    async def test_a_judge_that_names_no_runtime_never_starts_one(self) -> None:
        """Regression: ``runtime`` defaulted to ``model``, a paid model. Validation reports
        it missing; should a judge reach a trial without one, nothing is built."""
        runtimes = Runtimes(Scripted(_reply('{"score": 1}')))
        with pytest.raises(ValueError, match="needs a runtime"):
            await JudgeSensor(runtimes).sense(_interaction(), truth={}, options={"rubric": "r"})
        assert runtimes.built == []


def test_sessions_are_shared_by_handle_only() -> None:
    sessions = Sessions()
    with pytest.raises(HarnessError, match="no session"):
        sessions["gone"]


def test_no_test_here_can_reach_a_live_model() -> None:
    """The workspace's conftest refuses every call where a session reaches a model; this
    holds that it is in force for ix's tests, which build ``claude-sdk`` and ``model``
    sessions. The calls themselves are not made: one would fail this test at teardown."""
    import importlib
    import importlib.util

    guard = Path(__file__).resolve().parents[2] / "conftest.py"
    spec = importlib.util.spec_from_file_location("workspace_conftest", guard)
    assert spec is not None and spec.loader is not None, f"no guard at {guard}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checked = 0
    for module_name, attribute in module.LIVE_CALLS:
        try:
            found: Any = importlib.import_module(module_name)
        except ImportError:
            continue
        for part in attribute.split("."):
            found = getattr(found, part)
        assert found.__name__ == "refuse", f"{module_name}.{attribute} is not refused"
        checked += 1
    assert checked, "none of the live calls is installed here, so none was checked"
