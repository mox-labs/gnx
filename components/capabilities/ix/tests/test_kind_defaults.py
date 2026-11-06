"""A kind option's default is declared data (review D1, ruling Q1).

The declaration holds it, and nothing else does: the design resolves it for each entry that
leaves the option out, the plan shows it with the kind it came from, a registration freezes
it, so a default that moves later is a deviation and never a silent change of what a
registered run does, and the adapter is given it with the options written. A value an
option takes from its runtime is one ix cannot know, and the plan says whose it is.

The review's falsifiers are both here: a built-in adapter that still falls back to a value
of its own for an option its kind declares a default for, and a plan of
``examples/harness-integrity`` that leaves out an option its kinds default.
"""

from __future__ import annotations

import ast
import copy
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from ix_fakes import FakeLoad, experiment, fake_registry
from pydantic import ValidationError

import ix
from ix.adapters._out.matrix_judge import JudgeSensor
from ix.application.experiments import Vocabulary, examine, find_deviations, register
from ix.composition import Root
from ix.domain.kind import Kind, Kinds, Option, kinds
from ix.domain.ports import Surroundings
from ix.domain.run import Target
from ix.domain.schema import DeclarationFile

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from ix.domain.experiment import SubjectConfig

PACKAGE = Path(ix.__file__).resolve().parents[2]
ADAPTERS = PACKAGE / "src" / "ix" / "adapters" / "_out"

Write = Any  # the write_experiment fixture


def _design(data: dict[str, Any], vocabulary: Vocabulary) -> ix.Design:
    examined = examine(data, vocabulary)
    assert examined.design is not None, examined.problems
    return examined.design


def _sourced(design: ix.Design) -> dict[str, tuple[Any, str, str]]:
    return {value.path: (value.value, value.origin, value.note) for value in design.provenance}


class TestTheDeclaration:
    @pytest.mark.parametrize(
        ("option", "refused"),
        [
            ({"type": "duration", "default": "soon"}, "is not a duration"),
            ({"type": "string", "allowed": ["a", "b"], "default": "c"}, "of a, b"),
            ({"type": "integer", "default": 1.5}, "is not a integer"),
            ({"type": "string", "required": True, "default": "a"}, "a required option"),
            ({"default": "a", "default_from": ["runtime"]}, "one of default, default_by"),
            (
                {"type": "integer", "default_by": {"option": "x", "values": {"a": "b"}}},
                "is not a integer",
            ),
            ({"type": "string", "fields": {"a": {}}}, "only a mapping declares fields"),
        ],
    )
    def test_a_default_is_one_its_option_takes(self, option: dict[str, Any], refused: str) -> None:
        with pytest.raises(ValidationError, match=refused):
            Option.model_validate(option)

    def test_a_default_holds_only_a_placeholder_an_enclosing_option_binds(self) -> None:
        """A placeholder in a default is one only the adapter fills: an option that encloses
        it says it ``binds`` it (a loop's ``{response}``), or it is refused."""
        options: dict[str, Any] = {"x": {"default": "{size}"}}
        with pytest.raises(ValidationError, match="nothing binds there"):
            Kind.model_validate({"name": "lab.v1.subject.x", "summary": "s", "options": options})
        bound = {
            "loop": {
                "type": "mapping",
                "binds": ["response"],
                "fields": {"prompt": {"default": "again: {response}"}},
            }
        }
        Kind.model_validate({"name": "lab.v1.subject.x", "summary": "s", "options": bound})

    @pytest.mark.parametrize("named", [["nope"], ["model"]])
    def test_a_default_from_a_runtime_names_another_option(self, named: list[str]) -> None:
        with pytest.raises(ValidationError, match="each names another option"):
            Kind.model_validate(
                {
                    "name": "lab.v1.subject.x",
                    "summary": "s",
                    "options": {"runtime": {}, "model": {"default_from": named}},
                }
            )

    def test_a_kind_file_with_a_default_its_option_cannot_take_is_refused_whole(self) -> None:
        bad = {
            "name": "lab.v1.probe.x",
            "summary": "s",
            "options": {"timeout": {"type": "duration", "default": "soon"}},
        }
        found = kinds([DeclarationFile(origin="x.yaml", data=bad)], grammar=lambda name: None)
        assert "lab.v1.probe.x" not in found.kinds
        assert {problem.kind for problem in found.rejected["lab.v1.probe.x"]} == {"kind_invalid"}

    def test_what_an_entry_leaves_out_is_what_it_is_given(self, builtin_kinds: Kinds) -> None:
        load = builtin_kinds.get("probe", "http-load")
        assert load is not None
        assert load.defaults({"timeout": "5s"}) == {"warmup": "0s", "method": "GET"}
        session = builtin_kinds.get("subject", "agent-session")
        assert session is not None
        assert session.left_to_runtime({"runtime": "claude-sdk"}) == {
            "model": "claude-sdk",
            "max_turns": "claude-sdk",
        }
        assert session.left_to_runtime({"runtime": "mock", "max_turns": 3}) == {"model": "mock"}


#: What an adapter reads options from: an entry's, and a ``process`` environment's record
#: of one service, which its kind declares field by field.
OPTION_RECEIVERS = frozenset({"options", "service"})


def _fallbacks(tree: ast.AST, defaulted: set[str]) -> list[str]:
    """Each place ``tree`` reads an option its kind defaults with a value of its own:
    ``options.get("timeout", 30)``, ``options.get("timeout") or 30``, and the same of a
    service's record."""
    found: list[str] = []

    def reads(node: ast.AST) -> str | None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in OPTION_RECEIVERS
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in defaulted
        ):
            return str(node.args[0].value)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (name := reads(node)) and len(node.args) > 1:
            found.append(f"{name} (a second argument), line {node.lineno}")
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for value in node.values[:-1]:
                inner = value.args[0] if isinstance(value, ast.Call) and value.args else value
                if (name := reads(value) or reads(inner)) is not None:
                    found.append(f"{name} (or ...), line {node.lineno}")
    return found


def test_no_built_in_adapter_holds_a_default_its_kind_declares(builtin_kinds: Kinds) -> None:
    """The first falsifier: a built-in adapter that reads an option its kind defaults with a
    fallback of its own would hold the default in two places, one the plan never shows."""
    defaulted: set[str] = set()

    def walk(options: Mapping[str, Option]) -> None:
        for name, option in options.items():
            if option.default is not None or option.default_by is not None:
                defaulted.add(name)
            walk(option.fields)
            if option.entries is not None:
                walk(option.entries.fields)

    for kind in builtin_kinds.kinds.values():
        walk(kind.options)
    assert {"timeout", "interval", "startup_timeout", "pass_at", "repeats", "rounds"} <= defaulted
    assert {"concurrency", "until", "setup_timeout", "collect_limit", "cwd"} <= defaulted
    found = {
        file.name: held
        for file in sorted(ADAPTERS.glob("*.py"))
        if (held := _fallbacks(ast.parse(file.read_text(encoding="utf-8")), defaulted))
    }
    assert found == {}


def test_the_scan_finds_a_fallback(tmp_path: Path) -> None:
    source = (
        "def f(options):\n"
        "    a = options.get('timeout', 30)\n"
        "    b = seconds(options.get('interval')) or 0.2\n"
        "    c = options.get('repeats')\n"
    )
    found = _fallbacks(ast.parse(source), {"timeout", "interval", "repeats"})
    assert [line.split(" ")[0] for line in found] == ["timeout", "interval"]


class TestTheDesign:
    def test_an_option_left_out_is_its_kinds_default_and_says_so(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        design = _design(compare_data, vocabulary)
        assert design.defaults["probes.download"] == {
            "warmup": "0s",
            "timeout": "60s",
            "method": "GET",
            "concurrency": 100,  # load_model: open
        }
        assert design.defaults["subjects.buffered"] == {"cwd": ".", "startup_timeout": "30s"}
        assert "sensors.timing" not in design.defaults  # it takes no option
        sourced = _sourced(design)
        assert sourced["probes.download.timeout"] == ("60s", "kind", "ix.v1.probe.http-load")
        assert sourced["subjects.streaming.startup_timeout"] == (
            "30s",
            "kind",
            "ix.v1.subject.process",
        )

    def test_an_option_written_is_not_its_kinds(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        compare_data["probes"][0]["timeout"] = "5s"
        design = _design(compare_data, vocabulary)
        assert "timeout" not in design.defaults["probes.download"]
        assert "probes.download.timeout" not in _sourced(design)
        assert design.options("probes.download", {"timeout": "5s"})["timeout"] == "5s"

    def test_a_value_left_to_the_runtime_is_said_to_be_the_runtimes(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        design = _design(evaluate_data, vocabulary)
        value, origin, note = _sourced(design)["subjects.catalog.model"]
        assert (value, origin) == (None, "runtime") and "mock decides" in note
        assert "subjects.catalog.max_turns" not in _sourced(design)  # written: 1
        assert design.defaults["subjects.catalog"] == {"setup_timeout": "300s"}

    def test_the_example_plan_lists_every_default_its_kinds_declare(self) -> None:
        """The second falsifier: ``ix plan --json`` on examples/harness-integrity leaves
        out no option its kinds default, and names each value left to the runtime. Its
        probes are files of the ``prompt`` kind that write no ``timeout``."""
        folder = PACKAGE / "examples" / "harness-integrity"
        plan, design, written = ix.plan(folder), ix.resolve(folder), ix.load(folder)
        kinds = ix.kinds().kinds
        entries = [
            (f"subjects.{name}", kinds[f"ix.v1.subject.{subject.kind}"], subject.options)
            for name, subject in written.subjects.items()
        ]
        entries += [(f"sensors.{s.label}", kinds[s.kind], s.options) for s in design.sensors]
        entries += [
            (f"probes.{probe}", kinds["ix.v1.probe.prompt"], {}) for probe in plan.probes.ids
        ]
        expected: dict[str, str] = {}
        for key, kind, options in entries:
            expected |= {f"{key}.{name}": "kind" for name in kind.defaults(options)}
            expected |= {f"{key}.{name}": "runtime" for name in kind.left_to_runtime(options)}
        sourced = {value.path: value.origin for value in plan.provenance}
        assert "probes.correct-solution.timeout" in expected
        assert "subjects.canned.max_turns" in expected
        assert {path: sourced.get(path) for path in expected} == expected


class TestRegistration:
    def test_a_default_that_moves_after_registration_is_a_deviation(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        registered = register(
            examine(compare_data, vocabulary), vocabulary, None, location="r", now="", version=""
        ).registration
        assert registered.design["defaults"]["probes.download"]["timeout"] == "60s"
        load = vocabulary.kinds.kinds["ix.v1.probe.http-load"]
        later = load.model_copy(
            update={
                "options": {
                    **load.options,
                    "timeout": load.options["timeout"].model_copy(update={"default": "90s"}),
                }
            }
        )
        moved = Vocabulary(
            genres=vocabulary.genres,
            kinds=vocabulary.kinds.model_copy(
                update={"kinds": {**vocabulary.kinds.kinds, load.name: later}}
            ),
        )
        found = find_deviations(examine(copy.deepcopy(compare_data), moved), moved, registered)
        by_field = {deviation.field: deviation for deviation in found.deviations}
        moved_default = by_field["design.defaults.probes.download.timeout"]
        assert (moved_default.registered, moved_default.current) == ("60s", "90s")
        assert "plan_digest" in by_field


class _Recording(FakeLoad):
    """The fake load probe, keeping the options each application was given."""

    given: list[dict[str, Any]] = []

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        _Recording.given.append(dict(options))
        return await super().apply(options, target, seed=seed)


class _Reaching:
    """A subject that records the options it is reached with, and starts nothing."""

    given: list[dict[str, Any]] = []

    @asynccontextmanager
    async def reach(self, name: str, subject: SubjectConfig, *, base: str) -> AsyncIterator[Target]:
        _Reaching.given.append(dict(subject.options))
        yield Target(subject=name, facets={"url": "http://fake", "env": {}})


def test_the_adapter_is_given_each_default_with_what_is_written(write_experiment: Write) -> None:
    _Recording.given.clear()
    _Reaching.given.clear()
    root = Root(registry=fake_registry(probe=_Recording, subject=_Reaching))
    report = root.run(write_experiment(experiment()), exploratory=True)
    assert report.status == "completed", report.errors
    probe, subject = _Recording.given[0], _Reaching.given[0]
    assert (probe["timeout"], probe["method"], probe["warmup"]) == ("60s", "GET", "0s")
    assert probe["duration"] == "1s"  # as written
    assert (subject["cwd"], subject["startup_timeout"]) == (".", "30s")


def test_a_judge_asked_to_read_each_response_fewer_than_once_is_refused() -> None:
    judge = JudgeSensor(runtimes=None)  # type: ignore[arg-type]  # a review builds nothing
    around = Surroundings(path="sensors.0", base=".")
    (refused,) = judge.review({"model_runtime": "mock", "rubric": "r", "repeats": 0}, around)
    assert (refused.path, refused.kind) == ("sensors.0.repeats", "option_invalid")
    assert judge.review({"model_runtime": "mock", "rubric": "r"}, around) == []
