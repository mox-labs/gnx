"""The extension contract is one public module, and an adapter can be written against it alone.

Review D2's falsifiers: a built-in adapter that still imports a need's Protocol from another
adapter module, or a third party's adapter that cannot be written against ``ix.extension``
alone. The third party here is a lab: its kinds are files beside its experiment, and its
adapters (a subject that opens a :class:`~ix.extension.Respondent` in the shared
:class:`~ix.extension.Sessions`, and a sensor) import nothing of ix but ``ix.extension``. A
built-in probe puts its prompt to the subject's respondent, and an exploratory run reads it.
"""

from __future__ import annotations

import ast
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix
import yaml

import ix
import ix.extension
from ix.composition import Root
from ix.composition.entry_point import register
from ix.composition.settings import IxSettings
from ix.extension import (
    JUDGE_MODELS,
    LAUNCHER,
    POINTS,
    RUNTIMES,
    SESSIONS,
    Interaction,
    Sensed,
    Sessions,
    SubjectConfig,
    Target,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

SRC = Path(ix.__file__).parent

#: What the root supplies an adapter, by need, and the Protocol of each.
NEED_PROTOCOLS = {"Launcher", "Runtimes", "Respondent", "Sessions", "JudgeModels"}


class Shouted:
    """A third party's respondent: it answers every prompt in capitals."""

    simulated = False

    async def answer(
        self, prompt: str, options: Mapping[str, Any]
    ) -> Mapping[str, Mapping[str, Any]]:
        return {"response": {"prompt": prompt, "text": prompt.upper()}, "usage": {}}


class ShoutingSubject:
    """A third party's subject: each trial opens a :class:`Shouted` under a handle."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def reach(self, name: str, subject: SubjectConfig, *, base: str) -> AsyncIterator[Target]:
        handle = self._sessions.open(Shouted())
        try:
            yield Target(subject=name, facets={"respondent": handle})
        finally:
            self._sessions.close(handle)


class CapitalsSensor:
    """A third party's sensor: whether the reply is in capitals."""

    def watch(self, target: Target, options: Mapping[str, Any]) -> None:
        return None

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        text = str(interaction.observations["response"]["text"])
        return Sensed(values={"in_capitals": text.isupper()}, detail={"length": len(text)})


class NeedsEverything:
    """An adapter that declares every need: the root supplies each."""

    def __init__(
        self, launcher: object, runtimes: object, sessions: object, judge_models: object
    ) -> None:
        self.given = (launcher, runtimes, sessions, judge_models)

    def watch(self, target: Target, options: Mapping[str, Any]) -> None:
        return None

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        return Sensed(values={})


def _registry() -> matrix.Registry:
    registry = matrix.default_registry(discover=False)
    register(registry)
    registry.register(
        POINTS["subject"], "acme.v1.subject.shouting", ShoutingSubject, needs={SESSIONS}
    )
    registry.register(POINTS["sensor"], "acme.v1.sensor.capitals", CapitalsSensor)
    registry.register(
        POINTS["sensor"],
        "acme.v1.sensor.everything",
        NeedsEverything,
        needs={LAUNCHER, RUNTIMES, SESSIONS, JUDGE_MODELS},
    )
    return registry


def _root() -> Root:
    settings = matrix.Config[IxSettings](client=IxSettings(), matrix=matrix.MatrixConfig())
    return Root(registry=_registry(), settings=settings)


def _lab(tmp_path: Path) -> Path:
    kinds = tmp_path / "kinds"
    kinds.mkdir()
    declared: list[dict[str, Any]] = [
        {
            "schema": "ix.v1.kind",
            "name": "acme.v1.subject.shouting",
            "summary": "Answers every prompt in capitals.",
            "overlap": "allowed",
            "offers": ["respondent"],
        },
        {
            "schema": "ix.v1.kind",
            "name": "acme.v1.sensor.capitals",
            "summary": "Whether the reply is in capitals.",
            "overlap": "allowed",
            "needs": ["response"],
            "metrics": [{"name": "in_capitals", "direction": "higher", "shape": "boolean"}],
        },
        {
            "schema": "ix.v1.kind",
            "name": "acme.v1.sensor.everything",
            "summary": "Needs everything the root supplies.",
            "needs": ["response"],
            "metrics": [{"name": "nothing_read", "direction": "higher", "shape": "boolean"}],
        },
    ]
    for kind in declared:
        (kinds / f"{kind['name']}.yaml").write_text(yaml.safe_dump(kind))
    experiment = {
        "name": "shouts",
        "question": "Does the subject answer in capitals?",
        "genre": "evaluate",
        "subjects": {"it": {"kind": "acme.v1.subject.shouting"}},
        "probes": [{"id": "hello", "kind": "prompt", "prompt": "hello"}],
        "primary": {"metric": "in_capitals"},
        "threshold": {"op": ">=", "value": 0.5},
        "repeats": 5,
    }
    (tmp_path / "experiment.yaml").write_text(yaml.safe_dump(experiment))
    return tmp_path


class TestTheContractIsOneModule:
    def test_it_exports_what_an_adapter_author_needs(self) -> None:
        exported = set(ix.extension.__all__)
        assert {"SpanReader", "Recorded", "Attribution"} <= exported
        assert exported >= NEED_PROTOCOLS
        assert {"LAUNCHER", "RUNTIMES", "SESSIONS", "JUDGE_MODELS", "POINTS", "PORTS"} <= exported
        assert {"Subject", "Probe", "Sensor", "Environment", "Target", "SubjectError"} <= exported
        assert sorted(exported) == sorted(set(ix.extension.__all__))
        for name in exported:
            assert getattr(ix.extension, name) is not None, name

    def test_the_root_supplies_every_need_it_names(self, tmp_path: Path) -> None:
        kinds = _root().kinds(_lab(tmp_path))
        assert "acme.v1.sensor.everything" in kinds.runnable, kinds.unbuildable

    def test_no_adapter_takes_a_need_from_another_adapter(self) -> None:
        """Each need's Protocol is declared once, in ``ix.extension``; an adapter module
        neither declares one nor imports one from a fellow adapter."""
        for file in sorted((SRC / "adapters").rglob("*.py")):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            declared = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
            assert not declared & NEED_PROTOCOLS, (file.name, declared & NEED_PROTOCOLS)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "ix.adapters"
                ):
                    named = {alias.name for alias in node.names}
                    assert not named & NEED_PROTOCOLS, (file.name, node.module, named)

    def test_ix_domain_names_no_port(self) -> None:
        """What an extension implements has one public name, in ``ix.extension``."""
        import ix.domain

        ports = {"Subject", "Probe", "Sensor", "Environment", "Preflight", "Review", "Target"}
        assert not ports & set(ix.domain.__all__)


class TestAThirdPartyAdapter:
    def test_one_written_against_the_contract_alone_runs(self, tmp_path: Path) -> None:
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        adapters = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name in {"Shouted", "ShoutingSubject", "CapitalsSensor"}
        ]
        assert len(adapters) == 3
        names = {
            name.id for cls in adapters for name in ast.walk(cls) if isinstance(name, ast.Name)
        }
        from_ix = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "ix.extension"
            for alias in node.names
        }
        others = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] == "ix"
            and node.module != "ix.extension"
            for alias in node.names
        }
        assert names & from_ix and not names & others, names & others

        lab = _lab(tmp_path)
        report = _root().run(lab, exploratory=True)
        assert report.status == "completed", report.errors
        (estimate,) = [e for e in report.analysis.estimates if e.metric == "in_capitals"]
        assert (estimate.value, estimate.n) == (1.0, 5)
        assert report.indication == "holds"
