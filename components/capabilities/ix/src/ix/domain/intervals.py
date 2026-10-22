"""Intervals: the methods ix decides with, each a pure function of the numbers it is given.

Every method returns a :class:`Span`: the interval at any two-sided significance level, and
the smallest level at which its low end lies above a bound (or its high end below one).
That level is what a decision is made at and what Holm's procedure reads. Where the method
is the inversion of a test, the level is that test's p-value, in closed form; otherwise it
is found by bisection, down to :data:`LEVEL_FLOOR`.

The methods, and why each:

* :func:`mean`: Student's t interval. Exact for normal data at any size, and the interval a
  percentile bootstrap only approaches from below at the trial counts ix runs: Hesterberg
  (2015) shows the percentile interval too narrow by about ``sqrt((n - 1) / n)`` and
  recommends t at small n. A paired comparison is the t interval of the per-block
  differences.
* :func:`ratio`: Fieller's interval for a ratio of two means measured in the same blocks
  (Fieller 1954): the set of ratios ``R`` the paired t test of ``x - R * y`` does not
  reject. It is unbounded when the denominator's mean is not distinguishable from zero, and
  then decides nothing. A relative difference is ``R - 1``.
* :func:`median`: the distribution-free interval between two order statistics, the
  inversion of the sign test. Its coverage is at least the nominal one at every size; it
  exists at 95% from 6 values.
* :func:`wilson`: the Wilson score interval for a rate (Wilson 1927), the inversion of the
  score test. Never of zero width, at 0 of n or n of n.
* :func:`clustered_rate`: a rate pooled over trials, each with many units (requests, test
  cases), whose units are not independent: the Wilson interval at Korn and Graubard's (1998)
  effective sample size, with their adjustment for the few degrees of freedom the trials
  give. Zero events in N units bound the rate near ``3.8 / N`` (the rule of three's
  neighbour at 97.5% one-sided), never at 0.
* :func:`difference`: the difference of two independent estimates from their own
  intervals (Newcombe 1998, method 10; generalised as the method of variance estimates
  recovery by Zou and Donner 2008). Two Wilson intervals give Newcombe's hybrid score
  interval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: The smallest significance level a bisection searches; a level found there is reported as
#: this level, and the decision may be made below it.
LEVEL_FLOOR = 1e-6

#: Values a :func:`median` interval needs at 95%: with five, the widest interval it has
#: (the smallest value to the largest) covers 93.75%.
MEDIAN_MIN = 6

_NORMAL = NormalDist()

type Ends = tuple[float, float]


# --- distributions -----------------------------------------------------------------------


def _beta_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction of the incomplete beta function, by the modified Lentz
    method."""
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1)
    d = 1 / (d if abs(d) > tiny else tiny)
    result = d
    for m in range(1, 300):
        for numerator in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1 + numerator * d
            d = 1 / (d if abs(d) > tiny else tiny)
            c = 1 + numerator / c
            c = c if abs(c) > tiny else tiny
            result *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return result


def _incomplete_beta(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function ``I_x(a, b)``."""
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


def t_cdf(t: float, df: float) -> float:
    """``P(T <= t)`` for Student's t with ``df`` degrees of freedom."""
    if math.isinf(t):
        return 1.0 if t > 0 else 0.0
    tail = 0.5 * _incomplete_beta(df / 2, 0.5, df / (df + t * t))
    return 1 - tail if t > 0 else tail


def _t_density(t: float, df: float) -> float:
    log = (
        math.lgamma((df + 1) / 2)
        - math.lgamma(df / 2)
        - 0.5 * math.log(df * math.pi)
        - (df + 1) / 2 * math.log1p(t * t / df)
    )
    return math.exp(log)


def _t_start(p: float, df: float) -> float:
    """A first guess at the t quantile: exact for one and two degrees of freedom, the
    Cornish-Fisher expansion (Abramowitz and Stegun 26.7.5) above."""
    if df == 1:
        return math.tan(math.pi * (p - 0.5))
    if df == 2:
        return (2 * p - 1) / math.sqrt(2 * p * (1 - p))
    z = _NORMAL.inv_cdf(p)
    g1 = (z**3 + z) / 4
    g2 = (5 * z**5 + 16 * z**3 + 3 * z) / 96
    g3 = (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / 384
    g4 = (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / 92160
    return z + g1 / df + g2 / df**2 + g3 / df**3 + g4 / df**4


def t_quantile(p: float, df: float) -> float:
    """The ``p`` quantile of Student's t with ``df`` degrees of freedom, polished by Newton's
    method from :func:`_t_start` and kept inside a bracket that bisection narrows."""
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -t_quantile(1 - p, df)
    low, high = 0.0, max(1.0, _t_start(p, df) * 2)
    while t_cdf(high, df) < p:
        low, high = high, high * 2
    guess = min(max(_t_start(p, df), low), high)
    for _ in range(100):
        error = t_cdf(guess, df) - p
        if error > 0:
            high = guess
        else:
            low = guess
        step = error / _t_density(guess, df)
        nxt = guess - step
        if not low < nxt < high:
            nxt = (low + high) / 2
        if abs(nxt - guess) <= 1e-13 * max(1.0, abs(guess)):
            return nxt
        guess = nxt
    return guess


def z_quantile(p: float) -> float:
    return _NORMAL.inv_cdf(p)


def sign_cdf(k: int, n: int) -> float:
    """``P(B <= k)`` for B binomial with ``n`` trials and probability one half: exact, in
    integers, then divided once."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) for i in range(k + 1)) / (1 << n)


# --- the span ----------------------------------------------------------------------------


def _bisect(decides: Callable[[float], bool]) -> float:
    """The smallest level at which ``decides`` holds, searched down to :data:`LEVEL_FLOOR`;
    ``math.inf`` when no level makes it hold. ``decides`` is monotone: a larger level gives
    a narrower interval."""
    if not decides(1.0):
        return math.inf
    if decides(LEVEL_FLOOR):
        return LEVEL_FLOOR
    low, high = LEVEL_FLOOR, 1.0
    for _ in range(50):
        middle = math.sqrt(low * high)  # levels span orders of magnitude
        if decides(middle):
            high = middle
        else:
            low = middle
    return high


@dataclass(frozen=True)
class Span:
    """An interval at every two-sided significance level.

    ``above(bound)`` is the smallest level at which the low end lies above ``bound``, and
    ``below(bound)`` the smallest at which the high end lies below it; ``math.inf`` when no
    level does that. A method that inverts a test gives them in closed form; otherwise they
    are searched.
    """

    at: Callable[[float], Ends]
    closed_above: Callable[[float], float] | None = None
    closed_below: Callable[[float], float] | None = None

    def __call__(self, alpha: float) -> Ends:
        return self.at(alpha)

    def above(self, bound: float) -> float:
        if self.closed_above is not None:
            return self.closed_above(bound)
        return _bisect(lambda alpha: self.at(alpha)[0] > bound)

    def below(self, bound: float) -> float:
        if self.closed_below is not None:
            return self.closed_below(bound)
        return _bisect(lambda alpha: self.at(alpha)[1] < bound)

    def negated(self) -> Span:
        """The span of ``-x``."""
        above, below = self.above, self.below
        return Span(
            lambda alpha: (-self.at(alpha)[1], -self.at(alpha)[0]),
            lambda bound: below(-bound),
            lambda bound: above(-bound),
        )

    def scaled(self, factor: float, shift: float = 0.0) -> Span:
        """The span of ``x * factor + shift``, for a ``factor`` above zero."""
        above, below = self.above, self.below

        def at(alpha: float) -> Ends:
            low, high = self.at(alpha)
            return low * factor + shift, high * factor + shift

        return Span(
            at,
            lambda bound: above((bound - shift) / factor),
            lambda bound: below((bound - shift) / factor),
        )


def _p_two_sided(statistic: float, df: float | None) -> float:
    """The two-sided level at which ``|statistic|`` is the critical value: t with ``df``
    degrees of freedom, or normal when ``df`` is None."""
    if math.isinf(statistic):
        return 0.0
    upper = _NORMAL.cdf(abs(statistic)) if df is None else t_cdf(abs(statistic), df)
    return min(1.0, 2 * (1 - upper))


# --- one sample --------------------------------------------------------------------------


def spread(values: Sequence[float]) -> float:
    """The sample standard deviation (``n - 1`` in the denominator)."""
    centre = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((value - centre) ** 2 for value in values) / (len(values) - 1))


def mean(values: Sequence[float]) -> Span:
    """Student's t interval for the mean of ``values``: two or more, not all equal."""
    n = len(values)
    centre = math.fsum(values) / n
    error = spread(values) / math.sqrt(n)
    df = n - 1

    def at(alpha: float) -> Ends:
        half = t_quantile(1 - alpha / 2, df) * error
        return centre - half, centre + half

    def above(bound: float) -> float:
        return math.inf if centre <= bound else _p_two_sided((centre - bound) / error, df)

    def below(bound: float) -> float:
        return math.inf if centre >= bound else _p_two_sided((bound - centre) / error, df)

    return Span(at, above, below)


def median(values: Sequence[float]) -> Span:
    """The order-statistic interval for the median of ``values``: ``[x_(k), x_(n-k+1)]``
    with the largest ``k`` whose coverage ``1 - 2 P(B <= k - 1)`` is at least the level's.
    Unbounded at a level fewer values cannot reach."""
    ordered = sorted(values)
    n = len(ordered)

    def at(alpha: float) -> Ends:
        k = 0
        while k + 1 <= (n + 1) // 2 and 2 * sign_cdf(k, n) <= alpha:
            k += 1
        if k == 0:
            return -math.inf, math.inf
        return ordered[k - 1], ordered[n - k]

    def level(beyond: int) -> float:
        # The low end passes a bound once k exceeds the number of values not above it.
        if beyond + 1 > (n + 1) // 2:
            return math.inf
        return 2 * sign_cdf(beyond, n)

    return Span(
        at,
        lambda bound: level(sum(1 for value in ordered if value <= bound)),
        lambda bound: level(sum(1 for value in ordered if value >= bound)),
    )


def _score_level(rate: float, n: float, bound: float, df: float | None, *, low: bool) -> float:
    """The level at which a score interval for ``rate`` from ``n`` units has its low end
    above ``bound`` (``low``) or its high end below it: the score test of ``bound``."""
    if (low and rate <= bound) or (not low and rate >= bound):
        return math.inf
    if bound <= 0 or bound >= 1:
        return 0.0  # the interval never leaves [0, 1], so every level does it
    return _p_two_sided((rate - bound) / math.sqrt(bound * (1 - bound) / n), df)


def _wilson_ends(rate: float, n: float, z: float) -> Ends:
    scale = 1 + z * z / n
    centre = (rate + z * z / (2 * n)) / scale
    half = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / scale
    # At a rate of 0 or 1 one end is exactly 0 or 1; do not let rounding move it.
    low = 0.0 if rate == 0 else max(0.0, centre - half)
    high = 1.0 if rate == 1 else min(1.0, centre + half)
    return low, high


def wilson(successes: float, n: int) -> Span:
    """The Wilson score interval for ``successes`` out of ``n`` independent trials."""
    rate = successes / n
    return Span(
        lambda alpha: _wilson_ends(rate, n, z_quantile(1 - alpha / 2)),
        lambda bound: _score_level(rate, n, bound, None, low=True),
        lambda bound: _score_level(rate, n, bound, None, low=False),
    )


@dataclass(frozen=True)
class Pooled:
    """A rate pooled over trials, and what the clustering of its units in trials does to
    it."""

    rate: float
    units: int
    #: Korn and Graubard's effective sample size ``p (1 - p) / var``: the units a simple
    #: random sample would need for the same variance. None at a rate of 0 or 1, where
    #: there is no variance to estimate and every unit counts.
    effective: float | None
    #: Degrees of freedom: trials less one.
    df: int


def pool(events: Sequence[int], units: Sequence[int]) -> Pooled:
    """Pool per-trial counts: the ratio estimate and its variance with the trial as the
    cluster (the linearised ratio variance, ``n / (n - 1)`` corrected)."""
    trials, total = len(units), sum(units)
    rate = sum(events) / total
    residual = math.fsum((e - rate * m) ** 2 for e, m in zip(events, units, strict=True))
    variance = trials / (trials - 1) * residual / total**2
    binomial = rate * (1 - rate)
    effective = None if binomial <= 0 else math.inf if variance <= 0 else binomial / variance
    return Pooled(rate=rate, units=total, effective=effective, df=trials - 1)


def clustered_rate(events: Sequence[int], units: Sequence[int]) -> Span:
    """The Wilson interval of a pooled rate at Korn and Graubard's (1998) effective sample
    size adjusted for the trials' degrees of freedom, ``n_eff * (z / t_df)^2``, truncated at
    the units counted, as they recommend where the design effect is known to be at least
    one (units in one trial share its conditions). At a rate of 0 or 1 every unit counts,
    as they set it. The low end passes a bound exactly when both the score test of the
    bound over every unit and the t test of it at ``df`` reject it, so the level is the
    larger of the two p-values. Two or more trials."""
    pooled = pool(events, units)
    rate, effective, df, total = pooled.rate, pooled.effective, pooled.df, pooled.units

    def at(alpha: float) -> Ends:
        if alpha >= 1:
            return rate, rate
        z = z_quantile(1 - alpha / 2)
        adjusted = float(total)
        if effective is not None:
            adjusted = min(adjusted, effective * (z / t_quantile(1 - alpha / 2, df)) ** 2)
        return _wilson_ends(rate, adjusted, z)

    def level(bound: float, *, low: bool) -> float:
        found = _score_level(rate, total, bound, None, low=low)
        # Over thousands of units the score test's level is often exactly 0; the t test at
        # the effective size still applies then. Only a bound outside (0, 1) skips it.
        if effective is None or math.isinf(effective) or not math.isfinite(found):
            return found
        if not 0 < bound < 1:
            return found
        return max(found, _score_level(rate, effective, bound, df, low=low))

    return Span(
        at,
        lambda bound: level(bound, low=True),
        lambda bound: level(bound, low=False),
    )


# --- two samples -------------------------------------------------------------------------


def difference(first: Span, second: Span, first_value: float, second_value: float) -> Span:
    """The interval of ``first - second``, two independent estimates, from their own
    intervals (Newcombe's method 10; Zou and Donner's method of variance estimates
    recovery). The pairing of the blocks is not used, which makes it wider, not narrower,
    when the two move together."""
    delta = first_value - second_value

    def at(alpha: float) -> Ends:
        l1, u1 = first(alpha)
        l2, u2 = second(alpha)
        return (
            delta - math.sqrt((first_value - l1) ** 2 + (u2 - second_value) ** 2),
            delta + math.sqrt((u1 - first_value) ** 2 + (second_value - l2) ** 2),
        )

    return Span(at)


def ratio(numerator: Sequence[float], denominator: Sequence[float]) -> Span:
    """Fieller's interval for ``mean(numerator) / mean(denominator)``, the two measured in the
    same blocks: the ratios ``R`` for which the t test of the paired values
    ``numerator - R * denominator`` does not reject a mean of zero. Unbounded (``-inf, inf``)
    at a level where the denominator's own mean is not distinguishable from zero. Two or
    more pairs, not all equal on both sides."""
    n = len(numerator)
    df = n - 1
    x = math.fsum(numerator) / n
    y = math.fsum(denominator) / n

    def covariance(a: Sequence[float], ma: float, b: Sequence[float], mb: float) -> float:
        return math.fsum((i - ma) * (j - mb) for i, j in zip(a, b, strict=True)) / df

    sxx = covariance(numerator, x, numerator, x)
    syy = covariance(denominator, y, denominator, y)
    sxy = covariance(numerator, x, denominator, y)
    estimate = x / y

    def at(alpha: float) -> Ends:
        q = t_quantile(1 - alpha / 2, df) ** 2 / n
        a = y * y - q * syy
        if a <= 0:
            return -math.inf, math.inf
        b = x * y - q * sxy
        c = x * x - q * sxx
        root = math.sqrt(max(0.0, b * b - a * c))
        return (b - root) / a, (b + root) / a

    bounded = _p_two_sided(abs(y) / math.sqrt(syy / n), df) if syy > 0 else 0.0

    def level(bound: float, *, low: bool) -> float:
        if (low and estimate <= bound) or (not low and estimate >= bound):
            return math.inf
        residual = sxx - 2 * bound * sxy + bound * bound * syy
        if residual <= 0:
            return bounded
        statistic = (x - bound * y) / math.sqrt(residual / n)
        return max(bounded, _p_two_sided(statistic, df))

    return Span(
        at,
        lambda bound: level(bound, low=True),
        lambda bound: level(bound, low=False),
    )
