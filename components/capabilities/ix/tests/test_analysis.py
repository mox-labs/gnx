"""The analysis: what readings say about the decision a design declares.

Pure, and deterministic. Every table here is built by hand, so each test says exactly what
was measured and what must follow from it. ``test_calibration.py`` holds the methods to
their stated error rates by simulation.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pytest

from ix.application.experiments import examine
from ix.domain import analysis as stats
from ix.domain import intervals
from ix.domain.analysis import Analysis, analyze
from ix.domain.errors import ConfigError
from ix.domain.genre import AnalysisRef
from ix.domain.run import Failure, Interaction, Reading, Trial, schedule

if TYPE_CHECKING:
    from ix.application.experiments import Vocabulary
    from ix.domain.design import Design

#: Per-trial wobble, in order of use: a fixed pattern, so tables are the same every time.
_WOBBLE = (0.0, 0.4, -0.3, 0.2, -0.4, 0.1, -0.1, 0.3, -0.2, 0.05)


class Tables:
    """A design, its schedule, and readings written by a rule."""

    def __init__(self, data: dict[str, Any], vocabulary: Vocabulary) -> None:
        examined = examine(data, vocabulary, base=".")
        assert examined.problems == (), examined.problems
        assert examined.experiment and examined.design and examined.probes
        self.design: Design = examined.design
        self.trials: tuple[Trial, ...] = schedule(
            examined.experiment, examined.design, examined.probes
        )
        self.interactions: list[Interaction] = []
        self.readings: list[Reading] = []

    def measure(
        self,
        values: dict[str, float | bool | tuple[int, int]],
        *,
        metric: str = "ttfb",
        wobble: float = 1.0,
        fail: dict[int, str] | None = None,
    ) -> Tables:
        """Give every trial of each subject its value (role ``calibration`` reads the
        control's), plus the fixed wobble scaled by ``wobble``. ``fail`` maps a trial's
        position to the owner of the failure it ends in."""
        declared = self.design.metric(metric)
        assert declared is not None
        for trial in self.trials:
            owner = (fail or {}).get(trial.seq)
            self.interactions.append(
                Interaction(
                    seq=trial.seq,
                    subject=trial.subject,
                    role=trial.role,
                    started_ns=0,
                    duration_ns=1,
                    failure=None if owner is None else Failure(owner=owner, reason="test"),  # type: ignore[arg-type]
                )
            )
            if owner is not None:
                continue
            value = values[trial.subject]
            reading = Reading(
                seq=trial.seq,
                subject=trial.subject,
                role=trial.role,
                sensor="ix.v1.sensor.test",
                metric=metric,
                shape=declared.shape,
                unit=declared.unit,
            )
            if isinstance(value, tuple):
                events, units = value
                counted = {"events": float(events), "units": float(units)}
                reading = reading.model_copy(update={"statistics": counted})
            elif isinstance(value, bool):
                reading = reading.model_copy(update={"value": value})
            else:
                number = value + _WOBBLE[trial.seq % len(_WOBBLE)] * wobble
                if declared.shape == "scalar":
                    reading = reading.model_copy(update={"value": number})
                else:
                    reading = reading.model_copy(update={"statistics": {"p99": number}})
            self.readings.append(reading)
        return self

    def analyze(self) -> Analysis:
        return analyze(self.design, self.trials, self.interactions, self.readings)


def _compare(compare_data: dict[str, Any], vocabulary: Vocabulary, **changes: Any) -> Tables:
    data = {**compare_data, **changes}
    if data.get("margin") is None:
        data.pop("margin", None)
    return Tables(data, vocabulary)


class TestTheNumbers:
    def test_wilson_does_not_call_nine_of_nine_certain(self) -> None:
        low, high = intervals.wilson(9, 9)(0.05)
        assert low == pytest.approx(0.7008, abs=1e-3) and high == 1.0
        low, high = intervals.wilson(5, 10)(0.05)
        assert (low, high) == pytest.approx((0.2366, 0.7634), abs=1e-3)

    @pytest.mark.parametrize(
        ("df", "quantile"), [(1, 12.7062), (2, 4.3027), (4, 2.7764), (9, 2.2622), (29, 2.0452)]
    )
    def test_the_t_quantile_is_the_printed_tables(self, df: int, quantile: float) -> None:
        assert intervals.t_quantile(0.975, df) == pytest.approx(quantile, abs=1e-4)
        assert intervals.t_cdf(quantile, df) == pytest.approx(0.975, abs=1e-5)

    def test_a_level_in_closed_form_is_the_level_a_search_finds(self) -> None:
        """Each method that inverts a test gives its level as the test's p-value; it must be
        the level at which its own interval first excludes the bound."""
        spans = [
            intervals.mean([1.0, 2.0, 4.0, 3.5, 2.2]),
            intervals.median([5.0, 1.0, 3.0, 2.0, 4.0, 7.0, 6.0, 9.0]),
            intervals.wilson(9, 9),
            intervals.clustered_rate([3, 10, 0, 5, 2], [1000] * 5),
            intervals.ratio([11, 12, 10.5, 11.4, 12.2], [10, 10.2, 9.8, 10.1, 10.4]),
        ]
        bounds = [1.0, 1.5, 0.5, 0.001, 1.0]
        for span, bound in zip(spans, bounds, strict=True):
            searched = intervals._bisect(lambda alpha, s=span, b=bound: s(alpha)[0] > b)
            assert span.above(bound) == pytest.approx(searched, rel=1e-6)

    def test_the_search_stops_at_its_floor(self) -> None:
        assert intervals._bisect(lambda alpha: False) == math.inf
        assert intervals._bisect(lambda alpha: True) == intervals.LEVEL_FLOOR
        assert intervals._bisect(lambda alpha: alpha >= 0.2) == pytest.approx(0.2, rel=1e-6)

    def test_a_ratio_is_unbounded_when_its_denominator_is_not_shown_to_be_away_from_zero(
        self,
    ) -> None:
        assert intervals.ratio([1.0, 2.0, 3.0], [0.1, -0.1, 0.05])(0.05) == (-math.inf, math.inf)

    def test_a_median_needs_six_values_at_95_percent(self) -> None:
        assert intervals.median([1.0, 2.0, 3.0, 4.0, 5.0])(0.05) == (-math.inf, math.inf)
        assert intervals.median([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])(0.05) == (1.0, 6.0)

    def test_the_estimators(self) -> None:
        values = [1.0, 2.0, 3.0, 10.0]
        assert stats._COMBINE["mean"](values) == 4.0
        assert stats._COMBINE["median"](values) == 2.5 and stats._median([3.0, 1.0, 2.0]) == 2.0
        assert stats._COMBINE["min"](values) == 1.0 and stats._COMBINE["max"](values) == 10.0


class TestCompare:
    """ttfb, lower is better; the control is ``buffered`` at 100 ms."""

    def test_identical_subjects_are_inconclusive_under_superiority(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        found = (
            _compare(compare_data, vocabulary, comparison="superiority", margin=None)
            .measure({"buffered": 100.0, "streaming": 100.0})
            .analyze()
        )
        assert found.outcome == "inconclusive"
        (finding,) = found.findings
        (decision,) = finding.decisions
        assert decision.form == "superiority" and decision.outcome == "inconclusive"
        assert decision.interval is not None and decision.interval.low < 0 < decision.interval.high
        assert decision.method == "fieller"
        assert [check.stable for check in found.calibration] == [True]

    def test_a_decision_not_reached_keeps_its_level_and_still_counts_in_the_family(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        found = (
            _compare(compare_data, vocabulary, comparison="superiority", margin=None)
            .measure({"buffered": 100.0, "streaming": 100.0})
            .analyze()
        )
        (decision,) = found.findings[0].decisions
        assert decision.decided_at is not None and decision.allowed is not None
        assert decision.decided_at > decision.allowed
        assert decision.reason == "the interval crosses the boundary"
        assert found.family == 1

    def test_identical_subjects_hold_under_non_inferiority_with_a_margin_above_the_noise(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        found = _compare(compare_data, vocabulary).measure(
            {"buffered": 100.0, "streaming": 100.0}
        ).analyze()  # fmt: skip
        assert found.outcome == "holds"
        assert found.findings[0].decisions[0].claim == "at most 5% worse than buffered"

    @pytest.mark.parametrize(
        ("comparison", "margin"), [("superiority", None), ("non-inferiority", "5%")]
    )
    def test_a_variant_ten_percent_slower_fails(
        self,
        comparison: str,
        margin: str | None,
        compare_data: dict[str, Any],
        vocabulary: Vocabulary,
    ) -> None:
        found = (
            _compare(compare_data, vocabulary, comparison=comparison, margin=margin)
            .measure({"buffered": 100.0, "streaming": 110.0})
            .analyze()
        )
        assert found.outcome == "fails"
        decision = found.findings[0].decisions[0]
        assert decision.unit == "%" and decision.better == "lower"
        assert decision.difference == pytest.approx(10.0, abs=0.5)  # the variant minus the control
        assert (
            decision.decided_at is not None
            and decision.allowed is not None
            and decision.decided_at <= decision.allowed
        )

    def test_a_variant_ten_percent_faster_holds_under_superiority(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        found = (
            _compare(compare_data, vocabulary, comparison="superiority", margin=None)
            .measure({"buffered": 100.0, "streaming": 90.0})
            .analyze()
        )
        assert found.outcome == "holds"

    def test_a_loss_between_nothing_and_the_margin_holds_and_one_at_the_margin_does_not_decide(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        inside = _compare(compare_data, vocabulary).measure({"buffered": 100.0, "streaming": 102.0})
        assert inside.analyze().outcome == "holds"
        at = _compare(compare_data, vocabulary).measure({"buffered": 100.0, "streaming": 105.0})
        assert at.analyze().outcome == "inconclusive"

    def test_a_difference_inside_the_noise_of_the_trials_decides_nothing(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """The variant is 1% faster on every trial. With quiet trials that is a finding;
        when the trials vary by more than that, it is not. Changed requirement: the noise
        widens the interval itself, through the spread of the per-block differences; no
        noise floor is added to it."""
        quiet = (
            _compare(compare_data, vocabulary, comparison="superiority", margin=None)
            .measure({"buffered": 100.0, "streaming": 99.0}, wobble=0.1)
            .analyze()
        )
        assert quiet.outcome == "holds"
        noisy = (
            _compare(compare_data, vocabulary, comparison="superiority", margin=None)
            .measure({"buffered": 100.0, "streaming": 99.0}, wobble=4.0)
            .analyze()
        )
        assert noisy.outcome == "inconclusive"
        (decision,) = noisy.findings[0].decisions
        assert decision.interval is not None and decision.unit == "%"
        assert decision.interval.low < 0 < decision.interval.high

    def test_equivalence(self, compare_data: dict[str, Any], vocabulary: Vocabulary) -> None:
        def outcome(variant: float) -> str | None:
            return (
                _compare(compare_data, vocabulary, comparison="equivalence")
                .measure({"buffered": 100.0, "streaming": variant})
                .analyze()
                .outcome
            )

        assert outcome(100.5) == "holds" and outcome(112.0) == "fails" and outcome(88.0) == "fails"

    def test_the_same_tables_give_the_same_analysis(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary).measure({"buffered": 100.0, "streaming": 103.0})
        assert tables.analyze() == tables.analyze()

    def test_estimates_cover_every_subject_and_the_calibration_sample(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        found = _compare(compare_data, vocabulary).measure(
            {"buffered": 100.0, "streaming": 110.0}
        ).analyze()  # fmt: skip
        by_role = {estimate.role: estimate for estimate in found.estimates}
        assert set(by_role) == {"control", "variant", "calibration"}
        variant = by_role["variant"]
        assert variant.n == 10 and variant.statistic == "p99" and variant.estimator == "mean"
        assert variant.value == pytest.approx(110.0, abs=0.5) and variant.unit == "ms"
        assert variant.interval is not None
        assert variant.interval.low <= variant.value <= variant.interval.high


class TestFailuresAndThinData:
    def test_a_trial_the_harness_failed_is_left_out_and_counted(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary)
        lost = next(trial.seq for trial in tables.trials if trial.role == "variant")
        found = tables.measure(
            {"buffered": 100.0, "streaming": 110.0}, fail={lost: "harness"}
        ).analyze()
        assert found.outcome == "fails"  # nine blocks still say so
        assert found.findings[0].decisions[0].n == 9
        assert [(count.owner, count.count) for count in found.failures] == [("harness", 1)]

    def test_a_trial_the_variant_failed_fails_it_whatever_the_rest_measured(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary)
        lost = next(trial.seq for trial in tables.trials if trial.role == "variant")
        found = tables.measure(
            {"buffered": 100.0, "streaming": 90.0}, fail={lost: "subject"}
        ).analyze()
        assert found.outcome == "fails"
        assert found.findings[0].reason == "the subject failed 1 of 10 trial(s)"

    def test_a_trial_the_control_failed_leaves_nothing_sound_to_compare_with(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary)
        lost = next(trial.seq for trial in tables.trials if trial.role == "control")
        found = tables.measure(
            {"buffered": 100.0, "streaming": 110.0}, fail={lost: "subject"}
        ).analyze()
        assert found.outcome == "inconclusive"
        assert found.findings[0].reason == "the control failed 1 of 20 trial(s)"

    def test_a_calibration_turn_the_control_failed_leaves_nothing_sound_to_compare_with(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary)
        assert tables.design.calibration == "aa"
        lost = [trial.seq for trial in tables.trials if trial.role == "calibration"][:2]
        found = tables.measure(
            {"buffered": 100.0, "streaming": 100.0}, fail=dict.fromkeys(lost, "subject")
        ).analyze()
        assert found.outcome == "inconclusive"
        assert found.findings[0].reason == "the control failed 2 of 20 trial(s)"

    def test_too_few_usable_trials_decide_nothing(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary, repeats=5)
        lost = [trial.seq for trial in tables.trials if trial.role == "variant"][:3]
        found = tables.measure(
            {"buffered": 100.0, "streaming": 150.0}, fail=dict.fromkeys(lost, "harness")
        ).analyze()
        decision = found.findings[0].decisions[0]
        assert found.outcome == "inconclusive" and decision.decided_at is None
        assert decision.reason == "too few usable blocks (2; 3 needed)"

    def test_no_readings_at_all_is_inconclusive_not_an_error(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary)
        found = analyze(tables.design, tables.trials, [], [])
        assert found.outcome == "inconclusive" and found.family == 1


class TestGuardrailsAndCorrection:
    def test_a_guardrail_that_fails_fails_the_finding(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(
            compare_data, vocabulary, guardrails=[{"metric": "throughput", "margin": "5%"}]
        )
        tables.measure({"buffered": 100.0, "streaming": 100.0})
        tables.interactions.clear()  # the second metric of the same trials
        tables.measure({"buffered": 80.0, "streaming": 60.0}, metric="throughput")
        found = tables.analyze()
        primary, guardrail = found.findings[0].decisions
        assert (primary.part, primary.outcome) == ("primary", "holds")
        assert (guardrail.part, guardrail.outcome) == ("guardrail", "fails")
        assert guardrail.better == "higher" and guardrail.difference == pytest.approx(-25.0, abs=1)
        assert found.outcome == "fails" and found.family == 2

    def test_holds_is_read_at_the_full_level_and_fails_is_corrected(self) -> None:
        """Changed requirement: Holm's procedure applied to both sides, so a decision that
        held at 0.03 alone did not hold beside another. The verdict holds only if every
        decision holds (an intersection-union test), so ``holds`` takes no correction; it
        fails if any fails, so ``fails`` is corrected across the family."""
        decision = stats.Decision(
            part="primary", metric="m", statistic="value", estimator="mean",
            form="superiority", claim="better",
        )  # fmt: skip
        pair = [stats._Pending(decision, "holds", 0.03), stats._Pending(decision, "holds", 0.04)]
        settled = stats._settle(pair)
        assert [settled[item].outcome for item in pair] == ["holds", "holds"]
        assert {settled[item].allowed for item in pair} == {0.05}

        failing = [stats._Pending(decision, "fails", 0.03), stats._Pending(decision, "fails", 0.04)]
        settled = stats._settle(failing)
        assert [settled[item].outcome for item in failing] == ["inconclusive", "inconclusive"]
        assert "corrected with 1 other" in settled[failing[0]].reason

        alone = [stats._Pending(decision, "fails", 0.03)]
        (single,) = stats._settle(alone).values()
        assert single.outcome == "fails" and single.allowed == 0.05

        mixed = [stats._Pending(decision, "fails", 0.02), stats._Pending(decision, "holds", 0.04)]
        settled = stats._settle(mixed)
        assert [settled[item].outcome for item in mixed] == ["fails", "holds"]
        assert settled[mixed[0]].allowed == 0.025  # the first of two steps

    def test_every_variant_in_every_cell_is_one_of_the_family(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        compare_data["factors"] = {"size": ["1MiB", "1GiB"]}
        compare_data["probes"][0]["target"] = "{url}/{size}"
        compare_data["subjects"]["chunked"] = dict(compare_data["subjects"]["streaming"])
        found = (
            _compare(compare_data, vocabulary)
            .measure({"buffered": 100.0, "streaming": 100.0, "chunked": 120.0})
            .analyze()
        )
        assert found.family == 4 and len(found.findings) == 4
        assert {(f.subject, f.outcome) for f in found.findings} == {
            ("streaming", "holds"),
            ("chunked", "fails"),
        }
        assert found.outcome == "fails"  # it holds only if every finding does

    def test_a_sibling_the_harness_lost_stays_in_the_family(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Missing data never loosens the correction for the subjects that were measured."""
        compare_data["subjects"]["chunked"] = dict(compare_data["subjects"]["streaming"])
        values = {"buffered": 100.0, "streaming": 100.0, "chunked": 100.0}
        measured = _compare(compare_data, vocabulary).measure(values).analyze()
        tables = _compare(compare_data, vocabulary)
        lost = {trial.seq: "harness" for trial in tables.trials if trial.subject == "chunked"}
        missing = tables.measure(values, fail=lost).analyze()

        def decision(found: Analysis, subject: str) -> stats.Decision:
            (finding,) = [f for f in found.findings if f.subject == subject]
            return finding.decisions[0]

        assert missing.family == measured.family == 2
        assert decision(missing, "streaming") == decision(measured, "streaming")
        assert decision(missing, "chunked").allowed is not None
        assert decision(missing, "chunked").reason.startswith("too few usable blocks")

    def test_a_decision_far_beyond_the_boundary_reports_its_tests_level(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Changed requirement: a percentile bootstrap could not resolve a level below
        2 / (resamples - 1), so such a decision reported that bound. Fieller's interval
        inverts the paired t test, so its level is that test's p-value, however small."""
        found = (
            _compare(compare_data, vocabulary, comparison="superiority", margin=None)
            .measure({"buffered": 100.0, "streaming": 300.0})
            .analyze()
        )
        (decision,) = found.findings[0].decisions
        assert decision.outcome == "fails"
        assert decision.decided_at is not None and decision.decided_at < 1e-10


class TestEvaluate:
    def _tables(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary, repeats: int
    ) -> Tables:
        return Tables({**evaluate_data, "repeats": repeats}, vocabulary)

    def test_nine_passes_out_of_nine_do_not_show_ninety_percent(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        found = (
            self._tables(evaluate_data, vocabulary, 9)
            .measure({"catalog": True}, metric="activated_correctly")
            .analyze()
        )
        decision = found.findings[0].decisions[0]
        assert found.outcome == "inconclusive" and decision.form == "threshold"
        assert decision.difference == 1.0 and decision.interval is not None
        assert decision.interval.low == pytest.approx(0.7008, abs=1e-3)

    def test_sixty_out_of_sixty_do_and_none_out_of_nine_fail(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        holds = (
            self._tables(evaluate_data, vocabulary, 60)
            .measure({"catalog": True}, metric="activated_correctly")
            .analyze()
        )
        assert holds.outcome == "holds"
        fails = (
            self._tables(evaluate_data, vocabulary, 9)
            .measure({"catalog": False}, metric="activated_correctly")
            .analyze()
        )
        assert fails.outcome == "fails" and fails.calibration == ()


class TestWhatNeverDecides:
    def test_a_judged_metric_is_inconclusive_even_if_a_design_holds_one(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary).measure({"buffered": 100.0, "streaming": 150.0})
        judged = tuple(m.model_copy(update={"judged": True}) for m in tables.design.metrics)
        tables.design = tables.design.model_copy(update={"metrics": judged})
        found = tables.analyze()
        assert found.outcome == "inconclusive"
        assert found.findings[0].decisions[0].reason == "judged by a model: it never decides"

    def test_a_genre_that_gives_estimates_only_has_no_outcome(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary).measure({"buffered": 100.0, "streaming": 150.0})
        tables.design = tables.design.model_copy(update={"verdict_form": "none"})
        found = tables.analyze()
        assert found.outcome is None and found.findings == () and len(found.estimates) == 3

    def test_an_analysis_this_ix_does_not_have_is_refused(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary)
        tables.design = tables.design.model_copy(
            update={"analysis": AnalysisRef(id="acme.v1.analysis.sequential")}
        )
        with pytest.raises(ConfigError, match="no analysis"):
            tables.analyze()

    def test_a_control_of_zero_has_no_relative_difference(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        found = (
            _compare(compare_data, vocabulary)
            .measure({"buffered": 0.0, "streaming": 1.0}, wobble=0.0)
            .analyze()
        )
        assert found.outcome == "inconclusive"
        assert "no relative difference" in found.findings[0].decisions[0].reason
        assert not any(math.isnan(e.value or 0.0) for e in found.estimates)


class TestCountsMediansAndTheCheck:
    def _guarded(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary, guardrail: dict[str, Any]
    ) -> Tables:
        tables = _compare(compare_data, vocabulary, guardrails=[guardrail])
        tables.measure({"buffered": 100.0, "streaming": 100.0})
        tables.interactions.clear()  # the second metric of the same trials
        return tables

    def test_zero_errors_bound_the_error_rate_above_zero_and_still_hold(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """The defect behind this: an error rate was one number per trial, bootstrapped, so
        clean trials gave [0, 0] and held a threshold as if certain. As counts, ten clean
        trials of 1000 requests bound it near 3.8 / 10000, above zero, and hold a bound of
        1%."""
        tables = self._guarded(
            compare_data,
            vocabulary,
            {"metric": "error_rate", "threshold": {"op": "<=", "value": 0.01}},
        )
        found = tables.measure(
            {"buffered": (0, 1000), "streaming": (0, 1000)}, metric="error_rate"
        ).analyze()
        guardrail = found.findings[0].decisions[1]
        assert guardrail.method == "clustered wilson" and guardrail.outcome == "holds"
        assert guardrail.interval is not None and guardrail.interval.low == 0.0
        assert guardrail.interval.high == pytest.approx(3.84 / 10_003.84, rel=1e-2)

    def test_an_error_rate_above_its_bound_fails(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = self._guarded(
            compare_data,
            vocabulary,
            {"metric": "error_rate", "threshold": {"op": "<=", "value": 0.01}},
        )
        found = tables.measure(
            {"buffered": (0, 1000), "streaming": (50, 1000)}, metric="error_rate"
        ).analyze()
        assert found.findings[0].decisions[1].outcome == "fails" and found.outcome == "fails"

    def test_a_rate_compared_in_points(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = self._guarded(compare_data, vocabulary, {"metric": "error_rate", "margin": "1pp"})
        found = tables.measure(
            {"buffered": (10, 1000), "streaming": (12, 1000)}, metric="error_rate"
        ).analyze()
        guardrail = found.findings[0].decisions[1]
        assert guardrail.unit == "pp" and guardrail.method == "newcombe (clustered wilson)"
        assert guardrail.difference == pytest.approx(0.2) and guardrail.outcome == "holds"

    def test_a_median_compares_the_per_block_differences(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        compare_data["primary"]["estimator"] = "median"
        found = (
            _compare(compare_data, vocabulary, comparison="superiority", margin=None)
            .measure({"buffered": 100.0, "streaming": 110.0})
            .analyze()
        )
        (decision,) = found.findings[0].decisions
        assert decision.method == "order statistics" and decision.outcome == "fails"

    def test_a_maximum_never_decides(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary).measure({"buffered": 100.0, "streaming": 150.0})
        primary = tables.design.primary
        assert primary is not None
        tables.design = tables.design.model_copy(
            update={"primary": primary.model_copy(update={"estimator": "max"})}
        )
        (decision,) = tables.analyze().findings[0].decisions
        assert decision.outcome == "inconclusive" and decision.reason.startswith("min and max")

    def test_every_trial_reading_the_same_number_decides_nothing(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        evaluate_data["primary"] = {"metric": "tokens_total"}
        evaluate_data["threshold"] = {"op": "<", "value": 1000}
        evaluate_data["probes"][0].pop("truth")
        tables = Tables({**evaluate_data, "repeats": 5}, vocabulary)
        found = tables.measure({"catalog": 500.0}, metric="tokens_total", wobble=0.0).analyze()
        (decision,) = found.findings[0].decisions
        assert decision.outcome == "inconclusive" and "no spread" in decision.reason

    def test_a_control_that_differs_from_itself_makes_the_run_inconclusive(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Changed requirement: the A/A pass widened every interval by its far end. It is a
        check now: the verdict would be ``fails``, but the control's second sample reads 20%
        above its first, so nothing compared in that environment is relied on."""
        tables = _compare(compare_data, vocabulary)
        tables.measure({"buffered": 100.0, "streaming": 120.0})
        calibration = {trial.seq for trial in tables.trials if trial.role == "calibration"}
        tables.readings = [
            reading.model_copy(update={"statistics": {"p99": reading.statistics["p99"] * 1.2}})
            if reading.seq in calibration
            else reading
            for reading in tables.readings
        ]
        found = tables.analyze()
        assert found.findings[0].outcome == "fails"
        assert found.outcome == "inconclusive" and found.reason.startswith("calibration:")
        (check,) = found.calibration
        assert check.stable is False and check.confidence == pytest.approx(0.99)

    def test_a_check_that_cannot_be_made_leaves_the_run_inconclusive(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        tables = _compare(compare_data, vocabulary)
        lost = {trial.seq: "harness" for trial in tables.trials if trial.role == "calibration"}
        found = tables.measure({"buffered": 100.0, "streaming": 100.0}, fail=lost).analyze()
        assert found.findings[0].outcome == "holds"
        (check,) = found.calibration
        assert check.stable is None and "could not be made" in check.reason
        assert found.outcome == "inconclusive" and "could not be made" in found.reason


def test_a_run_whose_every_block_read_the_same_gives_no_spread(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    """Regression: a spread of 0 made the plan say a run could detect a difference of 0."""
    flat = _compare(compare_data, vocabulary).measure(
        {"buffered": 100.0, "streaming": 100.0}, wobble=0.0
    )
    assert stats.spread(flat.design, flat.trials, flat.interactions, flat.readings) is None
    varied = _compare(compare_data, vocabulary).measure({"buffered": 100.0, "streaming": 100.0})
    found = stats.spread(varied.design, varied.trials, varied.interactions, varied.readings)
    assert found is not None and found.sd > 0
