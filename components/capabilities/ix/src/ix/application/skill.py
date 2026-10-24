"""The text ``ix --skill`` prints, and the references ``ix --skill -r <name>`` prints.

The skill is what an agent acts on first, so it holds only what acting needs: what ix is
for, a working example, the commands in the order they are used, and the errors and exit
codes. Everything else is a reference, printed on request. The frontmatter makes the
printed skill a valid ``SKILL.md`` as it stands.

Every list in either is generated from the records the code uses (genres, kinds, the exit
code table, the problem kinds, the experiment model), so a third party's genre or kind is
taught the same way as ix's own. The test suite validates every example in the text and
holds each section to where the skill promises it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, get_args

from ix.domain import schema
from ix.domain.analysis import METHOD
from ix.domain.errors import ERROR_KINDS, EXIT_CODES, EXIT_MEANINGS, EXIT_NEXT, UsageError
from ix.domain.experiment import ESTIMATORS, STATISTICS, Experiment, Threshold
from ix.domain.kind import KIND_FAMILIES, NOTE_PREFIX
from ix.domain.plan import VERDICTS
from ix.domain.problem import PROBLEM_KINDS

if TYPE_CHECKING:
    from ix.domain.genre import Genre, Genres
    from ix.domain.kind import Kind, Kinds, Option

NAME = "ix"

DESCRIPTION = (
    "Writes, checks, registers and runs ix experiments: one YAML file that answers a "
    "question about a service or an AI agent with a measured decision, such as whether it "
    "meets a threshold, which of two builds or agent setups is better or no worse, whether "
    "a change may ship against a baseline, or how behaviour changes across a range. This "
    'skill should be used when the user asks to "write an ix experiment", "evaluate an '
    'agent", "check whether a skill fires", "compare two prompts or agent setups", '
    '"grade an agent with tests", "compare two builds", "gate a release on latency", '
    '"check for a performance regression", or runs ix validate, ix plan, ix register or '
    "ix run."
)

#: Where a release of ix is installed from: this repository at the release's tag. ix is on no
#: package index, and the project of that name on PyPI is another.
SOURCE = "git+https://github.com/mox-labs/gnx@ix-v{version}#subdirectory=components/capabilities/ix"

#: The commands ix has, in the order an author uses them.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("ix validate <path>", "every problem at once, each with path, kind, message, fix"),
    ("ix plan <path>", "the decision, the trials, and the smallest effect it can detect"),
    ("ix register <path>", "freeze the design beside the file, before any run"),
    ("ix deviations <path>", "what changed since registration"),
    (
        "ix run <path> --dry-run",
        "the same file on mock replies: checks the harness, calls no model, never a verdict",
    ),
    (
        "ix run <path> --exploratory --repeats N",
        "a pilot with N repeats, the file unchanged: no verdict",
    ),
    (
        "ix run <path> --exploratory --trials-in-flight N",
        "N trials at once, the file unchanged: no verdict; refused where overlap confounds",
    ),
    ("ix run <path>", "carry it out and print the report; the exit code is the verdict"),
    ("ix report <path>", "the latest run's report again; or give a run directory"),
    ("ix runs <path>", "each run: when, its commit, gated, exploratory or dry, verdict, trials"),
    ("ix genres", "the genres installed, and any genre file ix refused"),
    ("ix kinds", "the kinds installed, and which of them this ix can run"),
)

#: The line that sits under :data:`COMMANDS` in the skill: the way to a reference.
REFERENCE_COMMAND = ("ix --skill -r <name>", "one reference, listed at the end of this text")

#: The references ``ix --skill -r <name>`` prints, in the order the skill lists them.
REFERENCES: dict[str, str] = {
    "experiment": "every key of the experiment file, the value forms, the rules validate applies",
    "genres": "each genre: what it decides, what it needs, its guidance and a minimal example",
    "kinds": "each subject kind, probe kind, sensor and environment kind: options and metrics",
    "running": "registration, running, the run record, and how a verdict is reached",
    "agents": "evaluating an agent end to end: sessions, probe files, sensors, dry runs, "
    "screens, the judge, and reading the report",
    "problems": "every problem kind validate reports",
}

#: What a runnable kind needs on the host beyond ix, by kind label. The tests hold each key
#: to a kind this ix can run, and ``http-load`` to the program its adapter starts.
PREREQUISITES: dict[str, str] = {
    "agent-session": "runs on one of matrix's agent runtimes: `claude-sdk` needs the Claude "
    "Code CLI and its credentials; `pydantic-ai` needs a hardline model (its "
    "`models` or matrix's) and makes every model call through hardline; `mock` needs nothing "
    "and calls no model. `ix plan` lists one that cannot start under `Runnable: no`",
    "model-call": "runs on one of matrix's model runtimes: `hardline` needs a hardline model; "
    "`mock` needs nothing and calls no model. `ix plan` lists a runtime that cannot start, or "
    "a model it cannot resolve, under `Runnable: no`",
    "function-test": "and `outcome` run Python in a child process with a time limit and no "
    "credentials in its environment; it is not a sandbox, so test code from a subject you "
    "would run yourself",
    "http-load": "runs `k6` from `PATH`: check with `k6 version`. `ix plan` lists it under "
    "`Runnable: no` when it is not there",
    "memory": "samples resident memory with `ps`, so it reads memory on macOS and Linux",
    "process": "starts the `command` it is given; `ix plan` lists a program that is not "
    "there under `Runnable: no`",
    "compose": "runs `docker compose` against a daemon that answers: `ix plan` checks docker, "
    "the daemon, compose and the file, and lists what fails under `Runnable: no`. A dry run "
    "brings the stack up for real",
}

#: The example the skill opens with. It runs as written where `python3` and `k6` are
#: installed, and restates no default; the test suite validates and plans it.
QUICK_START = """\
name: static-errors
question: Does a static file server answer at least 99% of requests at 20 per second?
genre: evaluate
subjects:
  server:
    kind: process
    command: [python3, -m, http.server, "18080", --bind, 127.0.0.1]
    url: "http://127.0.0.1:18080"
probes:
  - {kind: http-load, target: "{url}/", load_model: open, rate: 20/s, duration: 10s}
primary: {metric: error_rate}
threshold: {op: "<=", value: 0.01}
"""

#: A minimal example per built-in genre, by short name. None restates a default; the test
#: suite validates and plans each one as written.
EXAMPLES: dict[str, str] = {
    "evaluate": """\
name: proxy-error-budget
question: Does the proxy answer at least 99% of requests at 80 per second?
genre: evaluate
subjects:
  proxy: {kind: process, command: [./proxy], url: "http://127.0.0.1:8080"}
probes:
  - kind: http-load
    target: "{url}/blobs/1MiB"
    load_model: open
    rate: 80/s
    duration: 60s
primary: {metric: error_rate}
threshold: {op: "<=", value: 0.01}
""",
    "compare": """\
name: proxy-streaming
question: Does streaming keep time to first byte without costing memory?
genre: compare
subjects:
  buffered:  {kind: process, command: [./proxy, --buffered], url: "http://127.0.0.1:8080"}
  streaming: {kind: process, command: [./proxy, --streaming], url: "http://127.0.0.1:8080"}
probes:
  - kind: http-load
    target: "{url}/blobs/64MiB"
    load_model: open
    rate: 80/s
    duration: 60s
primary: {metric: ttfb, statistic: p99}
comparison: non-inferiority
margin: 5%
guardrails:
  - {metric: memory_peak, margin: 0%}
""",
    "characterize": """\
name: proxy-memory-curve
question: How does peak memory grow with artifact size and request rate?
genre: characterize
subjects:
  proxy: {kind: process, command: [./proxy], url: "http://127.0.0.1:8080"}
factors:
  size: [1MiB, 64MiB, 1GiB]
  rate: [10/s, 80/s, 640/s]
probes:
  - kind: http-load
    target: "{url}/blobs/{size}"
    load_model: open
    rate: "{rate}"
    duration: 60s
primary: {metric: memory_peak, estimator: max}
""",
    "regress": """\
name: proxy-gate
question: May this build ship against the last release?
genre: regress
subjects:
  baseline:  {kind: process, command: [./build/release/proxy], url: "http://127.0.0.1:8080"}
  candidate: {kind: process, command: [./build/candidate/proxy], url: "http://127.0.0.1:8080"}
probes:
  - kind: http-load
    target: "{url}/blobs/64MiB"
    load_model: open
    rate: 80/s
    duration: 60s
primary: {metric: ttfb, statistic: p99}
margin: 5%
""",
}

#: From the question a person asks to the genre that answers it, in the person's words.
QUESTION_TO_GENRE: tuple[tuple[str, str], ...] = (
    ('"Does X meet the bar?" (a pass rate, an error budget)', "`evaluate`, with a `threshold`"),
    ('"Is B better than A?"', "`compare`, `comparison: superiority`, A the control"),
    (
        '"Is B worse (slower) than A?"',
        "`compare`, `comparison: superiority`, A the control: `fails` says B is worse, "
        "`holds` that it is better, `inconclusive` that neither was shown",
    ),
    (
        '"Is B no worse than A?", "may B ship?"',
        "`regress` (non-inferiority), with the `margin` the person would still ship",
    ),
    ('"Are A and B the same?"', "`compare`, `comparison: equivalence`, with a `margin`"),
    ('"How does X change with Y?"', "`characterize`, Y a factor; estimates, no verdict"),
)

#: The skill folder the ``agents`` reference evaluates, and its SKILL.md.
SKILL_LAYOUT = """\
lab/review-skill/
  experiment.yaml
  skills/review/SKILL.md      # the skill under test: one folder per skill"""

SKILL_FILE = """\
---
name: review
description: >-
  Reviews a diff for correctness before a merge. Use when the user asks to
  review a change, a diff or a pull request.
---
Read the diff, then list what is wrong with it, the most serious first."""

#: A probe file of the ``agents`` reference: frontmatter keys are truth, except ``id``,
#: ``kind`` and ``options``, which holds the prompt kind's options.
PROBE_FILE = """\
---
id: review-ask
expectation: must_trigger
expected_skill: review
contains: review
options:
  mock_response: Using the review skill.
---
Review this diff for correctness before I merge it."""

#: The agent evaluations the ``agents`` reference shows, by what each teaches. None restates a
#: default; the test suite validates and plans each one as written.
AGENT_EXAMPLES: dict[str, str] = {
    "skill": """\
name: review-skill
question: Does the review skill fire for a review request and stay quiet for a rename?
genre: evaluate
subjects:
  agent:
    kind: agent-session
    runtime: claude-sdk
    skills: [skills/review]
    tools: [Read, Grep, Skill]
    max_turns: 3
    runtime_options:
      setting_sources: [project]
      permission_mode: bypassPermissions
probes:
  - id: review-ask
    kind: prompt
    prompt: Review this diff for correctness before I merge it.
    mock_response: Using the review skill.
    truth: {expectation: must_trigger, expected_skill: review, contains: review}
  - id: rename-decoy
    kind: prompt
    prompt: Rename load_cfg to load_config in src/app/config.py.
    mock_response: Renamed.
    truth: {expectation: should_not_trigger, expected_skill: review, excludes: review}
primary: {metric: activated_correctly}
threshold: {op: ">=", value: 0.8}
metrics: [text_matched, cost]
""",
    "routing": """\
name: review-routing
question: Does the review skill fire for a review request and stay quiet for a rename?
genre: evaluate
subjects:
  catalog:
    kind: agent-session
    runtime: claude-sdk
    max_turns: 1
    plugins: [plugins/review]
    runtime_options:
      setting_sources: []
      permission_mode: bypassPermissions
probes:
  - id: review-ask
    kind: prompt
    prompt: Review this diff for correctness before I merge it.
    truth: {expectation: must_trigger, expected_skill: review}
  - id: rename-decoy
    kind: prompt
    prompt: Rename load_cfg to load_config in src/app/config.py.
    truth: {expectation: should_not_trigger, expected_skill: review}
primary: {metric: activated_correctly}
threshold: {op: ">=", value: 0.8}
metrics: [cost, hit_limit]
""",
    "loop": """\
name: self-review-merge
question: Does one self-review round make the merge function correct more often?
genre: compare
subjects:
  single: {kind: agent-session, runtime: claude-sdk, model: haiku, tools: []}
  reviewed:
    kind: agent-session
    runtime: claude-sdk
    model: haiku
    tools: []
    loop: {strategy: self-review, rounds: 2}
probes:
  - id: merge
    kind: prompt
    prompt: Write a Python function merge(intervals) that merges overlapping intervals.
    truth:
      function_name: merge
      test_cases:
        - {input: [[[1, 3], [2, 6], [8, 10]]], expected: [[1, 6], [8, 10]]}
        - {input: [[[1, 4], [4, 5]]], expected: [[1, 5]]}
        - {input: [[]], expected: []}
primary: {metric: tests_passed}
comparison: superiority
metrics: [tests_score, rounds, cost]
""",
    "control": """\
name: review-skill-effect
question: Does the review skill make a review find the bug more often than no skill?
genre: compare
subjects:
  without:
    kind: agent-session
    runtime: claude-sdk
    tools: [Read, Grep, Skill]
    max_turns: 3
    runtime_options: {setting_sources: [project]}
  with-skill:
    kind: agent-session
    runtime: claude-sdk
    skills: [skills/review]
    tools: [Read, Grep, Skill]
    max_turns: 3
    runtime_options: {setting_sources: [project]}
probes:
  - id: off-by-one
    kind: prompt
    prompt: "Review this before I merge it: for i in range(len(xs) + 1): total += xs[i]"
    mock_response: The loop reads past the end of xs.
    truth: {contains: past the end}
primary: {metric: text_matched}
comparison: superiority
metrics: [cost]
""",
    "evaluator": """\
name: reads-before-answering
question: Does the agent find total() and read it before it answers?
genre: evaluate
subjects:
  agent: {kind: agent-session, runtime: claude-sdk, tools: [Read, Grep], max_turns: 4}
probes:
  - id: where-is-total
    kind: prompt
    prompt: Where is total() defined, and what does it return for an empty list?
    mock_response: total() is in app.py and returns 0 for an empty list.
    truth: {contains: app.py}
sensors:
  - kind: evaluator
    evaluator: ToolCorrectness
    arguments: {expected_tools: [Grep, Read], allow_extra: true}
primary: {metric: eval_passed}
threshold: {op: ">=", value: 0.7}
metrics: [text_matched]
""",
}

#: The first line of a YAML block that shows the shape of the file rather than a file.
SHAPE_MARK = "# The shape of the file, not a file to validate."

#: The written forms of the scalar option types, each with examples the tests parse.
VALUE_FORMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "duration": ("a number and a unit (ms, s, m, h), or a bare number of seconds",
                 ("500ms", "60s", "2m", "1h", "90")),
    "rate": ("a whole number of arrivals per second or per minute, or a bare number per "
             "second; at least one (2.5/s is 150/m)",
             ("80/s", "600/m", "20")),
    "margin": ("relative to the control, in percentage points, or a bare number in the "
               "metric's unit", ("5%", "10pp", "0.5")),
}  # fmt: skip


def _yaml(text: str) -> str:
    return f"```yaml\n{text.rstrip()}\n```"


def _always_required() -> list[str]:
    fields = Experiment.model_fields.items()
    return [field.alias or name for name, field in fields if field.is_required()]


def _keys(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names)


def _frontmatter(version: str) -> str:
    # JSON strings are YAML scalars, so the values need no YAML emitter to be quoted right.
    return "\n".join(
        [
            "---",
            f"name: {NAME}",
            f"description: {json.dumps(DESCRIPTION)}",
            "metadata:",
            f"  version: {json.dumps(version)}",
            "---",
        ]
    )


def _genre_table(genres: Genres) -> str:
    rows = [
        "| Genre | The question it answers | Set besides the always-required keys | Verdict |",
        "|---|---|---|---|",
    ]
    for genre in genres.genres.values():
        must = ", ".join(genre.author_must_set) or "nothing"
        rows.append(
            f"| `{genre.label}` | {genre.question} | {must} | {VERDICTS[genre.verdict_form]} |"
        )
    return "\n".join(rows)


def _declared_only(kinds: Kinds) -> list[str]:
    declared = set(kinds.kinds) - set(kinds.runnable)
    return sorted({schema.short(name) for name in declared})


def _when(genres: Genres, kinds: Kinds) -> str:
    not_for = [
        "- for one measurement that informs no decision: run the load tool directly.",
        "- to check that code is correct: that is a test suite's job.",
        "- to find where a program spends its time: use a profiler.",
        "- to let a model's judgement decide: a judged metric never decides a verdict "
        "(`judged_gate`); it can only be reported.",
    ]
    declared = _declared_only(kinds)
    if declared:
        not_for.append(
            f"- to run an experiment that uses a kind declared only in this build: "
            f"{_keys(declared)}. ix validates, plans and registers it; `ix run` refuses it."
        )
    guide = [f"- {question}: {answer}." for question, answer in QUESTION_TO_GENRE]
    return "\n".join(
        [
            "## When to use ix",
            "",
            "Use ix when a decision rests on a measurement. Choose the genre by the decision, "
            "not by the system under test:",
            "",
            _genre_table(genres),
            "",
            "From the person's question to the genre:",
            "",
            *guide,
            "",
            "Do not use ix:",
            "",
            *not_for,
        ]
    )


def _install(version: str, kinds: Kinds) -> str:
    runnable = {schema.short(name) for name in kinds.runnable}
    needs = [f"- `{label}` {need}." for label, need in PREREQUISITES.items() if label in runnable]
    source = SOURCE.format(version=version)
    return "\n".join(
        [
            "## Install and check",
            "",
            "```bash",
            f'uv tool install "{source}"',
            f"ix --version      # ix, version {version}",
            "```",
            "",
            "ix is on no package index: it is installed from its repository at the release's "
            "tag, and uv takes matrix, which ix runs on, from the same repository. Every agent "
            "and model runtime comes with it (`claude-sdk`, `pydantic-ai`, `hardline`), and "
            "the OpenTelemetry SDK that exports spans over OTLP: there are no extras.",
            "",
            "What the runnable kinds need on the host:",
            "",
            *needs,
            "",
            "A `process` subject or environment runs the commands the file gives it. Write "
            "them yourself; never take them from untrusted input.",
        ]
    )


def _quick_start() -> str:
    ok, invalid = EXIT_CODES["ok"], EXIT_CODES["config"]
    return "\n".join(
        [
            "## Quick start",
            "",
            "Save this as `lab/static/experiment.yaml`. It runs as written where `python3` "
            "and `k6` are installed:",
            "",
            _yaml(QUICK_START),
            "",
            "Then run these in order, each only after the one before exits 0:",
            "",
            "```bash",
            f"ix validate lab/static   # {ok}: valid. {invalid}: fix every problem listed, "
            "validate again",
            "ix plan lab/static       # read Decides, Trials, Requires; runs nothing. "
            "Runnable: no (--json: runnable false) means stop and fix what it lists",
            f"ix register lab/static   # {ok}: the design is frozen in "
            "lab/static/experiment.registration.json",
            f"ix run lab/static        # the exit code is the verdict: {ok} holds, "
            f"{EXIT_CODES['fails']} fails, {EXIT_CODES['inconclusive']} inconclusive",
            "ix report lab/static     # the latest run's report again, from its record",
            "```",
            "",
            "`ix plan` exits 0 whether or not this host can run the plan: it only shows. "
            "Read the plan before every run: its Trials are what the run will do and Requires "
            "what it needs of this host, "
            "and nothing has happened yet. Decide before you run: the registration is what the "
            "verdict is held to.",
        ]
    )


def _decisions() -> str:
    amend = '`ix register <path> --amend --reason "<why>"`'
    return "\n".join(
        [
            "## What the person decides",
            "",
            "Ask the person for these and use their words; do not choose them yourself:",
            "",
            "- the question, and in a comparison which subject is the baseline;",
            "- the margin or threshold: for `regress`, the largest regression they would still "
            "ship. Never pick a margin for them: `5%` in an example is an example, and the "
            "plan says the margin was set in the experiment;",
            "- what to do with the verdict.",
            "",
            "Show them `ix plan` before `ix register`. The registration freezes their choices "
            "and is worth only what those choices are.",
            "",
            "After a run, report the verdict as it is. Do not change the design and run again "
            "for a different verdict: adding repeats after an `inconclusive`, or moving a margin "
            "after any result, raises the chance of a false `holds`. If the person decides to "
            f"extend the experiment, use {amend} with a reason that names the earlier result, "
            "so the record shows it.",
        ]
    )


def _commands() -> str:
    listed = (*COMMANDS, REFERENCE_COMMAND)
    width = max(len(command) for command, _ in listed)
    lines = "\n".join(f"{command:<{width}}   # {what}" for command, what in listed)
    required = _keys(_always_required())
    return "\n".join(
        [
            "## Commands, in the order they are used",
            "",
            "```",
            lines,
            "```",
            "",
            "`<path>` is the experiment file, a directory holding `experiment.yaml`, or `-` "
            "for an experiment on standard input (it has no file to register beside, so it "
            "runs only with `--exploratory` or `--dry-run`). Every "
            "command takes `--json` and then prints one document with a `schema` field on "
            "stdout: `ix.v1.validation`, `ix.v1.plan`, `ix.v1.registered`, "
            "`ix.v1.deviations`, `ix.v1.report`, `ix.v1.runs`, `ix.v1.genres`, `ix.v1.kinds`. "
            "Progress and commentary go to stderr. Nothing prompts.",
            "",
            "Writing the file:",
            "",
            f"- Always set {required}. The genre names what else must be set (the table "
            "above) and supplies the rest.",
            "- Leave out every value you do not mean to change. The plan shows each value "
            "and whether it came from the experiment, the genre or ix.",
            "- Expect every key to be checked: an unknown key is refused, at the top of the "
            f"file and beside every `kind`. A key starting with `{NOTE_PREFIX}` is a note.",
            "- `ix --skill -r experiment` has every key; `ix --skill -r kinds` every kind, "
            "option and metric.",
        ]
    )


def _exit_table() -> str:
    rows = ["| Exit | Kind | Meaning | What to do next |", "|---|---|---|---|"]
    rows += [
        f"| {code} | `{kind}` | {EXIT_MEANINGS[kind]} | {EXIT_NEXT[kind]} |"
        for kind, code in EXIT_CODES.items()
    ]
    return "\n".join(rows)


def _errors() -> str:
    envelope = (
        '{"schema": "ix.v1.error",\n'
        ' "error": {"kind": "config", "message": "...", "fix": "...",\n'
        '           "complete": true, "details": {"path": "...", "value": "..."},\n'
        '           "problems": [], "deviations": []}}'
    )
    return "\n".join(
        [
            "## Errors and exit codes",
            "",
            "A command that fails exits with its kind's code and prints one envelope on "
            "stderr, as one JSON line under `--json`:",
            "",
            "```json",
            envelope,
            "```",
            "",
            f"- Act on the exit code and `error.kind`, then on `fix`. `error.kind` is one of "
            f"{_keys(list(ERROR_KINDS))}. `complete`, `details`, `problems` and `deviations` "
            "are always present; `details` holds the wrong value (`value`) and where it is "
            "(`path`) when there is one. The envelope and the shared kinds are every gnx "
            "capability's (`exit-codes.json`, `error-envelope.json`).",
            "- A problem is `{path, kind, message, fix, allowed, got, source}`: `allowed` lists "
            "the legal values where the set is closed, `got` is what was written. "
            '`"complete": false` (in a validation, or an error) means checking stopped at the '
            "file's shape: fix those problems and check again. Warnings travel inside the "
            "document as `warnings` (`ix.v1.validation`, `ix.v1.plan`).",
            "- Some outcomes are not failures, yet exit non-zero: an invalid experiment from "
            "`ix validate`, deviations found, and a run that fails, is inconclusive, was not "
            "held to a registration it moved away from, was answered by a stand-in where a "
            "gate was asked for, stopped, could not finish or is still running. Each prints its "
            "own document on stdout.",
            "- Tolerate an error kind, a problem kind or a document field you do not know: "
            "each set grows. Refuse a `schema` whose major is not `v1`.",
            "",
            _exit_table(),
        ]
    )


def _reference_list() -> str:
    return "\n".join(
        [
            "## References",
            "",
            "`ix --skill -r <name>` prints one; an unknown name exits "
            f"{EXIT_CODES['usage']} (`usage`).",
            "",
            *(f"- `{name}`: {what}" for name, what in REFERENCES.items()),
        ]
    )


def _skill(genres: Genres, kinds: Kinds, version: str) -> str:
    lead = (
        f"# ix {version}: experiments that decide a question about a system\n\n"
        "ix answers a question about a system with an experiment. One YAML file names the "
        "question, the subjects, the probes and the metric that decides. ix checks the file, "
        "shows the decision a run would inform and how many trials it takes, freezes the design "
        "before anything runs, runs its trials (one at a time unless it says how many at "
        "once), and reports a verdict: `holds`, "
        "`fails` or `inconclusive`. The system is a service (a program, an endpoint), an AI "
        "agent (a fresh session per trial on one of matrix's agent runtimes, graded by its "
        "tool calls, tests, graders or a judge) or a model (one call per trial on one of "
        "matrix's model runtimes); `ix --skill -r agents` takes you through one."
    )
    sections = [
        _frontmatter(version),
        lead,
        _when(genres, kinds),
        _install(version, kinds),
        _quick_start(),
        _decisions(),
        _commands(),
        _errors(),
        _reference_list(),
    ]
    return "\n\n".join(sections) + "\n"


# --- the references ------------------------------------------------------------------------


def _experiment() -> str:
    template = f"""\
{SHAPE_MARK}
schema: ix.v1.experiment       # optional; absent means ix.v1.experiment
name: <one-token-name>
question: <what you want to know, one sentence>
rationale: <why you expect what you expect>
genre: <genre>
subjects:                      # a mapping: name -> {{kind, that kind's options}}
  <name>: {{kind: <subject kind>, <option>: <value>}}
control: <subject name>        # only where the genre has a control; default: the first
probes:                        # a list of probes; leave it out to read the probes/ folder
  - {{kind: <probe kind>, <option>: <value>, truth: {{<key>: <value>}}}}
factors:                       # conditions varied on purpose; every combination is a cell
  <name>: [<level>, <level>]
environment: {{kind: <environment kind>}}
primary: {{metric: <metric>, statistic: <statistic>, estimator: <estimator>}}
threshold: {{op: ">=", value: 0.9}}   # where the genre decides against a threshold
comparison: <comparison>            # where the genre compares subjects
margin: 5%                          # where the comparison needs one
guardrails:                         # metrics that must also hold: a margin or a threshold
  - {{metric: <metric>, margin: 5%}}
metrics: [<metric>]                 # further metrics to measure and report
sensors:                            # only to give a sensor options, or to choose between two
  - {{kind: <sensor>, <option>: <value>}}
repeats: <n>
seed: <n>
trials_in_flight: 1                 # trials carried out at once; 1 when left out"""
    shapes = "\n".join(
        f"  - `{shape}`: statistic {', '.join(STATISTICS[shape])}; "
        f"estimator {', '.join(ESTIMATORS[shape])}"
        for shape in STATISTICS
    )
    forms = "\n".join(
        f"- `{name}`: {what}, as in {', '.join(f'`{example}`' for example in examples)}."
        for name, (what, examples) in VALUE_FORMS.items()
    )
    operators = ", ".join(f"`{op}`" for op in get_args(Threshold.model_fields["op"].annotation))
    return f"""\
## The experiment file

{_yaml(template)}

Only {_keys(_always_required())} are always required; the genre says what else is, and
supplies the rest. Leave out what you do not mean to change: the plan shows every value and
whether it came from the experiment, the genre or ix.

## Value forms

{forms}
- `threshold`: `{{op, value}}`, with `op` one of {operators}; `value` is a number, or a
  number and a unit such as `300ms`.

## Rules validate applies

- **Every key is checked.** An unknown key is refused, at the top of the file and beside
  every `kind`: a kind declares its options, and `validate` reports `option_unknown`,
  `option_missing` and `option_invalid`. A key starting with `{NOTE_PREFIX}` is a note and is
  ignored.
- **The hypothesis is rendered** from the genre, the metric and the margin or threshold, so
  the file holds none. The plan prints it as `Decides:`.
- **Metrics come from sensors.** Name a metric; ix selects the one sensor that provides it
  and that the subjects and probes can feed. No sensor: `metric_unprovided`. Two:
  `metric_ambiguous`, settled by listing one under `sensors:`.
- **A model-judged metric never decides.** Whether a metric is judged is declared by its
  sensor. A judged metric is refused as `primary` and as a guardrail in any genre with a
  verdict (`judged_gate`); it can still be reported under `metrics:`.
- **Trials in flight.** `trials_in_flight: N` carries N trials out at once; left out it is
  1, one at a time, and the plan says so (`Order:`, and `trials_in_flight` under the
  provenance, from `ix` or the experiment). Use it to shorten a run of agent sessions or
  model calls, whose trials do not disturb each other. Trials start in the plan's order,
  at most N at once; each finishes when it finishes, and its rows carry its `seq`. The
  trials and the verdict's arithmetic (the n it rests on) are the same at any width: what
  changes is the load around each trial (a provider's rate limits, a timeout that a
  slowed session now meets), which is why the width is part of the design, digested by
  the plan and frozen by registration. Above 1 is refused (`trials_in_flight_refused`,
  at `trials_in_flight`) where any kind in use is `overlap: forbidden` (a `process` or
  `http` subject, `http-load`, `timing`, `memory`, a `process` or `compose` environment),
  where a deciding metric (`primary`, a guardrail) is contended (a time, a throughput, a
  memory peak, `usage`'s `duration`), or where the genre forbids overlap (`regress`).
  Its `fix` names each and says `trials_in_flight: 1`. A contended metric
  that is only reported is a warning (`trials_in_flight_contended`): its readings were
  taken under that load. `ix run --trials-in-flight N` tries a width without editing the
  file, with `--exploratory` or `--dry-run` only.
- **Two reductions.** `statistic` reduces one trial's reading to a number; `estimator`
  combines the trials. By the shape of a reading:
{shapes}
  A median is `statistic: p50`: "median latency" is `{{metric: latency, statistic: p50}}`,
  each trial's median, combined across trials by the estimator. With no `statistic`, ix
  takes the first listed. With no `estimator`, a boolean or a count combines as
  `rate` and anything else as the genre's estimator. A `count` reading is events out of
  units (failed requests out of requests sent), pooled over the trials.
- **What a verdict can rest on.** A `mean`, a `median` (it needs 6 blocks, repeats x
  probes, in a cell: `repeats_below_min`) or a rate. A `min` or a `max` is measured and
  reported, never decided on (`estimator_illegal`). A rate's margin is in percentage points
  (`10pp`) or a share (`0.05`), never in percent (`unit_mismatch`), and below the whole range
  (`1`, `100pp`). A rate's threshold is a share strictly between 0 and 1 (`0.9`, not `90%`
  or `90`): one at 0 or 1 or beyond is passed by every rate or by none (`invalid_value`).
- **Placeholders.** `{{name}}` in a subject's option is a factor's level, filled per cell;
  a value that is one placeholder takes the level with its type (`max_turns: "{{turns}}"`
  is the integer). In a probe, inline or from a file, it is a factor, an option every
  subject sets, or a facet every subject's kind offers (`{{pid}}`). Both take what the
  environment binds a service with: `{{<service>.url}}`, `.host`, `.port` (a `process`
  service's `url`, a `compose` service's published port). A loop's `prompt` fills
  `{{prompt}}`, `{{response}}`, `{{until}}` and `{{failures}}` itself. Anything else is
  `unbound_placeholder`: a problem, except in prompt text (an option `-r kinds` marks
  `prompt text`, such as a probe's `prompt`, a `system_prompt` or a `loop`), where a name
  nothing fills is sent as written and is a warning, so code (`print(f"{{user}}")`) can be
  put to an agent. Read those warnings (`validate`, `plan`, `register` and `run` print
  them, and a run's record keeps them): a typo (`{{sise}}` for the factor `size`) is one,
  and names the placeholder it is likely to be. A name a probe takes from some subjects and
  not others stays a problem, doubled or not, since the subjects would be put different
  prompts. In prompt text `{{{{name}}}}`, where `name` is filled there, is the text
  `{{name}}`; every other brace is sent as written, so JSON (`{{"a": {{"b": 1}}}}`) or a
  template (`{{{{ user.name }}}}`, or `{{{{user}}}}` where nothing fills `user`) in a prompt
  reaches the agent unchanged. So a template's own `{{{{model}}}}`, `{{{{runtime}}}}` or
  `{{{{workdir}}}}` is the text `{{model}}` wherever something fills that name there (an
  option every subject sets, or a facet, as much as a factor): write it with spaces,
  `{{{{ model }}}}`, to send it as written whatever fills the name. A doubled name nothing
  fills that is near one that is filled (`{{{{sise}}}}` for `size`) is warned of. In a
  subject's or a probe's other options `{{{{name}}}}` is always the text `{{name}}`:
  `setup: [sh, -c, 'echo ${{{{HOME}}}}']` runs `echo ${{HOME}}`. Nothing fills an
  environment's options or a sensor's, nor un-doubles an escape in them: `{{name}}` and
  `{{{{name}}}}` are sent as written, so an `environment.process` command takes the shell's
  own `${{HOME}}`, and a `text-match`'s `contains: '{{size}}'` is compared as that text.
  Validate warns of a `{{name}}` there that a trial fills elsewhere, of every
  `{{{{name}}}}` there, and of each `{{name}}` in a judge's `rubric`, which is sent as
  written.
- **Probes from files.** Leave `probes` out and ix reads the `probes/` folder beside the
  experiment, one file per probe (`.md`, `.yaml`): a markdown file's text is the prompt,
  and its frontmatter keys other than `id` (default: the file's name), `kind` (default
  `prompt`) and `options` are the probe's truth (reference data a sensor measures against;
  it never reaches the subject). A truth key no sensor in use reads is `truth_unclaimed`.
"""


def _count(low: int, high: int | None) -> str:
    if high == low:
        return str(low)
    return f"{low} or more" if high is None else f"{low} to {high}"


def _genre_section(genre: Genre) -> str:
    label = genre.label
    roles = ", ".join(f"{role.name} ({_count(role.min, role.max)})" for role in genre.roles)
    design = genre.design
    supplied = [
        f"repeats {design.repeats.default} (at least {design.repeats.min})",
        f"order {design.order}",
        f"estimator {design.estimator}",
    ]
    if design.calibration != "none":
        supplied.append(f"calibration {design.calibration} (the control against itself)")
    if design.overlap == "forbidden":
        supplied.append("overlap forbidden (its trials run one at a time)")
    if genre.default_comparison is not None:
        supplied.append(f"comparison {genre.default_comparison}")
    lines = [
        f"### {label}"
        + (f" (narrows {schema.short(genre.extends)})" if genre.extends else "")
        + (f"  `{genre.name}`" if label == genre.name else ""),
        "",
        f"*{genre.question}*",
        "",
        f"- Subjects: {roles}.",
        f"- Factors: {genre.factors}.",
        f"- Must set: {', '.join(genre.author_must_set) or 'only the always-required keys'}.",
    ]
    if genre.comparisons:
        lines.append("- Comparisons:")
        lines += [f"  - `{name}`: {form}" for name, form in genre.comparisons.items()]
    else:
        lines.append(f"- Decides: {genre.hypothesis_form}")
        lines.append("- Comparisons: none; do not set `comparison`.")
    lines += [
        f"- Verdict: {VERDICTS[genre.verdict_form]}.",
        f"- The genre supplies: {'; '.join(supplied)}. {design.note.capitalize()}.",
        "",
        genre.guidance.rstrip(),
    ]
    example = EXAMPLES.get(label)
    if example is not None:
        lines += ["", _yaml(example)]
    return "\n".join(lines)


def _genres(genres: Genres) -> str:
    sections = "\n\n".join(_genre_section(genre) for genre in genres.genres.values())
    return (
        "## Genres\n\n"
        "A genre is the kind of decision the answer must inform. Pick it by the decision, not "
        "by the system under test: to choose between ways of doing something, `compare`; to "
        "decide whether one change may ship against its baseline, `regress`. Each example "
        "below validates in this build; its `command` names the program under test, which "
        "must exist where it runs. A `genres/` or `kinds/` directory beside an experiment file "
        "holds that lab's own declarations, read like these; `ix genres --lab <dir>` and "
        "`ix kinds --lab <dir>` list them, and what was refused and why.\n\n"
        f"{sections}\n"
    )


def _option_values(option: Option) -> str:
    """What an option takes: its closed set, its open choices, or its type; and whether it
    is prompt text."""
    text = ", prompt text" if option.text else ""
    if option.allowed:
        return f" ({'|'.join(option.allowed)}{text})"
    if option.choices:
        return f" ({option.type}: {', '.join(option.choices)} or another{text})"
    return f" ({option.type}{text})"


def _kind_line(kind: Kind, runnable: bool) -> str:
    options = ", ".join(
        f"`{name}`{'*' if option.required else ''}" + _option_values(option)
        for name, option in kind.options.items()
    )
    standing = "" if runnable else " *(declared only: no adapter in this ix)*"
    parts = [f"- **`{schema.short(kind.name)}`**: {kind.summary}{standing}"]
    parts.append(f"  options: {options or 'none'}")
    facets = []
    if kind.needs:
        facets.append(f"needs {', '.join(kind.needs)}")
    if kind.offers:
        facets.append(f"offers {', '.join(kind.offers)}")
    if kind.truth:
        facets.append(f"reads truth {', '.join(kind.truth)}")
    if kind.service_keys:
        keys = ", ".join(f"`{{<service>.{key}}}`" for key in kind.service_keys)
        facets.append(f"binds each service as {keys}")
    facets.append(f"overlap {kind.overlap}")
    parts.append(f"  {'; '.join(facets)}")
    for metric in kind.metrics:
        traits = [metric.shape, f"{metric.direction} is better"]
        if metric.unit:
            traits.insert(1, metric.unit)
        if metric.judged:
            traits.append("JUDGED by a model")
        if metric.contended:
            traits.append("CONTENDED by trials in flight")
        parts.append(f"  metric `{metric.name}` ({', '.join(traits)}): {metric.description}")
    return "\n".join(parts)


def _kinds(kinds: Kinds) -> str:
    titles = {
        "subject": "Subject kinds (`subjects.<name>.kind`)",
        "probe": "Probe kinds (`probes[].kind`)",
        "sensor": "Sensors (selected by metric; `sensors[].kind` to name one)",
        "environment": "Environment kinds (`environment.kind`)",
    }
    blocks = []
    for family in KIND_FAMILIES:
        lines = (
            "\n".join(_kind_line(kind, kind.name in kinds.runnable) for kind in kinds.of(family))
            or "- (none installed)"
        )
        blocks.append(f"### {titles[family]}\n\n{lines}")
    families = "\n\n".join(blocks)
    return f"""\
## Kinds

Subjects and probes must fit: a probe kind needs facets, and every subject's kind must offer
them (`kind_unfit`). A sensor needs facets from the subjects or from what the probes leave
behind. Required options are marked `*`.

A kind marked **declared only** has no adapter in this ix: an experiment that uses it can be
validated, planned and registered, `validate` warns (`kind_unimplemented`), the plan says
`Runnable: no`, and `ix run` refuses it before anything starts; so does a kind whose adapter
cannot be built here (`ix kinds` says why). What an entry needs of this host (a program on
PATH, an `auth` variable that is set, a runtime that resolves the model, the docker daemon)
its adapter checks: the plan's `requirements` list each with `holds`, and one that does not
hold is under `Runnable: no` with its `why` and `fix`. It depends on the host, so the
experiment can still be registered.

A `process` subject is up when its `ready` URL answers an HTTP GET with a status below 500;
without `ready`, when its `url` accepts a connection; with neither, as soon as it starts. One
that is not up within `startup_timeout` (30s) fails the trial as the subject's (`not_ready`).

Each kind's `overlap` says whether two trials may use it at once: `allowed` when neither
changes what the other measures (a fresh agent session each), `forbidden` when they would (a
fixed port, a load tool, an environment brought up once for the run); a kind that does not
say forbids it. A metric marked **CONTENDED** moves with whatever else runs beside the
trial (a time, a throughput, a memory peak).

{families}
"""


def _running() -> str:
    method = "\n".join(f"- {line}" for line in METHOD)
    return f"""\
## Registration

`ix register <path>` writes `<file-stem>.registration.json` beside the experiment. It holds
the design as resolved (what the file chose and what the genre supplied: comparison, margin,
threshold, guardrails, control, repeats, seed, order, calibration, estimators, the analysis
and its parameters, the sensors), the digests of the probe set, the experiment and the
plan, and the digest of each file and folder the subjects and sensors name: `skills`,
`plugins` (each folder a `*` matches), `fixtures`, `definition`, `graders`, `rubric_file`,
any option a kind declares a `path` or `paths`, and each item of a `command` (a process's
`command`, relative to its `cwd`; a session's `setup`) that names a file: `[python3,
server.py]` digests `server.py`.

Digests are over meaning: a comment, key order or a default written out is not a change. A
reworded `question` or `rationale` changes the experiment's digest and not the plan's. A
file's digest is its bytes; a folder's is its files' relative paths and their bytes, never
their times. A skill, plugin, fixture or definition edited after registration is a
deviation named by what changed (`skill review changed after registration`): the verdict is
about what those files hold.

`ix deviations <path>` lists what differs from the registration and exits
{EXIT_CODES["deviations"]} when anything does. `ix register` on a changed experiment is refused
(exit {EXIT_CODES["conflict"]}) and the record is kept. To replace it on purpose, run
`ix register <path> --amend --reason "<why>"`: the new record keeps the superseded one and the
reason.

## Running

`ix run <path>` carries the experiment out: it brings the environment up once, then for
each trial starts the subject, applies the probe, takes the measurements and stops the
subject, starting the trials in the order the plan fixed, one at a time unless the
experiment writes `trials_in_flight`. It prints progress on stderr and then the report
(`ix.v1.report`) on stdout.

- **Register first.** An unregistered experiment is refused (exit
  {EXIT_CODES["unregistered"]}). `--exploratory` runs it anyway; such a run gives **no
  verdict**: its report says what it found as an *indication* and exits {EXIT_CODES["ok"]}.
- **The exit code is the verdict**: {EXIT_CODES["ok"]} holds (or there is no verdict to give),
  {EXIT_CODES["fails"]} fails, {EXIT_CODES["inconclusive"]} inconclusive. Inconclusive does
  not pass a gate. After the report, stderr says the code, what it means and the next step
  (`exit` in `--json`: `code`, `kind`, `meaning`, `next`).
- **What the evidence shows** is the report's `Shows` (`shows`), in plain words and in the
  direction it points, whatever the verdict: `build30's latency p50 is higher than
  build20's by 29.6% [26.2%, 33.1%]; lower is better, so build30 is worse`. The verdict
  is about the claim the plan's `Decides` states; `Shows` is the measurement behind it.
- **A pilot.** `--exploratory --repeats N` (or `--dry-run --repeats N`) runs N repeats in
  place of the file's, never below the genre's minimum, and lists the change under
  `Changed`; a run that gives a verdict refuses `--repeats` (exit {EXIT_CODES["usage"]}). A
  run too small for even every trial passing to show a pass rate's threshold says so under
  `Best case`, as the plan does under `Sensitivity`.
- **A width to try.** `--exploratory --trials-in-flight N` (or `--dry-run`) carries N trials
  out at once in place of the file's `trials_in_flight`, listed under `Changed`; a run that
  gives a verdict refuses it (exit {EXIT_CODES["config"]}): write `trials_in_flight:` in the
  file and register it. Where the kinds, a deciding metric or the genre forbid overlap it is
  refused before anything starts, `trials_in_flight_refused`, whose `fix` names each and
  says `trials_in_flight: 1`. The report's `Run` line says the width.
- **A run of an experiment that changed since registration gives no gate result.** It
  runs, its report lists the deviations and gives what it found as an *indication*, and it
  exits {EXIT_CODES["deviations"]}, never {EXIT_CODES["ok"]}, until the registration is
  amended with a reason (`ix register <path> --amend --reason "<why>"`).
- **The record** is `<experiment directory>/.ix/runs/<run id>/` (or under `runs:` in ix's
  settings): one JSONL file per table
  (`ix.v1.trial`, `ix.v1.interaction`, `ix.v1.reading`, ...), written as the run goes. A
  trial is on disk when it finishes, so a run that is killed keeps what it had.
- **From a reading to the model that answered.** A reading joins its interaction on `seq`.
  Each interaction's `sessions`, and each reading's (a judge's own calls), are row numbers,
  from 0, in `matrix.v1.session.jsonl`: one row per agent session or model call, failed
  ones included, with the runtime, the model that answered and its `family`, `usage`, the
  ids, `attempt_log`, and `status` (`failed` with its `reason`). `jq -c '{{seq, sessions}}'
  ix.v1.interaction.jsonl` lists them; `sed -n "$((N + 1))p" matrix.v1.session.jsonl` reads
  row N.
- **What a verdict is about** is in the manifest (`ix.v1.run`) and the report: the digest
  of each file the subjects and sensors name (`contents`: paths and digests, never what
  the files hold), the git commit and whether a tracked file differed from it
  (`provenance.commit`, `provenance.dirty`; `not a repository` outside one), the versions
  of ix, matrix and hardline, and each runtime installed with the distribution that
  registered it and the versions of what it drives (`claude-agent-sdk` for `claude-sdk`).
  `ix report` prints them under `Contents`, `Ran from` and `Versions`; `ix runs` gives each
  run's commit.
- **Stopping.** Ctrl-C stops the trial in flight, stops everything the run started and
  exits {EXIT_CODES["run_interrupted"]} with the partial report. `ix report <run directory>`
  reads any run again, finished or not; a run that did not finish gives no verdict, and one
  still running exits {EXIT_CODES["running"]}. A report calls no model and runs no code: a
  run stopped before a judge, an `evaluator`, `function-test` or `outcome` read is reported
  without readings (`unread` says why); run it again.
- **Refused before anything starts**: any requirement of this host that does not hold: a
  command that is not there, an `auth` variable that is not set, `k6` missing for
  `http-load`, the docker daemon down for `compose`, an agent runtime that cannot start or
  cannot play its agent (the plan's `requirements`, each with `holds`, `why` and `fix`).
- **A dry run gives no verdict.** `ix run <path> --dry-run` runs the file as written
  with every agent session and model call on matrix's `mock` runtime of its port (`ix
  --skill -r agents`); a subject with no stand-in (a `process`) is refused, and the
  environment is brought up for real (a `compose` stack, a `process` service). A run in which
  a stand-in answered any trial, this way or with `runtime: mock` written in, measured the
  harness, not the subject: the
  report says `Dry run on mock replies` and gives neither a verdict nor an indication, and
  the run is not `gated`. A registered run with `runtime: mock` written in exits
  {EXIT_CODES["simulated"]} (`simulated`), never {EXIT_CODES["ok"]}.
  `ix runs` lists each run as `gated`, `exploratory` or `dry`.
- **Failures have an owner.** A trial the *subject* failed (it died, it was not up in
  time, it answered no request inside the measured window, an agent session ran past its
  probe's `timeout`) counts against the subject. A
  trial the *harness* failed (a tool broke, a timeout, an environment that went down) is
  missing data; an environment that went down also ends the run (exit
  {EXIT_CODES["run_failed"]}). A *sensor* failure is a reading that could not be taken. The
  report counts each. A subject too slow for an open model's rate is measured, not
  dropped. `error_rate` counts a request that failed, timed out (`http-load`'s
  `timeout`, k6's 60s) or was never answered, and each arrival dropped, as an error.

## Reading a verdict

One finding per variant per cell; the verdict holds only if every finding holds, and fails
if any fails. Each finding is decided on an interval, not on a point:

{method}

Under `superiority`, two subjects that do not differ are **inconclusive**: nothing was
shown either way. Under `non-inferiority` they **hold** once the interval is narrower than
the margin. Choose the comparison for the claim to be made.

- **How small a difference a run can show.** Once the experiment has run (an exploratory
  run counts; a dry run does not), `ix plan` states it under `Sensitivity` (`sensitivity` in
  `--json`): the true difference this many repeats shows 4 times in 5. A margin smaller
  than that will rarely hold; more repeats are the remedy, decided before the run. For a
  pass rate held to a threshold the plan also says when even every trial passing cannot
  show it.
- **The A/A check.** Where the genre calibrates, the control also runs against itself. If
  it differs from itself, or the check cannot be made, the run is `inconclusive` and
  `analysis.reason` says which, whatever the variants did. A control that reads the same
  number in both samples of every block held still, and passes. It never widens an
  interval.
- **Intervals are printed unadjusted**, at 95%; `allowed` is the level a decision was held
  to (the full 0.05 for `holds`, Holm's share for `fails`).
"""


def _agents() -> str:
    dry, usage = EXIT_CODES["ok"], EXIT_CODES["usage"]
    return f"""\
## Evaluating an agent

An agent evaluation is an experiment like any other: a genre for the decision, subjects,
probes and a metric. What makes it one is the kinds.

- **The subject is `agent-session`.** Each trial is a fresh session in a new working
  directory outside the experiment, on the matrix agent runtime its `runtime` names. It is
  required, with no default, so a model provider is never called implicitly: `claude-sdk`
  (the Claude Agent SDK, with tools, turns, plugins and skills; calls a model provider),
  `mock` (canned replies; calls no model: a dry run), or any agent runtime installed, by
  its type URL. Left out, validate reports `option_missing`. The other options:
  `definition` (a Claude Code agent file: body is the system prompt), `model`,
  `system_prompt`, `tools` (`[]`: none), `max_turns`, `plugins` (folders, `*` allowed,
  handed to the runtime as its `plugins`), `skills` (folders copied to where the runtime
  says a session finds its skills: claude-sdk's `.claude/skills`, read as its `project`
  settings), `fixtures` (files and folders copied
  into the working directory first), `setup` (a command run there after them:
  `[python3, seed.py]`) and `runtime_options`: the runtime's own config, passed through as
  written. For `claude-sdk` that is `permission_mode`, `setting_sources` (`[]` keeps this
  machine's settings and plugins out), `fallback_model`, `max_budget_usd` and `agents`;
  `matrix describe matrix.v1.agent-runtime.claude-sdk` lists them. A key the runtime's
  config does not declare is `option_unknown`; `plugins` on a runtime that takes none are
  `option_inapplicable`.
- **To measure a model rather than an agent, the subject is `model-call`.** Each trial is
  one call on the matrix model runtime its `runtime` names (`hardline`, `mock`, or any
  installed): no turns, no tools, no working directory. Options: `model` (a name the
  runtime resolves, checked by `ix plan`), `system_prompt` and `runtime_options` (the
  runtime's config: `temperature`, `max_tokens` for `hardline`). The `prompt` probe
  applies, without `collect`; `activation` and `tool-usage` read tool calls a call never
  makes, so validate refuses them on it (`unobservable`), and a `function-test` `file`.
- **The probe is `prompt`.** Write one file per probe in a `probes/` folder beside the
  experiment and leave `probes:` out: the markdown body is the prompt, and every
  frontmatter key but `id`, `kind` and `options` is the truth sensors read
  (it never reaches the agent). Probe options, under `options:` in a file: `collect`
  (files the agent writes, kept for grading), `feedback` (tests a test-feedback loop shows
  the agent), `mock_response` (the reply a stand-in runtime such as `mock` gives),
  `timeout` (600s): a session or call that runs past it fails the trial as the subject's
  (`overran`).
  An option written beside the truth is truth, which the probe never sees: validate says
  so (`truth_unclaimed`) and shows where it goes. `probes/review-ask.md`:

```markdown
{PROBE_FILE}
```
- **Sensors read what the session said and did**, and are chosen by the metric you name:
  `activated_correctly` (`activation`: truth `expectation` must_trigger,
  should_not_trigger or acceptable, and `expected_skill`, compared whole: `review` is fired
  by `review` or `plugin:review`, never by `code-review-lite`), `tool_used_correctly` and
  `tool_usage_score` (`tool-usage`, option `tool`: a tool called by name or a program run
  through Bash; truth `expected_command`, the whole word, and `expected_args`), `tests_passed`,
  `tests_pass_share` and `tests_score` (`function-test`: truth `function_name`,
  `test_cases` of `{{input, expected}}` or `{{input, raises}}` (`raises` names an exception
  class or a base of it; `any` is `Exception`, so code that exits passes only where
  `SystemExit` is named), and `tiers` that weigh them; option `file` tests a collected
  file instead of the reply), `outcome_score` and
  `outcome_passed` (`outcome`, option `graders`: a Python module of your own whose
  `GRADERS` maps a probe id, or the truth's `grader`, to a function of the reply returning
  0 to 1; a probe with none is graded by the share of its truth's `expected_facts` the reply
  holds), `text_matched` (`text-match`: plain checks of the reply, no code of your own:
  `starts_with`, `ends_with`, `equals`, `contains` and `excludes` (a text or a list),
  `matches` (a regular expression), each in a probe's truth or as the sensor's option for
  every probe; options `ignore_case`, `strip`), `tokens_total`, `tokens_input`,
  `tokens_output`, `cost`, `turns`, `tool_calls`, `duration`, `rounds`, `hit_limit`
  (`usage`: what the runtime did not report is left out, so a model call has no `turns`),
  the judged `judged_score` and `judged_pass` (`judge`, option `rubric`), and
  pydantic-evals' evaluators: `eval_passed`, `eval_score`, `eval_duration_passed`
  (`MaxDuration`'s, contended) and the judged `judged_eval_passed` and `judged_eval_score`
  (`evaluator`, below).
- **Validate checks that the sensors can see.** A setting that makes what a sensor reads
  impossible is `unobservable`, refused before any trial, whatever the runtime is called:
  a skill whose `tools` leave out the tool the runtime says fires one (claude-sdk's
  `Skill`), `skills` on a runtime that names no place for them or, as configured,
  loads none from the working directory (claude-sdk with `setting_sources` that leave
  `project` out), a `tool-usage` tool the session may not call, a session with no skill,
  plugin or setting to fire, files to `collect` with no tool to write them or on a runtime
  that writes none (claude-sdk in `plan` mode), and `activation` or `tool-usage` on a
  `model-call` subject. What a runtime's sessions do is the runtime's to declare, so the
  same holds for any agent runtime that says so.
- **Conditions to compare are subjects** under `compare`: one changed option each (a
  `system_prompt`, a `definition` file, a `model`, a `loop`). A `loop` adds rounds:
  `{{strategy: self-review, rounds: 3}}` asks again with the previous answer until it
  carries `SOLUTION_VERIFIED`; `{{strategy: test-feedback}}` shows the agent which of the
  probe's `feedback` tests failed, until none do. Tokens, turns and cost add up over the
  rounds.

## Evaluating a skill

A skill is a folder holding `SKILL.md`, beside the experiment:

```
{SKILL_LAYOUT}
```

```markdown
{SKILL_FILE}
```

`SKILL.md` opens with frontmatter that has both keys: `name` (lowercase letters, digits
and hyphens, the folder's name) and `description` (what the skill does and when to use
it: the agent reads only this to decide whether to fire it). `skills:` copies each folder
to where the runtime says a session finds its skills; for claude-sdk, the session's
`.claude/skills`, which it reads as its `project` settings:
`setting_sources: [project]` reads them and nothing else of this machine, since the
working directory is the trial's own (`[]` would read none, and validate refuses it). A
skill fires through the tool the runtime names, claude-sdk's `Skill`, so `tools`, when you
list them, must include it. `expected_skill` is the skill's `name`. Skills in a plugin come
with `plugins:` instead; `expected_skill: review` matches the plugin's `acme:review`, and
`expected_skill: acme:review` names that plugin's skill only. A skill installed on this
machine is read only when its settings source is listed (`setting_sources: [user]`), and
then everything else there comes too.

{_yaml(AGENT_EXAMPLES["skill"])}

Two more: routing through a plugin, and one condition against another.

{_yaml(AGENT_EXAMPLES["routing"])}

{_yaml(AGENT_EXAMPLES["loop"])}

## Showing that the skill made the difference

A reply that passes a text check does not show that the skill produced it: the agent may
have written it anyway. Two things show more.

- **Read the activation beside the text.** Name `activated_correctly` with `text_matched`
  (the skill example above makes it the primary): beside the text check the report then
  shows whether the skill fired where it should have and stayed quiet where it should not
  (the expected-against-observed table, with precision and recall).
- **Compare against a control with no skill.** Under `compare`, the first subject is the
  control: the same session without `skills`, every other option the same. The verdict is
  then about the difference the skill made, and `Shows` says which way it went:

{_yaml(AGENT_EXAMPLES["control"])}

## The order of work

1. `ix validate` and `ix plan`. Runnable says whether the runtime can start here.
2. **Dry run.** `ix run <path> --dry-run` runs the file as written on stand-ins: each
   session on the `mock` runtime replying with its probe's `mock_response` (give each one
   whose grade you know), the real runtime's `runtime_options` and `plugins` set aside and
   listed under `Stood in`, a judge scoring every reply 0.5. Every sensor reads the canned
   replies as it would a model's, so a grader that grades wrong shows here, for nothing.
   It checks the harness, never the agent: the report says `Dry run on mock replies` and
   gives no verdict and no indication (exit {dry}).
3. **Pilot.** On the real runtime, `ix run <path> --exploratory --repeats N`, N the
   genre's fewest repeats: the file stays as it will be registered, and the report lists
   the change under `Changed`. A pilot gives no verdict, and one too small for even every
   trial passing to show a pass rate's threshold says so under `Best case`: that is its
   size, not the agent. Read the screen: a probe at `floor` or `ceiling` (every trial
   failed, or passed) or `noisy` (one subject's readings on it spread over more than 0.35
   of the range) cannot discriminate; fix or drop those probes. Then `ix plan` again:
   Sensitivity now rests on how much the primary varied.
4. `ix register`, then `ix run`: the exit code is the verdict. `--repeats` is refused
   here (exit {usage}): a verdict rests on the registered design.

## The judge

`judge` asks a model to score a reply against your `rubric` from 0 to 1. It names its
runtime with one of two options, and has no default: `model_runtime` (one model call per
reading: `hardline` calls a model provider, `mock` does not) or `agent_runtime` (a one-turn
session with no tools per reading: `claude-sdk` calls a model provider, `mock` does not).
Validate refuses a judge that names neither, or both:
`{{kind: judge, model_runtime: hardline, rubric: rubric.md}}`. Its readings
are judged: they are reported and **never decide** a verdict. `repeats: 3` asks it three
times per reply: `judge_agreement` is how often it agreed with itself. Two flags are
printed whenever a trial raises one: `judge_inconsistent` (its repeats split on pass or
fail) and `judge_in_family` (the judge is of the subject's model family; judge with
another). On an agent runtime the judge runs in a fresh temporary directory, given the
config its runtime declares keeps the machine out (claude-sdk's `setting_sources: []`)
under its own `runtime_options`, so what is where ix runs (a CLAUDE.md, your settings,
hooks) does not shape its scores.

## pydantic-evals' evaluators

`evaluator` runs one of pydantic-evals' evaluators over each trial; ix keeps the loop
(`Dataset.evaluate` is not used). Name it with `evaluator` and give its `arguments` as
pydantic-evals documents them. It reads the reply as `output`, the prompt as `inputs`, the
probe's truth `expected_output` and `metadata`, how long the probe took, and the spans that
ended in the trial. Every assertion it gives held is `eval_passed`; its score is
`eval_score` (`result` names one when it gives several); labels and each result's reason
are in the reading's detail. One `evaluator` entry per experiment: a second is
`name_taken`.

{_yaml(AGENT_EXAMPLES["evaluator"])}

- **Reading the reply**: `Equals`, `EqualsExpected` (the truth's `expected_output`),
  `Contains`, `IsInstance`.
- **Reading the time**: `MaxDuration` gives `eval_duration_passed` (whether the probe took
  no longer than `seconds`), never `eval_passed` (`metric_unprovided`, whose fix names
  it). A time is contended, so it decides no run with `trials_in_flight` above 1.
- **Reading the spans**: `ToolCorrectness`, `TrajectoryMatch` (an `eval_score`),
  `ArgumentCorrectness`, `MaxToolCalls`, `MaxModelRequests`, `HasMatchingSpan`, on matrix's
  `execute_tool` span of each tool call the runtime timed (`claude-sdk` does). They are
  captured from the OpenTelemetry SDK in ix's process, with nothing to set: the run puts
  matrix's `otel` observer on itself and keeps the spans in the process, exporting them only
  when the `OTEL_*` variables ask. `ArgumentCorrectness` also needs
  `observers: [{{type: otel, capture_content: true}}]` under `matrix:` in the settings, since a
  call's arguments are content. Without
  the spans the reading fails and says why; it never reads as no tool called. A dry run on
  `mock` has no tool calls to read.
- **Calling a model**: `LLMJudge` and `GEval` give `judged_eval_passed` and
  `judged_eval_score`, which never decide. Their model comes from the matrix model runtime
  `model_runtime` names (with `model`, `runtime_options`), never from `arguments` and never
  from pydantic-evals' default; validate refuses one with no `model_runtime`
  (`option_missing`) or a `model` in its arguments. A dry run judges on pydantic-ai's
  `TestModel`, which calls no model. On `hardline` it judges on the registry row `model`
  names, each request a hardline call; a model runtime that gives no pydantic-ai model is
  `Runnable: no` in `ix plan`, and the `judge` sensor scores there.

## Reading the report

Beside the verdict: `Shows`, what the evidence shows in plain words and in the direction
it points (`with-skill's text_matched is higher than without's by 40pp [12pp, 61pp]`),
whatever the verdict; the expected-against-observed table of `activation` and
`tool-usage` with precision and recall, the flags, and the probes that cannot discriminate
(left out of a dry run: canned replies screen nothing).
After it, on stderr, the exit code, what it means and the next step (`exit` in `--json`).
A runtime warning, such as a permissive `permission_mode`, is said once per run, in
`warnings`. Each trial's record keeps the prompt, the reply, every
tool call, each round's transcript, how the session stopped, its turns, tokens, cost and
duration (`ix.v1.interaction`), the rows of `matrix.v1.session` behind it (`sessions`: the
model that answered each round), and the run's events (`events.jsonl`). The `running`
reference shows how to follow a reading to them.

## What runs here, and what it can touch

`function-test` and `outcome` run Python in a child process with a time limit and only
`PATH` and the locale in its environment. It is not a sandbox: model code can still read,
write and connect as you. The code does not grade itself: the child gets each case's
input, never what is expected, and ix compares what came back, so printing passing lines
or rewriting the cases fails. A session's working directory is a fresh temporary directory,
removed after the trial; the agent's tools act on your machine as `permission_mode`
allows. Telemetry: with `OTEL_EXPORTER_OTLP_ENDPOINT` set, ix exports `ix.plan`, `ix.run`,
`ix.trial` and `ix.sensor` spans and matrix the sessions inside them.
"""


def _problems() -> str:
    kinds = ", ".join(f"`{kind}`" for kind in sorted(PROBLEM_KINDS))
    return f"""\
## Problem kinds

A problem is `{{path, kind, message, fix, allowed, got, source}}`. `validate` reports these
kinds:

{kinds}.

Tolerate a kind you do not know: the set grows.
"""


def render(
    genres: Genres,
    kinds: Kinds,
    *,
    version: str,
    reference: str | None = None,
) -> str:
    """The skill for the installed genres and kinds, or one of its
    :data:`REFERENCES`. Raises :class:`UsageError` for a reference that does not exist."""
    if reference is None:
        return _skill(genres, kinds, version)
    if reference not in REFERENCES:
        names = ", ".join(REFERENCES)
        raise UsageError(
            f"no skill reference named {reference!r}. References: {names}",
            fix=f"run `ix --skill -r <name>` with one of: {names}",
            details={"path": "--reference", "value": reference},
        )
    match reference:
        case "experiment":
            body = _experiment()
        case "genres":
            body = _genres(genres)
        case "kinds":
            body = _kinds(kinds)
        case "running":
            body = _running()
        case "agents":
            body = _agents()
        case _:
            body = _problems()
    return f"# ix {version} reference: {reference}\n\n{body}"
