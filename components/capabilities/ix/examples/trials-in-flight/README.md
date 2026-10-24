# The live validation slice for trials in flight

CI holds ix to what it can show on stand-ins: at width 4 every trial reads what it reads at
width 1, each names only its own sessions, and a reading that moves with load is caught
(`tests/test_trials_in_flight.py`). Whether a real provider measures the same at width 4 is
a question only real sessions answer. This is the protocol for answering it. It calls a
model on every trial, so it is run by hand, never in CI.

Two experiments:

- `experiment.yaml`: the slice itself. A claude-sdk agent answers four short prompts, ten
  times each: 40 sessions a run. It reads a pass rate (`text_matched`) and the tokens each
  session used (`tokens_total`, `tokens_output`).
- `k6-control.yaml`: the negative control. A server's latency under k6 load, which other
  load against the same server is known to move. It shows the test below can fail.

## What it can settle

What passes here is what was tested: single-turn agent sessions with no tools, on
claude-sdk, against one provider. A pass lets `regress` allow overlap for experiments of
that shape only. A multi-turn session, one that calls tools, another runtime or another
provider is outside what this slice measured, and needs a slice of its own before a gate
of it may run in flight.

## What decides

`regress` forbids overlap until this slice passes (ix design record §27). It passes when,
for the agent experiment, P is equivalent to S on every metric by a two one-sided test
(TOST) at 2.5% each side, the level ix's own analysis uses, and the control is **not**
equivalent:

| metric | margin | equivalent when the 95% interval of P against S lies within |
|---|---|---|
| `text_matched` (a rate) | 5 percentage points | -5pp to +5pp of the difference P - S |
| `tokens_total`, `tokens_output` | 5% relative | 0.95 to 1.05 of the ratio P / S |
| control `latency` p50 | 5% relative | 0.95 to 1.05; expected outside it |

If the control is called equivalent, the test cannot tell the widths apart and the slice
says nothing either way: lengthen the runs, do not loosen the margins.

**Its power is not settled.** The unit is the run (below), so each arm has four values. A
simulation of this analysis at four runs of 40 a side (record §27) accepts equivalence on a
pass rate of 0.9 about 2% of the time when the widths do not differ at all: as written, the
slice can show a difference but cannot show their absence on the rate. The number of runs
and trials it needs is the owner's to decide before it is run.

## The runs

Eight exploratory runs of each experiment, in the order **S P P S P S S P**: S one trial at
a time, P four at once. The order balances drift over the session (a provider's load, a
cache warming) between the two arms. Keep each run's directory: the last line of `ix run`
names it.

```bash
ix validate examples/trials-in-flight
# S, P, P, S, P, S, S, P
ix run examples/trials-in-flight --exploratory                         # S
ix run examples/trials-in-flight --exploratory --trials-in-flight 4    # P
ix run examples/trials-in-flight --exploratory --trials-in-flight 4    # P
ix run examples/trials-in-flight --exploratory                         # S
ix run examples/trials-in-flight --exploratory --trials-in-flight 4    # P
ix run examples/trials-in-flight --exploratory                         # S
ix run examples/trials-in-flight --exploratory                         # S
ix run examples/trials-in-flight --exploratory --trials-in-flight 4    # P
```

Each P report lists `trials_in_flight: 4 in place of 1 (--trials-in-flight)` under
`Changed` and `4 trials in flight` on its `Run` line; check both before reading on.

The control's kinds forbid overlap, so ix refuses it a width above one. Its P runs are four
`ix run`s of the same file started together against the one server, which serves four
requests at once (`--capacity 4`) and queues the rest, as a service with four workers does:
each S run has the server alone, and each P run shares its workers four ways.

```bash
python3 examples/delay-server/server.py --port 18474 --delay-ms 40 --capacity 4 &
ix run examples/trials-in-flight/k6-control.yaml --exploratory              # S
for i in 1 2 3 4; do
  ix run examples/trials-in-flight/k6-control.yaml --exploratory &           # P
done; wait
# ... and so on, in the order S P P S P S S P
```

Record how much the four runs of each P overlapped, from their trials' `started_ns` and
`duration_ns`: a P whose runs barely overlapped did not load the server, and is no P.

```sql
-- The four run directories of one P.
WITH edges AS (
  SELECT started_ns AS t_ns, 1 AS step
  FROM read_json_auto(['<run 1>/ix.v1.interaction.jsonl', '...'])
  UNION ALL
  SELECT started_ns + duration_ns, -1
  FROM read_json_auto(['<run 1>/ix.v1.interaction.jsonl', '...'])
), held AS (
  SELECT sum(step) OVER (ORDER BY t_ns, step ROWS UNBOUNDED PRECEDING) AS in_flight,
         lead(t_ns) OVER (ORDER BY t_ns, step) - t_ns AS gap
  FROM edges
)
-- The most trials of the four runs at once, and the share of the busy time with two or more.
SELECT max(in_flight) AS most_at_once,
       sum(CASE WHEN in_flight > 1 THEN gap ELSE 0 END)
         / sum(CASE WHEN in_flight > 0 THEN gap ELSE 0 END) AS share_overlapped
FROM held WHERE gap IS NOT NULL;
```

## Reading them

The unit is the run, not the trial: trials of one run share its moment (the provider's
load then, a cache), so pooling them understates the spread between runs. Take each run's
mean of each metric (for `text_matched`, the share of its trials that passed, a failed
session counting as a failure); then each arm has four values, one a run.

```sql
-- One row per run and metric: the arm, and the run's mean.
SELECT '<S or P>' AS arm, filename AS run, metric,
       avg(coalesce(CAST(value AS DOUBLE), statistics.p50)) AS mean
FROM read_json_auto(['<run 1>/ix.v1.reading.jsonl', '...'], filename = true)
WHERE failure IS NULL
GROUP BY run, metric;
```

A trial whose session failed (a rate limit, a timeout) has no reading of `text_matched`,
and a failed interaction none at all: count them from each run's report (`Run:`), and
count each as a failed trial in that run's pass rate, since a width that fails sessions is
a width that measures something else.

Then, for each metric, from the four run means a side, with the spread between the runs of
each arm (Welch's t, its degrees of freedom from the two variances):

- **A rate.** The difference of the arms' mean pass rates, P - S, and its 95% interval;
  equivalent when the interval lies within ±5pp. No Wald interval on pooled trials.
- **A mean.** The ratio P / S, as the difference of the arms' mean log run means and its
  95% interval, exponentiated; equivalent when it lies within 0.95 to 1.05.

Record the result, the overlap of each control P, and the run directories it rests on, in
the ix design record §27, beside ixian's dissent on `regress`, which this slice settles.
