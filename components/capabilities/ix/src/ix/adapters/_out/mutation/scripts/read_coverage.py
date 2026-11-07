"""Read a coverage.py data file recorded with per-test contexts, as the lines each test ran.

Run by the suite's own interpreter (``<python> -I read_coverage.py <data file> <tree>
<scope.json>``), where coverage.py is installed, never by ix's: ix depends on no coverage.
It uses coverage's public data API alone (``CoverageData``, ``measured_files``,
``contexts_by_lineno``). Contexts are pytest-cov's ``--cov-context=test`` ones, a test's node
id and the phase that ran the line (``<node id>|run``); the empty context is what ran outside
every test (imports, collection).

It prints one JSON object: ``coverage``, its version; ``python``, the suite's; ``tests``,
each node id with the lines it ran in each scope file (``lines``, by path relative to the
tree) and the other measured files it ran (``files``); and ``import_lines``, the lines of
each scope file that ran outside every test.
"""

from __future__ import annotations

import json
import os
import sys

import coverage


def _node(context: str) -> str:
    return context.rsplit("|", 1)[0] if "|" in context else context


def main(data_file: str, tree: str, scope_file: str) -> None:
    with open(scope_file, encoding="utf-8") as stream:
        scope = set(json.load(stream))
    data = coverage.CoverageData(basename=data_file)
    data.read()
    tests: dict[str, dict[str, object]] = {}
    import_lines: dict[str, list[int]] = {}
    for measured in sorted(data.measured_files()):
        relative = os.path.relpath(os.path.realpath(measured), os.path.realpath(tree))
        relative = relative.replace(os.sep, "/")
        by_line = data.contexts_by_lineno(measured)
        if relative in scope:
            outside: set[int] = set()
            for line, contexts in by_line.items():
                for context in contexts:
                    if not context:
                        outside.add(line)
                        continue
                    test = tests.setdefault(_node(context), {"lines": {}, "files": []})
                    lines = test["lines"]
                    assert isinstance(lines, dict)
                    lines.setdefault(relative, set()).add(line)
            import_lines[relative] = sorted(outside)
        else:
            for contexts in by_line.values():
                for context in contexts:
                    if context:
                        test = tests.setdefault(_node(context), {"lines": {}, "files": []})
                        files = test["files"]
                        assert isinstance(files, list)
                        if relative not in files:
                            files.append(relative)
    for test in tests.values():
        lines = test["lines"]
        assert isinstance(lines, dict)
        test["lines"] = {path: sorted(found) for path, found in lines.items()}
    json.dump(
        {
            "coverage": coverage.__version__,
            "python": sys.version.split()[0],
            "tests": tests,
            "import_lines": import_lines,
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main(*sys.argv[1:4])
