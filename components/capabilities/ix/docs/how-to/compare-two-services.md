# Comparing two builds of a service

This page compares two builds of an HTTP service on latency, and then gates a release with
`regress`. It needs `python3` and [k6](https://k6.io) on `PATH`, and calls no model.

## The experiment

`examples/delay-server` holds a small HTTP server that answers every request after a fixed
delay, and two experiments over it. `slower.yaml` asks whether a copy that waits 60 ms is
faster than one that waits 40:

```yaml
name: delay-slower
question: Is a server that waits 50% longer per request faster than the one it would replace?
genre: compare
subjects:
  baseline:
    kind: process
    command: [python3, server.py, --port, "18473", --delay-ms, "40"]
    url: "http://127.0.0.1:18473"
    ready: "http://127.0.0.1:18473/healthz"
  candidate:
    kind: process
    command: [python3, server.py, --port, "18473", --delay-ms, "60"]
    url: "http://127.0.0.1:18473"
    ready: "http://127.0.0.1:18473/healthz"
probes:
  - id: get
    kind: http-load
    target: "{url}/work"
    load_model: closed
    concurrency: 4
    duration: 3s
    warmup: 1s
primary: {metric: latency, statistic: p50}
comparison: superiority
```

The first subject is the control. Repeats, the order and the A/A check come from the genre,
and the sensor from the metric: `timing` provides `latency`.

## Plan it

`ix plan` shows each value and where it came from, the decision first:

```
delay-slower  compare (ix.v1.genre.compare)  plan 7b6ed29d74c6
Question:     Is a server that waits 50% longer per request faster than the one it would replace?
Decides:      candidate is better than baseline on latency p50
Comparison:   superiority
Verdict:      holds / fails / inconclusive
...
Trials:       2 subjects x 1 cell x 1 probe x 10 repeats = 20, + 10 A/A = 30
Duration:     about 2m (30 trials, probe time only; setup and teardown add to it)
Requires:     yes  'python3' on PATH  (subjects.baseline, subjects.candidate)
              yes  'k6' on PATH  (probes.0)
Registration: none
```

Where k6 or a program is missing, its `Requires` line reads `NO` and the plan ends with
`Runnable: no` and the fix.

## Register and run it

```bash
ix register examples/delay-server/slower.yaml
ix run examples/delay-server/slower.yaml       # exits 10: the verdict is fails
```

Each trial starts the subject's command, waits for `ready` to answer, puts load on it from
k6 for a second of warm-up and three measured seconds, and stops it. The baseline also runs
against itself in every block, as a check that the machine held still. The run takes a few
minutes:

```
Verdict:      fails
Shows:        candidate's latency p50 is higher than baseline's by 38.4% [35.7%, 41%]; lower is better, so candidate is worse
...
Findings
  candidate: fails
    primary: latency p50 (mean) better than baseline: fails
      difference +38.36% [+35.72%, +41.04%] (95%, unadjusted), lower is better, fieller, n 10, decided at 4.99e-11 (allowed 0.05)
...
Exit 10 (fails): the verdict is fails
Next: the claim does not hold: do not act on it; read the findings
```

The interval of the difference is nowhere near 0, so the verdict is `fails`.
`identical.yaml` compares two copies of one server: under `superiority` that is
`inconclusive`, since nothing was shown either way.

## Gate a release with `regress`

To gate a release, use `regress` with a `margin`, the largest regression you would still
ship: the candidate holds when it is at most the margin worse than the baseline. The margin
is the person's decision; the plan prints it on a line of its own and says it was set in
the experiment.

`regress` answers "may the candidate ship?", not "is it slower?". For that, use `compare`
with `superiority`, where `fails` says the candidate is worse. Two builds that do not differ
are inconclusive under `superiority`, and hold under `regress` once there are enough trials
that the interval stays above `-margin` ([Genres](../reference/genres.md)).

## A larger comparison

`examples/proxy-streaming` compares two proxies across a grid of sizes and rates, with
guardrails on throughput and memory. Its programs are not in this repository, so it
validates and plans, and `ix plan` ends with `Runnable: no`, naming each missing program.

A health check against a service, a predicate that must hold on every trial, is the `check`
genre with the `command` probe ([Genres](../reference/genres.md)).
