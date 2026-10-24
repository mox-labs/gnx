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

## What decides

`regress` keeps its trials apart until this slice passes (ix design record §27). It passes
when, for the agent experiment, P is equivalent to S on every metric by a two one-sided
test (TOST) at α = 0.05 each side, and the control is **not** equivalent:

| metric | margin | equivalent when the 90% interval of P against S lies within |
|---|---|---|
| `text_matched` (a rate) | 5 percentage points | -5pp to +5pp of the difference P - S |
| `tokens_total`, `tokens_output` | 5% relative | 0.95 to 1.05 of the ratio P / S of the means |
| control `latency` p50 | 5% relative | 0.95 to 1.05; expected outside it |

If the control is called equivalent, the test cannot tell the widths apart and the slice
says nothing either way: lengthen the runs, do not loosen the margins.

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
`ix run`s of the same file started together against the one server, so each S run has the
server alone and each P run shares it four ways:

```bash
python3 examples/delay-server/server.py --port 18474 --delay-ms 40 &
ix run examples/trials-in-flight/k6-control.yaml --exploratory              # S
for i in 1 2 3 4; do
  ix run examples/trials-in-flight/k6-control.yaml --exploratory &           # P
done; wait
# ... and so on, in the order S P P S P S S P
```

## Reading them

Every run keeps its readings in `ix.v1.reading.jsonl` under its directory. With DuckDB,
name the S runs and the P runs and take each arm's trials:

```sql
-- S and P are lists of run directories, as `ix run` printed them.
WITH readings AS (
  SELECT 'S' AS arm, * FROM read_json_auto(['<S run 1>/ix.v1.reading.jsonl', '...'])
  UNION ALL
  SELECT 'P' AS arm, * FROM read_json_auto(['<P run 1>/ix.v1.reading.jsonl', '...'])
)
SELECT arm, metric,
       count(*) AS n,
       avg(coalesce(CAST(value AS DOUBLE), statistics.p50)) AS mean,
       stddev_samp(coalesce(CAST(value AS DOUBLE), statistics.p50)) AS sd
FROM readings
WHERE failure IS NULL
GROUP BY arm, metric
ORDER BY metric, arm;
```

A trial whose session failed (a rate limit, a timeout) has no reading of `text_matched`,
and a failed interaction none at all: count them from each run's report (`Run:`), and
count each as a failed trial in the pass rate, since a width that fails sessions is a
width that measures something else.

Then, for each metric:

- **A rate.** The difference P - S of the two pass rates, with its 90% interval
  (Newcombe's, or Wald's at these counts); equivalent when the interval lies within ±5pp.
- **A mean.** The ratio P / S of the two means, with its 90% interval (Fieller's, or the
  delta method); equivalent when it lies within 0.95 to 1.05.

Record the result, and the run directories it rests on, in the ix design record §27,
beside ixian's dissent on `regress`, which this slice settles.
