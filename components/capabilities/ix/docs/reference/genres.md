# Genres

Each genre ix ships, what it decides, what an experiment must set, and how a genre extends another. `ix genres` lists what is installed; `ix --skill -r genres` gives an example of each.

A genre is a record in a YAML file: the roles subjects play, what the experiment must set,
the comparisons it may make, how a run is laid out, which analysis decides, and what the
report must contain. It holds no code and names no kind.

| genre | question | subjects | you must set | verdict |
|---|---|---|---|---|
| `evaluate` | Does this subject meet the threshold? | one | `primary`, `threshold` | holds / fails / inconclusive |
| `compare` | Is the variant better than the control, or no worse? | one control, one or more variants | `primary`, `comparison`, and `margin` for non-inferiority and equivalence | holds / fails / inconclusive |
| `regress` | May this change ship against the baseline? | one control, one variant | `primary`, `margin` | holds / fails / inconclusive |
| `characterize` | How does this subject behave across the range? | one | `primary`, `factors` | none: estimates only |
| `check` | Does the predicate hold on every trial of this subject? | one | `primary`, a boolean | holds / fails / inconclusive |
| `mutation-score` | What share of seeded faults in this code does the test suite detect? | one test suite | `primary` (`killed`) | none: estimates only |
| `mutation-regress` | Does the changed suite still detect what the baseline suite detects, within the margin? | one control suite, one variant | `primary`, `margin` | holds / fails / inconclusive |

`check` observes; it does not estimate. Its analysis decides on a boolean primary alone,
with no interval, so one trial (its default and its minimum) is enough to read: it holds
when the primary read true on every trial, fails when it read false on one or the subject
failed one, and is inconclusive only when a trial could not be read (a harness failure, a
reading that could not be taken), never because the subject failed. It reads no threshold,
margin or guardrail, and refuses one (`decision_inapplicable`), as it does a primary that is
not a boolean (`metric_shape`). Its warrant says what one observation cannot show: how often
the predicate holds. It gates reliably only a subject that gives the same result every time
it is run on the same input; for one whose output varies (an agent session, a model call, a
service under load) the report gives the Wilson interval the share it saw leaves (1 of 1
leaves about [0.21, 1]). To rely on such a subject, use `evaluate` with a pass-rate
threshold. A command checked against a service is the `command` probe with the
`exit-status` sensor:

```yaml
name: proxy-health
question: Does the proxy answer its health check once it is up?
genre: check
subjects:
  proxy: {kind: process, command: [./proxy], url: "http://127.0.0.1:8080"}
probes:
  - kind: command
    command: [curl, -fsS, "{url}/healthz"]
primary: {metric: exited_ok}
```

`compare` offers three comparisons: `superiority` asks for a difference (the variant is
better), `non-inferiority` for no loss beyond the margin, `equivalence` for no difference
beyond it either way. `regress` is `compare` narrowed to one variant, with the comparison
fixed to `non-inferiority`: the candidate is at most the margin worse than the baseline. So two builds that do not differ are inconclusive under `superiority`, and hold under
`regress` once there are enough trials that the interval stays above `-margin`.

`regress` extends `compare`. A genre extends at most one other, one level deep, and only by
narrowing: it may raise a minimum, lower a maximum, require more, allow fewer comparisons or
add report sections. It may not loosen its parent, change the analysis or add a parameter to
it, change the estimator or the verdict form, or add a role. A genre file that breaks this is
refused with a `genre_narrowing` problem for each place it does.

A genre's `design` (repeats, order, calibration, estimator, overlap) carries a note on its
standing. Its `overlap` (`allowed` when unsaid) says whether its trials may overlap at all:
`regress` says `forbidden`, so a gate's trials run one at a time whatever its kinds allow;
a genre that extends another may forbid it, never allow it again.
The built-in genres that estimate need at least 5 repeats to decide (3 for
`characterize`) and default to 10 (5); `check` needs 1. Their methods are checked by
simulation at those counts (see
[the verdict and its method](../explanation/the-verdict.md)). A genre that decides cannot use `min`
or `max` as its estimator.
