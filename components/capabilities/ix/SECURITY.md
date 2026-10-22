# Security — ix

ix validates, plans and registers an experiment, and runs one. Validating, planning and
registering read files, compute, and write one registration file. **Running does what the
experiment file says**: it starts programs, sends network requests, runs agent sessions
that call a paid model and use tools on this machine, and runs code (the agent's, and the
experiment's own graders) in child processes.

Audience: anyone running ix against an experiment, a genre or a kind they did not author.

## What ix touches

| Command | Reads | Writes |
|---|---|---|
| `validate`, `plan`, `deviations` | the experiment file (or standard input, given `-`); genre and kind files; probe files; the registration file; ix's settings; for `plan`, the runs under `.ix/runs`; for `plan` and `deviations` (and `register` and `run`, below), every file and folder the subjects and sensors name, to digest it | nothing |
| `register` | the same | `<file-stem>.registration.json` beside the experiment |
| `genres`, `kinds`, `--skill` | genre and kind files | nothing |
| `run` | the same as `plan`, the environment, and the git work tree the experiment sits in | `<experiment directory>/.ix/runs/<run id>/` (or under `runs:` in ix's settings); temporary directories it removes; whatever the programs and sessions it starts write |
| `report`, `runs` | the experiment file; the runs under `.ix/runs` | nothing |

`report` and `runs` call no model. A report of a run that ended before its sensors read
its interactions reads them then, but only with sensors whose adapters are not handed
matrix's runtimes; a run that used one (a `judge`) is reported without readings (the test
suite holds this with a judge that counts its calls over two reports). A third-party sensor
that reaches a model provider by its own means, not through the runtimes ix hands it, is
outside what ix can tell.

`run` also **starts**: `git -c core.fsmonitor=false -C <experiment directory>` three times
(`rev-parse --is-inside-work-tree`, `rev-parse --verify HEAD`, `status --porcelain
--untracked-files=no`) to record the commit, with the repository's file-system monitor
turned off. A repository's own config can still name a program git runs while it reads the
tree (a clean filter), as it can for any `git status`: the experiment's repository is
trusted as the experiment is (boundary 6). It also starts each `process` subject's
`command`, each `process` environment
service's `command`, `k6` for an `http-load` probe, `ps` for the `memory` sensor, an agent
session for each trial of an `agent-session` subject (on `claude-sdk`, the Claude Code CLI,
through the Claude Agent SDK), a model call for each trial of a `model-call` subject (on
`hardline`, an HTTP request to the provider its registry names), a subject's `setup`
command, and a Python child process for
each `function-test` reading, each `outcome` reading and each round of a `test-feedback`
loop. And it **sends**: HTTP requests from k6 to each probe's `target`; readiness checks to
`ready` and `url`; each prompt, and what the session does, to the model provider of the
session's or the call's runtime; each reply and its rubric to the judge's model provider;
and spans to an OpenTelemetry endpoint when the `OTEL_*` variables name one.

## Trust boundaries

### 1. Files are data

The experiment file, genre files, kind files, probe files and ix's settings are parsed with
`yaml.safe_load`, through matrix's loader. A YAML tag that would construct a Python object
is a parse error and the file is refused. The test suite holds this for a genre file, a kind
file and the experiment file, with a tag that creates a file under an unsafe loader (the test
checks that it does). Probe files and the settings go through the same loader and are not
tested separately. The registration is JSON.

### 2. An installed genre or kind package is imported

Genres and kinds are found through the `ix.genre` and `ix.kind` entry points. An entry point
names a package of YAML files. ix never calls the entry point, but locating the files
imports the package, so **that package's `__init__.py` runs**. An adapter registered at
ix's points through `matrix.extensions` is code by design, and runs when a run uses its
kind. This is the trust you already gave by installing the package; ix adds nothing to it
and removes nothing from it. A `genres/` or `kinds/` directory beside an experiment holds
YAML only and imports nothing.

### 3. Paths in an experiment are followed

Probes not written inline are read from the `probes/` folder beside the experiment.
`fixtures`, `skills`, `plugins`, `definition`, `graders` and `rubric_file` are read from
where they point, relative to the experiment or absolute, as the user running ix, and so is
any other option a kind declares a `path` or `paths`, and each item of a `command` (a
`process` subject's `command`, an agent session's `setup`) that names a file. `plan`,
`register`, `deviations` and `run` read each of them, every file under a folder with links followed, to digest it. A
probe file's content goes into the probe set's digest; the plan prints probe ids and the
digest, not the content. Of the files the subjects and sensors name, the registration and
the run's record keep the path as written and the digest, never what the file holds. The registration is written beside the experiment file, under a name
derived from the file's own name.

### 4. A declaration is a claim, not a control

A kind declares its effects (`process`, `network`, `tools`, `code`, `containers`,
`filesystem`) and the plan reports them. That is a statement by whoever wrote the kind file,
and ix has no code that enforces it. Two properties limit how it can mislead:

- an experiment can add effects to what a kind declares and cannot remove one;
- two files declaring the same kind are both refused, so a lab's `kinds/` directory cannot
  quietly redeclare a built-in kind with fewer effects, or a sensor's metric as not judged.

A third-party kind package is trusted to describe itself honestly, as in boundary 2.

### 5. The registration is not tamper-proof

A registration is a plain file. Its purpose is to make a change of design visible, not to
prevent one: anyone who can edit the experiment can edit or delete the record. Keep both in
version control, where an edit to the record is itself a visible change.

### 6. An experiment file is a program to run

`ix run` executes the `command` of every `process` subject and environment service, and an
agent subject's `setup`, with the user's environment and privileges, in the directory the
experiment names. **Running an experiment you did not write is running its author's
commands.** Nothing is sandboxed. `ix plan` shows what a run would start (`Effects:`)
without starting it; read the plan, and the file, before running someone else's.

What ix does to bound it:

- a command is an argument list passed to `exec`, never a shell string, so nothing in the
  file is interpreted by a shell;
- `ix run` refuses an unregistered experiment unless told `--exploratory`, so a run is
  normally of a design someone froze on purpose;
- each program is started in its own process group and the group is killed when its trial
  (or, for the environment, the run) ends, on completion, failure, Ctrl-C and SIGTERM alike.
  `kill -9` of ix itself leaves them running; a later trial whose subject's address already
  answers is recorded as a harness failure and not measured.

### 7. An agent session acts on this machine

An `agent-session` subject on the `claude-sdk` runtime is a Claude Code session with the
tools its definition allows. Its tools act as the user who runs ix, with ix's environment,
credentials included, as the `permission_mode` in its `runtime_options` allows:
`bypassPermissions` approves every tool call, and `ix plan` and the run's report say so in
their `warnings`, once per subject. Each trial's session starts in a fresh temporary
directory, removed when the trial ends, but nothing confines a tool to that directory.
`setting_sources: []` in its `runtime_options` keeps this machine's own settings and
plugins out of the session; without it the session reads them. ix passes a subject's
`runtime_options` to the runtime as written and grants nothing of its own: what a session
may do is what that config and its definition say, and the runtime's config schema refuses
a key it does not declare. The session sees its prompt and the files copied in; it never
sees the probe's truth.

matrix builds the session and owns its authority: matrix's SECURITY.md has the detail. A
`model-call` subject makes one model call per trial through a model runtime (`hardline`),
with no tools and no working directory, and the `mock` runtimes call no model. `ix run
--dry-run` puts every session, every call and a judge on the `mock` runtime of its port,
so it calls no model; it still copies the fixtures and skills, runs a subject's `setup`
command and runs the code the sensors run (section 8), as a real run would. Which runtime
stands in is what its registration declares (`stand_in`), never read from its name.

### 8. Code runs in a child process that is not a sandbox

`function-test` and `outcome` run code: `function-test` the code the agent wrote (and a
`test-feedback` loop the same, between rounds), `outcome` the graders module the experiment
names. Each runs in a child Python process started with `-I`, in a fresh temporary
directory, with a time limit (30 seconds unless `timeout` says otherwise) after which its
process group is ended, and with only `PATH` and the locale in its environment, so no
credential of ix's environment is in reach through it. **That is not a sandbox**: the code
can still read and write files, open sockets and start programs as the user ix runs as.
Model-written code is untrusted; run code from a subject you would run yourself, or run ix
in a container. A graders module is trusted as far as the experiment's author is. Neither
is imported by `validate` or `plan`.

**Code under test may try to game its grader.** Code that knows it is being tested can
print what a passing run would report and exit, rewrite the expectations it can reach, or
return a value that claims to equal anything. So `function-test` gives the child only each
case's input, never what is expected of it; the child reports what each call returned (as
plain data) or raised, and ix compares that with the truth in its own process. Those
reports go back on a copy of the child's stdout made before the code is loaded, each line
carrying a token drawn for the run, while the code's own stdout goes nowhere. Code that
searches its own process can still find that copy and the token and report a value it did
not return, but it cannot learn the expected values from the harness, since they never
enter its process. It can read them from disk if it finds the experiment's files, because
the child is not a sandbox: run ix in a container when that matters.

### 9. Nothing in ix bounds what a run costs

A session on `claude-sdk`, a call on `hardline`, and a judge on either call a paid model,
and ix neither estimates what a run will cost nor stops one for what it has cost. What bounds a
run is its shape: the plan's `Trials` is how many sessions it starts (and a judge's
`repeats` how many calls it makes per trial), and `max_turns` bounds each session. The
Claude Agent SDK's own `max_budget_usd`, given in a subject's `runtime_options`, ends a
`claude-sdk` session once that session has cost that much (`stop: max_budget_usd`). What a
runtime reports a session cost is recorded with the session, in its `usage`, as a fact.
Read the plan before a run, and run with `--dry-run` first: it calls no model.

### 10. Load is sent where the file says

An `http-load` probe sends requests to its `target` for its `duration`, at the rate or
concurrency the file gives. ix does not check that the target is yours. Pointing one at a
service you do not operate is a load test of that service.

The k6 script is fixed text shipped in ix. A probe's URL, method and headers reach it as
JSON in an environment variable, so nothing an experiment writes is interpolated into code.

### 11. Credentials

A `http` subject's `auth: env:NAME` names the variable that holds a bearer token. The token
is read when the subject is reached, passed to k6 in its environment, and is not written to
the experiment, the registration, the run's record or the generated script. It is visible,
like any environment variable, to processes that can read k6's environment. Headers written
literally under `headers:` are part of the experiment and are stored in the run's manifest:
do not put a secret there. A model runtime finds its own credentials (the Claude Code CLI's,
or the keys hardline's registry names); ix neither reads nor stores them.

### 12. The record holds what the run saw

`.ix/runs/` holds the experiment as run (commands, URLs, literal headers), the design, the
path and digest of each file the subjects and sensors name, the commit and the installed
versions, every measurement, and for an agent trial the prompt, the reply, every tool call with its input
(the commands a session ran included), each round's transcript and the files collected.
Keep it out of version control if any of that is private. A record is plain files: it can be
edited, and `ix report` reports what the files say.

## Findings

### I-8 — `ix` is a taken name on PyPI (2026-10-09)

`ix` on PyPI is an unrelated package. Observed 2026-10-09: a throwaway extension package
that declared `dependencies = ["ix"]`, installed beside ix with `uv run --with <package>`,
pulled PyPI's `ix` instead of this one, and its import failed before this ix's CLI ran.

On the git-subdirectory install path it resolves correctly: uv clones the whole repository,
finds `components/capabilities/pyproject.toml` as the workspace root, and applies its
`[tool.uv.sources]`. It resolves to the stranger whenever a third-party package names `ix`
as a bare dependency, which is what a package shipping genres or kinds would naturally
write.

Until a naming decision is made, a package extending ix should depend on it by git URL at
a release's tag
(`ix @ git+https://github.com/mox-labs/gnx@ix-v0.5.0#subdirectory=components/capabilities/ix`),
not by bare name.

### Earlier findings

I-1 to I-7 concerned code this package no longer has: a sensor that executed generated code
in process, the scoring of results, an earlier dependency on `matrix`, and an optional
evaluator's telemetry. That code was removed in 0.3.0 and the findings went with it; they
are in the repository's history. Generated code runs again from 0.5.0, in a child process
(boundary 8), never in ix's own.

## Not covered

- ix does not verify the provenance of an experiment, a genre or a kind. Nothing is signed.
- ix does not check that a subject's `command`, a probe's `target` or a plugin an agent
  session loads is safe to use. It reports what the kinds declare and then, on `ix run`,
  does what the file says.
- ix does not limit what a started program, an agent's tools or the code a sensor runs may
  do, or how much load a probe may send.

## Reporting

Open an issue on the gnx repository, https://github.com/mox-labs/gnx/issues. There is no
separate embargo channel.
