# The live validation slice for trials in flight

CI holds ix to what it can show on stand-ins: at width 4 every trial reads what it reads at
width 1, each names only its own sessions, and a reading that moves with load is caught
(`tests/test_trials_in_flight.py`). Whether a real provider answers the same at width 4 is
a question only real sessions answer. This is the protocol for answering it, registered
here before any run. It calls a model on every trial, so it is run by hand, never in CI.

- `experiment.yaml`: the slice. A claude-sdk agent (`claude-sonnet-4-5`, up to 4 turns, the
  `Read` tool) answers ten deterministic probes: five in one turn, five by reading a file of
  `fixtures/` and answering its fixed line. It reads a pass (`text_matched`), the tokens
  (`tokens_total`) and the turns (`turns`) of each session. Its genre is the slice's own
  (`genres/paired-runs.yaml`): runs of two repeats, estimates only.
- `analysis.py`: the decision, as code, and the only place it is made. CI tests it and
  simulates the slice through it (`tests/test_width_slice.py`).
- `k6-control.yaml`: the negative control, a server's latency under k6 load, which other
  load against the same server is known to move. It shows the decision can fail.

## What a pass settles

A pass lets `regress` allow overlap for **claude-sdk sessions of up to 4 turns, with tools,
at a width of at most 4, on the one provider and tier it ran on, as of the date it ran**. It
does not cover `model-call` subjects or `pydantic-ai` sessions through hardline, widths
above 4, or a subtle loss of quality on hard tasks, which deterministic probes cannot show.
Each of those needs a slice of its own.

## The design

1. **Screening**, fixed before any P run: a pilot of one run at width 1, the ten probes
   five times each (`--repeats 5`, 50 sessions). Keep the probes that pass 5 of 5
   (`python3 analysis.py screen <pilot run>`); delete the others from `experiment.yaml`
   before the first S run, and record which. A probe that fails alone measures the probe,
   not the width.
2. **Runs**: six S (one trial at a time) and six P (`--trials-in-flight 4`), each the kept
   probes twice: up to 120 trials an arm. In the order **S P P S S P P S S P P S**, which
   balances drift over the session (a provider's load, a cache warming) between the arms.
3. **Pairs**: each arm's runs are numbered in the order run (S1 P1 P2 S2 S3 P3 ...), and
   trial (run k, probe, repeat) of S is paired with the same of P.

## What decides

`analysis.py decide` gives `passes`, `fails` or `inconclusive`.

- **Primary**: the pass, any failure counted as a fail (a rate limit, a timeout, a wrong
  answer alike). P is **non-inferior** to S when the lower limit of Newcombe's paired
  interval for P - S (method 10) lies above **-5pp**, one-sided at 2.5%. One-sided, and
  registered so: running sessions side by side is not expected to raise a pass rate, so
  only a loss is asked about.
- **Secondary**, each needed for a pass:
  - `tokens_total` and `turns` equivalent by two one-sided tests at 2.5% each on the paired
    log ratio P / S: its 95% interval within 0.95 to 1.05;
  - every session answered by the declared model (the session table's `model`, or a dated
    release of it).
- **Inconclusive**, registered: the slice decides nothing when the baseline is not fit to
  compare with:
  - the S pass rate is below 0.97;
  - the S runs disperse (their pass counts by a chi-square test of homogeneity, their log
    tokens by an analysis of variance) at p < 0.05;
  - the first three S runs are shown to differ from the last three (an A/A check, as ix's
    own: a 95% interval of the difference that excludes 0).

**The margin rule.** δ = 5pp is at most half the smallest margin any `regress` experiment
gates on; a gate with a smaller margin needs a smaller δ, so a slice of its own. The noise
of the baseline sets the number of runs and the inconclusive rules, never the margin.

**What the simulation found** (record §27, `tests/test_width_slice.py`, 2,000 seeded
replicates each through `analysis.py`): at 120 pairs, a loss of twice the margin fails and a
loss of exactly the margin holds at about the 2.5% the test is set at; tokens at σ = 0.15
(the spread of the paired log ratio) behave as registered; drift trips the dispersion rule,
and a baseline of 0.95 trips the floor. **But with no difference at a pass rate of 0.98, the
primary holds only about 60% of the time at 120 pairs** (about 86% at 200). As registered
here, a slice that fails to show non-inferiority is not evidence of a loss. The number of
runs is the owner's to set before the first S run.

**Cost**: about 290 sessions (the pilot's 50 and twelve runs of 20), about 580 model
requests (a probe that reads takes two).

## The runs

```bash
ix validate examples/trials-in-flight
ix run examples/trials-in-flight --exploratory --repeats 5            # the pilot, width 1
python3 examples/trials-in-flight/analysis.py screen <pilot run>      # edit the probes
# S P P S S P P S S P P S
ix run examples/trials-in-flight --exploratory                        # S
ix run examples/trials-in-flight --exploratory --trials-in-flight 4   # P
ix run examples/trials-in-flight --exploratory --trials-in-flight 4   # P
ix run examples/trials-in-flight --exploratory                        # S
# ... and so on, in the order above
python3 examples/trials-in-flight/analysis.py decide --model claude-sonnet-4-5 \
  --s <S1> <S2> <S3> <S4> <S5> <S6> --p <P1> <P2> <P3> <P4> <P5> <P6>
```

Each P report lists `trials_in_flight: 4 in place of 1 (--trials-in-flight)` under
`Changed` and `4 trials in flight` on its `Run` line; check both before reading on.

The control's kinds forbid overlap, so ix refuses it a width above one. Each of its P runs
is four `ix run`s of the same file started together against the one server, which serves
four requests at once (`--capacity 4`) and queues the rest, as a service with four workers
does. The first of the four is the P run that is paired; the others are its load.

```bash
python3 examples/delay-server/server.py --port 18474 --delay-ms 40 --capacity 4 &
ix run examples/trials-in-flight/k6-control.yaml --exploratory              # S
for i in 1 2 3 4; do
  ix run examples/trials-in-flight/k6-control.yaml --exploratory &           # P
done; wait
# ... in the same order as the slice's runs
python3 examples/trials-in-flight/analysis.py control --s <S1> ... --p <P1> ...
```

The control must come out not equivalent. If it is called equivalent, the decision cannot
tell the widths apart and the slice says nothing either way: add runs, never loosen the
margins. Record how much the four runs of each P overlapped, from their trials'
`started_ns` and `duration_ns`: a P whose runs barely overlapped did not load the server,
and is no P.

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

Record the result, the probes the screen kept, each control P's overlap and the run
directories in the ix design record §27, beside ixian's dissent on `regress`, which this
slice settles for what it covers.
