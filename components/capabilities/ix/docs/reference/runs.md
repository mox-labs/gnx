# Registration, runs and the record

What `ix register` freezes, what `ix run` does trial by trial, how to follow a long run, and what a run leaves on disk.

## Registration

`ix register` writes `<file-stem>.registration.json` beside the experiment. It stores the
design as resolved, what the file sets, what the genre supplies and what each kind defaults
(comparison, margin, threshold, guardrails, control, repeats, seed, order, calibration,
estimators, the analysis and its parameters, the sensors, the defaults by entry), in a
declared form of its own, `ix.v1.registered-design`, with the digests of the probe set, the
experiment and the plan. The plan's digest is computed over that same form, built key by
key, so a rename inside ix's records moves no digest; a release that moves one says so in
the changelog, with the registrations to amend. The record also holds the digest of each file
and folder the subjects and sensors name: the skills, plugins (each folder a `*` matches),
fixtures and agent `definition`, a sensor's `graders` or `rubric_file`, and any option a kind
declares a `path` or `paths`. A `command` (a
`process` subject's, relative to its `cwd`; an agent session's `setup`) adds each item that
names a file: `[python3, server.py]` digests `server.py`, and `["./proxy", --config,
env/a.yaml]` the program and its config. A program found on PATH is not the experiment's.

- What a skill, plugin or definition holds is what the verdict is about. An edit to one
  after registration is a deviation named by what changed (`skill review changed after
  registration`), and a run of it gives no gate result, as an edited probe file does. A
  file's digest is the sha256 of its bytes; a folder's is over its files' relative paths and
  their digests, so a modification time, a permission or an empty folder is not a change,
  and neither is anything under `.git` or `__pycache__`, or a `.DS_Store`.

- `ix deviations <path>` lists each frozen value that changed, with what was registered and
  what it is now, and exits 14 when it finds any. A genre that changes after registration
  shows up here, because its values were frozen and not just its name.
- A kind's default is frozen with the design (`defaults`, by entry): one that a later ix
  declares otherwise is a deviation, as a genre's value is.
- A comment, key order or a value the file's own schema defaults written out
  (`environment: {kind: none}`) is not a deviation. A reworded `question` or
  `rationale` changes the experiment's digest and not the plan's.
- `ix register` on a changed experiment is refused (exit 7, `conflict`) and the record is
  kept.
- `ix register <path> --amend --reason "<why>"` replaces the record on purpose. The
  superseded record, the reason and the deviations it accepted stay inside the new one.
- A registration written by a newer ix still loads: fields this ix does not know are
  ignored, and a value an older record never stored is not compared. A registration that
  holds no digests of the subject's files is not compared on them; delete it and register
  again to freeze them.

## Running

A run brings the environment up once, binds each service it brought up with how to reach
it (a subject's or a probe's option names `{<service>.url}`, `.host`, `.port`; a run whose
environment comes up without a service the experiment names fails before any trial), then
carries out the trials, starting them in the order the plan fixed (`plan` and `run` compute the same list, and the plan's digest covers
it), one at a time unless the experiment writes `trials_in_flight` (below). For each
trial: start the subject, let the sensors that must watch begin, apply the probe, stop the
watchers, stop the subject. The sensors then read every trial that was carried out.
Progress goes to stderr (a bar on a terminal, one line per trial otherwise, nothing under
`--json`); the report goes to stdout.

**Trials in flight.** `trials_in_flight: 4` carries four trials out at once: each trial
starts in the plan's order as another lands, and each row carries its `seq`, which every
reader orders by. It is 1 when left out. The trials, and the n each estimate rests on, are
the same at any width; what changes is the load around each trial: a provider's rate
limits, and a session slowed by its neighbours that now meets its `timeout`. So the width
is part of the design: the plan states it (`Order:`), its digest covers it, and
registration freezes it, so a gate is never passed at a width nobody registered.
`ix run --trials-in-flight N` tries a width with `--exploratory` or `--dry-run` only, and is
listed under `Changed`; `ix plan --trials-in-flight N` shows that run's plan first, with the
refusal or warnings it would meet, and runs nothing. A width above 1 is refused (`trials_in_flight_refused`, its fix
`trials_in_flight: 1`) where any kind in use or the genre forbids overlap, or a deciding
metric is contended ([Kinds](kinds.md)); in practice, agent sessions and model calls judged on
what they said or did. With an environment, a trial counts as carried out only once a check
that began after it ended finds the environment still up; on the first that does not,
nothing more starts and every trial not yet confirmed is `environment_lost`. The sensors
read at the same width, one at a time for a kind that forbids overlap.

- **Register first.** `ix run` refuses an unregistered experiment (exit 15).
  `--exploratory` runs it anyway; that run gives no verdict, its report shows what it found
  as an *indication*, and it exits 0.
- **The exit code is the verdict**: 0 holds, 10 fails, 11 inconclusive. After the report,
  stderr says the code, what it means and the next step; `exit` in `--json`. A run with no
  verdict exits 0 only when none was asked for (`--exploratory`, `--dry-run`), or when its
  genre gives estimates only. A run that exits 0 names the step it calls for: for a genre
  that gives estimates, that there is no verdict (and for a mutation run, `ix mutants read`);
  for a dry run, holding each estimate to its canned grade; for an indication, registering;
  for a verdict that holds, acting on it within the warrant.
- **The report says what the evidence shows**, beside the verdict and without changing it:
  `Shows: build30's latency p50 is higher than build20's by 29.6% [26.2%, 33.1%]; lower is
  better, so build30 is worse` (`shows`).
- **A pilot.** `--exploratory --repeats N` (or `--dry-run --repeats N`) runs N repeats in
  place of the file's, never below the genre's minimum, and the report lists the change
  (`overridden`); a run that gives a verdict refuses `--repeats` (exit 2). A run too small
  for even every trial passing to show a pass rate's threshold says so (`best_case`).
- **A runtime's warning is said once per run** (a `claude-sdk` subject with a permissive
  `permission_mode`): in the plan's and the report's `warnings`, and once on stderr.
- **A changed experiment runs ungated.** How it differs from its registration is recorded
  with the run and printed in the report; what it found is an *indication*, not a verdict,
  and it exits 14 (`deviations`), never 0, until the registration is amended with a
  reason. A gate cannot pass on a design moved after it was registered.
- **A dry run gives no verdict.** `ix run <path> --dry-run` runs the experiment as written
  with every agent session and model call on matrix's `mock` runtime of its port; a subject
  with no stand-in (a `process`) is refused. The environment has no stand-in: a dry run
  brings it up for real (a `compose` stack's containers, a `process` environment's
  services). A run in which a stand-in (a runtime registered
  as one, as the mocks are) answered any trial measured the harness,
  not the subject: the report says `Dry run on mock replies: checks the harness, not the
  agent`, with neither a verdict nor an indication, nor a probe screen, and the run is not
  `gated`. A registered run in which a stand-in answered (`runtime: mock` written in) exits
  16 (`simulated`), never 0: a gate on the exit code cannot pass on it.
- **Stopping.** Ctrl-C (or SIGTERM) stops the trial in flight, kills the process groups the
  run started, and exits 12 with the partial report. A run the harness could not finish (an
  environment that did not come up, or went down between trials) exits 8. `ix report` of a
  run still being written exits 13. `ix stop <run>` stops a run from elsewhere
  ([Long runs](#long-runs)).
- **A run's id** is `<experiment>-<UTC time>-<4 hex digits>`, or what `--run-id ID` names
  (letters, digits, `.`, `_`, `-`); an id a run is kept under already is refused (exit 2),
  since a run's record is never reopened.
- **Refused before it starts**: whatever an entry needs of this host that does not hold
  here. Each adapter checks its own (a `preflight`): a program that is not there (a
  subject's or a service's command, `k6` for `http-load`), an `auth` variable that is not
  set, a file an agent subject names that is not there, an agent runtime that cannot start
  or cannot play the agent as written, a model runtime (a subject's or a judge's) that
  cannot start or cannot resolve the model. `ix plan` lists every requirement under
  `Requires` with whether it holds, and those that do not under `Runnable: no` with the
  fix; they depend on the host, so they do not stop `ix register`. Under `Model calls` it
  counts the work a run asks of model providers, from what each adapter says one trial
  starts (`ModelCalls` in `ix.extension`), and names each entry that may call a model and
  does not say: the count is a bound only over what it counts, and never a price.
- **Failures have an owner.** A trial the subject failed (it exited, it was not up in time,
  it answered no request successfully within the measured time, it returned a different body; an agent session that
  broke, was refused or ran past its probe's `timeout`) counts against the subject. A trial the harness failed (k6 missing
  or broken, no request sent, a timeout, a stop request, an environment that went down, a
  model runtime that was unavailable, rate-limited or refused its credentials) is missing
  data. Every request sent in the measured time is waited for, past it if need be, until
  it is answered or the probe's `timeout` (60s, k6's own, when left out) runs out. Latency and time
  to first byte count the requests the subject answered successfully, and throughput only
  those answered within the measured time. `error_rate` counts every request that failed,
  timed out or was never answered, of those sent. Under an open model, k6 starts every
  client before the load, so an arrival it drops found every client waiting on the
  subject: the subject was too slow for the rate, so each drop counts in `error_rate` as
  an error, and the trial is measured with its `dropped` count. A sensor that
  could not read is a reading with a failure. None of these stops a run, but a lost
  environment; the report counts each.

## Long runs

A run can outlast the call that started it: an agent's tool call has a time limit, and a
long experiment does not. `ix run <path> --detach` starts the run in a process of its own and
returns as soon as that process has opened the run's record, printing the run's status
(`ix.v1.status`): its id first, its directory, the process writing it (`pid`, `host`), the
trials planned and carried out, its event log. It exits as `ix status` does, 13 while the
run runs. It is refused before anything starts exactly as a run in the foreground is, with
the same exit codes (an unregistered experiment exits 15), and a path of `-` is refused
(exit 2): the process reads nothing on standard input. The process is an ordinary
`ix run` of the same experiment under the same id, in a session of its own, so the end of
the caller's terminal or tool call does not reach it; it records, stops and reports as a
run in the foreground does. What it writes to standard error is kept beside the run's
directory, `<run id>.log` (`log` in the status). One that has not opened its record within
two minutes is stopped, and the start fails (exit 3).

Then, for that run or any other (a run's directory, or an experiment for its latest run):

- `ix status <run>` says where it stands, read from the record alone: `running`, or how
  it ended (`completed`, `interrupted`, `failed`), and how many of its trials were carried
  out. It exits 13 while the run runs, 0 once it completed, 12 for a run that was stopped
  or whose process died, 8 for one the harness could not finish. A completed run's verdict
  is `ix report`'s, not the status's: exit 0 from `status` says the run completed, nothing
  about what it found.
- `ix events <run> --follow` prints the run's events as they are written (`--json`: one
  `matrix.v1.event` a line) and returns once the run has ended, however it ended: a run
  whose process was killed reads as interrupted, and the follower returns. It then exits as
  `ix status` would. `--follow` on a run still written on another host is refused (exit 2):
  whether its process there is alive cannot be read from here; read its events without
  `--follow`, or poll `ix status`. Events are telemetry and may be dropped: read results
  from the report. Ctrl-C stops following and leaves the run running.
- `ix stop <run>` stops a running run as Ctrl-C stops one in the foreground (it sends the
  process writing it SIGTERM): the trial in flight is kept as the harness's, everything the
  run started is released, and the record says `interrupted`. It waits up to 30 seconds for
  the run to end and prints its status; a run still releasing what it started then still
  says `running` (exit 13): ask `ix status` again. A run that has ended is left as it is; a
  run written on another host is refused (exit 2).
- `ix report <run>` gives its verdict once it has ended.

```bash
ix run lab/proxy --detach --json      # exits 13: running; prints its directory
ix status <run directory> --json      # 13 while it runs; 0, 12 or 8 once it has ended
ix events <run directory> --follow    # returns when it ends
ix report <run directory>             # the verdict
```

## The record

A run is one matrix run, and matrix's record is the only store. The composition root
builds it at matrix's `store` point: `jsonl`, a directory of tables per run (a program
that builds its own `Root` may name another store whose config takes `root`):

```
<experiment directory>/.ix/runs/<experiment>-<UTC time>-<4 hex>/
  run.json                  status, times, how each component ended
  ix.v1.run.jsonl           the manifest: experiment, design, registration, deviations,
                            the subject's digests, the commit, the versions
  ix.v1.trial.jsonl         the schedule: one row per planned trial
  ix.v1.bindings.jsonl      what the environment provided
  ix.v1.interaction.jsonl   what happened in each trial, with any failure and its owner
  ix.v1.reading.jsonl       one row per metric per trial
  matrix.v1.session.jsonl   one row per agent session or model call, failed ones included:
                            the runtime, the model that answered, usage, ids, attempts
  matrix.v1.ledger.jsonl    which component recorded each row, and when
  events.jsonl              the run's events
  writer.lock               locked by the process writing the run while it lives
```

The manifest records what the verdict is about and where the run ran from: the digest of
each file and folder the subjects and sensors name (`contents`, paths and digests, never what
the files hold); the git commit the experiment sits at and whether a tracked file differed
from it (`dirty`), or `not a repository`; the installed versions of ix, matrix and of each
distribution an installed runtime drives;
and every agent and model runtime installed, by type URL, with the distribution that
registered it and its version, and each distribution it drives with that one's version (the
Claude Agent SDK, for `claude-sdk`); and where each setting it read came from
(`provenance.settings`: `ix.runs`, `matrix.observers` and `matrix.model_runtimes`, each with the file
that set it or `default`, never the value). `ix report` prints the digests (`Contents`), the commit
(`Ran from`) and the versions, `ix runs` the commit, and `--json` carries all of it.

An agent trial's interaction keeps the prompt, the reply, every tool call, each round's
transcript, the files collected, how the session stopped, and its turns, tokens, cost and
duration. The run's events (`ix.trial.start`, `ix.trial.end`, ...) also reach the observers
matrix's config names for ix: `observers:` under `matrix:` in any tier
`matrix config --tool ix --sources` lists.

**From a verdict to the model that answered.** Each interaction names the rows of
`matrix.v1.session` behind its trial (`sessions`): every agent session (each round of a loop)
or model call the subject made, failed ones included. Each reading names the rows its sensor
made (`sessions`: a judge's calls, a judge evaluator's included), and joins its interaction on
`seq`. A session row holds
the runtime, the model that answered and its family, the usage, the ids (the runtime's, and
the OpenTelemetry trace and span the session ran in), the attempts, and whether it failed and
why. A row's position in its file, from 0, is the number the others name. In DuckDB, with
`RUN` the run's directory, each judged reading beside the model that answered its trial:

<!-- reading-to-session -->
```sql
WITH sessions AS (
    SELECT ordinality - 1 AS session, runtime, model, family, status
    FROM read_json_auto('RUN/matrix.v1.session.jsonl') WITH ORDINALITY
),
answered AS (
    SELECT seq, unnest(sessions) AS session
    FROM read_json_auto('RUN/ix.v1.interaction.jsonl')
)
SELECT reading.seq, reading.metric, reading.value, runtime, model, family, status
FROM read_json_auto('RUN/ix.v1.reading.jsonl') AS reading
JOIN answered USING (seq)
JOIN sessions USING (session)
WHERE reading.metric = 'judged_score'
ORDER BY reading.seq;
```

Each file is one JSON document per line, readable with `jq` or DuckDB. An interaction is on
disk when its trial finishes, so a run that is killed outright keeps every trial it
finished. `ix report <run directory>` reads a run in any state. When a run ended before its
sensors read the interactions (it was stopped or killed), the report reads them from the
stored interactions and says `readings: derived`; nothing is written back. A report never
calls a model or starts a process: when a sensor the run used may (a `judge`, an
`evaluator`, or `function-test` and `outcome`, which run code), it reads none and says so
(`unread`), and the readings come from running the experiment again. Keep `.ix/` out
of version control.

The work is three components that matrix wires by the kinds they exchange: `environment`
provides `ix.v1.bindings`; `trials` requires those and the schedule and provides
`ix.v1.interaction`; `sense` provides `ix.v1.reading`. Analysis and the report are pure
functions outside the run.

## Not built

Trials of a `process` or `http` subject, of `http-load`, or under `timing` or `memory`
cannot run in flight: those kinds forbid overlap, and no measurement says yet what
several at once would do to them. A subject left running by a run that was
killed outright is not found and stopped later: in the next run, each trial of a subject
whose address already answers is recorded as a harness failure (`address_in_use`) instead
of being measured. The sensors cannot be run again over a stored run and written back. A
stopped or killed run cannot be resumed; it is run again. A run written on another host
(a shared run directory) can be read, not followed or stopped from here, and one whose
process died there reads as running until a reader on that host finishes its record.
