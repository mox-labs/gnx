"""Defaults inside a mapping, defaults that follow another option, and a value a subject's
runtime decides: each declared, shown by the plan, frozen by registration, and handed to the
adapter, as every other default is (review D1, completed; design record §29).

A mapping option is declared further in one of two shapes: ``fields`` for fixed keys (an
agent session's ``loop``), ``entries`` for keys the author names (a ``process``
environment's ``services``). ``default_by`` gives a default that follows the value of an
option beside it (``http-load``'s ``concurrency`` follows ``load_model``; a loop's
``prompt``, its ``strategy``). Also here: a collected file too large to keep is no reading,
never a failing one.
"""

from __future__ import annotations

import asyncio
import copy
from typing import TYPE_CHECKING, Any

import pytest
from ix_fakes import carried, experiment, fake_registry

from ix.adapters._out.agent_sensors import FunctionTestSensor
from ix.adapters._out.matrix_agents import _collected
from ix.adapters._out.matrix_process import MatrixLauncher
from ix.application.experiments import Vocabulary, examine, find_deviations, register
from ix.composition import Root
from ix.domain.kind import merged
from ix.domain.run import Interaction
from ix.extension import POINTS

if TYPE_CHECKING:
    from pathlib import Path

    from ix.domain.design import Design
    from ix.domain.experiment import EnvironmentConfig
    from ix.domain.run import Bindings
    from ix.extension import Scope

Write = Any  # the write_experiment fixture


def _design(data: dict[str, Any], vocabulary: Vocabulary) -> Design:
    examined = examine(data, vocabulary)
    assert examined.design is not None, examined.problems
    return examined.design


def _problems(data: dict[str, Any], vocabulary: Vocabulary) -> set[tuple[str, str]]:
    return {(p.path, p.kind) for p in examine(data, vocabulary).problems}


def _sourced(design: Design) -> dict[str, tuple[Any, str]]:
    return {value.path: (value.value, value.origin) for value in design.provenance}


def _with_services(compare_data: dict[str, Any], **service: Any) -> dict[str, Any]:
    data = copy.deepcopy(compare_data)
    data["environment"] = {
        "kind": "process",
        "services": {"upstream": {"command": ["./upstream"], **service}},
    }
    return data


def _looped(evaluate_data: dict[str, Any], **loop: Any) -> dict[str, Any]:
    data = copy.deepcopy(evaluate_data)
    data["subjects"]["catalog"]["loop"] = loop
    return data


class TestAMappingDeclaredFieldByField:
    def test_a_loops_defaults_are_its_kinds_and_its_prompt_follows_its_strategy(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary, builtin_kinds: Any
    ) -> None:
        design = _design(_looped(evaluate_data, strategy="self-review"), vocabulary)
        loop = design.defaults["subjects.catalog"]["loop"]
        assert isinstance(loop, dict) and isinstance(loop["prompt"], str)
        session = builtin_kinds.get("subject", "agent-session")
        prompts = session.options["loop"].fields["prompt"].default_by.values
        assert loop == {
            "rounds": 3,
            "until": "SOLUTION_VERIFIED",
            "test_timeout": "30s",
            "prompt": prompts["self-review"],
        }
        assert "{response}" in loop["prompt"] and "{until}" in loop["prompt"]
        sourced = _sourced(design)
        assert sourced["subjects.catalog.loop.rounds"] == (3, "kind")
        assert sourced["subjects.catalog.loop.prompt"] == (prompts["self-review"], "kind")
        other = _design(_looped(evaluate_data, strategy="test-feedback", rounds=2), vocabulary)
        tested = other.defaults["subjects.catalog"]["loop"]
        assert isinstance(tested, dict) and isinstance(tested["prompt"], str)
        assert tested["prompt"] == prompts["test-feedback"] and "{failures}" in tested["prompt"]
        assert "rounds" not in tested  # written: 2

    def test_what_a_loop_writes_is_held_to_its_fields(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        found = _problems(_looped(evaluate_data, strategy="self-review", roundz=2), vocabulary)
        assert ("subjects.catalog.loop.roundz", "option_unknown") in found
        found = _problems(_looped(evaluate_data, rounds=2), vocabulary)
        assert ("subjects.catalog.loop.strategy", "option_missing") in found
        found = _problems(_looped(evaluate_data, strategy="sideways"), vocabulary)
        assert ("subjects.catalog.loop.strategy", "option_invalid") in found


class TestAMappingOfNamedEntries:
    def test_each_service_is_given_its_defaults_shown_at_its_path(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        design = _design(_with_services(compare_data, ready="http://127.0.0.1:1/"), vocabulary)
        assert design.defaults["environment"] == {
            "services": {"upstream": {"cwd": ".", "startup_timeout": "30s"}}
        }
        assert _sourced(design)["environment.services.upstream.startup_timeout"] == ("30s", "kind")
        assert design.options("environment", {"services": {"upstream": {"cwd": "bin"}}}) == {
            "services": {"upstream": {"cwd": "bin", "startup_timeout": "30s"}}
        }

    def test_each_service_is_held_to_its_declaration(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        data = _with_services(compare_data, startup_timeout="soon")
        del data["environment"]["services"]["upstream"]["command"]
        found = _problems(data, vocabulary)
        assert ("environment.services.upstream.command", "option_missing") in found
        assert ("environment.services.upstream.startup_timeout", "option_invalid") in found

    def test_a_nested_default_that_moves_after_registration_is_a_deviation(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        data = _with_services(compare_data)
        registered = register(
            examine(data, vocabulary), vocabulary, None, location="r", now="", version=""
        ).registration
        process = vocabulary.kinds.kinds["ix.v1.environment.process"]
        services = process.options["services"]
        assert services.entries is not None
        entries = services.entries.model_copy(
            update={
                "fields": {
                    **services.entries.fields,
                    "startup_timeout": services.entries.fields["startup_timeout"].model_copy(
                        update={"default": "90s"}
                    ),
                }
            }
        )
        later = process.model_copy(
            update={
                "options": {
                    **process.options,
                    "services": services.model_copy(update={"entries": entries}),
                }
            }
        )
        moved = Vocabulary(
            genres=vocabulary.genres,
            kinds=vocabulary.kinds.model_copy(
                update={"kinds": {**vocabulary.kinds.kinds, process.name: later}}
            ),
        )
        found = {
            d.field: (d.registered, d.current)
            for d in find_deviations(
                examine(copy.deepcopy(data), moved), moved, registered
            ).deviations
        }
        assert found["design.defaults.environment.services.upstream.startup_timeout"] == (
            "30s",
            "90s",
        )


class _Services:
    """A process environment that records what it is brought up with, and starts nothing."""

    given: list[dict[str, Any]] = []

    async def up(self, environment: EnvironmentConfig, *, base: str, scope: Scope) -> Bindings:
        _Services.given.append(dict(environment.options))
        return {}


def test_the_environment_is_given_each_services_defaults(write_experiment: Write) -> None:
    _Services.given.clear()
    data = experiment()
    data["environment"] = {"kind": "process", "services": {"up": {"command": ["./up"]}}}
    registry = fake_registry()
    registry.register(POINTS["environment"], "ix.v1.environment.process", _Services)
    report = Root(registry=registry).run(write_experiment(data), exploratory=True)
    assert report.status == "completed", report.errors
    (given,) = _Services.given
    assert given["services"]["up"] == {"command": ["./up"], "cwd": ".", "startup_timeout": "30s"}


class TestADefaultThatFollowsAnotherOption:
    def test_the_clients_follow_the_load_model(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        assert _design(compare_data, vocabulary).defaults["probes.download"]["concurrency"] == 100
        closed = copy.deepcopy(compare_data)
        closed["probes"][0] = {**closed["probes"][0], "load_model": "closed"}
        del closed["probes"][0]["rate"]
        design = _design(closed, vocabulary)
        assert design.defaults["probes.download"]["concurrency"] == 1
        assert _sourced(design)["probes.download.concurrency"] == (1, "kind")

    def test_it_must_be_written_when_a_cell_fills_what_it_follows(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        data = copy.deepcopy(compare_data)
        data["genre"], data["factors"] = "characterize", {"model": ["closed", "open"]}
        for key in ("comparison", "margin"):
            del data[key]
        data["probes"][0]["load_model"] = "{model}"
        assert ("probes.0.concurrency", "option_missing") in _problems(data, vocabulary)
        data["probes"][0]["concurrency"] = 8
        assert ("probes.0.concurrency", "option_missing") not in _problems(data, vocabulary)


def test_activations_tool_is_said_to_be_the_subjects_runtimes(
    evaluate_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    value, origin = _sourced(_design(evaluate_data, vocabulary))["sensors.activation.tool"]
    assert (value, origin) == (None, "runtime")
    note = next(
        v.note
        for v in _design(evaluate_data, vocabulary).provenance
        if v.path == "sensors.activation.tool"
    )
    assert "mock decides" in note


class TestAFileTooLargeToKeep:
    def test_it_is_said_to_be_too_large_and_not_kept(self, tmp_path: Path) -> None:
        (tmp_path / "small.py").write_text("x = 1\n")
        (tmp_path / "large.py").write_text("x" * 100)
        found = _collected(tmp_path, ["small.py", "large.py", "absent.py"], limit=50)
        assert found == {"files": {"small.py": "x = 1\n"}, "files_too_large": ["large.py"]}

    def test_a_sensor_reading_it_gives_no_reading_not_a_failing_one(self) -> None:
        interaction = Interaction(
            seq=0,
            subject="agent",
            role="subject",
            probe="ask",
            started_ns=0,
            duration_ns=1,
            observations={"response": {"files": {}, "files_too_large": ["solution.py"]}},
        )
        sensor = FunctionTestSensor(MatrixLauncher())
        truth = {"function_name": "f", "test_cases": [{"input": 1, "expected": 1}]}
        options = carried("sensor", "function-test", {"file": "solution.py"})
        with pytest.raises(ValueError, match="larger than the probe's collect_limit"):
            asyncio.run(sensor.sense(interaction, truth=truth, options=options))


def test_a_deep_merge_keeps_what_is_written() -> None:
    defaults = {"loop": {"rounds": 3, "until": "DONE"}, "timeout": "600s"}
    written = {"loop": {"rounds": 1, "strategy": "self-review"}, "timeout": "5s"}
    assert merged(defaults, written) == {
        "loop": {"rounds": 1, "until": "DONE", "strategy": "self-review"},
        "timeout": "5s",
    }
