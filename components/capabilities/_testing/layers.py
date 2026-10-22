"""The layer check each capability package's tests run: its ``layers.json`` against its tree.

This is the Python half of slick crafting-aces' ``layer-check.py``, kept here because that
script is not published where a test could pin it. A map therefore means the same thing to
both, and runs under both:

* **reach**: each file imports only what its layer's ``allow`` lists. An entry is a module
  root or a dotted path, and admits everything under it; ``*`` admits any module that is not
  the package's own. Every import counts, one under ``TYPE_CHECKING`` or inside a function
  included, and a relative import is resolved against its module first;
* **forbid**: a layer's file never names an identifier its layer forbids, as a name in code
  or as the root of an import;
* **single_importer**: one file alone names a technology;
* **coverage**: every scanned file belongs to a layer, every layer's file is scanned, and no
  glob matches nothing, since a rule over no file passes silently.

:func:`check` returns a :class:`Report` whose ``exit_code`` is the script's: 0 clean, 1 a
finding, 2 a file nothing judged. :func:`plants` derives, from a map, one violation per rule
for a test to plant in a copy of the tree, so a rule that stopped biting fails its package's
suite. :func:`reaches_outside` holds a driving adapter to the package's ``__all__``, which a
prefix allowance cannot say.

What an import check cannot see, so each package's design record lists what review reads
for: a module reached through an alias or by ``importlib``, a use that is not an import, a
re-export that hands an adapter's type out under another name, logic in a file whose imports
are all allowed, and what a dependency does at run time.
"""

from __future__ import annotations

import ast
import json
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable
    from pathlib import Path

#: Modules a layer without ``*`` is probed with: outside every package, and allowed by none.
_OUTSIDE = ("socket", "ctypes", "mmap")


@dataclass
class Report:
    """What :func:`check` found: findings break a rule, errors leave a file unjudged."""

    findings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    files_in_scan: int = 0
    layers: dict[str, int] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        """The script's exit code: 2 when anything went unjudged, 1 on a finding, else 0."""
        return 2 if self.errors else 1 if self.findings else 0


@dataclass(frozen=True)
class Plant:
    """A violation of one rule: ``line`` appended to ``file`` must produce ``finding``."""

    rule: str
    file: str
    line: str
    finding: str


def load(package: Path) -> dict[str, Any]:
    """The map at ``<package>/layers.json``."""
    spec: dict[str, Any] = json.loads((package / "layers.json").read_text(encoding="utf-8"))
    return spec


def module_of(rel: Path) -> list[str]:
    """A file's dotted module path, read after a leading ``src``: ``src/ix/api.py`` is
    ``ix.api``, and a package's ``__init__.py`` is the package."""
    parts = list(rel.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


def reaches(tree: ast.Module, rel: Path) -> list[tuple[int, list[str]]]:
    """Every (line, module path) a file imports, relative imports resolved."""
    module = module_of(rel)
    package = module if rel.name == "__init__.py" else module[:-1]
    found: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name.split(".")) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            found.append((node.lineno, base + (node.module.split(".") if node.module else [])))
    return found


def identifiers(tree: ast.Module) -> list[tuple[int, str]]:
    """Every (line, name) a file names: a name in code, or the root of an import."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name.split(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module.split(".")[0]))
    return found


def allowed(path: list[str], allow: Iterable[str], own: Collection[str]) -> bool:
    """Whether a layer allowed ``allow`` may reach ``path``; ``own`` are the package roots."""
    joined = ".".join(path)
    for entry in allow:
        if entry == "*":
            if path and path[0] not in own:
                return True
            continue
        if joined == entry or joined.startswith(entry + "."):
            return True
    return False


@lru_cache(maxsize=4096)
def _parse(text: str) -> ast.Module:
    """A file's tree. Cached by text: a planted copy reparses only the file it changed."""
    return ast.parse(text)


@lru_cache(maxsize=4096)
def _names(text: str) -> tuple[tuple[int, str], ...]:
    """A file's identifiers, read once per text for every rule that asks."""
    return tuple(identifiers(_parse(text)))


def _glob(root: Path, pattern: str) -> set[Path]:
    return {p for p in root.glob(pattern) if p.is_file()}


def check(spec: dict[str, Any], root: Path) -> Report:
    """Judge the tree at ``root`` (the directory holding ``src/``) by the map ``spec``."""
    report = Report()
    scan: set[Path] = set()
    for pattern in spec["scan"]:
        hits = _glob(root, pattern)
        if not hits:
            report.errors.append(f"scan glob {pattern!r} matches no file")
        scan |= hits
    report.files_in_scan = len(scan)
    texts: dict[Path, str] = {}
    for p in sorted(scan):
        try:
            text = p.read_text(encoding="utf-8")
            _parse(text)
            texts[p] = text
        except (SyntaxError, UnicodeDecodeError) as error:
            report.errors.append(f"cannot parse {p.relative_to(root)}: {error}")
    own = {module_of(p.relative_to(root))[0] for p in scan if module_of(p.relative_to(root))}
    claimed: set[Path] = set()
    for layer in spec["layers"]:
        name, allow, forbid = layer["name"], layer.get("allow", []), layer.get("forbid", [])
        files: set[Path] = set()
        for pattern in layer["files"]:
            hits = _glob(root, pattern)
            if not hits:
                report.errors.append(f"layer {name}: glob {pattern!r} matches no file")
            files |= hits
        report.errors.extend(
            f"layer {name}: {p.relative_to(root)} is not scanned" for p in sorted(files - scan)
        )
        report.layers[name] = len(files)
        claimed |= files & scan
        for p in sorted(files & set(texts)):
            rel = p.relative_to(root)
            for line, path in reaches(_parse(texts[p]), rel):
                if not allowed(path, allow, own):
                    report.findings.append(
                        f"{rel}:{line}: layer {name} may not reach {'.'.join(path)}"
                    )
            for line, ident in _names(texts[p]) if forbid else ():
                if ident in forbid:
                    report.findings.append(f"{rel}:{line}: layer {name} names forbidden {ident}")
    report.errors.extend(
        f"{p.relative_to(root)} is scanned but no layer claims it" for p in sorted(scan - claimed)
    )
    for rule in spec.get("single_importer", []):
        only = root / rule["only"]
        if only not in scan:
            report.errors.append(f"single_importer {rule['name']}: {rule['only']} is not scanned")
        for p, text in sorted(texts.items()):
            if p == only:
                continue
            for line, ident in _names(text):
                if ident == rule["name"]:
                    report.findings.append(
                        f"{p.relative_to(root)}:{line}: only {rule['only']} may name {ident}"
                    )
    return report


# --- planting violations ------------------------------------------------------------------


def copy_tree(package: Path, into: Path) -> Path:
    """A copy of ``<package>/src`` under ``into``, to plant violations in; returns ``into``."""
    shutil.copytree(package / "src", into / "src")
    return into


def plant(tree: Path, violation: Plant) -> None:
    """Append ``violation.line`` to its file in the copy at ``tree``."""
    path = tree / violation.file
    path.write_text(path.read_text(encoding="utf-8") + f"\n{violation.line}\n", encoding="utf-8")


def plants(spec: dict[str, Any], root: Path) -> list[Plant]:
    """One violation per rule of ``spec``, each in the first file the rule judges.

    Per layer: an import of an outside module, when the layer does not admit ``*``, and an
    import of the package's own module the layer may not reach, the first in the map's
    order, so the probe sits at the layer's edge; a layer that may reach everything has no
    reach rule to break. Per forbidden name: an assignment to it. Per single importer: an
    import of the technology in another scanned file.
    """
    scan = sorted(p for pattern in spec["scan"] for p in _glob(root, pattern))
    own = {module_of(p.relative_to(root))[0] for p in scan}
    internal = [
        ".".join(module_of(p.relative_to(root)))
        for layer in spec["layers"]
        for p in sorted(p for pattern in layer["files"] for p in _glob(root, pattern))
    ]
    found: list[Plant] = []
    for layer in spec["layers"]:
        name, allow = layer["name"], layer.get("allow", [])
        files = sorted(p for pattern in layer["files"] for p in _glob(root, pattern))
        if not files:
            continue
        rel = files[0].relative_to(root).as_posix()
        here = ".".join(module_of(files[0].relative_to(root)))
        outside = next((m for m in _OUTSIDE if not allowed([m], allow, own)), None)
        if outside is not None:
            found.append(
                Plant(
                    f"{name} reach {outside}",
                    rel,
                    f"import {outside}",
                    f"layer {name} may not reach {outside}",
                )
            )
        inside = next(
            (m for m in internal if m != here and not allowed(m.split("."), allow, own)), None
        )
        if inside is not None:
            found.append(
                Plant(
                    f"{name} reach {inside}",
                    rel,
                    f"import {inside}",
                    f"layer {name} may not reach {inside}",
                )
            )
        found.extend(
            Plant(
                f"{name} forbid {ident}",
                rel,
                f"{ident} = None",
                f"layer {name} names forbidden {ident}",
            )
            for ident in layer.get("forbid", [])
        )
    for rule in spec.get("single_importer", []):
        other = next(p for p in scan if p != root / rule["only"])
        found.append(
            Plant(
                f"single importer {rule['name']}",
                other.relative_to(root).as_posix(),
                f"import {rule['name']}",
                f"only {rule['only']} may name {rule['name']}",
            )
        )
    return found


# --- a driving adapter and the package's declared names ------------------------------------


def reaches_outside(files: Iterable[Path], package: str, public: Collection[str]) -> list[str]:
    """Each place one of ``files`` reaches ``package`` other than by a name in ``public``
    (its ``__all__``): a relative import, a submodule, an undeclared name imported or read
    as ``<package>.<name>``, or the package bound under another name, which hides the reads.
    """
    found: list[str] = []
    for path in files:
        where = path.name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    found.append(f"{where}:{node.lineno}: a relative import")
                elif module.startswith(package + "."):
                    found.append(f"{where}:{node.lineno}: reaches {module}")
                elif module == package:
                    found.extend(
                        f"{where}:{node.lineno}: {alias.name} is not in {package}.__all__"
                        for alias in node.names
                        if alias.name not in public
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(package + "."):
                        found.append(f"{where}:{node.lineno}: imports {alias.name}")
                    elif alias.name == package and alias.asname not in (None, package):
                        found.append(f"{where}:{node.lineno}: binds {package} as {alias.asname}")
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == package
                and node.attr not in public
            ):
                found.append(f"{where}:{node.lineno}: {package}.{node.attr} is not in __all__")
    return found
