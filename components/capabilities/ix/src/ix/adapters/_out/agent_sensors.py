"""The sensors that read an agent session: ``activation``, ``tool-usage``, ``function-test``,
``outcome``, ``text-match`` and ``usage``.

Each reads what a ``prompt`` probe left behind (the ``response`` and ``usage`` facets: see
:mod:`ix.adapters._out.matrix_agents`) and the probe's truth, and none calls a model. The
function tests and the graders run in a Python process of their own
(:mod:`ix.adapters._out.python_checks`), never in ix's.

A truth that says nothing a sensor can read (no test cases, an expectation it does not
know) is the sensor's failure, raised: the trial happened and the instrument could not read
it. An answer that is wrong (no code in it, a skill that did not fire) is a reading.
"""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING, Any

from ix.adapters._out.python_checks import TIMEOUT_S, GraderError, code_in, run_cases, run_grader
from ix.domain.kind import seconds
from ix.domain.ports import Sensed
from ix.domain.problem import Problem, problem

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ix.adapters._out.processes import Launcher
    from ix.domain.ports import Measured, Surroundings
    from ix.domain.run import Interaction, Target

#: What a probe's truth says should happen, for the sensors that read an ``expectation``.
MUST_TRIGGER = "must_trigger"
SHOULD_NOT_TRIGGER = "should_not_trigger"
ACCEPTABLE = "acceptable"
EXPECTATIONS = (MUST_TRIGGER, SHOULD_NOT_TRIGGER, ACCEPTABLE)

#: The label a confusion table uses for nothing expected, or nothing observed.
NONE = "none"


def _response(interaction: Interaction) -> Mapping[str, Any]:
    return interaction.observations["response"]


def _expectation(truth: Mapping[str, Any]) -> str:
    expectation = str(truth.get("expectation", MUST_TRIGGER))
    if expectation not in EXPECTATIONS:
        raise ValueError(
            f"expectation {expectation!r} is not one of {', '.join(EXPECTATIONS)}; correct the "
            "probe's truth"
        )
    return expectation


class _Reads:
    """A sensor that watches nothing: it reads what the probe left behind."""

    def watch(self, target: Target, options: Mapping[str, Any]) -> None:
        return None


class ActivationSensor(_Reads):
    """Whether the expected skill fired: a call of the ``Skill`` tool (option ``tool``) whose
    ``skill`` names it. ``must_trigger`` passes when it fired, ``should_not_trigger`` when no
    skill fired at all, ``acceptable`` always. Names are compared whole, never as parts:
    ``expected_skill: review`` is fired by ``review`` or ``plugin:review``, and
    ``expected_skill: plugin:review`` only by ``plugin:review``.

    Its detail is what a confusion table needs: ``expected`` (the skill, or ``none`` for a
    decoy) and ``fired`` (the skill that fired, written as the expected name is: without its
    plugin's prefix unless the expected name has one; or ``none``), with every skill called
    as written.
    """

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        tool = str(options.get("tool") or "Skill")
        expectation = _expectation(truth)
        expected = truth.get("expected_skill")
        expected = str(expected) if expected else None
        skills = [
            str((call.get("input") or {}).get("skill", ""))
            for call in _response(interaction).get("tool_calls", [])
            if call.get("name") == tool
        ]
        qualified = expected is not None and ":" in expected

        def named(skill: str) -> str:
            """The skill as the expected name is written: with its plugin, or without."""
            return skill if qualified else skill.rsplit(":", 1)[-1]

        matching = [skill for skill in skills if named(skill) == expected]
        fired = matching[0] if matching else (skills[0] if skills else None)
        hit = fired is not None and (expected is None or named(fired) == expected)
        passed = {MUST_TRIGGER: hit, SHOULD_NOT_TRIGGER: fired is None, ACCEPTABLE: True}
        label = NONE if fired is None else named(fired)
        detail: dict[str, Any] = {
            "expectation": expectation,
            "fired": label,
            "skills_called": skills,
        }
        if expectation != ACCEPTABLE:
            detail["expected"] = (expected or "any") if expectation == MUST_TRIGGER else NONE
        return Sensed(values={"activated_correctly": passed[expectation]}, detail=detail)


def _segments(command: str) -> list[str]:
    """The simple commands of a shell line: split on ``&&``, ``||``, ``;`` and ``|``."""
    return [part.strip() for part in re.split(r"&&|\|\||;|\|", command) if part.strip()]


def _parsed(command: str, tool: str) -> dict[str, str] | None:
    """``memex dig "auth" --limit 3`` as ``{command: dig, args: auth}``: the first word after
    the tool that is not a flag, then the rest that are not flags. None when no simple
    command of ``command`` runs ``tool``."""
    for segment in _segments(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        names = [word.rsplit("/", 1)[-1] for word in words]
        if tool not in names:
            continue
        rest = [word for word in words[names.index(tool) + 1 :] if not word.startswith("-")]
        return {"command": rest[0] if rest else "", "args": " ".join(rest[1:])}
    return None


def _uses(tool_calls: Sequence[Mapping[str, Any]], tool: str) -> list[dict[str, str]]:
    """Each use of ``tool``: called by that name (its input's ``command`` and ``query`` or
    ``args``), or run as a program in a ``Bash`` command line."""
    direct = [
        {
            "via": "tool",
            "command": str((call.get("input") or {}).get("command", "")),
            "args": " ".join(
                str((call.get("input") or {}).get(key, "")) for key in ("query", "args")
            ).strip(),
        }
        for call in tool_calls
        if call.get("name") == tool
    ]
    if direct:
        return direct
    found = []
    for call in tool_calls:
        if call.get("name") != "Bash":
            continue
        parsed = _parsed(str((call.get("input") or {}).get("command", "")), tool)
        if parsed is not None:
            found.append({"via": "Bash", **parsed})
    return found


class ToolUsageSensor(_Reads):
    """Whether the agent used the tool (option ``tool``) with the command the probe expects
    (``expected_command``, compared whole and ignoring case: ``dig`` is not ``digest``) and,
    when given, arguments containing ``expected_args``: called by name, or run as a program
    through ``Bash`` (``cd x && memex dig "q" | head``).

    Scores 1 for the command and the arguments, 0.5 for the command alone (a pass: the right
    tool was reached for), 0 otherwise. Under
    ``should_not_trigger`` any use of the tool scores 0. Its detail holds ``expected`` and
    ``used`` (the command, or ``none``) for a confusion table.
    """

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        tool = str(options["tool"])
        expectation = _expectation(truth)
        expected = str(truth.get("expected_command") or "")
        wanted_args = str(truth.get("expected_args") or "")
        uses = _uses(_response(interaction).get("tool_calls", []), tool)
        detail: dict[str, Any] = {"expectation": expectation, "uses": uses}
        if expectation == ACCEPTABLE:
            score = 1.0
        elif expectation == SHOULD_NOT_TRIGGER:
            score = 0.0 if uses else 1.0
            used = (uses[0]["command"] or tool) if uses else NONE
            detail |= {"expected": NONE, "used": used}
        else:
            if not expected:
                raise ValueError("expectation must_trigger needs expected_command in the truth")
            right = [use for use in uses if use["command"].casefold() == expected.casefold()]
            argued = [use for use in right if wanted_args.lower() in use["args"].lower()]
            score = 1.0 if argued else 0.5 if right else 0.0
            used = expected if right else (uses[0]["command"] if uses else NONE)
            detail |= {"expected": expected, "used": used}
        return Sensed(
            values={"tool_used_correctly": score >= 0.5, "tool_usage_score": score}, detail=detail
        )


def _weights(cases: Sequence[Mapping[str, Any]], tiers: Mapping[str, Any] | None) -> list[float]:
    """Each case's share of the score: equal shares, or with ``tiers`` each tier's weight
    divided among its cases and the whole scaled to the tiers present."""
    if not tiers:
        return [1 / len(cases)] * len(cases)
    named = [str(case.get("tier", "")) for case in cases]
    unknown = sorted({tier for tier in named if tier not in tiers})
    if unknown:
        raise ValueError(
            f"test cases name tiers {unknown} that tiers does not weigh; tiers: {sorted(tiers)}"
        )
    present = {tier: named.count(tier) for tier in set(named)}
    total = sum(float(tiers[tier]) for tier in present)
    if total <= 0:
        raise ValueError("the tiers in use weigh nothing")
    return [float(tiers[tier]) / present[tier] / total for tier in named]


class FunctionTestSensor(_Reads):
    """Runs the probe's ``test_cases`` against ``function_name`` in the code the agent gave:
    the last python block of its response, or a file it wrote (option ``file``, one the probe
    collected). A case is ``{input, expected}`` or ``{input, raises}`` (an exception's name,
    or ``any``: code that must refuse an input), optionally with a ``tier`` that the truth's
    ``tiers`` weighs (``{basic: 0.5, edge: 0.3, security: 0.2}``) and a ``description``.

    The code runs in a Python process of its own with a time limit (option ``timeout``):
    see :mod:`ix.adapters._out.python_checks` for what that does and does not protect.
    """

    def __init__(self, launcher: Launcher) -> None:
        self._launcher = launcher

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        function_name, cases = truth.get("function_name"), truth.get("test_cases")
        if not function_name or not isinstance(cases, list) or not cases:
            raise ValueError("function-test needs function_name and test_cases in the truth")
        weights = _weights(cases, truth.get("tiers"))
        response = _response(interaction)
        file = options.get("file")
        if file:
            code = (response.get("files") or {}).get(str(file))
            source = f"the file {file}"
        else:
            code = code_in(str(response.get("text", "")))
            source = "the response"
        if not code:
            failures = [f"{source} holds no code" if not file else f"{file} was not written"]
            results = [False] * len(cases)
        else:
            timeout_s = seconds(options.get("timeout")) or TIMEOUT_S
            run = await run_cases(
                self._launcher, code, str(function_name), cases, timeout_s=timeout_s
            )
            results = [result.passed for result in run.results]
            failures = [
                f"{case.get('description') or f'case {index}'}: {result.message}"
                for index, (case, result) in enumerate(zip(cases, run.results, strict=True))
                if not result.passed
            ]
        score = round(sum(w for w, ok in zip(weights, results, strict=True) if ok), 12)
        pass_at = float(options.get("pass_at", 1.0))
        return Sensed(
            values={
                "tests_passed": score >= pass_at - 1e-12,
                "tests_pass_share": {"events": sum(results), "units": len(results)},
                "tests_score": score,
            },
            detail={"failures": failures[:20], "tested": source},
        )


class OutcomeSensor(_Reads):
    """Grades the response with the experiment's own graders (option ``graders``: a module
    whose ``GRADERS`` maps a probe id, or the name a probe's truth gives as ``grader``, to a
    function of the response text, or of the text and the trial, returning a score from 0
    to 1). A probe with no grader is graded by the share of its ``expected_facts`` the
    response contains, ignoring case.

    The graders run in a Python process of their own (see
    :mod:`ix.adapters._out.python_checks`); the response reaches them as data.
    """

    def __init__(self, launcher: Launcher) -> None:
        self._launcher = launcher

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        response = _response(interaction)
        text = str(response.get("text", ""))
        name = str(truth.get("grader") or interaction.probe)
        trial = {
            "probe": interaction.probe,
            "truth": dict(truth),
            "files": dict(response.get("files") or {}),
            "tool_calls": list(response.get("tool_calls") or []),
        }
        timeout_s = seconds(options.get("timeout")) or TIMEOUT_S
        try:
            score = await run_grader(
                self._launcher, str(options["graders"]), name, text, trial, timeout_s=timeout_s
            )
        except GraderError as error:
            raise ValueError(str(error)) from error
        detail: dict[str, Any] = {"graded_by": f"grader {name}"}
        if score is None:
            facts = truth.get("expected_facts")
            if not isinstance(facts, list) or not facts:
                raise ValueError(
                    f"the graders have no {name!r} and the probe's truth has no expected_facts"
                )
            found = [fact for fact in facts if str(fact).lower() in text.lower()]
            score = len(found) / len(facts)
            missed = [fact for fact in facts if fact not in found]
            detail = {"graded_by": "expected_facts", "found": found, "missed": missed}
        above = float(options.get("pass_above", 0.5))
        return Sensed(
            values={"outcome_score": score, "outcome_passed": score > above}, detail=detail
        )


#: The checks ``text-match`` makes, each from the sensor's options or a probe's truth.
TEXT_CHECKS = ("starts_with", "ends_with", "contains", "excludes", "equals", "matches")


def _texts(value: Any) -> list[str] | None:
    """A text, or a list of texts, as a list; None for anything else."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):
        return list(value)
    return None


def _check_problem(name: str, value: Any, path: str) -> Problem | None:
    """What is wrong with one check as written; None when nothing is."""
    if name in ("contains", "excludes"):
        if _texts(value) is None:
            return problem(
                path,
                kind="option_invalid",
                message=f"{name} is a text or a list of texts; got {value!r}",
                fix=f"{name}: BANANA, or {name}: [BANANA, split]",
            )
        return None
    if not isinstance(value, str):
        return problem(
            path,
            kind="option_invalid",
            message=f"{name} is a text; got {value!r}",
            fix=f"{name}: BANANA",
        )
    if name == "matches":
        try:
            re.compile(value)
        except re.error as error:
            return problem(
                path,
                kind="option_invalid",
                message=f"matches is not a regular expression Python can compile: {error}",
                fix="correct the expression, and quote it in YAML",
                got=value,
            )
    return None


def _written_wrong(checks: Mapping[str, Any], path: str) -> list[Problem]:
    """What is wrong with each check in ``checks`` (options, or a truth) at ``path``."""
    found = []
    for name in TEXT_CHECKS:
        if name in checks:
            wrong = _check_problem(name, checks[name], f"{path}.{name}")
            if wrong is not None:
                found.append(wrong)
    return found


class TextMatchSensor(_Reads):
    """Checks the reply's text with plain rules, so a simple check needs no graders module:
    ``starts_with``, ``ends_with``, ``contains`` and ``excludes`` (a text or a list),
    ``equals`` and ``matches`` (a regular expression searched for anywhere). The sensor's
    options apply to every probe; a check in a probe's truth replaces the option of the
    same name. ``text_matched`` is true when every check given passes.

    ``strip`` (true when absent) ignores whitespace at either end of the reply;
    ``ignore_case`` (false) compares without regard to case. A probe with no check at all
    is the sensor's failure, and ``validate`` reports it beforehand, with any check written
    wrong. Its detail names each check and whether it passed.
    """

    def review(self, options: Mapping[str, Any], around: Surroundings) -> list[Problem]:
        """A check written wrong, and a probe left with none."""
        found = _written_wrong(options, around.path)
        given = any(name in options for name in TEXT_CHECKS)
        for at, probe in around.probes:
            found += _written_wrong(probe.truth, f"{at}.truth")
            if not given and not any(name in probe.truth for name in TEXT_CHECKS):
                found.append(
                    problem(
                        f"{at}.truth",
                        kind="option_missing",
                        message=f"text-match has no check for probe {probe.id}: neither its "
                        "truth nor the sensor's options give one",
                        fix=f"give the probe one ({', '.join(TEXT_CHECKS)}), as in "
                        "starts_with: BANANA; or the sensor one for every probe, as in "
                        "sensors: [{kind: text-match, starts_with: BANANA}]",
                        allowed=TEXT_CHECKS,
                    )
                )
        return found

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        checks = {name: options[name] for name in TEXT_CHECKS if name in options}
        checks |= {name: truth[name] for name in TEXT_CHECKS if name in truth}
        if not checks:
            raise ValueError(
                f"text-match has no check for probe {interaction.probe}: give one of "
                f"{', '.join(TEXT_CHECKS)} in its truth or the sensor's options"
            )
        text = str(_response(interaction).get("text", ""))
        if options.get("strip", True):
            text = text.strip()
        folded = options.get("ignore_case", False)

        def fold(value: str) -> str:
            return value.casefold() if folded else value

        passed: dict[str, bool] = {}
        for name, value in checks.items():
            texts = [fold(item) for item in _texts(value) or [str(value)]]
            match name:
                case "starts_with":
                    passed[name] = fold(text).startswith(texts[0])
                case "ends_with":
                    passed[name] = fold(text).endswith(texts[0])
                case "contains":
                    passed[name] = all(item in fold(text) for item in texts)
                case "excludes":
                    passed[name] = not any(item in fold(text) for item in texts)
                case "equals":
                    passed[name] = fold(text) == texts[0]
                case _:
                    flags = re.IGNORECASE if folded else 0
                    passed[name] = re.search(str(value), text, flags) is not None
        return Sensed(
            values={"text_matched": all(passed.values())},
            detail={"checks": passed, "failed": [name for name, ok in passed.items() if not ok]},
        )


class UsageSensor(_Reads):
    """What the session consumed and how it ended, from the ``usage`` facet. A number the
    runtime did not report is left out (the ``model`` runtime reports no cost)."""

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Mapping[str, Measured]:
        usage = interaction.observations["usage"]
        found: dict[str, Measured] = {
            "tokens_total": float(usage["tokens_total"]),
            "tokens_input": float(usage["tokens_input"]),
            "tokens_output": float(usage["tokens_output"]),
            "turns": float(usage["turns"]),
            "tool_calls": float(usage["tool_calls"]),
            "duration": float(usage["duration_ms"]),
            "rounds": float(usage["rounds"]),
            "hit_limit": usage.get("stop") not in (None, "completed"),
        }
        if usage.get("cost_usd") is not None:
            found["cost"] = float(usage["cost_usd"])
        return found
