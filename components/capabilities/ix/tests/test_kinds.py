"""Kinds are declared data: what each subject kind, probe kind, sensor and environment kind
takes, does, offers and needs.

The requirements pinned here: an experiment is held to those declarations (options, fit,
one provider per metric); effects and ``judged`` come from the declarations and never from
the author; a kind is a data file a third party can ship.
"""

from __future__ import annotations

from importlib import metadata
from typing import TYPE_CHECKING, Any, get_args

import pytest
import yaml
from pydantic import ValidationError

import ix
from ix.adapters._out.matrix_documents import KIND_GROUP, KindFiles
from ix.composition import type_url_problem
from ix.domain.design import select_sensors
from ix.domain.experiment import Experiment, probe_set
from ix.domain.kind import EFFECTS, EffectName, Kind, Kinds, check_options, kinds, seconds
from ix.domain.schema import DeclarationFile
from ix.domain.validation import check, parse

if TYPE_CHECKING:
    from pathlib import Path

    from ix.domain.genre import Genres

Write = Any  # the write_experiment fixture

#: The kinds DECISIONS §2 names, by family.
EXPECTED = {
    "subject": {"process", "http", "agent-session"},
    "probe": {"prompt", "http-load", "inspect-task"},
    "sensor": {
        "timing",
        "memory",
        "activation",
        "function-test",
        "usage",
        "judge",
        "tool-usage",
        "outcome",
        "text-match",
    },  # fmt: skip
    "environment": {"none", "process", "compose"},
}


def _load(data: dict[str, Any]) -> Experiment:
    experiment, problems = parse(data)
    assert experiment is not None, problems
    return experiment


def _found(data: dict[str, Any], genres: Genres, declared: Kinds) -> set[tuple[str, str]]:
    experiment = _load(data)
    assert isinstance(experiment.probes, tuple)
    checked = check(experiment, genres, declared, probe_set(experiment.probes))
    return {(p.path, p.kind) for p in checked.problems}


def _sensor(name: str, metric: str = "ttfb", **changes: Any) -> dict[str, Any]:
    return {
        "name": f"lab.v1.sensor.{name}",
        "summary": "A test sensor.",
        "needs": ["load"],
        "metrics": [{"name": metric, "unit": "ms", "direction": "lower", "shape": "histogram"}],
        **changes,
    }


def _with(builtin: Kinds, *extra: dict[str, Any]) -> Kinds:
    files = [*KindFiles(packages=["ix.builtin.kinds"]).files()]
    files += [DeclarationFile(origin=f"lab-{i}.yaml", data=data) for i, data in enumerate(extra)]
    found = kinds(files, grammar=type_url_problem)
    assert found.rejected == {}, found.rejected
    assert len(found.kinds) == len(builtin.kinds) + len(extra)
    return found


# --- the declarations ------------------------------------------------------------------


class TestBuiltInKinds:
    def test_every_kind_the_plan_names_is_declared(self, builtin_kinds: Kinds) -> None:
        assert builtin_kinds.rejected == {}
        for family, names in EXPECTED.items():
            assert set(builtin_kinds.names(family)) == names, family

    def test_a_kind_is_named_by_type_url_and_written_short(self, builtin_kinds: Kinds) -> None:
        process = builtin_kinds.get("subject", "process")
        assert process is not None
        assert (process.name, process.label, process.family) == (
            "ix.v1.subject.process", "process", "subject",
        )  # fmt: skip
        assert builtin_kinds.get("subject", "ix.v1.subject.process") is process
        assert builtin_kinds.get("environment", "process") is not process

    def test_options_are_declared_with_types_and_which_are_required(
        self, builtin_kinds: Kinds
    ) -> None:
        load = builtin_kinds.get("probe", "http-load")
        assert load is not None
        assert load.required({}) == ("target", "load_model", "duration")
        assert load.required({"load_model": "open"}) == ("target", "load_model", "duration", "rate")
        assert load.options["load_model"].allowed == ("open", "closed")
        assert load.options["duration"].type == "duration"
        assert load.duration == ("warmup", "duration")
        for kind in builtin_kinds.kinds.values():
            for name, option in kind.options.items():
                assert option.description, f"{kind.label}.{name}"

    def test_effects_are_a_closed_vocabulary(self, builtin_kinds: Kinds) -> None:
        assert set(get_args(EffectName)) == set(EFFECTS)  # every effect a kind may name is told
        for kind in builtin_kinds.kinds.values():
            assert set(kind.effects) <= set(EFFECTS), kind.name
        declared = {k.label: set(k.effects) for k in builtin_kinds.of("subject")}
        assert declared == {
            "process": {"process"}, "http": {"network"},
            "agent-session": {"spend", "tools", "process", "filesystem"},
        }  # fmt: skip
        with pytest.raises(ValidationError):
            Kind.model_validate(_sensor("x", effects=["telepathy"]))

    def test_subjects_offer_and_probes_need(self, builtin_kinds: Kinds) -> None:
        offers = {k.label: set(k.offers) for k in builtin_kinds.of("subject")}
        needs = {k.label: set(k.needs) for k in builtin_kinds.of("probe")}
        assert offers == {
            "process": {"pid", "url"},
            "http": {"url", "headers"},
            "agent-session": {"agent", "workdir"},
        }
        assert needs == {"prompt": {"agent"}, "http-load": {"url"}, "inspect-task": {"agent"}}

    def test_a_sensor_declares_its_metrics_and_whether_a_model_judges_them(
        self, builtin_kinds: Kinds
    ) -> None:
        judged = {
            metric.name: metric.judged
            for sensor in builtin_kinds.of("sensor")
            for metric in sensor.metrics
        }
        assert {name for name, is_judged in judged.items() if is_judged} == {
            "judged_score", "judged_pass", "judge_agreement", "judge_inconsistent",
        }  # fmt: skip
        assert judged["activated_correctly"] is False and judged["ttfb"] is False
        activation = builtin_kinds.get("sensor", "activation")
        assert activation is not None and activation.truth == ("expectation", "expected_skill")

    def test_no_metric_has_two_built_in_providers(self, builtin_kinds: Kinds) -> None:
        names = [m.name for sensor in builtin_kinds.of("sensor") for m in sensor.metrics]
        assert len(names) == len(set(names))

    def test_only_a_sensor_declares_metrics_and_only_a_probe_a_duration(self) -> None:
        with pytest.raises(ValidationError, match="only a sensor"):
            Kind.model_validate({**_sensor("x"), "name": "lab.v1.probe.x"})
        with pytest.raises(ValidationError, match="at least one metric"):
            Kind.model_validate({"name": "lab.v1.sensor.x", "summary": "s"})
        with pytest.raises(ValidationError, match="only a probe"):
            Kind.model_validate(_sensor("x", options={"t": {"type": "duration"}}, duration=["t"]))
        with pytest.raises(ValidationError, match="not a duration option"):
            Kind.model_validate({"name": "lab.v1.probe.x", "summary": "s", "duration": ["length"]})
        with pytest.raises(ValidationError, match="type URL"):
            Kind.model_validate({"name": "lab.v1.genre.x", "summary": "s"})

    def test_a_declaration_is_data_and_holds_no_code(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            Kind.model_validate(_sensor("x", implementation="acme.sensors:Timing"))

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("60s", 60.0), ("2m", 120.0), ("500ms", 0.5), ("1h", 3600.0), (15, 15.0),
         ("soon", None), (True, None), ("10/s", None)],
    )  # fmt: skip
    def test_durations(self, value: Any, expected: float | None) -> None:
        assert seconds(value) == expected


# --- options ---------------------------------------------------------------------------


class TestOptions:
    def test_an_unknown_option_is_refused_with_the_ones_there_are(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        """A typo beside ``kind`` used to be swept into the options and accepted."""
        compare_data["subjects"]["buffered"]["comand"] = ["./proxy"]
        experiment = _load(compare_data)
        assert isinstance(experiment.probes, tuple)
        problems = check(
            experiment, builtin_genres, builtin_kinds, probe_set(experiment.probes)
        ).problems
        (problem,) = [p for p in problems if p.kind == "option_unknown"]
        assert problem.path == "subjects.buffered.comand" and problem.got == "comand"
        assert "command" in problem.allowed and "command" in problem.fix

    def test_a_typo_in_effects_cannot_empty_the_plan(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        compare_data["subjects"]["buffered"]["efects"] = ["starts a proxy"]
        assert ("subjects.buffered.efects", "option_unknown") in _found(
            compare_data, builtin_genres, builtin_kinds
        )

    def test_a_missing_required_option(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        del compare_data["subjects"]["streaming"]["command"]
        del compare_data["probes"][0]["load_model"]
        found = _found(compare_data, builtin_genres, builtin_kinds)
        assert ("subjects.streaming.command", "option_missing") in found
        assert ("probes.0.load_model", "option_missing") in found

    @pytest.mark.parametrize(
        ("option", "value"),
        [("load_model", "half-open"), ("duration", "a while"), ("concurrency", "eight"),
         ("headers", "x: y"), ("verify_body", "md5"), ("rate", "fast"), ("rate", "0/s")],
    )  # fmt: skip
    def test_an_option_of_the_wrong_type_or_outside_its_choices(
        self, option: str, value: Any, builtin_kinds: Kinds
    ) -> None:
        load = builtin_kinds.get("probe", "http-load")
        assert load is not None
        options = {
            "target": "http://x", "load_model": "open", "rate": "80/s", "duration": "60s",
            option: value,
        }  # fmt: skip
        (problem,) = check_options(load, options, "probes.0")
        assert (problem.path, problem.kind) == (f"probes.0.{option}", "option_invalid")
        assert problem.got == value and problem.fix
        if load.options[option].allowed:
            assert problem.allowed == load.options[option].allowed

    def test_a_placeholder_is_typed_when_it_is_filled_not_before(
        self, builtin_kinds: Kinds
    ) -> None:
        load = builtin_kinds.get("probe", "http-load")
        assert load is not None
        options = {
            "target": "{url}/x",
            "load_model": "open",
            "rate": "{rate}",
            "duration": "{length}",
        }
        assert check_options(load, options, "probes.0") == []

    def test_a_kind_cannot_make_an_option_depend_on_one_it_does_not_take(self) -> None:
        declared = {
            "name": "acme.v1.probe.x",
            "summary": "s",
            "options": {"rate": {"type": "rate", "required_when": {"mode": "open"}}},
        }
        with pytest.raises(ValidationError, match="required_when"):
            Kind.model_validate(declared)

    def test_an_option_another_options_value_requires(self, builtin_kinds: Kinds) -> None:
        load = builtin_kinds.get("probe", "http-load")
        assert load is not None
        closed = {"target": "http://x", "load_model": "closed", "duration": "60s"}
        assert check_options(load, closed, "probes.0") == []
        (problem,) = check_options(load, {**closed, "load_model": "open"}, "probes.0")
        assert (problem.path, problem.kind) == ("probes.0.rate", "option_missing")
        assert "when load_model is open" in problem.message

    def test_an_agent_session_names_its_runtime_and_the_fix_says_what_each_spends(
        self, builtin_kinds: Kinds
    ) -> None:
        """Regression: ``runtime`` defaulted to claude-sdk, and an experiment and a test
        fixture that named none started paid Claude sessions. Spending is never implicit."""
        session = builtin_kinds.get("subject", "agent-session")
        assert session is not None and session.options["runtime"].required
        (problem,) = check_options(session, {"max_turns": 1}, "subjects.agent")
        assert (problem.path, problem.kind) == ("subjects.agent.runtime", "option_missing")
        for choice in ("claude-sdk (", "model (", "mock ("):
            assert choice in problem.fix, choice
        assert problem.fix.count("; spends)") == 2 and "; spends nothing)" in problem.fix
        assert problem.allowed == ()  # an open set: an installed runtime is accepted too
        assert check_options(session, {"runtime": "acme.v1.runtime.scripted"}, "s") == []

    def test_a_judge_names_its_runtime_and_the_fix_says_what_each_spends(
        self, builtin_kinds: Kinds
    ) -> None:
        """Regression: the judge's ``runtime`` defaulted to ``model``, which spends."""
        judge = builtin_kinds.get("sensor", "judge")
        assert judge is not None and judge.options["runtime"].required
        (problem,) = check_options(judge, {"rubric": "r"}, "sensors.0")
        assert (problem.path, problem.kind) == ("sensors.0.runtime", "option_missing")
        for choice in ("model (", "claude-sdk (", "mock ("):
            assert choice in problem.fix, choice
        assert problem.fix.count("; spends)") == 2 and "; spends nothing)" in problem.fix

    def test_an_options_open_choices_are_listed_and_never_enforced(self) -> None:
        declared = Kind.model_validate(
            {
                "name": "acme.v1.subject.x",
                "summary": "s",
                "options": {
                    "engine": {"required": True, "choices": {"a": "costs", "b": "free"}},
                    "mode": {"required": True},
                },
            }
        )
        found = {p.path: p.fix for p in check_options(declared, {}, "subjects.x")}
        assert found["subjects.x.engine"].endswith("; choose one of a (costs), b (free)")
        assert "choose" not in found["subjects.x.mode"]
        assert check_options(declared, {"engine": "c", "mode": "m"}, "subjects.x") == []

    def test_an_x_key_is_a_note(self, builtin_kinds: Kinds) -> None:
        process = builtin_kinds.get("subject", "process")
        assert process is not None
        assert check_options(process, {"command": ["./p"], "x-why": "the old build"}, "s") == []

    def test_an_unknown_kind_lists_the_kinds_of_that_family(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        compare_data["subjects"]["buffered"]["kind"] = "nonsense"
        compare_data["environment"] = {"kind": "docker"}
        experiment = _load(compare_data)
        assert isinstance(experiment.probes, tuple)
        problems = check(
            experiment, builtin_genres, builtin_kinds, probe_set(experiment.probes)
        ).problems
        unknown = {p.path: p for p in problems if p.kind == "name_unknown"}
        assert set(unknown) == {"subjects.buffered.kind", "environment.kind"}
        assert set(unknown["subjects.buffered.kind"].allowed) == EXPECTED["subject"]
        assert set(unknown["environment.kind"].allowed) == EXPECTED["environment"]
        assert unknown["environment.kind"].got == "docker"


# --- fit -------------------------------------------------------------------------------


class TestFit:
    def test_a_probe_needs_what_the_subject_offers(
        self, evaluate_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        """A prompt cannot be put to a process: that is refused before anything is spent."""
        evaluate_data["subjects"]["catalog"] = {"kind": "process", "command": ["./server"]}
        experiment = _load(evaluate_data)
        assert isinstance(experiment.probes, tuple)
        problems = check(
            experiment, builtin_genres, builtin_kinds, probe_set(experiment.probes)
        ).problems
        (unfit,) = [p for p in problems if p.kind == "kind_unfit"]
        assert unfit.path == "probes.0.kind" and unfit.got == "process"
        assert unfit.allowed == ("agent-session",)
        assert "needs agent" in unfit.message

    def test_a_sensor_needs_what_subjects_or_probes_offer(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        """Peak memory needs a pid. An endpoint that is already running offers only a url, so
        the metric has no provider there, and the message says why."""
        for subject in compare_data["subjects"].values():
            subject.clear()
            subject.update(kind="http", url="http://127.0.0.1:8080")
        compare_data["probes"][0]["target"] = "{url}/x"
        compare_data["guardrails"] = [{"metric": "memory_peak", "margin": "0%"}]
        experiment = _load(compare_data)
        assert isinstance(experiment.probes, tuple)
        problems = check(
            experiment, builtin_genres, builtin_kinds, probe_set(experiment.probes)
        ).problems
        (unprovided,) = [p for p in problems if p.kind == "metric_unprovided"]
        assert unprovided.path == "guardrails.0.metric"
        assert "needs pid" in unprovided.message
        assert "ttfb" in unprovided.allowed and "memory_peak" not in unprovided.allowed

    def test_a_listed_sensor_that_cannot_be_fed_is_refused(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        compare_data["sensors"] = [{"kind": "activation"}]
        assert ("sensors.0.kind", "kind_unfit") in _found(
            compare_data, builtin_genres, builtin_kinds
        )


# --- one provider per metric -----------------------------------------------------------


class TestMetricProviders:
    def test_the_sensor_is_selected_by_the_metric(
        self, compare_data: dict[str, Any], builtin_kinds: Kinds
    ) -> None:
        compare_data["guardrails"] = [{"metric": "memory_peak", "margin": "0%"}]
        compare_data["metrics"] = ["throughput"]
        experiment = _load(compare_data)
        assert isinstance(experiment.probes, tuple)
        found = select_sensors(experiment, builtin_kinds, probe_set(experiment.probes))
        assert found.problems == ()
        assert {s.label: (s.metrics, s.source) for s in found.sensors} == {
            "timing": (("ttfb", "throughput"), "ix"),
            "memory": (("memory_peak",), "ix"),
        }
        assert found.metrics["ttfb"].shape == "histogram"

    def test_a_metric_nothing_provides(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        compare_data["primary"] = {"metric": "tfb"}
        experiment = _load(compare_data)
        assert isinstance(experiment.probes, tuple)
        problems = check(
            experiment, builtin_genres, builtin_kinds, probe_set(experiment.probes)
        ).problems
        (problem,) = problems
        assert (problem.path, problem.kind, problem.got) == (
            "primary.metric", "metric_unprovided", "tfb",
        )  # fmt: skip
        assert "ttfb" in problem.allowed and "activated_correctly" not in problem.allowed

    def test_two_providers_are_ambiguous_until_the_author_names_one(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        declared = _with(builtin_kinds, _sensor("stopwatch"))
        experiment = _load(compare_data)
        assert isinstance(experiment.probes, tuple)
        probes = probe_set(experiment.probes)
        (problem,) = check(experiment, builtin_genres, declared, probes).problems
        assert (problem.path, problem.kind) == ("primary.metric", "metric_ambiguous")
        assert set(problem.allowed) == {"timing", "lab.v1.sensor.stopwatch"}
        assert problem.fix.startswith("say which: sensors: [{kind: ")

        named = _load({**compare_data, "sensors": [{"kind": "timing"}]})
        checked = check(named, builtin_genres, declared, probes)
        assert checked.problems == ()
        assert checked.design is not None
        (sensor,) = checked.design.sensors
        assert (sensor.label, sensor.source) == ("timing", "experiment")

    def test_two_listed_providers_are_still_ambiguous(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        declared = _with(builtin_kinds, _sensor("stopwatch"))
        compare_data["sensors"] = [{"kind": "timing"}, {"kind": "lab.v1.sensor.stopwatch"}]
        assert ("primary.metric", "metric_ambiguous") in _found(
            compare_data, builtin_genres, declared
        )

    def test_a_sensor_with_a_required_option_must_be_listed_to_give_it(
        self, evaluate_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        evaluate_data["metrics"] = ["tool_usage_score"]
        assert _found(evaluate_data, builtin_genres, builtin_kinds) == {
            ("sensors", "option_missing")
        }
        evaluate_data["sensors"] = [{"kind": "tool-usage", "tool": "memex"}]
        assert _found(evaluate_data, builtin_genres, builtin_kinds) == set()

    def test_metrics_is_a_checked_expectation(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        """What the author states about a metric is held against the sensor: writing ``ns``
        for a metric measured in ``ms`` is caught, not trusted."""
        compare_data["metrics"] = [
            {"name": "ttfb", "unit": "ns", "shape": "histogram"},
            {"name": "throughput", "direction": "higher"},
        ]
        experiment = _load(compare_data)
        assert isinstance(experiment.probes, tuple)
        (problem,) = check(
            experiment, builtin_genres, builtin_kinds, probe_set(experiment.probes)
        ).problems
        assert (problem.path, problem.kind) == ("metrics.0.unit", "metric_mismatch")
        assert (problem.got, problem.allowed, problem.fix) == ("ns", ("ms",), "unit: ms")

    def test_the_author_cannot_assert_judged(self, evaluate_data: dict[str, Any]) -> None:
        """``judged`` is not a key of the experiment file at all."""
        evaluate_data["metrics"] = [{"name": "judged_score", "judged": False}]
        experiment, problems = parse(evaluate_data)
        assert experiment is None
        assert [(p.path, p.kind) for p in problems] == [("metrics.0.judged", "unknown_key")]

    def test_truth_no_sensor_reads_is_refused(
        self, evaluate_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        evaluate_data["probes"][0]["truth"] = {"expectaton": "must_trigger", "x-note": "kept"}
        experiment = _load(evaluate_data)
        assert isinstance(experiment.probes, tuple)
        (problem,) = check(
            experiment, builtin_genres, builtin_kinds, probe_set(experiment.probes)
        ).problems
        assert (problem.path, problem.kind) == ("probes.0.truth.expectaton", "truth_unclaimed")
        assert problem.allowed == ("expectation", "expected_skill")

    def test_an_option_written_as_truth_is_said_to_belong_under_options(
        self, evaluate_data: dict[str, Any], tmp_path: Path
    ) -> None:
        """Regression: ``mock_response`` at the top of a probe file's frontmatter was told
        to correct its spelling or name a metric; it is an option of the prompt kind, and
        belongs under ``options:``."""
        lab = tmp_path / "lab"
        (lab / "probes").mkdir(parents=True)
        evaluate_data.pop("probes")
        (lab / "experiment.yaml").write_text(yaml.safe_dump(evaluate_data))
        (lab / "probes" / "q1.md").write_text(
            "---\nexpectation: must_trigger\nexpected_skill: review\nmock_response: Using "
            "review.\n---\nReview this diff.\n"
        )
        (problem,) = ix.validate(lab).problems
        assert (problem.path, problem.kind) == ("probes.q1.truth.mock_response", "truth_unclaimed")
        assert "an option of the probe's kind" in problem.message
        assert problem.fix.startswith(
            "write it under `options:` in the probe file's frontmatter (`options: "
            '{mock_response: "Using review."}`)'
        )
        evaluate_data["probes"] = [
            {"kind": "prompt", "prompt": "Review this diff.",
             "truth": {"expectation": "must_trigger", "timeout": "30s"}}
        ]  # fmt: skip
        (lab / "experiment.yaml").write_text(yaml.safe_dump(evaluate_data))
        (problem,) = ix.validate(lab).problems
        assert problem.path == "probes.0.truth.timeout"
        assert problem.fix.startswith("write it beside `kind`, out of `truth:`")


# --- effects come from the kinds -------------------------------------------------------


class TestEffects:
    def test_the_plan_reports_what_the_kinds_declare_without_being_told(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        plan = ix.plan(write_experiment(evaluate_data))
        assert {(e.effect, e.source, e.declared_by) for e in plan.effects} == {
            ("spend", "subjects.catalog", "kind"),
            ("tools", "subjects.catalog", "kind"),
            ("process", "subjects.catalog", "kind"),
            ("filesystem", "subjects.catalog", "kind"),
        }
        assert all(e.detail == EFFECTS[e.effect] for e in plan.effects)

    def test_the_author_adds_effects_and_cannot_remove_one(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        before = ix.plan(write_experiment(compare_data, "before"))
        compare_data["subjects"]["buffered"]["effects"] = ["binds 127.0.0.1:8080"]
        compare_data["subjects"]["streaming"]["effects"] = []
        after = ix.plan(write_experiment(compare_data, "after"))
        declared = {(e.effect, e.source) for e in before.effects}
        assert declared <= {(e.effect, e.source) for e in after.effects}
        added = [e for e in after.effects if e.declared_by == "experiment"]
        assert [(e.effect, e.source) for e in added] == [
            ("binds 127.0.0.1:8080", "subjects.buffered")
        ]
        assert ("process", "subjects.streaming") in declared

    def test_a_sensor_ix_selected_brings_its_effects(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        evaluate_data["genre"] = "characterize"
        evaluate_data["factors"] = {"model": ["small", "large"]}
        evaluate_data["subjects"]["catalog"]["model"] = "small"
        evaluate_data["primary"] = {"metric": "tests_passed"}
        evaluate_data["probes"][0]["truth"] = {"function_name": "f", "test_cases": []}
        del evaluate_data["threshold"]
        plan = ix.plan(write_experiment(evaluate_data))
        assert ("code", "sensor function-test") in {(e.effect, e.source) for e in plan.effects}

    def test_spending_needs_a_ceiling(
        self, evaluate_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        del evaluate_data["spend"]
        assert _found(evaluate_data, builtin_genres, builtin_kinds) == {
            ("spend.ceiling", "spend_unbounded")
        }


# --- the extension point ---------------------------------------------------------------


class TestKindExtensionPoint:
    def test_ix_registers_its_kinds_at_the_point_it_owns(self) -> None:
        points = {point.name: point.value for point in metadata.entry_points(group="ix.kind")}
        assert KIND_GROUP == "ix.kind"
        assert points.get("ix") == "ix.builtin.kinds"
        assert set(ix.kinds().names("sensor")) >= EXPECTED["sensor"]

    def test_a_package_that_declares_a_kind_in_ixs_namespace_is_refused_and_ix_keeps_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        package = tmp_path / "aaa_ix"
        package.mkdir()
        (package / "__init__.py").write_text("")
        impostor = {"name": "ix.v1.subject.process", "summary": "Not ix's.", "offers": ["url"]}
        (package / "process.yaml").write_text(yaml.safe_dump(impostor))
        monkeypatch.syspath_prepend(str(tmp_path))
        real = metadata.entry_points

        def entry_points(**params: Any) -> Any:
            found = list(real(**params))
            if params.get("group") == "ix.kind":
                found.append(metadata.EntryPoint("aaa", "aaa_ix", "ix.kind"))
            return found

        monkeypatch.setattr(metadata, "entry_points", entry_points)
        found = kinds(KindFiles().files(), grammar=type_url_problem)
        process = found.get("subject", "process")
        assert process is not None and "command" in process.options
        (refused,) = found.rejected["ix.v1.subject.process"]
        assert refused.kind == "name_taken" and "aaa_ix/process.yaml" in str(refused.source)

    def test_a_lab_file_that_redeclares_an_installed_kind_is_refused_alone(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        (file.parent / "kinds").mkdir()
        impostor = {"name": "ix.v1.subject.process", "summary": "Not ix's.", "offers": ["url"]}
        (file.parent / "kinds" / "impostor.yaml").write_text(yaml.safe_dump(impostor))
        found = ix.kinds(file.parent)
        assert found.get("subject", "process") is not None
        assert [p.kind for p in found.rejected["ix.v1.subject.process"]] == ["name_taken"]
        assert ix.validate(file).valid

    def test_a_lab_ships_a_kind_as_a_data_file(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """A sensor declared in ``kinds/`` beside the experiment provides a new metric, with
        no Python anywhere."""
        file: Path = write_experiment({**compare_data, "metrics": ["jitter"]})
        assert [p.kind for p in ix.validate(file).problems] == ["metric_unprovided"]
        (file.parent / "kinds").mkdir()
        (file.parent / "kinds" / "jitter.yaml").write_text(
            yaml.safe_dump(_sensor("jitter", metric="jitter"))
        )
        assert ix.validate(file).valid
        assert "lab.v1.sensor.jitter" in ix.kinds(file.parent).kinds
        assert "lab.v1.sensor.jitter" not in ix.kinds().kinds

    def test_a_broken_kind_file_is_refused_alone_and_reported(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        (file.parent / "kinds").mkdir()
        (file.parent / "kinds" / "bad.yaml").write_text("name: lab.v1.sensor.bad\nsummary: s\n")
        (file.parent / "kinds" / "worse.yaml").write_text("- not a mapping\n")
        result = ix.validate(file)
        assert result.valid
        assert [w.kind for w in result.warnings] == ["kind_rejected", "kind_rejected"]
        rejected = ix.kinds(file.parent).rejected
        # One holds the wrong keys (ix's judgment); one is not a mapping (the reader's).
        assert sorted(p[0].kind for p in rejected.values()) == ["kind_invalid", "wrong_shape"]

    def test_two_lab_files_declaring_one_kind_are_both_refused(self) -> None:
        files = [*KindFiles(packages=["ix.builtin.kinds"]).files()]
        twin = {**_sensor("x"), "name": "lab.v1.sensor.twin"}
        files += [
            DeclarationFile(origin="one.yaml", data=twin),
            DeclarationFile(origin="two.yaml", data=twin),
        ]
        found = kinds(files, grammar=type_url_problem)
        assert found.get("sensor", "twin") is None and found.get("sensor", "timing") is not None
        assert [p.kind for p in found.rejected["lab.v1.sensor.twin"]] == ["name_taken"] * 2
