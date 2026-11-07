# ix

ix decides a question about an AI agent or a service by experiment: one YAML file that
names its genre, its subjects, its probes and the metric that decides. ix validates the
file, plans it, registers it, runs it and reports a verdict: `holds`, `fails` or
`inconclusive`.

Start with the [README](../README.md): install, a first run that calls no model, reading a
result, your own experiment.

---

## Explanation

| Document | What it covers |
|----------|----------------|
| [What ix is](explanation/overview.md) | The problem, the mental model, a worked run, and what ix gives an agent |
| [The verdict, and the method behind it](explanation/the-verdict.md) | How readings become a verdict: the intervals, several decisions at once, the A/A check, the simulations that check the method, sensitivity, and the known limits |

## How-to

| Document | What it covers |
|----------|----------------|
| [Evaluating an agent](how-to/evaluate-an-agent.md) | An agent evaluation from the question to the verdict: the subject, probes as files, the metric that decides, a dry run, a pilot (and trying several trials at once), the report; then routing, a skill of your own, tool use, a check of the reply's text, your own graders and a judge |
| [Comparing two builds of a service](how-to/compare-two-services.md) | Two builds of an HTTP service on latency under k6 load, and gating a release with `regress` |
| [Testing a test suite](how-to/test-a-test-suite.md) | Mutation testing: drawing mutants, running them, reading the survivors, and confirming a pruned suite with `mutation-regress` |
| [The trials-in-flight slice](../examples/trials-in-flight/README.md) | The protocol, run by hand, that tests whether a real provider measures the same with four trials in flight as with one |

## Reference

| Document | What it covers |
|----------|----------------|
| [Terms and commands](reference/commands.md) | The words a report uses, the sequence from `validate` to `report`, and every command |
| [Genres](reference/genres.md) | Each genre, what it decides and what it needs; `check`; how a genre extends another |
| [Kinds](reference/kinds.md) | Subjects, probes, sensors and environments: declarations, what each adapter does, what `validate` holds an experiment to, writing an adapter, packaging genres and kinds |
| [Registration, runs and the record](reference/runs.md) | What registration freezes, how a run proceeds, trials in flight, long runs, the run's tables, and what is not built |
| [Settings and telemetry](reference/settings.md) | The `ix:` settings and the OpenTelemetry spans |
| [The library](reference/library.md) | `import ix`: the declared API, and what a caller may rely on |
| [Output and exit codes](reference/output.md) | The `--json` documents, the error envelope, and every exit code |
| `ix --skill` | The skill an agent reads: what ix is for, a quick start, the commands, the exit codes and what to do next. `ix --skill -r NAME` prints one of the references it lists. Generated from what is installed |
| `ix genres --json`, `ix kinds --json` | The installed genres and kinds as records |
| [CONTRACT](../CONTRACT.md) | What each published surface hides, its members, defaults, failure behaviour and stability |
| [SECURITY](../SECURITY.md) | What ix reads, writes, starts, runs and sends |
| [CHANGELOG](../CHANGELOG.md) | What each release changed |

## Design

| Document | What it covers |
|----------|----------------|
| [Design record, 2026-10-19](design/2026-10-19-record.md) | Every design judgment ix rests on, one row each with its test and evidence: the verdict's statistics with their sources and simulations, the agent path, the runtime ports, what a verdict is tied to, requirements in place of effects, environments, the layer map, the evaluator sensor, trials in flight, and the owner's rulings they follow |
