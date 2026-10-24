"""The analysis of the live trials-in-flight slice, and a simulation of it.

The slice (``examples/trials-in-flight/``) is run by hand and never here; its analysis is
code (``analysis.py`` beside it), and this file holds that code to what the slice's README
states, then simulates the slice at its own n (six runs a side, 20 trials a run: 120 pairs)
through that same code, seeded. The cases are the ones the slice's design was set to meet
(record §27): with a difference of twice the margin it does not hold, at the margin it holds
at about the one-sided level, and its inconclusive rules trip on what they are there for.
With no difference it holds only about 60% of the time at this n, short of the 80% the
design asked; that case is reported in the record, not asserted.

The cases run 2,000 replicates each, the design's number; they take a few seconds.
"""

from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

SLICE = Path(__file__).resolve().parents[1] / "examples" / "trials-in-flight"
REPLICATES = 2000
PAIRS = 120  # six runs a side of ten probes x two repeats


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "trials_in_flight_analysis", SLICE / "analysis.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


analysis = _load()


# --- the arithmetic, against values worked by hand ---------------------------------------


def test_wilsons_interval_matches_its_worked_value() -> None:
    low, high = analysis.wilson(81, 263, 0.025)
    assert (round(low, 4), round(high, 4)) == (0.2553, 0.3662)  # Newcombe 1998a's example


def test_newcombes_paired_interval_at_its_edges_and_by_symmetry() -> None:
    """Method 10 of Newcombe (1998): with every pair passing on both arms, each limit is
    the width of one arm's Wilson interval below 1, either way; swapping the arms negates
    the interval."""
    all_pass = [(True, True)] * PAIRS
    low, high = analysis.newcombe_paired(all_pass, 0.025)
    wilson_low, _ = analysis.wilson(PAIRS, PAIRS, 0.025)
    assert (low, high) == (pytest.approx(-(1 - wilson_low)), pytest.approx(1 - wilson_low))
    pairs = [(True, True)] * 90 + [(True, False)] * 7 + [(False, True)] * 3 + [(False, False)] * 20
    low, high = analysis.newcombe_paired(pairs, 0.025)
    swapped = analysis.newcombe_paired([(p, s) for s, p in pairs], 0.025)
    assert swapped == (pytest.approx(-high), pytest.approx(-low))
    assert low < -4 / 120 < high


def test_newcombes_paired_interval_covers_as_it_says() -> None:
    """Its two-sided 95% interval covers the true difference about 95% of the time at the
    slice's n, for correlated pairs: the coverage the decision rests on, checked."""
    rng = random.Random(7)
    covered = 0
    for _ in range(REPLICATES):
        pairs = []
        for _ in range(PAIRS):
            both = rng.random() < 0.85  # a shared draw: the pair's probe is easy or not
            pairs.append((both or rng.random() < 0.3, both or rng.random() < 0.2))
        low, high = analysis.newcombe_paired(pairs, 0.025)
        truth = (0.85 + 0.15 * 0.2) - (0.85 + 0.15 * 0.3)
        covered += low <= truth <= high
    assert 0.93 <= covered / REPLICATES <= 0.975, covered / REPLICATES


def test_the_tails_match_their_closed_forms() -> None:
    from ix.domain.intervals import t_cdf

    assert analysis.chi2_sf(11.0705, 5) == pytest.approx(0.05, abs=1e-4)  # the table's 5%
    assert analysis.chi2_sf(4.0, 2) == pytest.approx(math.exp(-2.0))
    assert analysis.chi2_sf(0.0, 5) == 1.0
    assert analysis.f_sf(3.0, 2, 40) == pytest.approx((1 + 2 * 3.0 / 40) ** -20)
    assert analysis.f_sf(2.5**2, 1, 30) == pytest.approx(2 * (1 - t_cdf(2.5, 30)), abs=1e-6)


def _trial(arm: str, run: int, probe: str, repeat: int, passed: bool, **more: Any) -> Any:
    return analysis.Trial(arm=arm, run=run, probe=probe, repeat=repeat, passed=passed, **more)


def test_trials_pair_by_run_probe_and_repeat_and_a_lone_one_is_left_out() -> None:
    s = [_trial("S", 1, "a", 1, True), _trial("S", 1, "a", 2, True), _trial("S", 2, "a", 1, False)]
    p = [_trial("P", 1, "a", 2, False), _trial("P", 1, "a", 1, True)]
    paired = analysis.pair(s, p)
    assert [(a.key, b.key, a.passed, b.passed) for a, b in paired] == [
        ((1, "a", 1), (1, "a", 1), True, True),
        ((1, "a", 2), (1, "a", 2), True, False),
    ]


def test_the_screen_keeps_the_probes_that_passed_every_pilot_trial() -> None:
    pilot = [_trial("S", 1, "a", r, True) for r in range(1, 6)]
    pilot += [_trial("S", 1, "b", r, r != 3) for r in range(1, 6)]
    pilot += [_trial("S", 1, "c", r, True) for r in range(1, 5)]  # four of five ran
    assert analysis.screen(pilot, repeats=5) == ("a",)


def test_the_declared_model_answered_every_session() -> None:
    answered = [_trial("S", 1, "a", 1, True, models=("claude-sonnet-4-5-20250929",))]
    assert analysis.answered_by(answered, "claude-sonnet-4-5")
    other = [*answered, _trial("P", 1, "a", 1, True, models=("claude-haiku-4-5",))]
    assert not analysis.answered_by(other, "claude-sonnet-4-5")
    assert not analysis.answered_by([_trial("S", 1, "a", 1, False)], "claude-sonnet-4-5")


def test_a_runs_tables_are_read_as_its_trials(tmp_path: Path) -> None:
    """The tables ix writes for a run (trials, interactions, readings, sessions) become
    the analysis's trials: a failed interaction is a failed trial, whatever it read."""
    rows: dict[str, list[dict[str, Any]]] = {
        "ix.v1.trial": [
            {"seq": 0, "repeat": 1, "probe": "a", "subject": "agent", "role": "subject"},
            {"seq": 1, "repeat": 1, "probe": "b", "subject": "agent", "role": "subject"},
        ],
        "ix.v1.interaction": [
            {"seq": 0, "sessions": [0], "failure": None},
            {"seq": 1, "sessions": [1], "failure": {"owner": "harness", "reason": "timeout"}},
        ],
        "ix.v1.reading": [
            {"seq": 0, "metric": "text_matched", "value": True, "failure": None},
            {"seq": 0, "metric": "tokens_total", "value": 120.0, "failure": None},
            {"seq": 0, "metric": "turns", "value": 2.0, "failure": None},
        ],
        "matrix.v1.session": [{"model": "claude-sonnet-4-5-x"}, {"model": None}],
    }
    for table, written in rows.items():
        (tmp_path / f"{table}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in written))
    first, second = analysis.load(tmp_path, arm="P", run=3)
    assert (first.key, first.passed, first.tokens, first.turns, first.models) == (
        (3, "a", 1), True, 120.0, 2.0, ("claude-sonnet-4-5-x",),
    )  # fmt: skip
    assert (second.key, second.passed, second.tokens, second.models) == (
        (3, "b", 1),
        False,
        None,
        (),
    )


def test_the_control_is_decided_on_its_latency() -> None:
    """The negative control pairs its latency p50 as the agent's tokens are paired, and is
    equivalent only when the arms read alike."""
    rng = random.Random(4)

    def runs(slower: float) -> list[Any]:
        return [
            _trial(arm, run, "get", repeat, True,
                   latency=40.0 * (slower if arm == "P" else 1.0) * math.exp(rng.gauss(0, 0.02)))
            for arm in ("S", "P") for run in range(1, 7) for repeat in range(1, 11)
        ]  # fmt: skip

    assert not analysis.control(runs(1.5)).holds  # queued at the server: slower, as expected
    assert analysis.control(runs(1.0)).holds


def test_the_command_decides_from_run_directories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``decide`` reads each run's tables, numbered in the order given, and prints the
    result; here the S arm is too short a baseline to be anything but inconclusive."""
    directories = []
    for name in ("s1", "p1"):
        run = tmp_path / name
        run.mkdir()
        tables: dict[str, list[dict[str, Any]]] = {
            "ix.v1.trial": [{"seq": 0, "repeat": 1, "probe": "a"}],
            "ix.v1.interaction": [{"seq": 0, "sessions": [0], "failure": None}],
            "ix.v1.reading": [{"seq": 0, "metric": "text_matched", "value": False}],
            "matrix.v1.session": [{"model": "claude-sonnet-4-5"}],
        }
        for table, rows in tables.items():
            (run / f"{table}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        directories.append(str(run))
    code = analysis.main(
        ["decide", "--model", "claude-sonnet-4-5", "--s", directories[0], "--p", directories[1]]
    )
    printed = json.loads(capsys.readouterr().out)
    assert code == 0 and printed["verdict"] == "inconclusive" and printed["unpaired"] == 0
    assert printed["inconclusive"][0].startswith("baseline")


# --- the simulation ------------------------------------------------------------------------


def _held(rate: float) -> Callable[[float], bool]:
    return lambda share: share >= rate


def _share(cases: int, decides: Callable[[random.Random], bool], seed: int) -> float:
    rng = random.Random(seed)
    return sum(decides(rng) for _ in range(cases)) / cases


def _non_inferior(p_s: float, p_p: float) -> Callable[[random.Random], bool]:
    def decides(rng: random.Random) -> bool:
        pairs = [(rng.random() < p_s, rng.random() < p_p) for _ in range(PAIRS)]
        return bool(analysis.non_inferior(pairs).holds)

    return decides


#: The most a rate set at 2.5% may read over 2,000 replicates and still be 2.5%: two
#: standard errors of the simulation above it. A test at its nominal level reads above it
#: half the time, so a bound of exactly 2.5% would fail on the seed, not on the test.
AT_MOST_NOMINAL = 0.025 + 2 * math.sqrt(0.025 * 0.975 / REPLICATES)


@pytest.mark.parametrize(
    ("p_s", "p_p", "verdict"),
    [(0.98, 0.88, "fails at least 80%"), (0.98, 0.93, "holds at most 2.5%")],
)
def test_the_pass_rate_decision_at_the_slices_n(p_s: float, p_p: float, verdict: str) -> None:
    """(b) a loss of twice the margin fails, (c) a loss of exactly the margin holds no more
    often than the one-sided 2.5% the test is set at. (a), no difference holding at least
    80% of the time, does not hold at this n (about 60% at 0.98): record §27 has the
    figures, and the n it needs is the owner's to set."""
    held = _share(REPLICATES, _non_inferior(p_s, p_p), seed=20261024)
    if verdict == "fails at least 80%":
        assert 1 - held >= 0.80, held
    else:
        assert held <= AT_MOST_NOMINAL, held


def _tokens(ratio: float, sigma: float = 0.15) -> Callable[[random.Random], bool]:
    """``sigma`` is the spread of the paired log ratio of one (probe, repeat): what the
    pairing leaves of a session's variation."""

    def decides(rng: random.Random) -> bool:
        pairs = []
        for _ in range(PAIRS):
            alone = 100.0 * math.exp(rng.gauss(0, 0.3))
            pairs.append((alone, alone * ratio * math.exp(rng.gauss(0, sigma))))
        return bool(analysis.equivalent_ratio(pairs).holds)

    return decides


@pytest.mark.parametrize(
    ("ratio", "verdict"),
    [(1.0, "holds at least 80%"), (1.10, "fails at least 80%"), (1.05, "holds at most 2.5%")],
)
def test_the_token_decision_at_the_slices_n(ratio: float, verdict: str) -> None:
    """(d) at sigma 0.15: a ratio of 1 is equivalent, one of 1.10 is not, and one at the
    margin is called equivalent no more often than 2.5%."""
    held = _share(REPLICATES, _tokens(ratio), seed=20261025)
    if verdict == "holds at least 80%":
        assert held >= 0.80, held
    elif verdict == "fails at least 80%":
        assert 1 - held >= 0.80, held
    else:
        assert held <= AT_MOST_NOMINAL, held


def _baseline(
    rng: random.Random, rates: list[float], log_shift: list[float] | None = None
) -> list[Any]:
    """Six S runs of 20 trials, run ``k`` passing at ``rates[k]`` and its tokens shifted by
    ``log_shift[k]``."""
    shifts = log_shift or [0.0] * 6
    return [
        _trial("S", run + 1, f"p{index % 10}", index // 10 + 1, rng.random() < rates[run],
               tokens=100.0 * math.exp(shifts[run] + rng.gauss(0, 0.15)), turns=1.0)
        for run in range(6)
        for index in range(20)
    ]  # fmt: skip


def _trips(
    rule: str, rates: list[float], log_shift: list[float] | None = None
) -> Callable[..., bool]:
    def decides(rng: random.Random) -> bool:
        return any(
            reason.startswith(rule)
            for reason in analysis.inconclusive(_baseline(rng, rates, log_shift))
        )

    return decides


def test_drift_between_runs_trips_the_dispersion_rule() -> None:
    """(e) the S runs' pass rate stepping down part way through, or their tokens climbing
    4% run on run, is dispersion; the rule calls the slice inconclusive on either. (A pass
    rate falling gradually from 0.99 to 0.82 trips it only about 40% of the time at 20
    trials a run: record §27.)"""
    stepped = [1.0, 1.0, 1.0, 0.80, 0.80, 0.80]
    assert _share(REPLICATES, _trips("dispersion", stepped), seed=1) >= 0.80
    climbing = [0.0, 0.04, 0.08, 0.12, 0.16, 0.20]
    assert _share(REPLICATES, _trips("dispersion", [0.98] * 6, climbing), seed=2) >= 0.80


def test_a_baseline_below_the_floor_trips_its_rule() -> None:
    """(e) a baseline pass rate of 0.95 trips the 0.97 floor."""
    assert _share(REPLICATES, _trips("baseline", [0.95] * 6), seed=3) >= 0.80
