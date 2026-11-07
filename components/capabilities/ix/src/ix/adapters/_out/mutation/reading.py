"""``ix mutants read``: a mutation run's survivors, and the tests it found no use for.

**Survivors.** Each mutant read as not killed, with its change and its class:

* ``assertion_gap``: tests ran its statement and passed: strengthen what they assert (the tests
  are listed; for a line that runs at import, ``at_import``, every test ran it);
* ``coverage_gap``: no test ran its statement: a test is missing.

**Pruning candidates**, from the run's kill matrix (each killed mutant and the tests that
killed it attributably) on the subject a pruning would change (``subject`` in
``mutation-score``, ``control`` in ``mutation-regress``):

1. *judged* tests are those a sampled mutant's selection held, and every killer; the others
   ran no line of a sampled mutant and are *not judged*;
2. the *keep set* starts with every test that is the only killer of some mutant, then adds,
   while a killed mutant has no killer in it, the test that kills the most of those (the
   shortest in the baseline, of equals): a greedy cover, not a minimal one;
3. the candidates are the judged tests outside it, removable *together* as far as these
   mutants show; each says what it killed (each kill also made by the keep set), whether it
   killed nothing, and the files outside the scope it also runs, where it was not judged;
4. ``deselect`` is the list a ``mutation-regress`` variant leaves out.

A candidate is a recommendation, never a deletion: confirm it with a ``mutation-regress`` on a
fresh sample disjoint from this one (``ix mutants sample --seed <new> --exclude <this
experiment>``), its margin set before the run. ix writes nothing to the tests.

What it returns is bounded: :data:`LIMIT` survivors and candidates unless every one is asked
for, and it says when it cut.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ix.adapters._out.mutation.selection import select

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: The document ``read`` returns.
READING_SCHEMA = "ix.v1.mutants-reading"

#: How many survivors, and how many candidates, a reading lists unless asked for every one.
LIMIT = 50

#: How many of a survivor's tests a reading names.
TESTS_NAMED = 10

#: Always said beside a reading.
CAVEATS = (
    "These are the mutants sampled from this scope: a test may be the only one to kill a "
    "mutant that was not sampled, or a fault of a file outside the scope.",
    "Mutants model these operators' faults only: not performance, concurrency, messages or "
    "anything a test checks outside the code it runs.",
    "A kill rate measures how often the suite shows a seeded fault, not whether the code is "
    "correct.",
    "Pruning candidates are a recommendation: confirm them with a mutation-regress on a fresh, "
    "disjoint sample before removing any test. ix removes nothing.",
)


@dataclass(frozen=True)
class TrialRead:
    """One trial of a mutation run: its probe, subject and role, and either the probe's
    outcome or why there is none."""

    probe: str
    subject: str
    role: str
    outcome: Mapping[str, Any] | None
    reason: str = ""


def _short(text: str, limit: int = 60) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _change(options: Mapping[str, Any]) -> str:
    before = _short(str(options.get("original", "")))
    after = _short(str(options.get("replacement", "")))
    return f"`{before}` -> `{after}`"


def read(
    trials: Sequence[TrialRead],
    probes: Mapping[str, Mapping[str, Any]],
    maps: Mapping[str, Mapping[str, Any] | None],
    deselected: Mapping[str, Sequence[str]],
    *,
    run: str,
    experiment: str,
    pruned: str | None,
    every: bool = False,
) -> dict[str, Any]:
    """The reading of a mutation run: see the module docstring. ``pruned`` is the subject a
    pruning would change; ``every`` lists every survivor and candidate."""
    subjects: dict[str, dict[str, Any]] = {}
    survivors: list[dict[str, Any]] = []
    for trial in trials:
        tally = subjects.setdefault(
            trial.subject,
            {"subject": trial.subject, "role": trial.role, "read": 0, "killed": 0,
             "survived": 0, "uncovered": 0, "unread": Counter()},
        )  # fmt: skip
        if trial.outcome is None:
            tally["unread"][trial.reason or "unread"] += 1
            continue
        tally["read"] += 1
        status = str(trial.outcome.get("status"))
        tally[status if status in ("killed", "survived", "uncovered") else "survived"] += 1
        if status == "killed":
            continue
        options = probes.get(trial.probe, {})
        covered = bool(trial.outcome.get("covered"))
        tests = list(trial.outcome.get("covered_by") or ())
        survivors.append(
            {
                "subject": trial.subject,
                "probe": trial.probe,
                "mutant": options.get("mutant", trial.probe),
                "file": options.get("file", ""),
                "line": (options.get("start") or [0])[0],
                "operator": options.get("operator", ""),
                "change": _change(options),
                "class": "assertion_gap" if covered else "coverage_gap",
                # Its line runs at import, so every test ran it and none is named.
                "at_import": covered and trial.outcome.get("selected") == "all",
                "stage": trial.outcome.get("stage", ""),
                "tests": tests[:TESTS_NAMED],
                "tests_count": len(tests),
            }
        )
    for tally in subjects.values():
        tally["unread"] = dict(tally["unread"])
        tally["kill_rate"] = round(tally["killed"] / tally["read"], 4) if tally["read"] else None
    survivors.sort(
        key=lambda found: (found["subject"], found["file"], found["line"], found["probe"])
    )
    shown = survivors if every else survivors[:LIMIT]
    return {
        "schema": READING_SCHEMA,
        "run": run,
        "experiment": experiment,
        "subjects": list(subjects.values()),
        "survivors": shown,
        "survivors_total": len(survivors),
        "truncated": len(shown) < len(survivors),
        "limit": None if every else LIMIT,
        "pruning": (
            None
            if pruned is None or maps.get(pruned) is None
            else _pruning(
                trials, probes, maps[pruned] or {}, deselected.get(pruned, ()), pruned, every
            )
        ),
        "caveats": list(CAVEATS),
    }


def _pruning(
    trials: Sequence[TrialRead],
    probes: Mapping[str, Mapping[str, Any]],
    found: Mapping[str, Any],
    deselect: Sequence[str],
    subject: str,
    every: bool,
) -> dict[str, Any]:
    recorded: Mapping[str, Any] = found.get("tests", {})
    judged: set[str] = set()
    killers: dict[str, frozenset[str]] = {}
    for trial in trials:
        if trial.subject != subject or trial.outcome is None:
            continue
        options = probes.get(trial.probe, {})
        # The statement's lines the trial selected by; a span's own for an outcome without.
        lines = trial.outcome.get("lines") or [
            (options.get("start") or [0])[0],
            (options.get("end") or [0])[0],
        ]
        chosen = select(dict(found), str(options.get("file", "")), int(lines[0]), int(lines[1]),
                        tuple(deselect))  # fmt: skip
        judged.update(chosen.tests if chosen.tests is not None else ())
        if trial.outcome.get("status") == "killed":
            named = frozenset(
                str(node) for node in trial.outcome.get("killers") or () if node in recorded
            )
            judged.update(named)
            if named:
                killers[trial.probe] = named
    keep = {next(iter(named)) for named in killers.values() if len(named) == 1}
    left = {probe for probe, named in killers.items() if not named & keep}
    while left:
        counts = Counter(test for probe in left for test in killers[probe])
        chosen_test = min(
            counts,
            key=lambda test: (
                -counts[test],
                float(recorded.get(test, {}).get("duration_s", 0)),
                test,
            ),
        )
        keep.add(chosen_test)
        left = {probe for probe in left if chosen_test not in killers[probe]}
    candidates = []
    for test in sorted(judged - keep):
        kills = sorted(
            str(probes.get(probe, {}).get("mutant", probe))
            for probe, named in killers.items()
            if test in named
        )
        candidates.append(
            {
                "test": test,
                "kills": kills,
                "zero_kill": not kills,
                "also_runs": list(recorded.get(test, {}).get("files", [])),
            }
        )
    shown = candidates if every else candidates[:LIMIT]
    return {
        "subject": subject,
        "judged": len(judged),
        "not_judged": len(set(recorded) - judged - set(deselect)),
        "keep": len(keep),
        "candidates": shown,
        "candidates_total": len(candidates),
        "truncated": len(shown) < len(candidates),
        "deselect": sorted({*deselect, *(candidate["test"] for candidate in candidates)}),
        "note": "a greedy cover of the killed mutants, not a minimal one; confirm on a fresh "
        "sample with mutation-regress, deselecting these in the variant",
    }
