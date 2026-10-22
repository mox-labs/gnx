"""The text ``matrix --skill`` prints: what matrix is for, the component protocol, writing
and registering a component, config, the commands, and the errors and exit codes; and the
references ``matrix --skill -r NAME`` prints, for detail the main text points to.

It teaches the protocol and the config, which are what a component author depends on, and
names the registry only as far as registering takes. The frontmatter makes the printed text
a valid ``SKILL.md`` as it stands. The exit table, the extension points, the forbidden kind
segments, the problem kinds, the retry policy's fields and the extras are read from the
code; the config keys, the tiers, the needs, the commands and the references are held to it
by the tests, which also run every example.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, metadata

from matrix.domain.agent_step import AGENT_RESPONSE
from matrix.domain.errors import (
    EXIT_CODES,
    EXIT_MEANINGS,
    EXIT_NEXT,
    REQUIREMENT,
    SOURCE,
    UsageError,
)
from matrix.domain.executor import Retry
from matrix.domain.ids import KIND_SEGMENTS
from matrix.domain.model_step import MODEL_CALL
from matrix.domain.problems import MATRIX_PROBLEM_KINDS, PROBLEM_KINDS
from matrix.domain.registry import MATRIX_POINTS

NAME = "matrix"

DESCRIPTION = (
    "Writes and registers matrix components, units of work that declare the kinds of data "
    "they require and provide and are wired and recorded by those kinds, and composes agents "
    "and runtimes from matrix config. This skill should be used when the user asks to "
    '"write a matrix component", "register a matrix extension", "add a component to '
    'matrix.yaml", "configure matrix runtimes or agents", "check that a matrix config '
    'composes", "plan or run a matrix config", or "list the installed matrix extensions".'
)

#: This release, pinned by its tag: as a library in a project, and as the `matrix` command.
INSTALL = (
    f'# the library, in a project\nuv add "{REQUIREMENT}"\n'
    f'# the matrix command\nuv tool install "{SOURCE}"'
)

#: What each optional extra adds. The tests hold the keys to the package's extras.
EXTRAS: dict[str, str] = {
    "claude": "the `claude-sdk` agent runtime (Claude Agent SDK sessions)",
    "models": "the `hardline` model runtime (one model call through hardline)",
    "otel": "the OpenTelemetry SDK and OTLP exporters: export of spans and metrics",
}

#: The commands, in the order they are used; the last two are the reference.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("matrix config --sources", "the files the config is read from, lowest priority first"),
    ("matrix config", "the merged config"),
    ("matrix check", "compose runtimes and agents, wire the components; calls nothing"),
    ("matrix plan", "the order of work, effects, agents, calls; every problem at once"),
    ("matrix run --input KIND=PATH", "run it; prints the run id and its record's files"),
    ("matrix runs", "the recorded runs, oldest first: id, status, start, where"),
    ("matrix agents", "the agents the config defines, with their runtimes"),
    ("matrix catalog [--point P]", "every registered extension, and any that failed to load"),
    ("matrix describe TYPE_URL", "one extension: its config schema, needs and effects"),
)

#: The config tiers, lowest priority first. The tests hold them to ``discover_sources``.
TIERS: tuple[tuple[str, str], ...] = (
    ("~/.matrix/config.yaml", "your runtimes, models and agents, for every tool"),
    ("~/.<tool>/config.yaml", "your defaults for one tool"),
    ("./matrix.yaml", "the project's shared runtimes, agents and components"),
    ("./<tool>.yaml", "the project's settings for one tool"),
    ("$MATRIX_CONFIG", "an explicit shared file, which must exist"),
    ("$<TOOL>_CONFIG", "an explicit file for one tool, which must exist"),
)

#: Each key of the ``matrix:`` section. The tests hold the keys to :class:`MatrixConfig`.
CONFIG_KEYS: dict[str, str] = {
    "definitions": "directories of agent definitions (`*.md`, Claude Code format)",
    "agent_runtimes": "name: `{type, ...options}`: agent sessions, with a loop and tools; "
    "`type` is `claude-sdk`, `mock`, or a registered agent runtime's type URL",
    "model_runtimes": "name: `{type, ...options}`: one model call each; `type` is `hardline`, "
    "`mock`, or a registered model runtime's type URL",
    "observers": "a short name or type URL, or `{type, ...options}`",
    "agents": "name: `{runtime, definition?, description?, system_prompt?, model?, tools?, "
    "max_turns?}`; `runtime` names an agent runtime",
    "components": "a list of `{name, type, config}`: the components of a run",
    "inputs": "the kinds the caller supplies when it runs the components",
    "store": "where runs are recorded: `{type, ...options}`; default `jsonl` under `.matrix/runs`",
    "limits": "concurrency, deadlines, grace, cleanup, `on_failure`, `retry`, per-component "
    "overrides",
    "models": "a hardline registry handed to every `type: hardline` model runtime without its own",
}

#: What an extension may declare it needs, handed to its ``build`` by name. The tests hold
#: these to what composition supplies.
NEEDS: dict[str, str] = {
    "agents": "the composed agents, by name",
    "model_runtimes": "the composed model runtimes, by name",
    "model_runtime_entries": "the registry entry of each composed model runtime, by name (its "
    "type URL and what it drives, for the sessions a component records)",
    "schemas": "the registered payload schemas, by kind",
    "models": "the config's `models` section",
    "cwd": "the working directory a caller supplies",
    "name": "the component's name in the run (components only)",
}

COMPONENT_EXAMPLE = """\
import asyncio

from matrix import Executor, wire

TEXT, SHOUT, COUNT = "demo.v1.text", "demo.v1.shout", "demo.v1.count"


class Upper:
    requires = {TEXT}
    provides = {SHOUT}

    async def run(self, inputs, ctx):
        return {SHOUT: [line.upper() for line in inputs[TEXT]]}


class Count:
    requires = {SHOUT}
    provides = {COUNT}

    async def run(self, inputs, ctx):
        return {COUNT: sum(len(line) for line in inputs[SHOUT])}


wiring = wire({"count": Count(), "upper": Upper()}, inputs=[TEXT])
run = asyncio.run(Executor().run(wiring, {TEXT: ["hello", "world"]}))
assert wiring.order() == ("upper", "count")  # inferred from the kinds
assert run.rows(COUNT) == (10,)
"""

EXTENSION_EXAMPLE = """\
from pydantic import BaseModel, ConfigDict

DIFF, SCORE = "acme.v1.diff", "acme.v1.score"


class ScorerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an unknown key is refused, by its path

    weight: float = 1.0


class Scorer:
    requires = {DIFF}
    provides = {SCORE}

    def __init__(self, config: ScorerConfig) -> None:
        self.weight = config.weight

    async def run(self, inputs, ctx):
        return {SCORE: [self.weight * len(diff) for diff in inputs[DIFF]]}


def register(registry):
    registry.register(
        "component",
        "acme.v1.scorer",
        Scorer,  # called as Scorer(validated_config, **needs)
        config=ScorerConfig,
        effects=set(),  # what it may touch; leave it out and the effects are unknown
        summary="Scores each diff by its length",
    )
"""

ENTRY_POINT = """\
[project.entry-points."matrix.extensions"]
acme = "acme.matrix_extension:register"
"""

CONFIG_EXAMPLE = """\
matrix:
  agent_runtimes:
    offline:                      # a stand-in: canned replies, no model is called
      type: mock
      by_agent: {claimer: '{"claims": ["b is added"]}'}   # agent name -> its reply
  model_runtimes:
    offline:                      # a stand-in for a model: one canned reply
      type: mock
      default: '{"score": 4}'
  agents:
    claimer: {runtime: offline, system_prompt: 'List the claims. Reply with JSON only.'}
  inputs: [acme.v1.diff]
  components:
    - name: claim
      type: matrix.v1.agent-step
      config: {agent: claimer, inputs: {diff: acme.v1.diff}, task: "Claims in:\\n{diff}",
               response: acme.v1.claim-reply, output: acme.v1.claims}
    - name: rate
      type: matrix.v1.model-step
      config: {runtime: offline, system: 'Rate the claims 1-5. Reply {"score": N} only.',
               inputs: {claims: acme.v1.claims}, prompt: "Rate:\\n{claims}",
               output: acme.v1.rating}
"""


def _fence(language: str, text: str) -> str:
    return f"```{language}\n{text.rstrip()}\n```"


def _keys(names: list[str] | tuple[str, ...]) -> str:
    return ", ".join(f"`{name}`" for name in names)


def _extras() -> list[str]:
    try:
        declared = metadata("matrix").get_all("Provides-Extra") or []
    except PackageNotFoundError:  # a source tree that was never installed
        declared = list(EXTRAS)
    return [extra for extra in declared if extra in EXTRAS]


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


def _when() -> str:
    return """\
## When to use matrix

Use matrix to:

- write a unit of work that declares the kinds of data it requires and provides, and let
  matrix order the work, run it, and keep every row it hands over;
- bind agent definitions to agent runtimes, and make model calls on model runtimes, from
  config;
- check and plan a config, then run it from the command line (`matrix plan`, `matrix run`)
  or from Python (`compose(load_config()).run(...)`), recorded as it goes;
- read YAML and markdown documents as your own pydantic types, every problem reported with
  its key path and file (`matrix --skill -r loading`).

Do not use matrix:

- to schedule work over time: a run is one call that ends; a retry policy repeats a failed
  component within a run, nothing repeats a run.
- for two steps that always run in the same order on the same data: call them directly."""


def _install(version: str) -> str:
    extras = "\n".join(f"- `{name}`: {EXTRAS[name]}" for name in _extras())
    commands = f"{INSTALL}\nmatrix --version      # {version}"
    return f"""\
## Install and check

{_fence("bash", commands)}

Extras, as `matrix[<extra>]`:

{extras}

A script that imports matrix runs where matrix is installed: inside the project you added it
to, `uv run python script.py`; outside any project, name it for that one run:

{_fence("bash", f'uv run --with "{REQUIREMENT}" python script.py')}"""


def _protocol() -> str:
    segments = _keys(sorted(KIND_SEGMENTS))
    return f"""\
## The component protocol

A component is any class with `requires`, `provides` and an async `run(inputs, ctx)`; it
need not import matrix. This runs as written:

{_fence("python", COMPONENT_EXAMPLE)}

- **Kinds.** `requires` and `provides` are collections of kinds. A kind is a type URL,
  `<namespace>.v<N>.<name>`: lowercase kebab-case segments joined by dots, as in
  `acme.v1.review`. No segment may be {segments}.
  A kind without its `v<N>` does not wire, and a type URL without one does not register.
- **Wiring.** Each kind has exactly one provider; two streams of the same shape are two
  kinds. A required kind nothing provides must be a run input. No cycles. `wire` reports
  every problem at once as one `CompilationError` (kind `config`).
- **`inputs`** holds the rows of each required kind, as a tuple, in the order they were
  recorded, and nothing else. `inputs.one(kind)` is the single row, or `LookupError`.
- **Returning.** `run` returns a mapping of kind to one row or a list of rows, or nothing.
  A row of a kind it does not provide, or one its kind's schema refuses, is a
  `ContractError` (kind `contract`).
- **`ctx.provide(kind, row)`** hands one row over while still running; when it returns the
  row is checked, recorded and durable. Use it for long work: a run given a store
  (`Executor().run(..., store=JsonlConstructStore(dir))`) and then killed keeps it.
- **`ctx.stopping`** is an `asyncio.Event` set when the run asks the component to finish
  (its deadline, another component's failure, a cancel). Check it and return what you
  have: it is recorded, and the component ends `stopped` (past its deadline, `failed`), not
  `completed`, because what it returned may be partial; `--from` never reuses it. A
  component that does not return is cancelled after the grace period.
- **`ctx.defer(release)`** registers a sync or async release, called once when the run
  ends, last first, whether it completed, failed or was cancelled.
- **`ctx.emit`, `ctx.measure`, `ctx.span`** are telemetry under your own namespace
  (`acme.*`; `matrix.*` is matrix's). They are never part of the result. OpenTelemetry
  gets only the numbers and booleans among a `ctx.span`'s fields: a string is content.
- **Configuration** arrives when the component is built, never through `run`."""


def _extension() -> str:
    points = _keys(sorted(MATRIX_POINTS))
    needs = "\n".join(f"  - `{name}`: {what}" for name, what in NEEDS.items())
    return f"""\
## Write and register a component

Declare an entry point in the `matrix.extensions` group in the package's `pyproject.toml`:

{_fence("toml", ENTRY_POINT)}

Its value is `register(registry)`, which registers each component under a type URL in the
package's own namespace (`acme` owns `acme.*`):

{_fence("python", EXTENSION_EXAMPLE)}

- The points matrix declares: {points}. Register a component at `component`.
- `build` is called as `build(validated_config, **needs)`, or `build(**needs)` without a
  `config` model. The needs it may declare, with `needs={{...}}`:
{needs}
- `effects` says what it may touch; leave it out and the run's effects are unknown, never
  read as none.
- A runtime registers at `agent-runtime` or `model-runtime` and is held to that point's port
  (`check` and async `run`; `check` and async `call`) when it is registered and when it is
  built: one that lacks a member is a `config` error naming it. A mock declares
  `stand_in=True`; a runtime over a package declares it as `drives=("<distribution>",)`, so a
  tool can record that package's version (`matrix catalog --json` shows it).
- An extension that fails to import or register is quarantined: its entries are dropped,
  the others load, and `matrix catalog` lists it as `FAILED`.

Install the package, then name the component in config (below) and run `matrix check`."""


def _config() -> str:
    tiers = "\n".join(f"| {tier} | `{path}` | {what} |" for tier, (path, what) in
                      enumerate(TIERS, start=1))  # fmt: skip
    keys = "\n".join(f"- `{key}`: {what}" for key, what in CONFIG_KEYS.items())
    return f"""\
## Config

Every tool that reads matrix config reads the same tiers, later tiers winning; `matrix`
reads them as the tool `matrix` (`--tool T` reads T's), and each `CONFIG` a command names
sits above them, in order (`-` is standard input):

| Tier | File | For |
|---|---|---|
{tiers}

Mappings merge key by key, so every tier's runtimes and agents are present; lists replace;
`definitions` directories accumulate, each relative to the file that names it, so one agent
defined in two tiers' directories is a `name_taken` problem. `--only` reads the `CONFIG`
files alone, no tier: `matrix plan live.yaml --only`, then `matrix run live.yaml --only
--input KIND=PATH`; the record still goes to the store that config names (default
`.matrix/runs` under the working directory). A file holds a `matrix:` section and one
section per tool. The `matrix:` keys, each optional; an unknown key is refused:

{keys}

A `./matrix.yaml` of an agent step and a model step that composes, plans and runs in this
build as written:

{_fence("yaml", CONFIG_EXAMPLE)}

- **Two kinds of runtime.** An agent runtime hosts a session with a loop and tools: agents
  bind to it and an `agent-step` runs one. A model runtime makes one call: a `model-step`
  names it. An agent bound to a model runtime is refused, with the fix.
- **One `response` per step.** An agent step provides its reply (an `AgentResponse`, the
  task as sent in its `task`) under `response`; a model step its call (a `ModelCall`, the
  prompt as sent in its `prompt`). Each default serves one step, so each further step names
  its own.
- **`output` is the reply as a row of that kind.** A model step asks its runtime for the
  kind's registered schema when there is one; otherwise, and always for an agent step, the
  reply must be one JSON document, alone or in a ```` ```json ```` block: ask for it in the
  prompt or the task.
- **The mocks** are stand-ins (`stand_in: true` in `matrix catalog`). The agent one replies by
  `responses` (keyed by the whole task as sent), then `by_agent`, then `default`, else echoes
  `mock:<agent>:<task>`; the model one by `responses`, `by_model`, `default`, else
  `mock:<model>:<prompt>`.
- **`model`** is the runtime's to read: on `claude-sdk` an alias (`haiku`, `sonnet`, `opus`)
  or a full model id; on a model step on `hardline`, a hardline registry name, checked when
  the step is built.

The order of `components` means nothing: the work is ordered by the kinds. Steps and runtimes
in full: `matrix --skill -r agents`; store, limits and retries: `-r config`."""


def _commands() -> str:
    width = max(len(command) for command, _ in COMMANDS)
    lines = "\n".join(f"{command:<{width}}   # {what}" for command, what in COMMANDS)
    return f"""\
## Commands, in the order they are used

{_fence("bash", lines)}

Plan before you run: `plan` calls nothing and reports every problem at once, `run` may
call models. Each takes `--json` and then prints one document with a `schema` field on
stdout (`matrix.v1.config`, `matrix.v1.config-sources`, `matrix.v1.check`, `matrix.v1.plan`,
`matrix.v1.run`, `matrix.v1.runs`, `matrix.v1.agents`, `matrix.v1.catalog`,
`matrix.v1.extension`), with its
`warnings` (a permissive runtime, telemetry asked for without its extra) inside it. `run`
reads each `--input` as JSON lines (`-` is standard input) and prints the run id and
its record's files (`run.json`, a `<kind>.jsonl` per kind, `events.jsonl`); `matrix runs`
lists the recorded runs; `--from RUN_ID` starts from one (`matrix --skill -r running`).
Nothing prompts."""


def _errors() -> str:
    rows = "\n".join(
        f"| {code} | `{kind}` | {EXIT_MEANINGS[kind]} | {EXIT_NEXT[kind]} |"
        for kind, code in EXIT_CODES.items()
    )
    kinds = _keys(sorted(PROBLEM_KINDS))
    own = _keys(sorted(MATRIX_PROBLEM_KINDS))
    envelope = (
        '{"schema": "matrix.v1.error",\n'
        ' "error": {"kind": "config", "message": "...", "fix": "...", "complete": true,\n'
        '           "problems": [{"path": "matrix.agent_runtimes.m.type", "kind": "name_unknown",\n'
        '                         "message": "...", "fix": "...", "allowed": ["mock"],\n'
        '                         "got": "nope", "source": "./matrix.yaml"}],\n'
        '           "details": {}}}'
    )
    return f"""\
## Errors and exit codes

A command that fails exits with its kind's code and prints one envelope on stderr, as one
JSON line under `--json`:

{_fence("json", envelope)}

- Act on the exit code and `error.kind`, then on `fix`. Each problem names the key (`path`,
  dotted from the document's root), the file it is in (`source`), what was written (`got`)
  and the legal values (`allowed`). Every problem is reported at once; `complete: false`
  means checking stopped early, so fix these and check again.
- Problem kinds: {kinds}; matrix's own: {own}. A failed run exits `run_failed` with the
  component's error kind in `error.details.cause` (`contract`: it broke what it declares;
  `component`: it raised).
- Tolerate an error kind, a problem kind or a field you do not know: each set grows. Refuse
  a `schema` whose major is not `v1`.

| Exit | Kind | Meaning | What to do next |
|---|---|---|---|
{rows}"""


#: The references ``matrix --skill -r NAME`` prints, and what each holds.
REFERENCES: dict[str, str] = {
    "config": "every config key in full: the store, limits and retries, merging, --only",
    "agents": "agent and model steps in full, the mocks' replies, each runtime's options",
    "running": "plan then run: inputs, run ids, reading a run's record, `matrix runs`, --from",
    "telemetry": "OpenTelemetry from the standard variables, the conventions followed, the "
    "local event log",
    "loading": "the loader's Python API: one file, a folder, tiers; markers; problem records",
}


def _references() -> str:
    lines = "\n".join(f"- `{name}`: {what}" for name, what in REFERENCES.items())
    return f"""\
## References

`matrix --skill -r NAME` prints one:

{lines}"""


RETRY_EXAMPLE = """\
matrix:
  store: {type: jsonl, root: runs}          # default: jsonl under .matrix/runs
  limits:
    concurrency: 4                          # components at once; default: as wired
    deadline_s: 600                         # each component, from its start
    grace_s: 5                              # to return after a stop request
    cleanup_s: 30                           # for a cancelled component, or one release
    on_failure: isolate                     # fast (default): stop all; isolate: dependents
    retry: {attempts: 3, backoff_s: 2, errors: [transient]}
    components:
      review: {deadline_s: 1800, retry: {attempts: 5, errors: [rate_limited]}}
"""


def _reference_config() -> str:
    fields = ", ".join(f"`{name}`" for name in Retry.model_fields)
    return f"""\
# matrix config, in full

The tiers and the `matrix:` keys are in the main skill. This is the rest.

## Merging

Later tiers win. Mappings merge key by key, so `agent_runtimes`, `model_runtimes` and
`agents` hold every tier's entries, a later tier's replacing an earlier one's of the same
name; lists replace, except `definitions`, which keeps every tier's directories, each read
from beside the file that names it: an agent defined in two of them is a `name_taken`
problem at `matrix.definitions`. A problem names the file that set the value it is about
(`source`), so `matrix config --sources` is rarely needed.

Each `CONFIG` given to a command is a tier above the others, in the order given; `-` reads
one from standard input, where a relative directory stays as written. `--only` reads the
`CONFIG` files and none of the tiers, so a config runs on its own. The record still goes
to the store the files name, by default `jsonl` under `.matrix/runs` in the working
directory:

```bash
matrix plan live.yaml --only                        # live.yaml alone
matrix run live.yaml --only --input acme.v1.diff=d.jsonl
matrix run ~/.matrix/config.yaml live.yaml --only --input acme.v1.diff=d.jsonl
```

## store

`{{type, ...options}}`: a built-in's short name (`jsonl`, `memory`) or the type URL of a store
a package registered at the `store` point. `jsonl` takes `root` (default `.matrix/runs`,
relative to the working directory) and `fsync` (default false: a kill loses nothing, a power
cut may lose the last rows). `matrix describe matrix.v1.store.jsonl` prints the schema. A
store of your own registers like a component, at the point `store`, and is then named here.

## limits

{_fence("yaml", RETRY_EXAMPLE)}

`retry` is the policy every component gets; `components.<name>.retry` replaces it for one.
Its fields: {fields}. A failure is retried when `errors` names its kind (`transient`) or its
reason (`unavailable`, `rate_limited`, `timeout`), fewer than `attempts` have been made, the
run is not stopping, and the failed attempt handed over no rows: rows are part of the
record and a second attempt would add to them. The wait is `backoff_s * multiplier ** (n -
1)`, or the `retry_after_s` the failure reported, never more than `max_wait_s`. The key is
`errors`, not `on`: YAML reads a bare `on` as `true`.
"""


def _reference_running() -> str:
    return """\
# Running a matrix config

## Plan, then run

`matrix plan [CONFIG] --json` composes, wires and reports, calling nothing:
`stages` (what starts together), each component's `requires`, `provides`, `after`,
`effects` and `attempts`, the `agents`, `agent_runtimes` and `model_runtimes` in use (each
runtime's `stand_in` says whether its answers are canned), `effects` for the whole run (null
when any is unknown, never read as none), `agent_calls_min` and `agent_calls_max`, and
`model_calls_min` and `model_calls_max` (one per step at least, one per attempt at most; the
maxima are null when a component that is neither an agent step nor a model step may call a
model), the `store` and where a run would be recorded, and the `limits`. A problem anywhere
is the error envelope with every problem.

`matrix run [CONFIG] --input KIND=PATH ... --json` runs it. Each `--input` file holds one
JSON value a line, the rows of that run-input kind (`-` is standard input, once). It prints
`matrix.v1.run`: `run_id`, `status`, `components` (how each ended: `completed`, `stopped`,
`failed`, `cancelled`, `skipped`, `reused`, with `attempts` when retried), `failures`, `releases`,
`location` and `warnings`. A run that fails exits 8 (`run_failed`) and still prints the
document; Ctrl-C or SIGTERM stops every component, calls every release, and exits 130.
`--run-id` names the run (default `<UTC time>-<4 hex>`); an id already taken is a `store`
error.

## The record, and how to read it

`matrix run` prints where the record is and its files; `matrix runs` lists every recorded
run, oldest first, a line each: id, status, start and where; with `--json`, `run_id`,
`status`, `started`, `ended`, `location`.
With the default store a run is a directory under `.matrix/runs/<run_id>/`:

- `run.json`: the run document, replaced once when the run ends: `status`, `components` (how
  each ended), `failures`, and `tables` (each kind's file and row count);
- `<kind>.jsonl`: one per kind, each line one row as recorded; an agent step's `response`
  rows are `AgentResponse`s, the task as sent in `task`, the reply in `content`; a model
  step's are `ModelCall`s, the prompt as sent in `prompt`, the reply in `text`, why it
  ended (`finish_reason`); each points at its session row (`session`);
- `matrix.v1.session.jsonl`: one line per agent session or model call, a failed one
  included: `status` (`completed`, or `failed` with its `reason` and `error`), `runtime`, the
  model that answered, `stop`, `finish_reason`, `usage` (`input_tokens` counts the cache's),
  `cost_usd`, `session_id`, `request_id`, `trace_id` and `span_id`, `fallback_from`,
  `attempts`, `attempt_log`, and `sent` (a digest of what was sent). The line's position
  (from 0) is the row a reply's `session` names;
- `matrix.v1.ledger.jsonl`: which component handed each row over, when, and the session
  rows behind it (`sessions`);
- `events.jsonl`: the run's events; `matrix.agent.start` carries each task as sent, so a
  step that failed before recording its reply still shows what it was asked.

```bash
jq . .matrix/runs/RUN_ID/run.json
jq -c '{task, content, session}' .matrix/runs/RUN_ID/acme.v1.claim-reply.jsonl
jq -c '{status, reason, runtime, model, stop, usage}' .matrix/runs/RUN_ID/matrix.v1.session.jsonl
jq -c 'select(.name == "matrix.agent.start") | .fields' .matrix/runs/RUN_ID/events.jsonl
```

A run killed mid-way keeps every row it had recorded and reads `interrupted`.

## Starting from a recorded run

`matrix run --from RUN_ID` runs again with that run's inputs, and reuses the rows of each
component that completed there (`stopped` is not completed), was built from the same
type, config and needs (an agent step's agent definition, runtime and the models its runtime
resolved; a model step's model runtime and the models it resolved; `matrix.models`; the
working directory), and whose providers are reused too: those
components end `reused`, the rest run. A code change with the same config is not seen. A
reused row of a kind with a registered schema reaches its consumers as an instance of that
schema, as on a fresh run; a kind without one is reused as the JSON the store kept.
`--input` cannot be given with it.

## From Python

```python
import asyncio
import matrix

# plan_config(tool="matrix", config=None, *, only=False): config is a path, a stream, or a
# list of them, each a tier above the tool's; the first argument is the tool, not a path.
plan = matrix.plan_config(config="live.yaml", only=True)
container = matrix.compose(matrix.load_config(sources=["live.yaml"]))
run = asyncio.run(container.run({"acme.v1.diff": ["..."]}))
print(run.status, container.location(run.run_id))
```

`check_config`, `list_agents` and `list_runs` take the same `(tool, config, *, only)`.
"""


def _reference_agents() -> str:
    return f"""\
# Agent steps, model steps and runtimes

## matrix.v1.agent-step

```yaml
- name: claim
  type: matrix.v1.agent-step
  config:
    agent: claimer                    # a matrix.agents name
    inputs: {{diff: acme.v1.diff}}      # placeholder -> the kind whose rows fill it
    task: "Claims in:\\n{{diff}}"        # {{placeholder}}s; {{{{ }}}} for literal braces
    response: acme.v1.claim-reply     # default {AGENT_RESPONSE}: one step per kind
    output: acme.v1.claims            # optional: the reply, parsed as one JSON document
```

- It requires the kinds `inputs` names; one row fills a placeholder as itself (text as it
  stands, data as JSON), several as a JSON list. Without `task`, a single input is the task.
  Each placeholder must name an input and each input must have a placeholder: the step is
  refused when it is built (`config`), not when a run reaches it.
- It provides an `AgentResponse` under `response`: `content` (the session's final result),
  `transcript` (every text block, in order), `task` (as sent), `tool_calls`, `stop`
  (`completed`, `max_turns`, `max_budget_usd`), `finish_reason`, `family`, and `session`,
  the row of `matrix.v1.session` that holds the rest: the model that answered, tokens (input
  counts the cache's), cost, turns, duration and the session id. A kind has one
  provider: two steps on the default clash, and the problem's path is the second step's
  `config.response`.
- With `output`, the reply must be one JSON document, alone or in a ```` ```json ```` block;
  it is validated against the kind's schema when a package registered one. A reply without
  JSON fails the step (`contract`), quoting the reply. Ask for the JSON in the agent's
  `system_prompt` or the task, and give the mock that JSON.

## matrix.v1.model-step

```yaml
- name: rate
  type: matrix.v1.model-step
  config:
    runtime: fast                     # a matrix.model_runtimes name
    model: haiku                      # a name the runtime resolves; unset, its default
    system: "Rate the claims 1-5."    # optional, as written
    inputs: {{claims: acme.v1.claims}}  # placeholder -> the kind whose rows fill it
    prompt: "Rate:\\n{{claims}}"         # {{placeholder}}s, checked as an agent step's task
    response: acme.v1.rate-call       # default {MODEL_CALL}: one step per kind
    output: acme.v1.rating            # optional: the reply as one row of this kind
```

- One call: no loop, no tools, no turns. The model name is checked with the runtime when the
  step is built, so an unknown one is a `config` problem at `matrix check`.
- It provides a `ModelCall` under `response`: `text`, `value` (the structured reply),
  `prompt` (as sent), `finish_reason` (`stop`, `length`, `content_filter`, `refusal`,
  `tool_call`, `other`; null when the runtime does not say), `refusal`, `family`, and
  `session`, the row of `matrix.v1.session` that holds the rest: `name` and `model` (the
  registry name and the provider's id of the model that answered), `backend`, `provider`,
  `usage` (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`),
  `request_id`, `attempts`, `retries`, `fallback_from` (the model asked for, when a fallback
  answered), latency, `params`, `attempt_log`, `turns_digest` and `row` (the registry row
  that answered).
- A reply that is not a whole answer (refused, cut at `max_tokens`, withheld, empty) is not
  provided: the step fails with reason `refused` or `failed`, naming the `finish_reason`, and
  the fix says what to change (`max_tokens` for a cut reply). The call is still a row of
  `matrix.v1.session`, under the step's name.
- With `output`, when a package registered a schema for the kind, the runtime is asked for
  that structure and validates the reply (hardline repairs a reply that does not fit, within
  its bound); the row is the validated value. Without a schema, the reply must be one JSON
  document, as for an agent step.

## Runtimes

`matrix describe matrix.v1.agent-runtime.<type>` or `matrix.v1.model-runtime.<type>` prints
each one's options. A mock is a stand-in: `stand_in: true` in `matrix catalog` and the plan.

Agent runtimes (`matrix.agent_runtimes`):

- `mock`: offline. A reply is the first of `responses` (keyed by the whole task as sent),
  `by_agent` (keyed by agent name), `default`; else the echo `mock:<agent>:<task>`, which
  shows each task as sent. `model` is recorded, never used.
- `claude-sdk` (extra `claude`): Claude Agent SDK sessions with tools. Its config is all a
  session can be given: `permission_mode` (default `default`), `cwd`, `setting_sources`
  (`user`, `project`, `local`; `[]` is hermetic), `plugins` (`{{type: local, path}}`),
  `skills` (names, or `all`), `agents` (inline subagents: `description`, `prompt`, `tools`,
  `disallowed_tools`, `model`, `skills`, `max_turns`), `fallback_model`, `max_budget_usd`.
  An agent whose `tools` leave out `Skill` on a runtime with `skills`, or an option the
  installed SDK lacks, is refused at binding. `model`: an alias the Claude Code CLI knows
  (`haiku`, `sonnet`, `opus`) or a full model id (as `claude-sonnet-4-5`); unset, the CLI's
  default. `max_budget_usd` is the SDK's own option: the SDK ends a session once it has spent
  that many USD (`stop: max_budget_usd`), and `cost_usd` is what the SDK reported;
  `max_turns` (on the agent) bounds its turns.

Model runtimes (`matrix.model_runtimes`):

- `mock`: offline. A reply is the first of `responses` (keyed by the whole prompt as sent),
  `by_model` (keyed by model name), `default`; else the echo `mock:<model>:<prompt>`. Any
  model name is accepted.
- `hardline` (extra `models`): one call through hardline, in any family it has a row for.
  `model`: a hardline registry name (`matrix.models`, or hardline's own tiers); unset,
  `default_model`, then the registry's default. `max_tokens` bounds a reply; it reports no
  cost.
"""


def _reference_telemetry() -> str:
    return """\
# Telemetry

## With nothing configured

A run recorded by the JSONL store keeps its events beside its tables, `events.jsonl`, one
`matrix.v1.event` a line, flushed as written. The `console` observer (on a terminal, `matrix
run` adds it) shows progress on stderr; `observers: [jsonl]` streams events to stderr or a
file. Events are telemetry: lossy under load, never the result.

## OpenTelemetry

The OpenTelemetry API is a dependency; install the `otel` extra for the SDK and the OTLP
exporters. Then the standard variables are enough: when `OTEL_EXPORTER_OTLP_ENDPOINT` (or
its `_TRACES_` and `_METRICS_` forms) is set, or `OTEL_TRACES_EXPORTER` /
`OTEL_METRICS_EXPORTER` names an exporter other than `none`, every run gets the `otel`
observer and `matrix run` installs the SDK: exporters `otlp` (default) or `console`, OTLP
over `OTEL_EXPORTER_OTLP_PROTOCOL` `http/protobuf` (default) or `grpc`, the resource from
`OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES` (`service.name` is `matrix` unless they
set it). `OTEL_SDK_DISABLED=true` turns it off. Asked for without the extra, nothing is
exported and every document's `warnings` says so (`extra_missing`). From Python, call
`matrix.configure_telemetry()` once, and `.shutdown()` on what it returns at the end.

No content leaves by default: no task, no error message, no string field of a component's
`ctx.span`; `error.type` says what failed. `observers: [{type: otel, capture_content: true}]`
exports them too, as `matrix.task`, `matrix.error` and `<root>.<field>`.

## What is emitted

The GenAI semantic conventions as read on 2026-10-19 (repository
`open-telemetry/semantic-conventions-genai`, no release tagged, status Development), scope
`matrix`:

- spans: `invoke_workflow` (a run), `matrix.component`, `invoke_agent {agent}` with
  `gen_ai.agent.name`, `gen_ai.request.model`, `gen_ai.response.model`,
  `gen_ai.provider.name`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and
  `error.type`; `matrix.model` for a model step's call (no usage: the model runtime's own
  spans count each attempt); `execute_tool {tool}` for each tool call a runtime timed
  (`claude-sdk` does); `matrix.release`; a component's own `ctx.span`. Each is current in the
  task doing its work, so a span a library opens there (an HTTP client, hardline) nests under
  it; each ends with its work, whatever events were dropped. `ctx.emit` opens no span;
- metrics: `gen_ai.invoke_workflow.duration`, `gen_ai.invoke_agent.duration`,
  `gen_ai.invoke_agent.tool_calls`, `gen_ai.execute_tool.duration`,
  `gen_ai.client.inference.usage.input_tokens` / `output_tokens` (agent sessions only: a
  model call's tokens are counted by the model runtime, hardline, per attempt, and are on the
  call's session row; nothing is counted twice); matrix's own where the
  conventions have none: `matrix.component.duration`, `matrix.component.count`,
  `matrix.component.retries`, `matrix.agent.cost_usd` (as a runtime reported it),
  `matrix.events.dropped`; and each
  `ctx.measure` under its own name.
"""


def _reference_loading() -> str:
    return """\
# Loading documents as your own types

One loader reads every document matrix reads, and any document a caller names a pydantic
type for. A source reads plain data; the loader validates it once and reports every problem
as a `matrix.Problem` (`path`, `kind`, `message`, `fix`, `allowed`, `got`, `source`) in one
`ConfigError` (exit 3); nothing at the location is a `NotFoundError` (exit 4).

```python
from typing import Annotated

from pydantic import BaseModel, Field

import matrix


class Probe(BaseModel):
    id: str
    kind: str = "prompt"
    prompt: str = Field("", alias="body")      # a markdown file's text arrives as `body`


class Experiment(BaseModel):
    name: str
    probes: Annotated[str, matrix.BesideFile()]   # a relative path is read from beside the file


experiment = matrix.load(Experiment, "lab/experiment.yaml")   # one file, or a stream
probes = matrix.load_each(Probe, experiment.probes, key="id", name_from_file=True)
settings = matrix.load_config(MySettings, client_key="acme")  # the tiers, section `acme:`
```

- `load(model, location)`: one YAML file, one markdown file (frontmatter, with its text
  under `body`), or a text stream read as YAML.
- `load_each(model, *locations, key="name", name_from_file=False, suffixes=...)`: every
  document in each folder (a file is a folder of one), in name order; a `key` value defined
  twice is a `name_taken` problem naming both files; one unreadable file hides nothing.
- `load_config(client_type, client_key, sources=None, loader=None)`: the tiers, merged, the
  `matrix:` section and the tool's validated together.
- A location is a path, a folder inside an installed package
  (`importlib.resources.files("acme") / "kinds"`), or a stream.
- Markers on top-level fields: `BesideFile()` resolves relative paths against the folder of
  the file that wrote them (a default no file wrote stays as it is); `Accumulate()` keeps a
  list from every tier when merging.
- `Loader(source)` takes any `DocumentSource` (`document`, `documents`, `beside`): files are
  one source, not the only one.
"""


_RENDERERS = {
    "config": _reference_config,
    "agents": _reference_agents,
    "running": _reference_running,
    "telemetry": _reference_telemetry,
    "loading": _reference_loading,
}


def reference(name: str) -> str:
    """One reference's text. Raises UsageError for one the skill does not have."""
    render_one = _RENDERERS.get(name)
    if render_one is None:
        names = ", ".join(REFERENCES)
        raise UsageError(
            f"the skill has no reference {name!r}. References: {names}",
            fix=f"name one of: {names}",
            value=name,
            allowed=list(REFERENCES),
        )
    return render_one()


def render(version: str, name: str | None = None) -> str:
    """The skill for this matrix, or its reference ``name``."""
    if name is not None:
        return reference(name)
    lead = (
        f"# matrix {version}: components wired by kind, composed from config\n\n"
        "matrix runs components wired by the kinds of data they exchange, records every row "
        "as the run goes, and composes agents, runtimes and a store from config. The "
        "`matrix` command inspects what is installed, checks and plans a config, and runs it."
    )
    sections = [
        _frontmatter(version),
        lead,
        _when(),
        _install(version),
        _protocol(),
        _extension(),
        _config(),
        _commands(),
        _errors(),
        _references(),
    ]
    return "\n\n".join(sections) + "\n"
