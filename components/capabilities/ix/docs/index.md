# ix

ix decides a question about an AI agent or a service by experiment: one YAML file that
names its genre, its subjects, its probes and the metric that decides. ix validates the
file, plans it, registers it, runs it and reports a verdict: `holds`, `fails` or
`inconclusive`.

Start with the README: the experiment model, the sequence from `validate` to `report`, a
service comparison and an agent evaluation.

---

## How-to

| Document | What it covers |
|----------|----------------|
| [Evaluating an agent](how-to/evaluate-an-agent.md) | An agent evaluation from the question to the verdict: the subject, probes as files, the metric that decides, a dry run, a pilot, the report; then routing, a skill of your own, tool use, a check of the reply's text, your own graders and a judge |

## Reference

| Document | What it covers |
|----------|----------------|
| [README](../README.md) | The experiment model, genres, kinds, registration, running, the record, the verdict and its method, settings and telemetry, the library, output and exit codes |
| `ix --skill` | The skill an agent reads: what ix is for, a quick start, the commands, the exit codes and what to do next. `ix --skill -r NAME` prints one of the references it lists. Generated from what is installed |
| `ix genres --json`, `ix kinds --json` | The installed genres and kinds as records |
| [SECURITY](../SECURITY.md) | What ix reads, writes, starts, runs and sends |
| [CHANGELOG](../CHANGELOG.md) | What each release changed |

## Design

| Document | What it covers |
|----------|----------------|
| [Design record, 2026-10-19](design/2026-10-19-record.md) | Every design judgment ix rests on, one row each with its test and evidence: the verdict's statistics with their sources and simulations, the agent path, the runtime ports, what a verdict is tied to, requirements in place of effects, environments, the layer map, the evaluator sensor, and the owner's rulings they follow |
