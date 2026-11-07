"""ix's pytest plugin for a mutation trial, loaded into the suite's own interpreter.

ix starts the suite's command with ``-p ix_mutation_trial`` and this directory first on
``PYTHONPATH``; nothing here imports ix or anything outside the standard library, so it runs
in whatever environment the suite has. It reads its instructions from the environment:

* ``IX_MUTATION_REPORT``: the file it writes its report to when the session ends;
* ``IX_MUTATION_SELECT``: a JSON file of the node ids to run, every other test deselected and
  every test file none of them is in left uncollected (absent: every test);
* ``IX_MUTATION_DESELECT``: a JSON file of node ids never to run;
* ``IX_MUTATION_MODULES``: a JSON object of each module the trial judges, by name, and the
  file it must be imported from (the trial's copy).

The report is a JSON object: ``rootdir``; ``exit``, pytest's exit status; ``tests``, each
node id run with its ``outcome`` (``passed``, ``failed``, ``error`` or ``skipped``) and
``duration_s`` over its phases; ``collection_errors``, the nodes that failed to collect; and
``modules``, where each judged module was imported from: ``copy``, ``absent`` (never
imported in this process) or the file it was imported from instead. A module is looked at
once collection ends and again when the session ends, and a file other than the copy's at
either time is the one reported, so a test that drops a module from ``sys.modules`` does not
hide where it came from.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _read(variable: str) -> Any:
    named = os.environ.get(variable)
    if not named:
        return None
    return json.loads(Path(named).read_text(encoding="utf-8"))


_SELECT: frozenset[str] | None = None
_DESELECT: frozenset[str] = frozenset()
_MODULES: dict[str, str] = {}
_ROOT = Path.cwd()
_TESTS: dict[str, dict[str, Any]] = {}
_COLLECTION_ERRORS: list[str] = []
_ELSEWHERE: dict[str, str] = {}
#: The files, relative to the rootdir, that hold a selected test.
_FILES: frozenset[str] = frozenset()


def pytest_configure(config: Any) -> None:
    global _SELECT, _DESELECT, _MODULES, _ROOT, _FILES
    selected = _read("IX_MUTATION_SELECT")
    _SELECT = None if selected is None else frozenset(selected)
    _DESELECT = frozenset(_read("IX_MUTATION_DESELECT") or ())
    _MODULES = dict(_read("IX_MUTATION_MODULES") or {})
    _ROOT = Path(str(config.rootpath))
    if _SELECT is not None:
        _FILES = frozenset(nodeid.split("::", 1)[0] for nodeid in _SELECT)


def pytest_ignore_collect(collection_path: Path, config: Any) -> bool | None:
    """A test file that holds no selected test is not collected, so its imports cost nothing.
    Directories, ``conftest.py`` and package ``__init__.py`` files are always collected."""
    if _SELECT is None or collection_path.suffix != ".py":
        return None
    if collection_path.name in ("conftest.py", "__init__.py") or not collection_path.is_file():
        return None
    try:
        relative = collection_path.resolve().relative_to(_ROOT.resolve()).as_posix()
    except ValueError:
        return None
    return True if relative not in _FILES else None


def pytest_collection_modifyitems(session: Any, config: Any, items: list[Any]) -> None:
    kept: list[Any] = []
    dropped: list[Any] = []
    for item in items:
        chosen = (_SELECT is None or item.nodeid in _SELECT) and item.nodeid not in _DESELECT
        (kept if chosen else dropped).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def pytest_collectreport(report: Any) -> None:
    if report.failed:
        _COLLECTION_ERRORS.append(str(report.nodeid) or "<session>")


def pytest_runtest_logreport(report: Any) -> None:
    test = _TESTS.setdefault(report.nodeid, {"outcome": "passed", "duration_s": 0.0})
    test["duration_s"] += float(report.duration)
    if report.failed:
        test["outcome"] = "failed" if report.when == "call" else "error"
    elif report.skipped and test["outcome"] == "passed":
        test["outcome"] = "skipped"


def _where(name: str) -> str:
    module = sys.modules.get(name)
    if module is None:
        return "absent"
    found = getattr(module, "__file__", None)
    if not found:
        return "absent"
    if os.path.realpath(found) == os.path.realpath(_MODULES[name]):
        return "copy"
    return str(found)


def _look() -> None:
    for name in _MODULES:
        where = _where(name)
        if where not in ("copy", "absent"):
            _ELSEWHERE.setdefault(name, where)


def pytest_collection_finish(session: Any) -> None:
    _look()


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    _look()
    target = os.environ.get("IX_MUTATION_REPORT")
    if not target:
        return
    modules = {name: _ELSEWHERE.get(name) or _where(name) for name in _MODULES}
    report = {
        "rootdir": str(_ROOT),
        "exit": int(exitstatus),
        "tests": _TESTS,
        "collection_errors": _COLLECTION_ERRORS,
        "modules": modules,
    }
    Path(target).write_text(json.dumps(report), encoding="utf-8")
