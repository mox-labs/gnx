# What is ix?

## The question it answers

You changed something about an agent: its system prompt, a skill description, the model, the
tools it may call. Did the change help? Asking the agent once and reading the answer does not
tell you. The same prompt gives different answers on different runs, an answer can be right
on one dimension and wrong on another, and a difference you see once may be the run's
wobble rather than the change.

ix turns the question into an experiment. You write the prompts (probes) and the variants
(subjects) as files. ix puts every probe to every subject a number of times, has a sensor
judge each response, and reports how often each subject passed, how sure that number is, and
whether one subject beat another by more than the uncertainty.

## The four values

Everything ix measures is built from four frozen values in `ix.domain.types`. None of them
knows about evals in particular; the same shapes describe a benchmark or a load test.

- **Probe**: an `id`, a `prompt`, and `metadata`. The metadata is the probe's ground truth
  (`expectation: must_trigger`, `test_cases`, `expected_facts`), and only sensors read it.
- **Subject**: a `name`, a `description` and a `config`. The config names a configured agent
  or defines one inline. A subject is data; ix's composition root turns it into a runnable
  matrix agent.
- **Trial**: one probe put to one subject once. It holds the `response`, or the `error`
  together with an `error_reason` when the session failed.
- **Reading**: what a sensor concluded about one trial: `passed`, an optional `score`,
  `metrics`, `details`, and `fault` when the sensor never judged the response.

## A run, step by step

`ix run` repeats this for each subject:

1. For each of `repeats` repeats, the **engine** runs every probe × trial and returns exactly
   one Trial for each. It does not judge anything.
2. The **experiment** measures every trial with its one rule (`ix.eval.measure.measure_trial`
   over the configured sensors). The rule is memoised per repeat: the Inspect engine calls it
   while it runs so Inspect's log carries the scores, and the experiment's own pass reuses
   those readings instead of judging twice.
3. Every trial, with its response and readings, is appended to the run's `trials.jsonl`.
4. Readings are aggregated per probe. A probe passes when a majority of its trials passed,
   by the sensor's own verdict; ix never re-derives a verdict from a score.
5. The summary is computed (pass rate, mean score, standard errors, noise floors, faults,
   provenance) and saved as the subject's `summary-latest.json`.

Because the engine only executes, the same experiment is judged the same way on either
engine. The native engine runs each trial as one run of a matrix flow, compiled once per
repeat, so matrix's observers (tracing, for one) see every trial. The Inspect engine runs each
repeat as an Inspect AI task and leaves an `.eval` log you can open with `inspect view`.

## Whose failure is it?

An agent session can fail for reasons that have nothing to do with the agent. Counting a rate
limit against a subject makes it look worse than it is; dropping the trial silently makes the
denominator smaller without anyone noticing. ix does neither. A trial that did not produce a
judgment becomes a failed reading with a `fault`:

| fault | when | in the score? |
|---|---|---|
| `subject` | the runtime classified the failure as the agent's own: `failed` (the session ran and broke) or `refused` (the model declined) | yes, as a failure |
| `harness` | the session never had a fair chance: `rate_limited`, `unavailable`, `timeout` (including a session cut off by the native engine's `trial_timeout_s`), `auth`, `incapable` (the runtime cannot run the definition), `engine` (the engine lost the trial), `setup` (the subject's agent could not be built), `unclassified` (the runtime raised something other than matrix's classified error, an adapter bug) | no |
| `sensor` | the sensor raised while judging | yes, as a failure |

Harness faults are counted in `harness_faults`. A probe whose every trial was a harness fault
is listed in `unmeasured_probes` and left out of `n_probes` and every rate. Sensor faults are
counted in `sensor_faults`. Either kind makes `ix compare` return `inconclusive`: a grader
that crashes on one subject's answers, or a provider that throttled one run, moves the
difference without the subjects differing. The fix is to rerun, or to fix the sensor, not to
read the delta.

A reason a runtime invents beyond these counts as the subject's. A trial whose agent could
not be built is recorded like any other failed trial, so the trials already run are kept.
`trial_timeout_s` is a native-engine option.

ix also counts how each completed session ended, in `stops` (`completed`, `max_turns`, ...).
A session stopped by a limit is still judged as it stood; the results table shows
"Sessions cut short" when any session ended other than `completed`.

## Two kinds of uncertainty

ix reports two numbers and keeps them apart.

- **Standard error over probes** (`pass_rate_stderr`, `mean_score_stderr`). Probes are a
  sample of the prompts the agent will meet. This says how much the pass rate would move with
  a different draw of probes like these. It is shown as `±` in the results table.
- **Noise floor across repeats** (`noise_floor_sd`, `score_noise_floor_sd`). With
  `repeats: 2` or more, this is the standard deviation of the pass rate and mean score from
  repeat to repeat: how much the number moves when nothing changes but the run.

`ix compare <experiment> A B` pairs the two subjects probe by probe and computes the mean of
(score B − score A), its standard error over the shared probes, and a 95% interval. The
verdict is `b_better` or `a_better` only when that interval excludes zero **and** the mean
difference is larger than the larger of the two subjects' noise floors. Otherwise, and
always when either side has harness or sensor faults or no real model answered on either
side, it is `inconclusive`. With one repeat
there is no noise floor, and the comparison says so.

## Measured, or only exercised?

`--simulate` puts every subject on ix's simulator. It returns a probe's canned
`mock_response` when there is one, and otherwise activates the expected skill 90% of the time
for must-trigger probes and 10% for should-not-trigger ones. matrix's `mock` runtime returns
fixed replies from config. Neither calls a model. Both prove that the experiment loads,
composes, runs, measures and saves; neither says anything about an agent.

ix records which model families actually answered, read off the responses rather than the
config. When only `simulated` or `mock` answered, `measured_a_model` is false, the status is
`unmeasured` (the table prints "harness only"), and `ix compare` attaches a warning and
returns `inconclusive` however large the difference looks (`both_measured` is false). A simulated run
of a subject whose own runtime is something else is saved as `<subject>@simulated`, so it
never replaces that subject's real results.

## Configuration is shared

ix does not keep its own list of models and agents. It reads matrix's config tiers, so a
runtime or agent you configure once in `~/.matrix/config.yaml` or `./matrix.yaml` is available
to ix and to every other tool built on matrix. ix adds an `ix:` section for its own defaults
(`trials`, `repeats`, `engine`, `lab`). An experiment's `experiment.yaml` overrides those
defaults, and command-line flags override the experiment.

A subject either names a configured agent (`agent: reviewer`), which brings its definition
and its runtime, or defines an agent inline with a `runtime: {type: ...}`. Any definition
field the subject sets (`system_prompt`, `model`, `tools`, `max_turns`) overrides the named
agent's. An inline subject gets one turn unless it says otherwise, because most experiments
measure a single reply.

## Extension points

ix declares two extension points on matrix's registry, `sensor` and `engine`, and registers
its own sensors and engines there through the same `matrix.extensions` entry point a third
party uses. ix has no private list of built-ins. A config names a built-in by its short name
(`type: activation`, which means `ix.v1.sensor.activation`) and anyone else's by its full
type URL (`type: acme.v1.sensor.brevity`).

An engine entry may declare `observers` (matrix's configured observers) and `results_dir`
(where the experiment's results go; Inspect writes its logs under it). A sensor entry
declares what it needs from composition: `probes` (the experiment's probes, to
read ground truth from) and, for a sensor that asks a model to grade, `judge`. `judge(name)`
returns a matrix agent: the configured agent of that name, or else that model on matrix's
`model` runtime. A sensor's build function may also declare `truth_keys`, the probe keys it
reads; that is how ix refuses a probe key no configured sensor reads. A sensor that does not
declare them may read anything, and the check is skipped for that experiment.

## What ix is not

- **Not a test framework.** An assertion on a single run of a stochastic system is noise.
  ix runs trials and aggregates.
- **Not a benchmark suite.** It ships no canonical tasks and ranks nothing. You bring the
  probes, the subjects and, if the built-ins do not fit, the sensor.
- **Not a sandbox.** The `function-test` sensor runs model-generated Python in the ix
  process. See SECURITY.md.
- **Not a pass/fail gate by itself.** `ix run` exits 0 when the run worked, whatever the pass
  rate. A pipeline that gates on quality reads the JSON results.
