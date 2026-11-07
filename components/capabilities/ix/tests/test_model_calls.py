"""The plan counts the model calls a run starts, as work, never as money (ruling Q6).

Each adapter that starts model calls says how many one trial of its entry starts
(:class:`~ix.extension.ModelCalls`): an agent session one a round, a model call one, a judge
its ``repeats``, a judge evaluator one; none on a stand-in. The plan adds them over every
trial of the schedule, cell by cell, and names each entry whose adapter may call a model and
does not say how many: the count is a bound only over what it counts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix
import pytest

import ix
from ix.composition import Root
from ix.composition.entry_point import register
from ix.composition.settings import IxSettings
from ix.domain.plan import Work
from ix.extension import POINTS, RUNTIMES, Interaction, Sensed, Target

PACKAGE = Path(ix.__file__).resolve().parents[2]

if TYPE_CHECKING:
    from collections.abc import Mapping

Write = Any  # the write_experiment fixture

#: Words a price is said in: the work is never said in any of them.
MONEY = ("cost", "spend", "price", "usd", "$", "dollar", "budget")


def _session(**options: Any) -> dict[str, Any]:
    return {
        "name": "counted",
        "question": "Does the agent say hello?",
        "genre": "evaluate",
        "subjects": {"agent": {"kind": "agent-session", "runtime": "claude-sdk", **options}},
        "probes": [{"id": "hi", "kind": "prompt", "prompt": "Say hello."}],
        "primary": {"metric": "text_matched"},
        "threshold": {"op": ">=", "value": 0.5},
        "sensors": [{"kind": "text-match", "contains": "hello"}],
        "repeats": 5,
    }


def _counts(made: ix.Plan) -> dict[str, tuple[int, int, int]]:
    return {c.entry: (c.per_trial, c.trials, c.total) for c in made.work.entries}


class TestTheCount:
    def test_a_session_is_one_call_a_round_over_every_trial(self, write_experiment: Write) -> None:
        made = ix.plan(write_experiment(_session()))
        assert _counts(made) == {"subjects.agent": (1, 5, 5)}
        assert made.work.model_calls == 5 and made.work.uncounted == ()
        looped = ix.plan(write_experiment(_session(loop={"strategy": "self-review", "rounds": 3})))
        assert _counts(looped) == {"subjects.agent": (3, 5, 15)}

    def test_a_factor_on_the_rounds_is_counted_cell_by_cell(self, write_experiment: Write) -> None:
        data = _session(loop={"strategy": "self-review", "rounds": "{rounds}"})
        data["genre"], data["factors"] = "characterize", {"rounds": [1, 3]}
        del data["threshold"]
        made = ix.plan(write_experiment(data))
        assert _counts(made) == {"subjects.agent": (3, 10, 5 * 1 + 5 * 3)}

    def test_a_judge_adds_its_repeats_to_every_trial(self, write_experiment: Write) -> None:
        data = _session()
        data["sensors"] = [
            {"kind": "text-match", "contains": "hello"},
            {
                "kind": "judge",
                "rubric": "Polite.",
                "model_runtime": "matrix-hardline.v1.model-runtime",
                "repeats": 2,
            },
        ]
        data["metrics"] = ["judged_score"]
        made = ix.plan(write_experiment(data))
        assert _counts(made)["sensors.1"] == (2, 5, 10)
        assert made.work.model_calls == 15

    def test_a_stand_in_calls_no_model(self, write_experiment: Write) -> None:
        made = ix.plan(write_experiment(_session(runtime="mock")))
        assert made.work.model_calls == 0 and _counts(made) == {"subjects.agent": (0, 5, 0)}
        assert made.work.text() == "none: each entry that calls one is on a stand-in"

    def test_an_experiment_that_calls_no_model_says_so(self) -> None:
        made = ix.plan(PACKAGE / "examples" / "delay-server" / "slower.yaml")
        assert made.work == Work() and made.work.text() == "none: no entry here calls a model"

    def test_it_is_work_and_never_money(self, write_experiment: Write) -> None:
        made = ix.plan(write_experiment(_session()))
        said = (made.work.text() + json.dumps(made.work.document())).lower()
        assert not [word for word in MONEY if word in said]
        assert made.work.text().startswith("at most 5 (subjects.agent 5)")


class Unsaid:
    """A third party's sensor that may call a model (it needs matrix's runtimes) and does
    not say how many calls it starts."""

    def __init__(self, runtimes: object) -> None:
        self.runtimes = runtimes

    def watch(self, target: Target, options: Mapping[str, Any]) -> None:
        return None

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        return Sensed(values={})


class Raising(Unsaid):
    """One that says, and cannot count these options."""

    def model_calls(self, options: Mapping[str, Any]) -> int:
        raise ValueError("cannot count")


@pytest.mark.parametrize(
    ("adapter", "why"),
    [(Unsaid, "it does not say how many"), (Raising, "its adapter raised ValueError")],
)
def test_an_entry_it_cannot_count_is_named_not_left_out(
    tmp_path: Path, adapter: type, why: str
) -> None:
    registry = matrix.default_registry(discover=False)
    register(registry)
    registry.register(POINTS["sensor"], "acme.v1.sensor.unsaid", adapter, needs={RUNTIMES})
    (tmp_path / "kinds").mkdir()
    (tmp_path / "kinds" / "unsaid.yaml").write_text(
        "schema: ix.v1.kind\nname: acme.v1.sensor.unsaid\nsummary: Says nothing.\n"
        "needs: [response]\n"
        "metrics: [{name: said_nothing, direction: higher, shape: boolean}]\n"
    )
    data = _session(runtime="mock")
    data["sensors"] = [
        {"kind": "text-match", "contains": "hello"},
        {"kind": "acme.v1.sensor.unsaid"},
    ]
    (tmp_path / "experiment.yaml").write_text(json.dumps(data))
    settings = matrix.Config[IxSettings](client=IxSettings(), matrix=matrix.MatrixConfig())
    made = Root(registry=registry, settings=settings).plan(tmp_path)
    assert made.work.uncounted == (f"sensors.1 ({why})",)
    assert "not counted, though each may call a model: sensors.1" in made.work.text()
