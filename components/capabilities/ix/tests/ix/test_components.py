"""Tests for ix DAG components — ProbeNode, SubjectNode, TrialNode, SensorNode.

Four-node inner DAG (run by the native engine):
  ProbeNode → TrialNode ← SubjectNode
                 ↓
             SensorNode
"""

from matrix import AgentResponse, Artifact, Component, Construct, Orchestrator

from ix.adapters._out.components import ProbeNode, SensorNode, SubjectNode, TrialNode
from ix.domain import type_urls
from ix.domain.types import Probe, Subject, Trial
from ix.eval.sensors import ActivationSensor

# --- Test Helpers ---


class _EchoAgent:
    """Test agent — echoes prompt as content, adds a tool call."""

    async def run(self, prompt: str) -> AgentResponse:
        return AgentResponse(
            content=f"echo: {prompt}",
            tool_calls=({"name": "Skill", "input": {"skill": "build-eval"}},),
        )


class _FailingAgent:
    """Test agent — always raises."""

    async def run(self, prompt: str):
        raise RuntimeError("timeout")


def _factory(agent=None, seen=None):
    """An AgentFactory: (subject, trial_index) -> agent. Records what it was called with."""

    def build(subject, trial_index, run_index=0):
        if seen is not None:
            seen.append((subject, trial_index))
        return agent or _EchoAgent()

    return build


def _probe(id: str = "must-001", prompt: str = "How do I write evals?") -> Probe:
    return Probe(id=id, prompt=prompt, metadata={"expectation": "must_trigger"})


def _subject() -> Subject:
    return Subject(
        name="test-subject",
        config={"system_prompt": "You are helpful.", "runtime": {"type": "mock"}},
    )


def _construct(*items: tuple[str, object]) -> Construct:
    construct = Construct()
    for kind, data in items:
        construct.append(Artifact.create(type_url=kind, producer="test", data=data))
    return construct


# --- Protocol Tests ---


class TestProtocol:
    def test_probe_node_satisfies_component(self):
        assert isinstance(ProbeNode(_probe()), Component)

    def test_subject_node_satisfies_component(self):
        assert isinstance(SubjectNode(_subject()), Component)

    def test_trial_node_satisfies_component(self):
        assert isinstance(TrialNode(_factory()), Component)

    def test_sensor_node_satisfies_component(self):
        sensor = ActivationSensor(expected_skill="build-eval")
        assert isinstance(SensorNode(sensor), Component)


# --- ProbeNode Tests ---


class TestProbeNode:
    async def test_produces_stimulus(self):
        probe = _probe()
        result = await ProbeNode(probe).run(Construct())
        assert result.type_url == type_urls.PROBE == "ix.v1/probe.stimulus"
        assert result.value is probe

    async def test_root_node_no_requires(self):
        assert ProbeNode(_probe()).requires == frozenset()


class TestSubjectNode:
    async def test_produces_the_subject(self):
        subject = _subject()
        result = await SubjectNode(subject).run(Construct())
        assert result.type_url == type_urls.SUBJECT
        assert result.value is subject

    async def test_root_node_no_requires(self):
        assert SubjectNode(_subject()).requires == frozenset()


class TestTrialNode:
    async def test_produces_observation(self):
        construct = _construct((type_urls.PROBE, _probe()), (type_urls.SUBJECT, _subject()))
        result = await TrialNode(_factory(), trial_index=0).run(construct)

        assert result.type_url == type_urls.TRIAL
        trial = result.value
        assert isinstance(trial, Trial)
        assert (trial.probe_id, trial.trial_index, trial.error) == ("must-001", 0, None)

    async def test_builds_agent_for_the_subject_and_trial(self):
        seen: list = []
        subject = _subject()
        construct = _construct((type_urls.PROBE, _probe()), (type_urls.SUBJECT, subject))
        await TrialNode(_factory(seen=seen), trial_index=3).run(construct)
        assert seen == [(subject, 3)]

    async def test_error_captured_not_raised(self):
        construct = _construct(
            (type_urls.PROBE, _probe("err-001")), (type_urls.SUBJECT, _subject())
        )
        result = await TrialNode(_factory(agent=_FailingAgent())).run(construct)
        trial = result.value
        assert (trial.error, trial.probe_id, trial.response) == ("timeout", "err-001", None)


class TestSensorNode:
    async def test_produces_readings(self):
        trial = Trial(
            probe_id="must-001",
            trial_index=0,
            response=AgentResponse(
                tool_calls=({"name": "Skill", "input": {"skill": "build-eval"}},)
            ),
        )
        node = SensorNode(ActivationSensor(expected_skill="build-eval"))
        result = await node.run(_construct((type_urls.TRIAL, trial)))

        assert result.type_url == type_urls.READINGS
        assert len(result.value) == 1
        assert result.value[0].passed is True
        assert result.value[0].sensor_name == "activation"

    async def test_error_trial_produces_failed_reading(self):
        error_trial = Trial(probe_id="err-001", trial_index=0, error="timeout")
        node = SensorNode(ActivationSensor(expected_skill="build-eval"))
        readings = (await node.run(_construct((type_urls.TRIAL, error_trial)))).value
        assert len(readings) == 1
        assert readings[0].passed is False
        assert "timeout" in readings[0].details


class TestInnerDag:
    async def test_four_node_dag(self):
        """Orchestrator runs ProbeNode → TrialNode ← SubjectNode → SensorNode, reads enforced."""
        construct = await Orchestrator(
            [
                ProbeNode(_probe()),
                SubjectNode(_subject()),
                TrialNode(_factory(), trial_index=0),
                SensorNode(ActivationSensor(expected_skill="build-eval")),
            ]
        ).run()

        trial = construct[type_urls.TRIAL]
        assert isinstance(trial, Trial)
        assert trial.probe_id == "must-001"
        assert trial.response.content == "echo: How do I write evals?"

        readings = construct[type_urls.READINGS]
        assert len(readings) == 1
        assert readings[0].passed is True
