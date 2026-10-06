"""Tests for ix domain and eval models."""

import pytest
from matrix import AgentResponse
from pydantic import ValidationError

from ix.domain.types import Probe, Reading, Subject, Trial
from ix.eval.models import (
    ExperimentConfig,
    ExperimentResults,
    TrialRecord,
)


class TestProbe:
    def test_basic(self):
        p = Probe(id="must-001", prompt="test prompt")
        assert p.id == "must-001"
        assert p.metadata == {}

    def test_with_metadata(self):
        p = Probe(id="must-001", prompt="test", metadata={"expectation": "must_trigger"})
        assert p.metadata["expectation"] == "must_trigger"

    def test_frozen(self):
        p = Probe(id="x", prompt="p")
        with pytest.raises(ValidationError):
            p.id = "y"  # type: ignore[misc]


class TestSubject:
    def test_basic(self):
        s = Subject(name="build-eval")
        assert s.name == "build-eval"
        assert s.description == ""
        assert s.config == {}

    def test_with_config(self):
        s = Subject(name="build-eval", description="Eval skill", config={"version": "1.0"})
        assert s.description == "Eval skill"
        assert s.config["version"] == "1.0"

    def test_frozen(self):
        s = Subject(name="build-eval")
        with pytest.raises(ValidationError):
            s.name = "other"  # type: ignore[misc]


class TestTrial:
    def test_basic(self):
        t = Trial(probe_id="must-001", trial_index=0, response="hello")
        assert t.probe_id == "must-001"
        assert t.response == "hello"
        assert t.error is None

    def test_with_error(self):
        t = Trial(probe_id="must-001", trial_index=0, error="boom")
        assert t.error == "boom"
        assert t.response is None


class TestExperimentConfig:
    def test_minimal(self):
        exp = ExperimentConfig(name="test")
        assert exp.name == "test"
        assert exp.trials == 5
        assert exp.probes == ()
        assert exp.sensors == ({"type": "activation"},)

    def test_sensor_normalized_to_sensors(self):
        """Single sensor dict is normalized to one-element sensors tuple."""
        exp = ExperimentConfig(name="test", sensor={"type": "latency"})
        assert exp.sensors == ({"type": "latency"},)

    def test_with_probes(self):
        probes = (
            Probe(id="a", prompt="p1", metadata={"expectation": "must_trigger"}),
            Probe(id="b", prompt="p2", metadata={"expectation": "should_not_trigger"}),
        )
        exp = ExperimentConfig(
            name="test",
            subjects=(Subject(name="build-eval"),),
            probes=probes,
        )
        assert len(exp.probes) == 2
        assert exp.subjects[0].name == "build-eval"

    def test_explicit_subjects(self):
        subjects = (Subject(name="build-eval"), Subject(name="data-store"))
        exp = ExperimentConfig(name="test", subjects=subjects)
        assert len(exp.subjects) == 2

    def test_sensors_list(self):
        exp = ExperimentConfig(
            name="test",
            sensors=(
                {"type": "activation", "expected_skill": "build-eval"},
                {"type": "deepeval", "metric": "answer_relevancy"},
            ),
        )
        assert len(exp.sensors) == 2
        assert exp.sensors[0]["type"] == "activation"
        assert exp.sensors[1]["type"] == "deepeval"


class TestAgentResponseInMatrix:
    """Verify AgentResponse (Matrix type) works in ix context."""

    def test_with_tool_calls(self):
        r = AgentResponse(
            content="response text",
            tool_calls=({"name": "Skill", "input": {"skill": "build-eval"}},),
        )
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0]["name"] == "Skill"

    def test_defaults(self):
        r = AgentResponse()
        assert r.content == ""
        assert r.tool_calls == ()
        assert r.tokens_input == 0
        assert r.cost_usd is None
        assert r.num_turns == 0


class TestTrialRecord:
    def test_basic(self):
        v = TrialRecord(
            run_id="r1",
            run_index=0,
            probe_id="must-001",
            trial_index=0,
            response=AgentResponse(content="hi").model_dump(mode="json"),
            readings=(
                Reading(
                    sensor_name="activation",
                    probe_id="must-001",
                    trial_index=0,
                    passed=True,
                    score=1.0,
                ),
            ),
        )
        assert v.probe_id == "must-001"
        assert v.readings[0].passed is True
        assert v.response["content"] == "hi"


class TestExperimentResults:
    def test_with_metrics(self):
        results = ExperimentResults(
            experiment_name="skill-activation",
            pass_rate=0.85,
            mean_score=0.80,
            min_score=0.6,
            max_score=1.0,
            families=("claude",),
        )
        assert results.pass_rate == 0.85
        assert results.mean_score == 0.80
        assert results.status == "good"

    def test_defaults(self):
        results = ExperimentResults(experiment_name="test")
        assert results.pass_rate == 0.0
        assert results.mean_score == 0.0
        assert results.status == "unmeasured"  # no family answered: no grade
        assert results.grade == "poor"
        assert results.seed is None and results.simulated is False

    def test_serialization_roundtrip(self):
        results = ExperimentResults(
            experiment_name="test",
            pass_rate=0.85,
            mean_score=0.80,
            min_score=0.6,
            max_score=1.0,
        )
        data = results.model_dump()
        restored = ExperimentResults.model_validate(data)
        assert restored == results

    def test_status_computed_from_pass_rate(self):
        """Status is derived from pass_rate, not stored — computed_field."""
        f = ("claude",)
        assert ExperimentResults(experiment_name="t", pass_rate=1.0, families=f).status == (
            "excellent"
        )
        assert ExperimentResults(experiment_name="t", pass_rate=0.90, families=f).status == "good"
        assert (
            ExperimentResults(experiment_name="t", pass_rate=0.60, families=f).status
            == "needs_work"
        )
        assert ExperimentResults(experiment_name="t", pass_rate=0.30, families=f).status == "poor"

    @pytest.mark.parametrize("families", [(), ("simulated",), ("mock", "simulated")])
    def test_a_run_that_measured_no_model_is_unmeasured_not_graded(self, families):
        """F4: `status: excellent` on a simulated run read as a result; it is a harness check."""
        results = ExperimentResults(experiment_name="t", pass_rate=1.0, families=families)
        assert results.status == "unmeasured"
        assert results.grade == "excellent"
        assert results.model_dump(mode="json")["status"] == "unmeasured"

    def test_provenance_fields(self):
        """Results carry provenance for reproducibility."""
        results = ExperimentResults(
            experiment_name="test",
            config_hash="abc123",
            ix_version="0.0.1-alpha",
        )
        assert results.config_hash == "abc123"
        assert results.ix_version == "0.0.1-alpha"
