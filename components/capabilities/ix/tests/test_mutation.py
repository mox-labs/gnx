"""Mutation testing: whether a test suite fails when the code is wrong, as ix measures it.

Each test guards one requirement of the mutation design (DESIGN-ix-mutation-genre-2026-11-06,
its D-numbers) or of the rulings on it (Q-MUT-R, Q-MUT-G), named in its docstring. The trials
are real: a small package and its pytest suite in a temporary tree, run by this interpreter,
which has pytest and pytest-cov. No model is called. Every process a trial starts is in a
group the launcher ends, within the trial's own time limits; sampling's processes the same.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix
import pytest
import yaml

import ix
from ix.adapters._out.mutation import reading
from ix.adapters._out.mutation.population import Mutant, candidates, probe_file, sample
from ix.composition import Root
from ix.composition.entry_point import register
from ix.composition.settings import IxSettings
from ix.extension import MUTANT_SOURCE, Mutation, Mutations, Unmade

if TYPE_CHECKING:
    from collections.abc import Callable

CORE = """\
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

LIMIT = 3


def small(x: int) -> bool:
    if x < LIMIT:
        return True
    return False


def count(n: int) -> int:
    total = 0
    while total < n:
        total += 1
    return total


def label(flag: bool) -> str:
    return "on" if flag else "off"


def refused(resume: object, kind: str) -> bool:
    return (
        resume is not None
        and kind == "run_failed"
    )
"""

TESTS = """\
from calc.core import count, small


def test_small():
    assert small(1) is True
    assert small(5) is False


def test_count():
    assert count(2) == 2
"""

#: A test that passes only after another has run: it fails alone, mutant or not.
ORDER = """\
from calc.core import label

SEEN = []


def test_a_first():
    SEEN.append(True)


def test_b_after():
    assert label(True) == "on"
    assert SEEN, "passes only after test_a_first"
"""

SCOPE = "src/calc/core.py"


def _tree(where: Path, *, order: bool = False, failing: bool = False) -> Path:
    tree = where / "tree"
    (tree / "src" / "calc").mkdir(parents=True)
    (tree / "tests").mkdir()
    (tree / "src" / "calc" / "__init__.py").write_text("")
    (tree / SCOPE).write_text(CORE)
    (tree / "tests" / "test_core.py").write_text(TESTS)
    if order:
        (tree / "tests" / "test_order.py").write_text(ORDER)
    if failing:
        (tree / "tests" / "test_broken.py").write_text("def test_broken():\n    assert False\n")
    return tree


def _digest(tree: Path) -> str:
    """Every file of the tree and what it holds."""
    found = hashlib.sha256()
    for path in sorted(p for p in tree.rglob("*") if p.is_file()):
        found.update(f"{path.relative_to(tree)}\0".encode() + path.read_bytes())
    return found.hexdigest()


def _at(old: str, *, after: str = "") -> tuple[list[int], list[int]]:
    """Where ``old`` first stands in CORE, after the first ``after``: [line, column] each."""
    offset = CORE.index(old, CORE.index(after) if after else 0)
    line = CORE.count("\n", 0, offset) + 1
    column = offset - (CORE.rfind("\n", 0, offset) + 1)
    return [line, column], [line, column + len(old)]


def _mutant(old: str, new: str, *, after: str = "", **more: Any) -> dict[str, Any]:
    start, end = _at(old, after=after)
    return {"kind": "mutant", "file": SCOPE, "start": start, "end": end, "original": old,
            "replacement": new, "operator": "hand/swap", **more}  # fmt: skip


def _suite(experiment: Path, tree: Path, **options: Any) -> dict[str, Any]:
    def relative(path: Path) -> str:
        return os.path.relpath(path, experiment)

    return {
        "kind": "test-suite",
        "root": relative(tree),
        "command": [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        "tests": [relative(tree / "tests")],
        "scope": [relative(tree / SCOPE)],
        "map": "mutation/suite.map.json",
        **options,
    }


def _experiment(
    where: Path,
    tree: Path,
    *,
    probes: list[dict[str, Any]] | None = None,
    genre: str = "mutation-score",
    subjects: dict[str, dict[str, Any]] | None = None,
    **options: Any,
) -> Path:
    experiment = where / "experiment"
    experiment.mkdir(exist_ok=True)
    data: dict[str, Any] = {
        "name": "calc-mutation",
        "question": "Do calc's tests fail when core.py is wrong?",
        "genre": genre,
        "subjects": subjects or {"suite": _suite(experiment, tree, **options)},
        "primary": {"metric": "killed"},
    }
    if genre == "mutation-regress":
        data["margin"] = "5pp"
    if probes:
        data["probes"] = probes
    (experiment / "experiment.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return experiment


class Swaps:
    """A homemade mutant source, a second generator beside cosmic-ray: each ``(operator, old,
    new)`` at every place ``old`` stands in the text, and one operator it could not make."""

    def __init__(self, *swaps: tuple[str, str, str]) -> None:
        self._swaps = swaps

    def mutations(self, text: str) -> Mutations:
        made = []
        for operator, old, new in self._swaps:
            count = 0
            for number, line in enumerate(text.splitlines(keepends=True), start=1):
                column = line.find(old)
                while column >= 0:
                    made.append(
                        Mutation(
                            key=f"{operator}:{count}",
                            operator=operator,
                            start=(number, column),
                            end=(number, column + len(old)),
                            replacement=new,
                        )
                    )
                    count += 1
                    column = line.find(old, column + 1)
        unmade = (Unmade(operator="swap/broken", count=2, reason="ValueError: no"),)
        return Mutations(made=tuple(made), unmade=unmade)


def _root(**sources: Callable[[], Any]) -> Root:
    """A root over ix's own registrations and ``sources`` at the mutant-source point, by
    type URL: none installed unless given."""
    registry = matrix.default_registry(discover=False)
    register(registry)
    for name, build in sources.items():
        registry.register(MUTANT_SOURCE, name, build)
    settings = matrix.Config[IxSettings](client=IxSettings(), matrix=matrix.MatrixConfig())
    return Root(registry=registry, settings=settings)


NEGATE = ("swap/negate", "if x < LIMIT", "if not x < LIMIT")


def _outcomes(experiment: Path) -> list[dict[str, Any]]:
    """What each trial of the experiment's latest run left, or why it left nothing."""
    (run,) = sorted((experiment / ".ix" / "runs").iterdir())[-1:]
    rows = [json.loads(line) for line in (run / "ix.v1.interaction.jsonl").read_text().splitlines()]
    return [row["observations"].get("outcome") or {"failure": row["failure"]} for row in rows]


# --- a trial ---------------------------------------------------------------------------------


def test_a_mutant_a_test_asserts_is_killed_from_the_copy_and_the_tree_is_untouched(
    tmp_path: Path,
) -> None:
    """D6 and acceptance A1, A3: the tree is read, never written; each trial imports its own
    copy; a negated condition a test asserts reads killed, by the test that caught it."""
    tree = _tree(tmp_path)
    before = _digest(tree)
    root = _root(**{"acme.v1.mutant-source": lambda: Swaps(NEGATE)})
    experiment = _experiment(tmp_path, tree)
    sampled = root.mutants_sample(experiment)
    assert (sampled["population"], sampled["sampled"], sampled["census"]) == (1, 1, True)
    root.run(experiment, exploratory=True)
    (outcome,) = _outcomes(experiment)
    assert outcome["status"] == "killed" and outcome["stage"] == "selected"
    assert outcome["killers"] == ["tests/test_core.py::test_small"]
    assert outcome["imported_from"] == {"calc.core": "copy"}
    assert _digest(tree) == before


def _site(where: Path, line: str) -> Path:
    """A site folder whose ``.pth`` holds ``line``, as an editable install's does."""
    site = where / "site"
    site.mkdir()
    (site / "calc-editable.pth").write_text(line + "\n")
    return site


def _with_site(site: Path) -> list[str]:
    """The suite's command, with ``site`` processed as site-packages are, ``.pth`` and all."""
    code = (
        f"import site, sys; site.addsitedir({str(site)!r}); import pytest; sys.exit(pytest.main())"
    )
    return [sys.executable, "-c", code, "tests", "-q", "-p", "no:cacheprovider"]


def test_the_copy_is_imported_ahead_of_an_editable_install_of_the_tree(tmp_path: Path) -> None:
    """D6, the observed hazard: an editable install puts the original tree on the path through
    a ``.pth`` file, and a copy run under it imported the original, so every mutant survived.
    Here the original is on the path that way, and the mutant is still the one run."""
    tree = _tree(tmp_path)
    site = _site(tmp_path, str(tree / "src"))
    probes = [_mutant("x < LIMIT", "x >= LIMIT")]
    experiment = _experiment(tmp_path, tree, probes=probes, command=_with_site(site))
    root = _root()
    root.mutants_map(experiment)
    root.run(experiment, exploratory=True)
    (outcome,) = _outcomes(experiment)
    assert outcome["status"] == "killed", outcome
    assert outcome["imported_from"] == {"calc.core": "copy"}


def test_a_trial_that_imported_the_original_is_the_harnesss_failure_never_a_survivor(
    tmp_path: Path,
) -> None:
    """D6's invariant, checked on every run: an install that puts the original ahead of the
    copy (a ``.pth`` that inserts it first, as an editable finder may) leaves the mutant
    unrun. The trial is the harness's (``not_the_mutant``), never ``survived``, and a baseline
    run so is refused before any mutant is drawn."""
    tree = _tree(tmp_path)
    site = _site(tmp_path, f"import sys; sys.path.insert(0, {str(tree / 'src')!r})")
    probes = [_mutant("x < LIMIT", "x >= LIMIT")]
    experiment = _experiment(tmp_path, tree, probes=probes)
    root = _root()
    root.mutants_map(experiment)
    trapped = _experiment(tmp_path, tree, probes=probes, command=_with_site(site))
    root.run(trapped, exploratory=True)
    (outcome,) = _outcomes(trapped)
    assert outcome["failure"]["reason"] == "not_the_mutant", outcome
    read = root.mutants_read(trapped)["subjects"][0]
    assert (read["read"], read["unread"]) == (0, {"not_the_mutant": 1})
    with pytest.raises(ix.ConfigError, match="not from its copy"):
        root.mutants_map(trapped)


def test_a_failure_the_original_also_fails_is_no_kill(tmp_path: Path) -> None:
    """D8: a kill counts only when the failing test passes without the mutant. A test that
    fails whenever it runs alone fails on the mutant and on the original, so it is
    ``unattributed`` and the trial the harness's (``unattributable``)."""
    tree = _tree(tmp_path, order=True)
    probes = [_mutant('"on"', '"no"')]
    experiment = _experiment(tmp_path, tree, probes=probes)
    root = _root()
    root.mutants_map(experiment)
    root.run(experiment, exploratory=True)
    (outcome,) = _outcomes(experiment)
    assert outcome["failure"]["reason"] == "unattributable", outcome
    assert "test_b_after" in outcome["failure"]["message"]


#: A test that fails the first time a trial selects it in a copy, and passes after: a flake.
FLAKY = """\
import os
from pathlib import Path

from calc.core import label


def test_fails_once_under_a_trial():
    label(True)
    marker = Path("flaked.marker")
    if os.environ.get("IX_MUTATION_SELECT") and not marker.exists():
        marker.write_text("x")
        raise AssertionError("fails the first time a trial selects it, and passes after")
"""


def test_a_failure_that_does_not_recur_on_the_mutant_is_no_kill(tmp_path: Path) -> None:
    """Regression, found in the acceptance run on ``items.py``: a test that failed under load
    in a whole-suite run and passed alone was credited with killing mutants on lines it never
    ran, since it failed on the mutant and passed without it. A kill must recur on the
    mutant; a failure that does not is ``unreproduced``, and the test passed when it ran
    again."""
    tree = _tree(tmp_path)
    (tree / "tests" / "test_flaky.py").write_text(FLAKY)
    probes = [_mutant('"on"', '"no"')]
    experiment = _experiment(tmp_path, tree, probes=probes)
    root = _root()
    root.mutants_map(experiment)
    root.run(experiment, exploratory=True)
    (outcome,) = _outcomes(experiment)
    assert outcome["status"] == "survived", outcome
    assert outcome["unreproduced"] == ["tests/test_flaky.py::test_fails_once_under_a_trial"]


#: Two tests of one statement: a slow neighbour, and one that waits for what the mutant
#: breaks, and so takes far longer on the mutant than its baseline time.
SLOW = """\
import time

from calc.core import label


def test_a_slow_neighbour():
    label(True)
    time.sleep(2)


def test_waits_until_it_is_on():
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        if label(True) == "on":
            return
        time.sleep(0.05)
    raise AssertionError("it never read on")
"""


@pytest.mark.timeout(120)
def test_a_kill_that_takes_longer_on_the_mutant_is_reproduced_within_its_stages_limit(
    tmp_path: Path,
) -> None:
    """Regression, found in the second acceptance run: running a failure again on the mutant
    was held to its baseline time, though on the mutant it took far longer (a loop the mutant
    broke, run out by a wait), so real kills read as timeouts. It may take what its stage was
    allowed."""
    tree = _tree(tmp_path)
    (tree / "tests" / "test_slow.py").write_text(SLOW)
    probes = [_mutant('"on"', '"no"')]
    experiment = _experiment(tmp_path, tree, probes=probes, timeout_factor=2, timeout_floor="200ms")
    root = _root()
    root.mutants_map(experiment)
    root.run(experiment, exploratory=True)
    (outcome,) = _outcomes(experiment)
    assert outcome.get("status") == "killed", outcome
    assert outcome["killers"] == ["tests/test_slow.py::test_waits_until_it_is_on"]


def test_each_run_has_a_temporary_directory_of_its_own(tmp_path: Path) -> None:
    """D6, extended by the acceptance run: trials in flight shared the system's temporary
    directory, and a recon test that holds it to what it held before failed under another
    trial's files. Each run of the suite has one of its own, which nothing before it wrote."""
    marker = f"ix-mutation-{uuid.uuid4().hex}"
    probe_temp = textwrap.dedent(f"""\
        import tempfile
        from pathlib import Path

        from calc.core import small


        def test_the_temporary_directory_is_its_own():
            assert small(1) is True
            mark = Path(tempfile.gettempdir(), "{marker}")
            assert not mark.exists(), "an earlier run's file is here"
            mark.write_text("x")
        """)
    tree = _tree(tmp_path)
    (tree / "tests" / "test_temp.py").write_text(probe_temp)
    experiment = _experiment(tmp_path, tree, probes=[_mutant("x < LIMIT", "x >= LIMIT")])
    root = _root()
    try:
        root.mutants_map(experiment)
        root.run(experiment, exploratory=True)
        (outcome,) = _outcomes(experiment)
        assert outcome["status"] == "killed" and outcome["unattributed"] == [], outcome
        assert "tests/test_temp.py::test_the_temporary_directory_is_its_own" in outcome["killers"]
    finally:
        with contextlib.suppress(FileNotFoundError):
            (Path(tempfile.gettempdir()) / marker).unlink()


@pytest.mark.timeout(120)
def test_a_mutant_that_hangs_the_suite_is_unread_never_killed(tmp_path: Path) -> None:
    """R6 and D7: a run past its limit (its tests' baseline time times the factor, never under
    the floor) showed nothing. The trial is unread (``timeout``), never killed, and the run
    ends within the limit."""
    tree = _tree(tmp_path)
    probes = [_mutant("total += 1", "total += 0")]
    experiment = _experiment(tmp_path, tree, probes=probes, timeout_factor=1, timeout_floor="2s")
    root = _root()
    root.mutants_map(experiment)
    root.run(experiment, exploratory=True)
    (outcome,) = _outcomes(experiment)
    assert outcome["failure"]["reason"] == "timeout", outcome
    assert root.mutants_read(experiment)["subjects"][0]["killed"] == 0


@pytest.mark.parametrize(("confirm", "status", "stage"), [
    ("uncovered", "survived", "confirmed"),
    ("none", "uncovered", "none"),
])  # fmt: skip
def test_a_mutant_no_test_runs_is_confirmed_against_every_test_and_is_a_coverage_gap(
    tmp_path: Path, confirm: str, status: str, stage: str
) -> None:
    """R5 and D10, D12: a mutant on a line no test ran is run against every test by default
    (``confirm: uncovered``), and never with ``none``; a survivor no test ran is a coverage
    gap, and either way it counts against the suite."""
    tree = _tree(tmp_path)
    probes = [_mutant('"on"', '"no"')]
    experiment = _experiment(tmp_path, tree, probes=probes, confirm=confirm)
    root = _root()
    root.mutants_map(experiment)
    root.run(experiment, exploratory=True)
    (outcome,) = _outcomes(experiment)
    assert (outcome["status"], outcome["stage"], outcome["covered"]) == (status, stage, False)
    read = root.mutants_read(experiment)
    assert [found["class"] for found in read["survivors"]] == ["coverage_gap"]
    assert read["subjects"][0]["killed"] == 0 and read["subjects"][0]["read"] == 1


def test_a_collection_error_kills_only_when_collection_succeeds_without_the_mutant(
    tmp_path: Path,
) -> None:
    """D8: a mutant that breaks the module at import fails collection, and kills
    (``<collection>``) because collection succeeds once it is restored. A line only imports
    run is run against every test."""
    tree = _tree(tmp_path)
    probes = [_mutant("LIMIT = 3", "LIMIT = 1 / 0")]
    experiment = _experiment(tmp_path, tree, probes=probes)
    root = _root()
    root.mutants_map(experiment)
    root.run(experiment, exploratory=True)
    (outcome,) = _outcomes(experiment)
    assert outcome["status"] == "killed" and outcome["killers"][0] == "<collection>", outcome
    assert outcome["selected"] == "all"


@pytest.mark.parametrize(("probe", "reason"), [
    (_mutant("x < LIMIT", "x >= LIMIT", equivalent="the same at every input"), "equivalent"),
    ({**_mutant("x < LIMIT", "x >= LIMIT"), "original": "x <= LIMIT"}, "mutant_inapplicable"),
])  # fmt: skip
def test_a_mutant_that_cannot_be_read_is_unread_with_its_reason(
    tmp_path: Path, probe: dict[str, Any], reason: str
) -> None:
    """D10 and D2: a mutant marked equivalent runs nothing and is unread; one whose
    ``original`` text the tree does not hold is the harness's, never applied elsewhere."""
    tree = _tree(tmp_path)
    experiment = _experiment(tmp_path, tree, probes=[probe])
    root = _root()
    root.mutants_map(experiment)
    root.run(experiment, exploratory=True)
    (outcome,) = _outcomes(experiment)
    assert outcome["failure"]["reason"] == reason


def test_a_pruned_suite_is_compared_with_the_whole_one_on_the_same_mutants(tmp_path: Path) -> None:
    """D2 and R1: mutation-regress applies each mutant to both suites in one block, and the
    difference of kill rates is the paired one; a variant that deselects the test that kills
    loses those kills, and only those."""
    tree = _tree(tmp_path)
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    subjects = {
        "whole": _suite(experiment, tree),
        "pruned": _suite(experiment, tree, deselect=["tests/test_core.py::test_small"]),
    }
    probes = [
        _mutant("x < LIMIT", "x >= LIMIT"),
        _mutant("return True", "return False"),
        _mutant("total < n", "total <= n"),
    ]
    experiment = _experiment(
        tmp_path, tree, probes=probes, genre="mutation-regress", subjects=subjects
    )
    root = _root()
    root.mutants_map(experiment)
    report = root.run(experiment, exploratory=True)
    read = {found["subject"]: found for found in root.mutants_read(experiment)["subjects"]}
    assert (read["whole"]["killed"], read["pruned"]["killed"]) == (3, 1)
    (decision,) = report.analysis.findings[0].decisions
    assert decision.method == "tango (paired)"
    assert decision.difference == pytest.approx(-200 / 3)


#: Two tests of ``refused``: the first short-circuits before its second line, the second runs
#: that line and cannot tell ``and`` from ``or`` there.
CHAIN = """\
from calc.core import refused


def test_no_resume_is_never_refused():
    assert refused(None, "run_failed") is False


def test_a_resumed_failure_is_refused():
    assert refused(object(), "run_failed") is True
"""


def test_a_test_that_short_circuits_before_the_mutated_line_is_still_run_against_it(
    tmp_path: Path,
) -> None:
    """Regression, found in the acceptance run on ``items.py``: tests were selected by the
    mutated line, and a test that short-circuits a multi-line condition before that line never
    runs it, though the mutant (``and`` for ``or``) changes its path. The only test that ran
    the line could not tell, so the mutant survived with ``confirm: uncovered``, a false
    survivor. Tests are selected by the statement the mutant lies in."""
    tree = _tree(tmp_path)
    (tree / "tests" / "test_chain.py").write_text(CHAIN)
    probes = [_mutant('and kind == "run_failed"', 'or kind == "run_failed"')]
    experiment = _experiment(tmp_path, tree, probes=probes)
    root = _root()
    root.mutants_map(experiment)
    root.run(experiment, exploratory=True)
    (outcome,) = _outcomes(experiment)
    assert outcome["status"] == "killed" and outcome["stage"] == "selected", outcome
    assert outcome["killers"] == ["tests/test_chain.py::test_no_resume_is_never_refused"]


# --- what sampling needs, and says --------------------------------------------------------------


def test_with_no_mutant_source_installed_sampling_and_validation_say_what_to_install(
    tmp_path: Path,
) -> None:
    """HO1 (c') and Q-MUT-G: ix depends on no mutant source. With none installed, sampling
    refuses and validation's problem says what to install."""
    tree = _tree(tmp_path)
    experiment = _experiment(tmp_path, tree)
    root = _root()
    with pytest.raises(ix.ConfigError) as refused:
        root.mutants_sample(experiment)
    fixes = [p.fix for p in root.validate(experiment).problems if p.path == "subjects.suite"]
    assert fixes
    # What to install, and from where: it is on no package index, where another project
    # could take the name (SECURITY.md, I-8).
    for fix in (refused.value.fix or "", fixes[0] or ""):
        assert "install ix-cosmic-ray beside ix" in fix and "no package index" in fix, fix


def test_any_source_at_the_point_draws_the_population_and_two_need_a_choice(
    tmp_path: Path,
) -> None:
    """The mutant-source point is generic: a second generator registers there with no change
    to ix, its population and what it could not make are recorded, and with two installed
    sampling names both and asks which."""
    tree = _tree(tmp_path)
    experiment = _experiment(tmp_path, tree)
    root = _root(**{
        "acme.v1.mutant-source": lambda: Swaps(NEGATE),
        "other.v1.mutant-source": lambda: Swaps(("swap/lt", "<", "<=")),
    })  # fmt: skip
    with pytest.raises(ix.ConfigError, match="acme.v1.mutant-source, other.v1.mutant-source"):
        root.mutants_sample(experiment)
    found = root.mutants_sample(experiment, mutant_source="other.v1.mutant-source", seed=7)
    assert found["source"]["type_url"] == "other.v1.mutant-source"
    assert found["unmade"] == {"swap/broken": {"count": 2, "reason": "ValueError: no"}}
    assert found["settings"]["seed"] == {"value": 7, "from": "given"}
    assert found["settings"]["n"] == {"value": 100, "from": "default"}
    record = json.loads((experiment / "mutation" / "population.json").read_text())
    assert record["sampled"] == [f"{SCOPE}:swap/lt:{n}" for n in range(found["population"])]
    assert len(list((experiment / "probes").iterdir())) == found["sampled"]


@pytest.mark.parametrize("confirm", ["uncovered", "all"])
def test_the_sample_says_what_the_run_will_take_and_its_worst_case(
    tmp_path: Path, confirm: str
) -> None:
    """D12: the sample states the test time a run of it takes, from the baseline; under
    ``confirm: all`` the survivors are not known before the run, so it states the worst case
    beside it, every covered mutant run again against every test."""
    tree = _tree(tmp_path)
    experiment = _experiment(tmp_path, tree, confirm=confirm)
    root = _root(**{"acme.v1.mutant-source": lambda: Swaps(NEGATE, ("swap/lt", "<", "<="))})
    (subject,) = root.mutants_sample(experiment)["subjects"]
    whole = subject["baseline"]["test_time_s"] + subject["baseline"]["overhead_s"]
    assert subject["estimated_s"] > 0
    extra = subject["worst_s"] - subject["estimated_s"]
    assert extra == pytest.approx(0 if confirm == "uncovered" else 3 * whole, abs=0.2)


def test_without_coverage_in_the_suite_environment_sampling_and_the_plan_say_so(
    tmp_path: Path,
) -> None:
    """R4: the per-test line map comes from coverage.py's contexts in the suite's own
    environment, which ix does not depend on. A suite whose Python lacks it is refused, and
    the plan's requirement says what to install."""
    tree = _tree(tmp_path)
    bare = getattr(sys, "_base_executable", "")
    if not bare or bare == sys.executable:
        pytest.fail("this interpreter is not a virtual environment over a bare one")
    probes = [_mutant("x < LIMIT", "x >= LIMIT")]
    experiment = _experiment(tmp_path, tree, probes=probes, command=[bare, "-m", "pytest"])
    root = _root()
    with pytest.raises(ix.ConfigError) as refused:
        root.mutants_map(experiment)
    assert "pip install pytest-cov" in (refused.value.fix or "")
    (mapped,) = [r for r in root.plan(experiment).requirements if "selection map" in r.need]
    assert not mapped.holds and "pip install pytest-cov" in mapped.fix


def test_a_baseline_that_does_not_pass_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    """D8 and the sample's contract: a suite that already fails kills nothing attributably,
    so sampling refuses, names the failing test, and writes no probe, map or record."""
    tree = _tree(tmp_path, failing=True)
    experiment = _experiment(tmp_path, tree)
    root = _root(**{"acme.v1.mutant-source": lambda: Swaps(NEGATE)})
    with pytest.raises(ix.ConfigError, match="test_broken"):
        root.mutants_sample(experiment)
    assert sorted(path.name for path in experiment.iterdir()) == ["experiment.yaml"]


def test_a_map_of_another_scope_stops_the_plan_until_it_is_made_again(tmp_path: Path) -> None:
    """D5 (``map_stale``): a scope that changed since the map was made would be judged on
    another code's lines; the plan's requirement fails and says to make the map again."""
    tree = _tree(tmp_path)
    experiment = _experiment(tmp_path, tree, probes=[_mutant("x < LIMIT", "x >= LIMIT")])
    root = _root()
    root.mutants_map(experiment)
    assert all(r.holds for r in root.plan(experiment).requirements)
    (tree / SCOPE).write_text(CORE + "\n# changed\n")
    (mapped,) = [r for r in root.plan(experiment).requirements if "selection map" in r.need]
    assert not mapped.holds and "changed" in mapped.why and "ix mutants" in mapped.fix


def test_a_mutant_outside_the_scope_is_refused_by_validate(tmp_path: Path) -> None:
    """D9: every mutant lies in the scope its subject declares, or its tests were not judged
    against it."""
    tree = _tree(tmp_path)
    elsewhere = {**_mutant("x < LIMIT", "x >= LIMIT"), "file": "src/calc/other.py"}
    experiment = _experiment(tmp_path, tree, probes=[elsewhere])
    problems = _root().validate(experiment).problems
    assert [(p.path, p.kind) for p in problems] == [("probes.0.file", "option_invalid")]


# --- the population -------------------------------------------------------------------------


def _mutation(old: str, new: str, *, after: str = "", key: str = "k") -> Mutation:
    start, end = _at(old, after=after)
    return Mutation(key=key, operator="op", start=(start[0], start[1]), end=(end[0], end[1]),
                    replacement=new)  # fmt: skip


def test_a_candidate_parses_and_changes_the_code_and_every_other_mutation_is_counted() -> None:
    """Q-MUT-G and D10, acceptance A3: a candidate parses and differs from its source as
    Python's tree; the rest are left out and counted by why (a parenthesis-only change is
    unchanged), never dropped silently. So are the design's exclusions: a type annotation,
    an ``if TYPE_CHECKING:`` block, a line marked ``# ix: no mutate``."""
    marked = CORE.replace("    total = 0\n", "    total = 0  # ix: no mutate\n")
    made = Mutations(
        made=(
            _mutation("x < LIMIT", "x >= LIMIT", key="kept"),
            _mutation("x < LIMIT", "(x < LIMIT)", key="parens"),
            _mutation("x < LIMIT", "x <", key="broken"),
            _mutation("int", "str", after="def small", key="annotation"),
            _mutation("Sequence", "Mapping", after="if TYPE_CHECKING", key="typing"),
            Mutation(key="far", operator="op", start=(999, 0), end=(999, 1), replacement="x"),
        )
    )
    found = candidates(SCOPE, CORE, made)
    assert [m.key for m in found.candidates] == ["kept"]
    assert found.dropped == {"unchanged": 1, "unparsable": 1, "annotation": 1,
                             "type_checking": 1, "outside_text": 1}  # fmt: skip
    line = _mutation("total = 0", "total = 1", key="marked")
    assert candidates(SCOPE, marked, Mutations(made=(line,))).dropped == {"marked": 1}


def test_a_mutants_id_is_its_file_and_key_pinned_to_the_files_digest() -> None:
    """Q-MUT-G: the id is ``<file>:<operator>:<occurrence>`` (the source's key) plus the
    file's hash, so a probe drawn from a file that has since changed is another probe."""
    made = Mutations(made=(_mutation("x < LIMIT", "x >= LIMIT", key="core/Op:3"),))
    (mutant,) = candidates(SCOPE, CORE, made).candidates
    (moved,) = candidates(SCOPE, CORE + "\n", made).candidates
    assert mutant.id == moved.id == f"{SCOPE}:core/Op:3"
    assert mutant.file_digest != moved.file_digest and mutant.probe_id != moved.probe_id


def _population(size: int) -> list[Mutant]:
    return [
        Mutant(
            file=SCOPE,
            key=f"op:{n}",
            operator="op",
            start=(1, 0),
            end=(1, 1),
            original="a",
            replacement="b",
            file_digest="sha256:x",
        )  # fmt: skip
        for n in range(size)
    ]


def test_the_sample_is_the_same_for_the_same_seed_and_disjoint_from_an_excluded_one() -> None:
    """D2 and D11: a seeded simple random sample, the same on every machine; a census when the
    population is no larger; and a fresh sample drawn disjoint from the first."""
    population = _population(40)
    first = sample(population, 10, seed=1)
    assert first == sample(list(reversed(population)), 10, seed=1)
    assert len(sample(population, 100, seed=1)) == 40
    second = sample(population, 10, seed=2, exclude=[m.id for m in first])
    assert len(second) == 10 and not {m.id for m in first} & {m.id for m in second}


def test_a_probe_file_reads_back_as_the_mutant_it_was_written_from() -> None:
    """D4, the data boundary: a probe file is YAML any tool can read and write, and its
    options are the mutant exactly, quotes, backslashes and characters outside ASCII
    included."""
    mutant = Mutant(file=SCOPE, key="op:0", operator="op", start=(2, 4), end=(2, 9),
                    original='"é\\n"', replacement="'ü: \\t'", file_digest="sha256:x")  # fmt: skip
    read = yaml.safe_load(probe_file(mutant, source="acme.v1.mutant-source", seed=1))
    assert read == {"kind": "mutant", "options": mutant.options()}


# --- the read side ---------------------------------------------------------------------------


def _killed(probe: str, *killers: str) -> reading.TrialRead:
    outcome = {"status": "killed", "killers": list(killers), "covered": True}
    return reading.TrialRead(probe=probe, subject="suite", role="subject", outcome=outcome)


def test_the_keep_set_keeps_every_sole_killer_and_one_of_two_that_kill_the_same_mutants() -> None:
    """D11: tests that each kill only what another kills are not removable together; the keep
    set covers every killed mutant, and the candidates are the rest, a test that killed
    nothing among them."""
    tests = {name: {"duration_s": 1.0, "outcome": "passed", "lines": {SCOPE: [1]}, "files": []}
             for name in ("t1", "t2", "t3", "t4")}  # fmt: skip
    found_map = {"tests": tests, "import_lines": {}}
    probes = {p: {"file": SCOPE, "start": [1, 0], "end": [1, 1], "mutant": p} for p in "abc"}
    trials = [_killed("a", "t1"), _killed("b", "t2", "t3"), _killed("c", "t2", "t3")]
    found = reading.read(
        trials, probes, {"suite": found_map}, {"suite": ()}, run="r", experiment="e",
        pruned="suite",
    )  # fmt: skip
    pruning = found["pruning"]
    assert pruning["keep"] == 2 and pruning["judged"] == 4
    candidates_found = {c["test"]: c["zero_kill"] for c in pruning["candidates"]}
    assert candidates_found == {"t3": False, "t4": True}
    assert pruning["deselect"] == ["t3", "t4"]


def test_a_reading_is_bounded_and_says_when_it_cut() -> None:
    """D13: what a tool returns is bounded, and a cut is said, never silent."""
    survived = {"status": "survived", "covered": False}
    trials = [
        reading.TrialRead(probe=f"m{n}", subject="suite", role="subject", outcome=survived)
        for n in range(60)
    ]
    cut = reading.read(trials, {}, {}, {}, run="r", experiment="e", pruned=None)
    every = reading.read(trials, {}, {}, {}, run="r", experiment="e", pruned=None, every=True)
    assert (len(cut["survivors"]), cut["truncated"], cut["survivors_total"]) == (50, True, 60)
    assert (len(every["survivors"]), every["truncated"]) == (60, False)
