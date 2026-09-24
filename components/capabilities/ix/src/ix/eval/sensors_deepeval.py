"""DeepEval sensor — wraps DeepEval metrics as ix Sensors.

Lazy-imports deepeval to avoid heavy dependency unless explicitly used.
Install with: uv add ix[deepeval]

With ``judge: <model>`` set, DeepEval's LLM calls go through a matrix agent on the
``model`` runtime — any family modelrt has a registry row for — and every reading records
the judge's family next to the subject's, so whether the grading was out of family is
recorded rather than assumed. Without a judge, DeepEval uses its own default provider.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ix.domain.types import Probe, Reading, Trial

if TYPE_CHECKING:
    from collections.abc import Callable

    from matrix import Agent, AgentResponse
    from modelrt import ModelRuntime

# --- Config ---


class DeepEvalSensorConfig(BaseModel, frozen=True, extra="forbid"):
    """Config for DeepEvalSensor. Validated from experiment.yaml sensor section."""

    type: str = "deepeval"
    metric: str = "answer_relevancy"
    threshold: float = 0.5
    #: A modelrt registry name. The judge runs through matrix's model runtime.
    judge: str | None = None
    criteria: str | None = None  # For GEval custom criteria


# --- Agent Adapter ---


def _create_agent_adapter(agent: Agent, model_name: str = "ix-agent") -> Any:
    """Create a DeepEval model adapter wrapping a Matrix Agent.

    Bridges Matrix's Agent protocol (async run(prompt) → AgentResponse)
    to DeepEval's DeepEvalBaseLLM interface (sync generate(prompt) → str).

    Class defined inside function to keep deepeval as a lazy import.
    """
    from deepeval.models import DeepEvalBaseLLM

    # DeepEvalBaseLLM.__init_subclass__ is untyped (it wraps generate/a_generate in
    # deepeval's tracing), so defining any subclass is an untyped call under strict mode.
    # Scoped to this line rather than relaxed for the module.
    class AgentModelAdapter(DeepEvalBaseLLM):  # type: ignore[no-untyped-call]
        """Routes DeepEval LLM calls through a Matrix Agent.

        DeepEval constructs evaluation prompts internally.
        This adapter just provides the LLM execution layer.
        """

        def __init__(self) -> None:
            self._agent = agent
            self._model_name = model_name
            # DeepEvalBaseLLM.__init__ parses a provider model name and calls load_model;
            # neither applies to an agent-backed judge, so the attributes are set directly.
            self.name = model_name
            self.model = self.load_model()
            self.last_family: str | None = None

        def load_model(self, *args: Any, **kwargs: Any) -> DeepEvalBaseLLM:
            """The adapter is the model: there is nothing to load."""
            return self

        def generate(self, prompt: str, **kwargs: Any) -> str:
            """Sync bridge: runs Agent.run() in a thread with its own event loop."""
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                response = pool.submit(asyncio.run, self._agent.run(prompt)).result()
            self.last_family = response.family
            return response.content

        async def a_generate(self, prompt: str, **kwargs: Any) -> str:
            """Async path: directly awaits Agent.run()."""
            response = await self._agent.run(prompt)
            self.last_family = response.family
            return response.content

        def get_model_name(self) -> str:
            return self._model_name

    return AgentModelAdapter()


# --- Metric Builder ---


_METRIC_BUILDERS: dict[str, str] = {
    "answer_relevancy": "deepeval.metrics:AnswerRelevancyMetric",
    "faithfulness": "deepeval.metrics:FaithfulnessMetric",
    "contextual_precision": "deepeval.metrics:ContextualPrecisionMetric",
    "contextual_recall": "deepeval.metrics:ContextualRecallMetric",
    "contextual_relevancy": "deepeval.metrics:ContextualRelevancyMetric",
    "hallucination": "deepeval.metrics:HallucinationMetric",
    "bias": "deepeval.metrics:BiasMetric",
    "toxicity": "deepeval.metrics:ToxicityMetric",
    "g_eval": "deepeval.metrics:GEval",
}


def _build_metric(
    metric_name: str,
    threshold: float,
    model: Any | None,
    criteria: str | None,
) -> Any:
    """Build a DeepEval metric instance by name.

    ``model`` is an AgentModelAdapter (a matrix agent on the model runtime) or None, in
    which case DeepEval uses its own default provider.
    """
    dotpath = _METRIC_BUILDERS.get(metric_name)
    if dotpath is None:
        valid = sorted(_METRIC_BUILDERS)
        raise ValueError(
            f"Unknown DeepEval metric: {metric_name!r}. Valid metrics: {', '.join(valid)}"
        )

    module_path, class_name = dotpath.rsplit(":", 1)

    # deepeval sends usage telemetry unless opted out. Default to off before its first
    # import; setdefault leaves an operator's explicit choice alone. See SECURITY.md I-7.
    os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
    try:
        import importlib

        mod = importlib.import_module(module_path)
        metric_cls = getattr(mod, class_name)
    except ImportError as e:
        raise ImportError(
            "DeepEvalSensor requires 'deepeval'. Install with: uv add ix[deepeval]"
        ) from e

    kwargs: dict[str, Any] = {"threshold": threshold}
    if model is not None:
        kwargs["model"] = model
    if criteria is not None and metric_name == "g_eval":
        kwargs["criteria"] = criteria

    return metric_cls(**kwargs)


# --- Sensor ---


class DeepEvalSensor:
    """Wraps a DeepEval metric as an ix Sensor.

    Translates between ix's sense(trial) → list[Reading] protocol
    and DeepEval's BaseMetric.measure(LLMTestCase) API.

    When a judge agent is provided, LLM calls route through the
    AgentModelAdapter — observable via AgentResponse.
    When no judge is provided, DeepEval uses its default model.
    """

    Config = DeepEvalSensorConfig

    def __init__(
        self,
        *,
        metric_name: str = "answer_relevancy",
        threshold: float = 0.5,
        criteria: str | None = None,
        judge: Agent | None = None,
        judge_name: str = "ix-judge",
        ground_truth: dict[str, dict[str, Any]] | None = None,
    ):
        self._adapter = (
            _create_agent_adapter(judge, model_name=judge_name) if judge is not None else None
        )
        self._metric = _build_metric(metric_name, threshold, self._adapter, criteria)
        self._metric_name = metric_name
        self._threshold = threshold
        self._ground_truth = ground_truth or {}

    @classmethod
    def from_config(
        cls,
        config: DeepEvalSensorConfig,
        probes: tuple[Probe, ...] = (),
        *,
        models: Callable[[], ModelRuntime] | None = None,
        **kwargs: Any,
    ) -> DeepEvalSensor:
        judge: Agent | None = None
        if config.judge:
            if models is None:
                raise ValueError(
                    f"deepeval judge {config.judge!r} needs a model registry, and none was wired"
                )
            from matrix import AgentDefinition, BoundAgent
            from matrix.adapters._out.runtime.model import (
                ModelAgentRuntime,
                ModelAgentRuntimeConfig,
            )

            runtime = ModelAgentRuntime(
                models(), ModelAgentRuntimeConfig(default_model=config.judge)
            )
            judge = BoundAgent(AgentDefinition(name="deepeval-judge", tools=()), runtime)

        ground_truth = {
            p.id: {
                "prompt": p.prompt,
                "expected_output": p.metadata.get("expected_output"),
                "context": p.metadata.get("context"),
            }
            for p in probes
        }
        return cls(
            metric_name=config.metric,
            threshold=config.threshold,
            criteria=config.criteria,
            judge=judge,
            judge_name=config.judge or "ix-judge",
            ground_truth=ground_truth,
        )

    @property
    def name(self) -> str:
        return f"deepeval.{self._metric_name}"

    def measure(self, trial: Trial) -> list[Reading]:
        """Measure a trial via the DeepEval metric."""
        from deepeval.test_case import LLMTestCase

        response: AgentResponse = trial.response
        truth = self._ground_truth.get(trial.probe_id, {})

        kwargs: dict[str, Any] = {
            "input": truth.get("prompt", ""),
            "actual_output": response.content,
        }
        if truth.get("expected_output") is not None:
            kwargs["expected_output"] = truth["expected_output"]
        if truth.get("context") is not None:
            kwargs["context"] = truth["context"]

        test_case = LLMTestCase(**kwargs)
        self._metric.measure(test_case)

        score = float(self._metric.score) if self._metric.score is not None else 0.0
        reason = getattr(self._metric, "reason", "") or ""

        judge_family = getattr(self._adapter, "last_family", None)
        subject_family = getattr(response, "family", None)
        return [
            Reading(
                sensor_name=self.name,
                probe_id=trial.probe_id,
                trial_index=trial.trial_index,
                passed=score >= self._threshold,
                score=score,
                details=reason,
                metrics={
                    "judge_family": judge_family,
                    "subject_family": subject_family,
                    # None when either side is unknown — never a guess.
                    "out_of_family": (
                        judge_family != subject_family if judge_family and subject_family else None
                    ),
                },
            )
        ]
