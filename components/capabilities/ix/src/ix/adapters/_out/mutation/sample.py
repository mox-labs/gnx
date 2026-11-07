"""``ix mutants sample``: the population of a scope, pinned as probe files, and the map.

For an experiment with ``test-suite`` subjects, sampling:

1. asks the mutant source for every mutant of each scope file (the whole file: a source's
   keys count within it) and keeps the candidates (:mod:`.population`);
2. draws ``n`` of them from ``seed``, none the ``--exclude`` experiment sampled;
3. runs each suite's baseline once in a fresh copy, under coverage.py's per-test contexts
   (pytest-cov's ``--cov-context=test``, in the suite's own environment), and refuses a
   baseline that does not pass: a failing suite kills nothing attributably;
4. writes ``probes/m-<id>.yaml`` for each mutant drawn, each subject's selection map
   (``ix.v1.mutation-map``) where its ``map`` names, and ``mutation/population.json``
   (``ix.v1.mutation-population``);
5. returns ``ix.v1.mutation-sample``: the population by operator, what was left out and why,
   what the source could not make, the baseline, the uncovered mutants drawn and the test
   time the run will take.

It writes nothing until every step has succeeded, and refuses an experiment whose ``probes/``
already holds a file: a sample is a registered population, replaced only on purpose.

``ix mutants map`` (:func:`map_suites`) is step 3 and the maps of step 4 alone: probe files of
kind ``mutant`` that another tool wrote need a selection map and no mutant source.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ix.adapters._out.mutation.population import (
    DROPPED,
    Mutant,
    candidates,
    digest,
    probe_file,
)
from ix.adapters._out.mutation.population import sample as draw
from ix.adapters._out.mutation.runner import OverranError, Suite, copy_tree, run
from ix.adapters._out.mutation.selection import (
    MAP_SCHEMA,
    baseline_time,
    select,
    statement_lines,
)
from ix.adapters._out.mutation.suite import coverage_fix, coverage_installed
from ix.extension import HarnessError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ix.extension import Launcher, MutantSource

#: The documents sampling writes and returns.
POPULATION_SCHEMA = "ix.v1.mutation-population"
SAMPLE_SCHEMA = "ix.v1.mutation-sample"
MAPS_SCHEMA = "ix.v1.mutation-maps"

#: Where the population record is written, beside the experiment.
POPULATION_FILE = "mutation/population.json"

#: The script the suite's Python runs to read coverage's data.
READER = Path(__file__).resolve().parent / "scripts" / "read_coverage.py"

#: How many failing tests a refused baseline names.
NAMED = 10

#: What a sample draws with unless told: 100 mutants (a Wilson half-width near 8 points at a
#: kill rate of 0.8), seed 1, and a baseline that may take an hour.
DEFAULT_N = 100
DEFAULT_SEED = 1
DEFAULT_TIMEOUT_S = 3600.0


@dataclass(frozen=True)
class Settings:
    """What a sample is drawn with, each value with where it came from: ``given``, or ix's
    ``default``."""

    n: int
    seed: int
    timeout_s: float
    sources: dict[str, str]

    @classmethod
    def of(cls, *, n: int | None, seed: int | None, timeout_s: float | None) -> Settings:
        given = {"n": n, "seed": seed, "timeout_s": timeout_s}
        return cls(
            n=DEFAULT_N if n is None else n,
            seed=DEFAULT_SEED if seed is None else seed,
            timeout_s=DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s,
            sources={key: "default" if value is None else "given" for key, value in given.items()},
        )

    def document(self) -> dict[str, dict[str, Any]]:
        return {
            key: {"value": getattr(self, key), "from": self.sources[key]}
            for key in ("n", "seed", "timeout_s")
        }


class SampleRefusedError(Exception):
    """Sampling cannot go on as asked: ``message`` says why and ``fix`` what to do."""

    def __init__(self, message: str, *, fix: str) -> None:
        super().__init__(message)
        self.fix = fix


@dataclass(frozen=True)
class Sampled:
    """One subject to sample for: its name in the experiment and its suite."""

    name: str
    suite: Suite


@dataclass
class _Population:
    candidates: list[Mutant] = field(default_factory=list)
    made: Counter[str] = field(default_factory=Counter)
    dropped: Counter[str] = field(default_factory=Counter)
    unmade: dict[str, list[Any]] = field(default_factory=dict)
    digests: dict[str, str] = field(default_factory=dict)


def _ix_version() -> str:
    try:
        return version("ix")
    except PackageNotFoundError:
        return "0+unknown"


def _population(suite: Suite, source: MutantSource) -> _Population:
    found = _Population()
    for file in suite.scope:
        try:
            text = (suite.root / file).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise SampleRefusedError(
                f"the scope file {file} cannot be read: {error}",
                fix="name source files of the tree, as UTF-8 text",
            ) from error
        found.digests[file] = digest(text)
        try:
            mutations = source.mutations(text)
        except Exception as error:  # a source is third-party code: say what it raised
            raise SampleRefusedError(
                f"the mutant source refused {file}: {type(error).__name__}: {error}",
                fix="name a file the source can read, or report it to the source's authors",
            ) from error
        try:
            made = candidates(file, text, mutations)
        except SyntaxError as error:
            raise SampleRefusedError(
                f"{file} is not Python ix can parse: {error}",
                fix="name Python source files in scope",
            ) from error
        found.candidates += made.candidates
        found.dropped += made.dropped
        for mutant in made.candidates:
            found.made[mutant.operator] += 1
        for operator, (count, reason) in made.unmade.items():
            found.unmade.setdefault(operator, [0, reason])[0] += count
    return found


def _excluded(experiment: Path | None) -> set[str]:
    if experiment is None:
        return set()
    record = experiment / POPULATION_FILE
    try:
        found = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SampleRefusedError(
            f"--exclude: {record} cannot be read: {error}",
            fix="name the experiment directory an earlier `ix mutants sample` wrote into",
        ) from error
    if found.get("schema") != POPULATION_SCHEMA:
        raise SampleRefusedError(
            f"--exclude: {record} is not an {POPULATION_SCHEMA}", fix="name another"
        )
    return {str(mutant) for mutant in found.get("sampled", ())}


async def _baseline(
    launcher: Launcher, sampled: Sampled, digests: Mapping[str, str], *, timeout_s: float
) -> dict[str, Any]:
    """The suite run once, unmutated, under per-test contexts: its selection map."""
    suite = sampled.suite
    missing = coverage_installed(suite.python, suite.root)
    if missing is not None:
        raise SampleRefusedError(
            f"coverage.py and pytest-cov are not installed for the suite's Python "
            f"{suite.python} ({missing}), and the selection map is made with them",
            fix=coverage_fix(suite.python),
        )
    with tempfile.TemporaryDirectory(prefix="ix-mutation-baseline-") as scratch:
        copy = copy_tree(suite, Path(scratch))
        data = Path(scratch, "coverage.data")
        rc = Path(scratch, "coveragerc")
        sources = "\n".join(f"    {copy / folder}" for folder in suite.import_roots())
        rc.write_text(f"[run]\nsource =\n{sources}\ndata_file = {data}\n", encoding="utf-8")
        try:
            ran = await run(
                launcher,
                suite,
                copy,
                select=None,
                timeout_s=timeout_s,
                arguments=["--cov", f"--cov-config={rc}", "--cov-context=test", "--cov-report="],
                # The tracer core, never sys.monitoring's (Python 3.14's default): that core
                # reports a line once and stops listening, so a second test that runs it is
                # not recorded under its own context, is never selected, and its kills read
                # as survivors.
                environment={"COVERAGE_FILE": str(data), "COVERAGE_CORE": "ctrace"},
            )
        except OverranError as overran:
            raise SampleRefusedError(
                f"the baseline of {sampled.name} {overran}",
                fix="give the baseline longer (--timeout), or a smaller suite in command",
            ) from None
        except HarnessError as error:
            raise SampleRefusedError(f"the baseline of {sampled.name} did not run: {error}", fix=(
                "run the suite's command by hand in its root to see why"
            )) from error  # fmt: skip
        elsewhere = ran.elsewhere()
        failed = ran.failed()
        if elsewhere:
            name, where = next(iter(elsewhere.items()))
            raise SampleRefusedError(
                f"the baseline of {sampled.name} imported {name} from {where}, not from its "
                "copy, so no trial could run a mutant",
                fix="make each scope file importable from its tree (a package with "
                "__init__.py files, under a folder the tree holds)",
            )
        if ran.exit != 0 or failed or ran.collection_errors:
            named = [*ran.collection_errors, *failed][:NAMED]
            raise SampleRefusedError(
                f"the baseline of {sampled.name} does not pass (pytest exited {ran.exit}"
                + (f"; failing: {', '.join(named)}" if named else "")
                + "): a mutant that fails a test that already fails is not a kill",
                fix="make the suite pass first, or deselect what fails (the subject's "
                "deselect), then sample again",
            )
        scope_file = Path(scratch, "scope.json")
        scope_file.write_text(json.dumps(list(suite.scope)), encoding="utf-8")
        read = await _read_coverage(launcher, suite, data, copy, scope_file)
    covered = read["tests"]
    tests = {
        node: {
            "duration_s": round(float(test["duration_s"]), 6),
            "outcome": test["outcome"],
            "files": covered.get(node, {}).get("files", []),
            "lines": covered.get(node, {}).get("lines", {}),
        }
        for node, test in sorted(ran.tests.items())
    }
    took = sum(test["duration_s"] for test in tests.values())
    return {
        "schema": MAP_SCHEMA,
        "python": read.get("python", ""),
        "ix_version": _ix_version(),
        "coverage": read.get("coverage", ""),
        "scope": dict(digests),
        "baseline": {
            "passed": True,
            "exit": ran.exit,
            "tests": len(tests),
            "duration_s": round(ran.duration_s, 3),
            "test_time_s": round(took, 3),
            "overhead_s": round(max(0.0, ran.duration_s - took), 3),
        },
        "tests": tests,
        "import_lines": read["import_lines"],
    }


async def _read_coverage(
    launcher: Launcher, suite: Suite, data: Path, copy: Path, scope_file: Path
) -> dict[str, Any]:
    """Coverage's data, read by the suite's Python through coverage's own API."""
    argv = [suite.python, "-I", str(READER), str(data), str(copy), str(scope_file)]
    process = await launcher.start(
        argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        async with asyncio.timeout(600):
            out, err = await process.communicate()
    except TimeoutError:
        raise SampleRefusedError(
            "reading coverage's data ran past 600s", fix="sample a smaller scope"
        ) from None
    finally:
        await launcher.stop(process, grace_s=1.0)
    if process.returncode != 0:
        raise SampleRefusedError(
            f"coverage's data could not be read: {err.decode(errors='replace').strip()[-600:]}",
            fix=coverage_fix(suite.python),
        )
    found: dict[str, Any] = json.loads(out)
    return found


def _scope_digests(suite: Suite) -> dict[str, str]:
    found = {}
    for file in suite.scope:
        try:
            found[file] = digest((suite.root / file).read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError) as error:
            raise SampleRefusedError(
                f"the scope file {file} cannot be read: {error}",
                fix="name source files of the tree, as UTF-8 text",
            ) from error
    return found


async def _maps(
    launcher: Launcher, subjects: Sequence[Sampled], *, timeout_s: float
) -> tuple[dict[str, dict[str, Any]], dict[Path, list[str]]]:
    """Each subject's selection map, made and not yet written, and the subjects each map file
    serves. A map is the suite's (its tree, command and scope), not the subject's: two
    subjects of one suite (a pruned variant deselects tests at run time) share one baseline,
    and may name one file."""
    maps: dict[str, dict[str, Any]] = {}
    made: dict[tuple[Any, ...], dict[str, Any]] = {}
    for subject in subjects:
        suite = subject.suite
        key = (suite.root, suite.argv, suite.scope, suite.exclude)
        if key not in made:
            made[key] = await _baseline(
                launcher, subject, _scope_digests(suite), timeout_s=timeout_s
            )
        maps[subject.name] = made[key]
    serves: dict[Path, list[str]] = {}
    for subject in subjects:
        serves.setdefault(subject.suite.map, []).append(subject.name)
    for path, names in serves.items():
        if len({id(maps[name]) for name in names}) > 1:
            raise SampleRefusedError(
                f"{', '.join(names)} name one map, {path}, but are different suites",
                fix="give each suite (tree, command, scope) a map of its own",
            )
    return maps, serves


def _write(maps: Mapping[str, dict[str, Any]], serves: Mapping[Path, list[str]]) -> None:
    for path, names in serves.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**maps[names[0]], "subjects": names}), encoding="utf-8")


async def map_suites(
    launcher: Launcher, subjects: Sequence[Sampled], *, settings: Settings
) -> dict[str, Any]:
    """Make each subject's selection map and nothing else: for mutant probe files another
    tool wrote, which need a map and no mutant source. The ``ix.v1.mutation-maps`` document.
    Raises :class:`SampleRefusedError` before writing anything."""
    if not subjects:
        raise SampleRefusedError(
            "the experiment has no test-suite subject to make a map for",
            fix="give it a subject of kind test-suite",
        )
    if settings.timeout_s <= 0:
        raise SampleRefusedError(
            f"--timeout {settings.timeout_s:g} leaves the baseline no time", fix="--timeout 3600"
        )
    maps, serves = await _maps(launcher, subjects, timeout_s=settings.timeout_s)
    _write(maps, serves)
    return {
        "schema": MAPS_SCHEMA,
        "subjects": [
            {
                "subject": subject.name,
                "map": str(subject.suite.map),
                "baseline": maps[subject.name]["baseline"],
            }
            for subject in subjects
        ],
        "settings": {"timeout_s": settings.document()["timeout_s"]},
    }


async def sample(
    launcher: Launcher,
    subjects: Sequence[Sampled],
    *,
    experiment: Path,
    source: MutantSource,
    source_name: str,
    source_drives: Mapping[str, str],
    settings: Settings,
    exclude: Path | None = None,
) -> dict[str, Any]:
    """Sample the mutants of the subjects' scope into ``experiment``: see the module
    docstring. Raises :class:`SampleRefusedError` before writing anything."""
    if not subjects:
        raise SampleRefusedError(
            "the experiment has no test-suite subject to sample mutants for",
            fix="give it a subject of kind test-suite",
        )
    n, seed, timeout_s = settings.n, settings.seed, settings.timeout_s
    if n < 1:
        raise SampleRefusedError(f"--n {n} draws no mutant", fix="draw at least one: --n 100")
    if timeout_s <= 0:
        raise SampleRefusedError(
            f"--timeout {timeout_s:g} leaves the baseline no time", fix="--timeout 3600"
        )
    probes = experiment / "probes"
    if probes.is_dir() and any(probes.iterdir()):
        raise SampleRefusedError(
            f"{probes} already holds probe files, a population that may be registered",
            fix="sample into a new experiment (and --exclude this one for a disjoint sample), "
            "or remove that folder first on purpose",
        )
    first = subjects[0].suite
    for other in subjects[1:]:
        if other.suite.scope != first.scope:
            raise SampleRefusedError(
                f"{subjects[0].name} and {other.name} name different scopes; one population "
                "is applied to every subject",
                fix="give every test-suite subject the same scope files, relative to its root",
            )
    population = _population(first, source)
    if not population.candidates:
        raise SampleRefusedError(
            f"the source made no candidate mutant of {', '.join(first.scope)}",
            fix="name files with code the source's operators change",
        )
    excluded = _excluded(exclude)
    drawn = draw(population.candidates, n, seed, exclude=excluded)
    if not drawn:
        raise SampleRefusedError(
            "every candidate was excluded", fix="exclude a smaller earlier sample"
        )
    maps, serves = await _maps(launcher, subjects, timeout_s=timeout_s)

    probes.mkdir(parents=True, exist_ok=True)
    for mutant in drawn:
        (probes / f"{mutant.probe_id}.yaml").write_text(
            probe_file(mutant, source=source_name, seed=seed), encoding="utf-8"
        )
    _write(maps, serves)
    record = {
        "schema": POPULATION_SCHEMA,
        "source": {"type_url": source_name, "drives": dict(source_drives)},
        "seed": seed,
        "n": n,
        "scope": population.digests,
        "size": len(population.candidates),
        "by_operator": dict(sorted(population.made.items())),
        "dropped": {reason: population.dropped.get(reason, 0) for reason in DROPPED},
        "unmade": {
            op: {"count": c, "reason": r} for op, (c, r) in sorted(population.unmade.items())
        },
        "excluded": sum(1 for mutant in population.candidates if mutant.id in excluded),
        "sampled": [mutant.id for mutant in drawn],
        "probes": {mutant.id: mutant.probe_id for mutant in drawn},
    }
    (experiment / POPULATION_FILE).parent.mkdir(parents=True, exist_ok=True)
    (experiment / POPULATION_FILE).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return {**_summary(record, subjects, maps, drawn), "settings": settings.document()}


def _summary(
    record: Mapping[str, Any],
    subjects: Sequence[Sampled],
    maps: Mapping[str, dict[str, Any]],
    drawn: Sequence[Mutant],
) -> dict[str, Any]:
    """What sampling made, and what a run of it will take."""
    texts = {
        file: (subjects[0].suite.root / file).read_bytes().decode("utf-8")
        for file in subjects[0].suite.scope
    }
    suites = []
    for subject in subjects:
        found, suite = maps[subject.name], subject.suite
        uncovered = 0
        seconds = worst = 0.0
        per_run = float(found["baseline"]["overhead_s"])
        whole = baseline_time(found, None, suite.deselect) + per_run
        for mutant in drawn:
            first, last = statement_lines(texts[mutant.file], mutant.start[0], mutant.end[0])
            chosen = select(found, mutant.file, first, last, suite.deselect)
            if not chosen.covered:
                uncovered += 1
                seconds += whole if suite.confirm != "none" else 0.0
                worst += whole if suite.confirm != "none" else 0.0
                continue
            selected = baseline_time(found, chosen.tests, suite.deselect) + per_run
            seconds += selected
            worst += selected
            # Under confirm: all a covered survivor runs every test again; which survive is
            # not known before the run, so the worst case counts every covered mutant.
            if suite.confirm == "all" and chosen.tests is not None:
                worst += whole
        suites.append(
            {
                "subject": subject.name,
                "map": str(suite.map),
                "baseline": found["baseline"],
                "uncovered": uncovered,
                "confirm": suite.confirm,
                "estimated_s": round(seconds, 1),
                "worst_s": round(worst, 1),
            }
        )
    return {
        "schema": SAMPLE_SCHEMA,
        "source": record["source"],
        "seed": record["seed"],
        "scope": record["scope"],
        "population": record["size"],
        "by_operator": record["by_operator"],
        "dropped": record["dropped"],
        "unmade": record["unmade"],
        "sampled": len(drawn),
        "census": len(drawn) == record["size"],
        "probes": "probes/",
        "population_file": POPULATION_FILE,
        "subjects": suites,
        "estimate_note": "test time only, one trial in flight, divided by trials in flight "
        "for the wall: the selected tests of each mutant and each run's start as the baseline "
        "measured them, and a whole-suite run for each uncovered mutant confirmed; worst_s also "
        "confirms every covered mutant (confirm: all, when every one survives); neither counts "
        "the reruns a kill takes to attribute",
    }
