# The verdict, and the method behind it

How ix turns readings into `holds`, `fails` or `inconclusive`, what each interval is, how the simulations check the method, and where it is known to be weak.

One finding per variant per cell. The verdict holds only if every finding holds, and fails
if any fails. Every choice below, with its source and the simulation that checks it, is in
[the design record](../design/2026-10-19-record.md); the report prints the method with
every verdict.

1. Each trial's reading is reduced to a number by the `statistic`; the trials combine by
   the `estimator`. A comparison is made *in blocks*: one repeat of one probe in one cell,
   where every subject ran side by side in an order drawn from the seed.
2. The decision is made on a 95% interval, by the measurement:
   - a `mean` gets Student's t interval: of the per-block differences for a comparison in
     points or the metric's unit, and Fieller's interval for the ratio of the two means for
     a comparison in percent;
   - a `median` gets the order-statistic interval (the sign test inverted): of the
     per-block differences in a comparison. It needs 6 blocks;
   - a `boolean` is a rate over trials, with a Wilson score interval; a `count` (failed
     requests out of requests sent) is a rate pooled over every unit, with the Wilson
     interval at Korn and Graubard's effective sample size, the trial as the cluster. Zero
     failures in 10,000 requests bound the rate near 3.8/10,000, never at 0. A difference of
     two booleans' rates is paired, block by block (Tango's score interval of the paired
     table: only the blocks where the two disagree move it); a difference of two counts is
     Newcombe's interval;
   - `min` and `max` have no interval ix can stand behind: they are reported, never decided
     on.
3. With the gain being the variant minus the control, turned so that positive is better:
   `superiority` holds when the interval is above 0 and fails when it is below;
   `non-inferiority` holds when it is above `-margin` and fails when it is below (a
   one-sided test at 2.5%, as FDA's guidance on non-inferiority has it); `equivalence`
   holds inside `(-margin, margin)` and fails wholly outside (two one-sided tests at 2.5%
   each). A threshold holds or fails when the subject's own interval is wholly on one side.
   Anything else is inconclusive.
4. **More than one decision.** The verdict holds only if every decision (each variant, each
   cell, the primary and each guardrail) holds, so each is read at the full 0.05 and no
   correction applies: requiring all of them adds no chance of a false `holds` (an
   intersection-union test). The verdict fails if any decision fails, so the `fails` side
   is corrected by Holm's procedure across the whole family, one that missing data left
   undecided included. A decision's `decided_at` is the smallest level at which it would be
   made (its test's p-value), and `allowed` the level it was held to. The intervals printed
   are unadjusted.
5. **The A/A check.** Under A/A calibration the control runs twice in every block, and its
   second sample is compared with its first by the same method, at 1% shared out over every
   such check. A control that reads the same in both samples of every block held still,
   and passes. A control that differs from itself makes the run inconclusive: the
   environment did not hold still enough to compare in. So does a check that cannot be
   made, since nothing then shows that it did. The report says which. The check never
   changes an interval.
6. A subject that failed a trial of a cell fails that cell. In a comparison, a control that
   failed one makes the cell inconclusive instead, whatever the variant did. A model-judged
   metric never decides. ix's analyses take no parameters: a genre that gives one is warned
   about (`analysis_unimplemented`) and refused by `ix run`.

What the simulations show, on normal data, at the genres' minimum of 5 blocks and their
default of 10 (10,000 runs each): the 95% interval of a difference covers 94.7% of the
time at both; a variant worse by exactly the margin holds 2.5% of the time (nominal 2.5%);
a mean exactly on a threshold holds 2.4% and 2.8%; a stable environment fails the A/A check
1.1% and 1.0% of the time (stated 1%). An error rate exactly on its threshold, over 1,000 or
10,000 requests a trial whose failures cluster within trials, holds 1.9% to 3.9% of the time
(4,000 runs each).

What follows for two subjects that do not differ: under `superiority` the verdict is
**inconclusive** (nothing was shown either way); under `non-inferiority` it **holds** once
the interval is narrower than the margin (a loss beyond the margin was ruled out), and that
is the comparison `regress` fixes.

`Sensitivity:` in `ix plan` is what a run can detect. Once the experiment has run (an
exploratory run counts; a dry run on the `mock` runtime does not), it is the true difference
a run of this size shows 4 times in 5, from the spread of the per-block differences in the
latest run: a margin below it will rarely hold. For a pass rate held to a threshold, the plan also says when even every trial
passing cannot show it (30 trials can never show a rate above 0.9; 35 can).

Known limits: fewer than 3 usable blocks on a side decide nothing (6 for a median); every
trial reading the same number (on both sides, in a comparison) decides nothing; a one-sample t interval under-covers on
strongly skewed per-trial numbers (a median is the remedy); a relative difference has no
bounded interval when the control's mean is not distinguishable from zero; a difference of
two rates does not use the pairing, so it is wider than it might be; a rate whose rare
events come in bursts concentrated in a few trials is under-covered, and on its threshold
holds up to 3.9% of the time where 2.5% is stated; Wilson's one-sided
error oscillates with the number of trials, up to 3.9% where 2.5% is stated; the A/A check
sees a shift, not every kind of instability; the memory peak is sampled, so a spike
between two samples is missed.
