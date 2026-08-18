# lab — experiments against the catalog

A **lab** is a directory of experiments; each experiment is a directory with an
`experiment.yaml` and a `tasks/` folder of probes. This lab is where gnx measures its own
claims instead of asserting them.

```
ix run catalog-routing  --lab lab --mock --seed 42
ix run sensor-integrity --lab lab --mock
ix results catalog-routing --lab lab
```

Run from the repo root, with the capabilities workspace:

```
uv --project components/capabilities run ix run catalog-routing --lab lab --mock --seed 42
```

## The experiments

| Experiment | Question | Sensor | Needs credentials |
|---|---|---|---|
| `catalog-routing` | Does the right skill trigger, and do decoys stay quiet? | `activation` | for a live run |
| `sensor-integrity` | Does the grading path itself grade correctly? | `function-test` | no |

### catalog-routing

The catalog's whole promise is that a description makes the right component fire at the
right moment. That is a measurable claim, and this is the measurement: `must_trigger`
probes name a real ask, `should_not_trigger` probes are decoys pitched near a skill's
vocabulary without needing it.

`--mock` exercises the full harness — DAG, store, aggregation, confusion matrix — on a
simulated 90/10 activation split. **It does not measure the catalog.** A mock run that
reports 90% tells you the plumbing works; only a live run tells you the descriptions do.
Both are useful and they are not the same claim.

### sensor-integrity

Runs offline, and deliberately so. Each probe carries a `mock_response` holding the code a
subject "returned", so the experiment measures **the sensor** rather than a model:
extraction from markdown fences, execution under timeout, and scoring against test cases.

That path is also ix's sharpest trust boundary — it imports and runs model-generated
Python in-process (see `components/capabilities/ix/SECURITY.md`, boundary 1). An eval that
never exercises it leaves the riskiest code unproven, so the probe set includes code that
fails its tests and code that raises, not only code that passes.

## Adding an experiment

```
lab/<name>/
  experiment.yaml     # subjects, sensors, trials
  tasks/*.md          # one probe per file: frontmatter + prompt body
```

Probe frontmatter is untyped YAML read into `Probe.metadata`; each sensor reads the keys
it needs (`expectation`, `expected_skill`, `function_name`, `test_cases`, `mock_response`).
