"""The analysis of the trials-in-flight slice: what README.md beside this file states, as code.

Run it once the runs are done, from the run directories `ix run` printed, in the order run:

    python3 analysis.py screen <pilot run directory>
    python3 analysis.py decide --model <declared model> --s <S run 1> ... --p <P run 1> ...
    python3 analysis.py control --s <S run 1> ... --p <P run 1> ...

``screen`` prints the probes the pilot kept (every trial passed); ``decide`` prints the
slice's result as JSON: the primary decision, each secondary, the inconclusive rules that
tripped, and the verdict (``passes``, ``fails`` or ``inconclusive``); ``control`` prints the
negative control's latency decision, which must come out not equivalent.

**Pairs.** The S runs and the P runs are numbered in the order each arm ran (S1 P1 P2 S2
...): trial (run k, probe, repeat) of S is paired with the same of P. A trial with no
partner (a run cut short) is left out of the paired decisions and said.

**Primary.** Pass, any failure counted as a fail. P is non-inferior to S when the lower
limit of Newcombe's paired interval for P - S (method 10, from Wilson's score limits) at a
one-sided 2.5% lies above ``-DELTA``. One-sided, as registered: running trials side by side
is not expected to raise a pass rate, so only a loss is asked about.

**Secondary.** ``tokens_total`` and ``turns`` by two one-sided tests at 2.5% each on the
paired log ratio P / S per pair: equivalent when its 95% interval (Student's t) lies within
0.95 to 1.05. And every session answered by the declared model.

**Inconclusive**, decided on the S runs alone, before P is read: the S pass rate below
``S_FLOOR``; a dispersion across the S runs (their pass counts by a chi-square test of
homogeneity, their log tokens by one-way analysis of variance) at p below
``DISPERSION_P``; or the first three S runs shown to differ from the last three (an A/A
check, as ix's own calibration: a two-sided 95% interval of the difference, paired by run,
probe and repeat, that excludes 0).

It reads only the run's tables and needs nothing installed but ix (for its t and normal
quantiles).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ix.domain.intervals import t_quantile, z_quantile

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

#: The non-inferiority margin of the pass rate: at most half the smallest margin any
#: regress experiment gates on (the README's margin rule).
DELTA = 0.05
#: The equivalence margin of a ratio (tokens, turns): 0.95 to 1.05.
RATIO_MARGIN = 0.05
#: Each one-sided test's level: ix's own analysis's.
ALPHA = 0.025
#: Below this S pass rate the slice is inconclusive.
S_FLOOR = 0.97
#: Below this p a dispersion across the S runs makes the slice inconclusive.
DISPERSION_P = 0.05


@dataclass(frozen=True)
class Trial:
    """One trial as the analysis reads it."""

    arm: str  # "S" or "P"
    #: Its run's place among its arm's runs, from 1, in the order run.
    run: int
    probe: str
    repeat: int
    #: The probe passed: no failure of the trial or the reading, and the check held.
    passed: bool
    tokens: float | None = None
    turns: float | None = None
    #: The models that answered its sessions, as the session table names them.
    models: tuple[str, ...] = ()
    #: The control's latency p50, in ms.
    latency: float | None = None

    @property
    def key(self) -> tuple[int, str, int]:
        return (self.run, self.probe, self.repeat)


@dataclass(frozen=True)
class Decision:
    """A decision and the interval it rests on (a difference, or a ratio)."""

    holds: bool
    low: float
    high: float
    n: int


# --- the intervals ----------------------------------------------------------------------


def wilson(successes: int, n: int, alpha: float = ALPHA) -> tuple[float, float]:
    """Wilson's score interval for a proportion, each side at ``alpha``."""
    if n == 0:
        return (0.0, 1.0)
    z = z_quantile(1 - alpha)
    rate = successes / n
    spread = 1 + z * z / n
    centre = (rate + z * z / (2 * n)) / spread
    half = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / spread
    return (max(0.0, centre - half), min(1.0, centre + half))


def newcombe_paired(
    pairs: Sequence[tuple[bool, bool]], alpha: float = ALPHA
) -> tuple[float, float]:
    """Newcombe's interval (1998, method 10) for the difference P - S of two paired
    proportions, each side at ``alpha``. ``pairs`` holds (S passed, P passed)."""
    n = len(pairs)
    if n == 0:
        return (-1.0, 1.0)
    both = sum(1 for s, p in pairs if s and p)
    p_only = sum(1 for s, p in pairs if p and not s)
    s_only = sum(1 for s, p in pairs if s and not p)
    neither = n - both - p_only - s_only
    rate_p, rate_s = (both + p_only) / n, (both + s_only) / n
    low_p, high_p = wilson(both + p_only, n, alpha)
    low_s, high_s = wilson(both + s_only, n, alpha)
    margins = (both + p_only) * (s_only + neither) * (both + s_only) * (p_only + neither)
    phi = (both * neither - p_only * s_only) / math.sqrt(margins) if margins else 0.0
    difference = rate_p - rate_s
    below = (rate_p - low_p) ** 2 - 2 * phi * (rate_p - low_p) * (high_s - rate_s)
    above = (high_p - rate_p) ** 2 - 2 * phi * (high_p - rate_p) * (rate_s - low_s)
    below += (high_s - rate_s) ** 2
    above += (rate_s - low_s) ** 2
    return (difference - math.sqrt(max(0.0, below)), difference + math.sqrt(max(0.0, above)))


def non_inferior(
    pairs: Sequence[tuple[bool, bool]], delta: float = DELTA, alpha: float = ALPHA
) -> Decision:
    """The primary: P - S no worse than ``-delta``, one-sided at ``alpha``."""
    low, high = newcombe_paired(pairs, alpha)
    return Decision(holds=low > -delta, low=low, high=high, n=len(pairs))


def _mean_interval(values: Sequence[float], alpha: float) -> tuple[float, float]:
    """Student's t interval for the mean of ``values``, each side at ``alpha``."""
    centre = statistics.fmean(values)
    if len(values) < 2:
        return (-math.inf, math.inf)
    spread = statistics.stdev(values)
    if spread == 0:
        return (centre, centre)
    half = t_quantile(1 - alpha, len(values) - 1) * spread / math.sqrt(len(values))
    return (centre - half, centre + half)


def equivalent_ratio(
    pairs: Sequence[tuple[float, float]], margin: float = RATIO_MARGIN, alpha: float = ALPHA
) -> Decision:
    """A secondary: the ratio P / S within ``1 - margin`` to ``1 + margin``, by two
    one-sided tests at ``alpha`` on the paired log ratio. ``pairs`` holds (S, P), each
    above 0."""
    logs = [math.log(p / s) for s, p in pairs]
    if not logs:
        return Decision(holds=False, low=0.0, high=math.inf, n=0)
    low, high = _mean_interval(logs, alpha)
    holds = math.log(1 - margin) < low and high < math.log(1 + margin)
    return Decision(holds=holds, low=math.exp(low), high=math.exp(high), n=len(logs))


# --- the tails ----------------------------------------------------------------------------


def _gamma_q(a: float, x: float) -> float:
    """The regularized upper incomplete gamma function Q(a, x)."""
    if x <= 0:
        return 1.0
    log_front = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1:  # the series for P
        term = total = 1.0 / a
        n = a
        while abs(term) > abs(total) * 1e-15:
            n += 1
            term *= x / n
            total += term
        return 1.0 - total * math.exp(log_front)
    tiny = 1e-300  # Lentz's continued fraction for Q
    b = x + 1 - a
    c, d = 1 / tiny, 1 / b
    fraction = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        step = d * c
        fraction *= step
        if abs(step - 1) < 1e-15:
            break
    return math.exp(log_front) * fraction


def chi2_sf(x: float, df: int) -> float:
    """P(chi-square with ``df`` degrees of freedom >= ``x``)."""
    return 1.0 if x <= 0 else _gamma_q(df / 2, x / 2)


def _beta_fraction(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    c, d = 1.0, 1 - (a + b) * x / (a + 1)
    d = 1 / (tiny if abs(d) < tiny else d)
    fraction = d
    for m in range(1, 1000):
        for numerator in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1 + numerator * d
            d = 1 / (tiny if abs(d) < tiny else d)
            c = 1 + numerator / c
            c = tiny if abs(c) < tiny else c
            fraction *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return fraction


def _beta_regularized(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1) / (a + b + 2):
        return front * _beta_fraction(a, b, x) / a
    return 1 - front * _beta_fraction(b, a, 1 - x) / b


def f_sf(f: float, d1: int, d2: int) -> float:
    """P(F with ``d1`` and ``d2`` degrees of freedom >= ``f``)."""
    return 1.0 if f <= 0 else _beta_regularized(d2 / 2, d1 / 2, d2 / (d2 + d1 * f))


# --- the rules ------------------------------------------------------------------------------


def pair(s: Iterable[Trial], p: Iterable[Trial]) -> list[tuple[Trial, Trial]]:
    """Each S trial with the P trial of the same run number, probe and repeat, in the S
    trials' order; one with no partner is left out."""
    partner = {trial.key: trial for trial in p}
    return [(trial, partner[trial.key]) for trial in s if trial.key in partner]


def homogeneity_p(counts: Sequence[tuple[int, int]]) -> float:
    """The chi-square test of homogeneity of ``(passed, trials)`` across runs: its p."""
    passed = sum(k for k, _ in counts)
    trials = sum(n for _, n in counts)
    failed = trials - passed
    if not passed or not failed or len(counts) < 2:
        return 1.0
    statistic = 0.0
    for k, n in counts:
        expected_pass, expected_fail = n * passed / trials, n * failed / trials
        statistic += (k - expected_pass) ** 2 / expected_pass
        statistic += (n - k - expected_fail) ** 2 / expected_fail
    return chi2_sf(statistic, len(counts) - 1)


def anova_p(groups: Sequence[Sequence[float]]) -> float:
    """One-way analysis of variance across ``groups``: its p."""
    groups = [group for group in groups if group]
    total = sum(len(group) for group in groups)
    if len(groups) < 2 or total <= len(groups):
        return 1.0
    grand = statistics.fmean(value for group in groups for value in group)
    between = sum(len(g) * (statistics.fmean(g) - grand) ** 2 for g in groups)
    within = sum((value - statistics.fmean(g)) ** 2 for g in groups for value in g)
    if within == 0:
        return 1.0 if between == 0 else 0.0
    f = (between / (len(groups) - 1)) / (within / (total - len(groups)))
    return f_sf(f, len(groups) - 1, total - len(groups))


def inconclusive(s: Sequence[Trial]) -> list[str]:
    """Why the S runs make the slice inconclusive, each a sentence starting with the rule's
    name (``baseline``, ``dispersion``, ``a/a``); none when they are fit to compare with."""
    reasons: list[str] = []
    if not s:
        return ["baseline: there are no S trials"]
    rate = sum(t.passed for t in s) / len(s)
    if rate < S_FLOOR:
        reasons.append(f"baseline: the S pass rate is {rate:.3f}, below {S_FLOOR}")
    runs = sorted({t.run for t in s})
    counts = [
        (sum(t.passed for t in s if t.run == run), sum(1 for t in s if t.run == run))
        for run in runs
    ]
    passes = homogeneity_p(counts)
    if passes < DISPERSION_P:
        reasons.append(f"dispersion: the S runs' pass counts differ (chi-square p = {passes:.4f})")
    logs = [[math.log(t.tokens) for t in s if t.run == run and t.tokens] for run in runs]
    tokens = anova_p(logs)
    if tokens < DISPERSION_P:
        reasons.append(
            f"dispersion: the S runs' tokens differ (analysis of variance p = {tokens:.4f})"
        )
    half = len(runs) // 2
    first = [t for t in s if t.run in runs[:half]]
    second = [
        Trial(arm=t.arm, run=t.run - half, probe=t.probe, repeat=t.repeat, passed=t.passed,
              tokens=t.tokens)
        for t in s
        if t.run in runs[half:]
    ]  # fmt: skip
    paired = pair(first, second)
    low, high = newcombe_paired([(a.passed, b.passed) for a, b in paired])
    if paired and not low <= 0 <= high:
        reasons.append(f"a/a: the S halves' pass rates differ ([{low:.3f}, {high:.3f}])")
    ratios = [(a.tokens, b.tokens) for a, b in paired if a.tokens and b.tokens]
    if len(ratios) > 1:
        low, high = _mean_interval([math.log(b / a) for a, b in ratios], ALPHA)
        if not low <= 0 <= high:
            reasons.append(
                f"a/a: the S halves' tokens differ ([{math.exp(low):.3f}, {math.exp(high):.3f}])"
            )
    return reasons


def control(trials: Sequence[Trial]) -> Decision:
    """The negative control: P's latency p50 against S's, by the same paired ratio test as
    the tokens. It must come out not equivalent, or the test cannot tell the widths apart."""
    s = [t for t in trials if t.arm == "S"]
    p = [t for t in trials if t.arm == "P"]
    return equivalent_ratio(
        [(a.latency, b.latency) for a, b in pair(s, p) if a.latency and b.latency]
    )


def answered_by(trials: Iterable[Trial], model: str) -> bool:
    """Every session was answered by ``model``: its id, or a dated release of it
    (``claude-sonnet-4-5-20250929`` for ``claude-sonnet-4-5``); and at least one says so."""
    named = [m for trial in trials for m in trial.models]
    return bool(named) and all(m == model or m.startswith(f"{model}-") for m in named)


def screen(pilot: Sequence[Trial], repeats: int) -> tuple[str, ...]:
    """The probes the pilot keeps: each that ran ``repeats`` times and passed every time,
    in the order first seen."""
    order = list(dict.fromkeys(t.probe for t in pilot))
    kept = [
        probe
        for probe in order
        if sum(1 for t in pilot if t.probe == probe) == repeats
        and all(t.passed for t in pilot if t.probe == probe)
    ]
    return tuple(kept)


@dataclass(frozen=True)
class Result:
    """The slice's result."""

    verdict: str
    primary: Decision
    tokens: Decision
    turns: Decision
    declared_model: bool
    inconclusive: tuple[str, ...]
    unpaired: int


def decide(trials: Sequence[Trial], model: str) -> Result:
    """The slice's result, as the README registers it."""
    s = [t for t in trials if t.arm == "S"]
    p = [t for t in trials if t.arm == "P"]
    paired = pair(s, p)
    primary = non_inferior([(a.passed, b.passed) for a, b in paired])
    tokens = equivalent_ratio([(a.tokens, b.tokens) for a, b in paired if a.tokens and b.tokens])
    turns = equivalent_ratio([(a.turns, b.turns) for a, b in paired if a.turns and b.turns])
    declared = answered_by(trials, model)
    reasons = tuple(inconclusive(s))
    held = primary.holds and tokens.holds and turns.holds and declared
    verdict = "inconclusive" if reasons else "passes" if held else "fails"
    return Result(
        verdict=verdict,
        primary=primary,
        tokens=tokens,
        turns=turns,
        declared_model=declared,
        inconclusive=reasons,
        unpaired=len(s) + len(p) - 2 * len(paired),
    )


# --- reading a run --------------------------------------------------------------------------


def _rows(directory: Path, table: str) -> list[dict[str, Any]]:
    file = directory / f"{table}.jsonl"
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text().splitlines() if line.strip()]


def load(directory: Path, *, arm: str, run: int) -> list[Trial]:
    """The trials of one run, from the tables ix wrote in its ``directory``."""
    interactions = {row["seq"]: row for row in _rows(directory, "ix.v1.interaction")}
    readings: dict[tuple[int, str], dict[str, Any]] = {
        (row["seq"], row["metric"]): row for row in _rows(directory, "ix.v1.reading")
    }
    sessions = _rows(directory, "matrix.v1.session")

    def value(seq: int, metric: str) -> Any:
        row = readings.get((seq, metric))
        return None if row is None or row.get("failure") else row.get("value")

    def p50(seq: int, metric: str) -> float | None:
        row = readings.get((seq, metric))
        held = None if row is None or row.get("failure") else (row.get("statistics") or {})
        return None if held is None or "p50" not in held else float(held["p50"])

    trials = []
    for row in _rows(directory, "ix.v1.trial"):
        seq = row["seq"]
        interaction = interactions.get(seq)
        carried = interaction is not None and not interaction.get("failure")
        behind = interaction.get("sessions", []) if interaction else []
        models = tuple(
            str(sessions[n]["model"])
            for n in behind
            if n < len(sessions) and sessions[n].get("model")
        )
        tokens, turns = value(seq, "tokens_total"), value(seq, "turns")
        trials.append(
            Trial(
                arm=arm,
                run=run,
                probe=str(row["probe"]),
                repeat=int(row["repeat"]),
                passed=carried and value(seq, "text_matched") is True,
                tokens=None if tokens is None else float(tokens),
                turns=None if turns is None else float(turns),
                models=models,
                latency=p50(seq, "latency"),
            )
        )
    return trials


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    screening = commands.add_parser("screen", help="the probes the pilot keeps")
    screening.add_argument("pilot", type=Path)
    screening.add_argument("--repeats", type=int, default=5)
    deciding = commands.add_parser("decide", help="the slice's result")
    deciding.add_argument("--model", required=True, help="the model the experiment declares")
    deciding.add_argument("--s", nargs="+", type=Path, required=True, help="S runs, in order")
    deciding.add_argument("--p", nargs="+", type=Path, required=True, help="P runs, in order")
    checking = commands.add_parser("control", help="the negative control's latency decision")
    checking.add_argument("--s", nargs="+", type=Path, required=True, help="S runs, in order")
    checking.add_argument("--p", nargs="+", type=Path, required=True, help="P runs, in order")
    arguments = parser.parse_args(argv)
    if arguments.command == "screen":
        kept = screen(load(arguments.pilot, arm="S", run=1), arguments.repeats)
        print(json.dumps({"kept": kept}))
        return 0
    trials = [
        trial
        for arm, runs in (("S", arguments.s), ("P", arguments.p))
        for run, directory in enumerate(runs, start=1)
        for trial in load(directory, arm=arm, run=run)
    ]
    if arguments.command == "control":
        print(json.dumps(asdict(control(trials)), indent=2))
        return 0
    print(json.dumps(asdict(decide(trials, arguments.model)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
