"""Tests for the DeepEval sensor's judge path.

The judge is a matrix agent on the ``model`` runtime, so any family modelrt has a registry
row for can grade. Every reading records the judge's family next to the subject's, and
whether the two differ — computed, never assumed.
"""

import os

import pytest

deepeval = pytest.importorskip("deepeval", reason="deepeval not installed")

from matrix import AgentDefinition, AgentResponse, BoundAgent  # noqa: E402
from matrix.adapters._out.runtime.mock import MockRuntime, MockRuntimeConfig  # noqa: E402
from modelrt import build_runtime  # noqa: E402

from ix.domain.types import Trial  # noqa: E402
from ix.eval.sensors_deepeval import (  # noqa: E402
    DeepEvalSensor,
    DeepEvalSensorConfig,
    _create_agent_adapter,
)

MODELS = {
    "models": {
        "qwen-judge": {
            "backend": "mock",
            "model": "q",
            "family": "qwen",
            "options": {"default": "judged"},
        }
    }
}


def _agent(family: str = "mock", reply: str = "judged") -> BoundAgent:
    return BoundAgent(
        AgentDefinition(name="judge"),
        MockRuntime(MockRuntimeConfig(default=reply, family=family)),
    )


class TestAgentModelAdapter:
    """The adapter bridges a matrix Agent to DeepEval's model interface."""

    def test_generate_returns_content_and_records_family(self):
        adapter = _create_agent_adapter(_agent(family="gemini"), model_name="j")
        assert adapter.generate("test prompt") == "judged"
        assert adapter.last_family == "gemini"

    async def test_a_generate_returns_content(self):
        adapter = _create_agent_adapter(_agent(), model_name="j")
        assert await adapter.a_generate("test prompt") == "judged"

    def test_model_name(self):
        assert _create_agent_adapter(_agent(), model_name="haiku").get_model_name() == "haiku"

    def test_is_deepeval_base_llm(self):
        from deepeval.models import DeepEvalBaseLLM

        assert isinstance(_create_agent_adapter(_agent()), DeepEvalBaseLLM)

    def test_generate_ignores_extra_kwargs(self):
        """DeepEval may pass schema=... which the adapter ignores."""
        assert isinstance(_create_agent_adapter(_agent()).generate("test", schema=None), str)


class TestJudgeWiring:
    def test_from_config_builds_a_model_runtime_judge(self):
        config = DeepEvalSensorConfig(metric="answer_relevancy", threshold=0.5, judge="qwen-judge")
        sensor = DeepEvalSensor.from_config(config, probes=(), models=lambda: build_runtime(MODELS))
        assert sensor.name == "deepeval.answer_relevancy"

    def test_judge_without_a_registry_is_refused(self):
        config = DeepEvalSensorConfig(judge="qwen-judge")
        with pytest.raises(ValueError, match="needs a model registry"):
            DeepEvalSensor.from_config(config, probes=())

    def test_unknown_config_key_rejected(self):
        with pytest.raises(ValueError):
            DeepEvalSensorConfig.model_validate({"model": "haiku"})  # renamed to judge

    def test_reading_records_families_and_out_of_family(self):
        """A qwen judge grading a claude subject: out_of_family is True, and says so."""
        config = DeepEvalSensorConfig(metric="answer_relevancy", threshold=0.5, judge="qwen-judge")
        sensor = DeepEvalSensor.from_config(config, probes=(), models=lambda: build_runtime(MODELS))

        class _Metric:
            """Stands in for a DeepEval metric: calls the judge once, scores 0.9."""

            score = 0.9
            reason = "relevant"

            def measure(self, test_case):
                sensor._adapter.generate("grade this")

        sensor._metric = _Metric()
        trial = Trial(
            probe_id="p", trial_index=0, response=AgentResponse(content="answer", family="claude")
        )
        (reading,) = sensor.measure(trial)
        assert reading.passed and reading.score == 0.9
        assert reading.metrics == {
            "judge_family": "qwen",
            "subject_family": "claude",
            "out_of_family": True,
        }

    def test_unknown_family_is_none_not_a_guess(self):
        config = DeepEvalSensorConfig(metric="answer_relevancy", judge="qwen-judge")
        sensor = DeepEvalSensor.from_config(config, probes=(), models=lambda: build_runtime(MODELS))

        class _Metric:
            score, reason = 0.1, ""

            def measure(self, test_case):
                sensor._adapter.generate("grade")

        sensor._metric = _Metric()
        (reading,) = sensor.measure(
            Trial(probe_id="p", trial_index=0, response=AgentResponse(content="a"))
        )
        assert reading.metrics["out_of_family"] is None

    @pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="DeepEval's default model is GPTModel — needs OPENAI_API_KEY",
    )
    def test_from_config_without_judge(self):
        config = DeepEvalSensorConfig(metric="answer_relevancy", threshold=0.5)
        assert DeepEvalSensor.from_config(config, probes=()).name == "deepeval.answer_relevancy"


class TestCompositeSensorWithDeepEval:
    def test_composite_includes_deepeval(self):
        from ix.eval.sensors import ActivationSensor, CompositeSensor

        deepeval_sensor = DeepEvalSensor(metric_name="answer_relevancy", judge=_agent())
        composite = CompositeSensor(
            [ActivationSensor(expected_skill="build-eval"), deepeval_sensor]
        )
        assert "activation" in composite.name
        assert "deepeval.answer_relevancy" in composite.name
