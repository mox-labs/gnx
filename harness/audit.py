#!/usr/bin/env python3
"""audit: the gate that keeps a dependency with a known vulnerability out of a lockfile.

osv-scanner reads each lockfile and asks the OSV database (GitHub's advisories, PyPA's and
the npm ecosystem's among its sources) which locked versions have a published vulnerability.
Every one it reports fails the gate unless the allowlist accepts it.

The allowlist (`--allow`) holds one `[[accept]]` table per accepted finding: the advisory
`id` (any of its aliases: GHSA, PYSEC, CVE), the `package` it is accepted for, the `reason`,
and the date it `expires`. An entry past that date fails the gate, so an acceptance is
re-decided rather than forgotten; so does an entry that matches no finding any more, so the
file holds only what is still true.

Each open finding is printed with its package, the locked version, the advisory, the version
that fixes it, and the command that applies the fix.

Usage: audit.py --allow FILE LOCKFILE... [--osv-json FILE]
OSV_SCANNER names the scanner (default: `osv-scanner` on PATH). `--osv-json` reads a report
already made (`osv-scanner scan source --format json`) instead of running the scanner.
Exit 0 = clean, 1 = an open finding or an allowlist problem, 2 = the scan could not run.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOW_FIELDS = ("id", "package", "reason", "expires")
# osv-scanner reads its own ignore file from beside a lockfile, which would drop a finding
# before this gate sees it: the allowlist, with its reasons and dates, is the only one.
SCANNER_CONFIG = "osv-scanner.toml"


class ScanError(Exception):
    """The scan could not run, or its report could not be read."""


# --- versions -----------------------------------------------------------------------------
#
# Used only to name the version that fixes a finding. Whether a finding exists is
# osv-scanner's answer, never this module's.

Key = tuple[Any, ...]

PEP440 = re.compile(
    r"^v?(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-_.]?(?P<pre_l>alpha|beta|preview|pre|rc|a|b|c)[-_.]?(?P<pre_n>\d+)?)?"
    r"(?:-(?P<post_n1>\d+)|[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>\d+)?)?"
    r"(?:[-_.]?(?P<dev_l>dev)[-_.]?(?P<dev_n>\d+)?)?"
    r"(?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?$",
    re.IGNORECASE,
)
PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def pep440_key(version: str) -> Key | None:
    """PEP 440 order: a dev release before its pre-releases, and both before the release."""
    m = PEP440.match(version.strip())
    if not m:
        return None
    release = [int(part) for part in m["release"].split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    has_post = m["post_n1"] is not None or m["post_l"] is not None
    if m["pre_l"]:
        pre: Key = (PRE_RANK[m["pre_l"].lower()], int(m["pre_n"] or 0))
    elif m["dev_l"] and not has_post:
        pre = (-1, 0)
    else:
        pre = (3, 0)
    post = int(m["post_n1"] or m["post_n2"] or 0) if has_post else -1
    dev: Key = (0, int(m["dev_n"] or 0)) if m["dev_l"] else (1, 0)
    return (int(m["epoch"] or 0), tuple(release), pre, post, dev)


def semver_key(version: str) -> Key | None:
    """SemVer 2.0 precedence: a pre-release before its release, compared field by field."""
    m = SEMVER.match(version.strip())
    if not m:
        return None
    if m[4] is None:
        pre: Key = (1,)
    else:
        fields = tuple((0, int(f), "") if f.isdigit() else (1, 0, f) for f in m[4].split("."))
        pre = (0, fields)
    return (int(m[1]), int(m[2]), int(m[3]), pre)


VERSION_KEYS: dict[str, Callable[[str], Key | None]] = {"PyPI": pep440_key, "npm": semver_key}


def normalized(ecosystem: str, name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower() if ecosystem == "PyPI" else name


def first_fix(vuln: dict[str, Any], ecosystem: str, name: str, installed: str) -> str:
    """The lowest version above `installed` that the advisory names as fixed; "" if it names
    none. An advisory that names only the last affected version gives `>` that version.
    Where versions cannot be ordered here, every version it names, joined."""
    fixes: set[str] = set()
    for affected in vuln.get("affected") or []:
        package = affected.get("package") or {}
        if package.get("ecosystem") != ecosystem:
            continue
        if normalized(ecosystem, str(package.get("name", ""))) != normalized(ecosystem, name):
            continue
        for span in affected.get("ranges") or []:
            for event in span.get("events") or []:
                if "fixed" in event:
                    fixes.add(str(event["fixed"]))
                elif "last_affected" in event:
                    fixes.add(f">{event['last_affected']}")
    key = VERSION_KEYS.get(ecosystem)
    here = order(key, installed) if key else None
    if key is None or here is None:
        return " or ".join(sorted(fixes))
    above = sorted((k, fix) for fix in fixes if (k := order(key, fix)) is not None and k > here)
    return above[0][1] if above else ""


def order(key: Callable[[str], Key | None], version: str) -> Key | None:
    """`version`'s place, where `>V` (any version above V) sits just above V."""
    k = key(version.removeprefix(">"))
    return None if k is None else (k, version.startswith(">"))


def highest(versions: list[str], ecosystem: str) -> str:
    """The highest of `versions` (the one that fixes them all); "" for none."""
    known = sorted({v for v in versions if v})
    key = VERSION_KEYS.get(ecosystem)
    if not known:
        return ""
    if key is None or any(order(key, v) is None for v in known):
        return " or ".join(known)
    return max(known, key=lambda v: order(key, v) or ())


# --- findings -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    lockfile: str
    ecosystem: str
    package: str
    version: str
    id: str  # the advisory's GHSA id where it has one, else the first id osv-scanner names
    aliases: frozenset[str]  # every id the advisory goes by, `id` among them
    fixed: str  # "" when no fixed version is published; `>V` when only V is named, as last affected
    summary: str


def fixed_in(fixed: str) -> str:
    """How a finding's fix reads: `>V` names V only as the last version affected."""
    if not fixed:
        return "no fix published"
    return f"last affected {fixed[1:]}" if fixed.startswith(">") else f"fixed in {fixed}"


def target(fixed: str) -> str:
    """The version a fix moves to: `>V` is whatever release follows V, if one is out."""
    return f"a release above {fixed[1:]}, if one is out" if fixed.startswith(">") else fixed


def findings(report: dict[str, Any], root: Path) -> list[Finding]:
    """One finding per advisory per locked package version. osv-scanner groups the ids
    (GHSA, PYSEC, CVE) that name one vulnerability; a group is one finding."""
    found: list[Finding] = []
    for result in report.get("results") or []:
        path = Path(str((result.get("source") or {}).get("path", "?")))
        lockfile = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        for entry in result.get("packages") or []:
            package = entry["package"]
            ecosystem, name, version = package["ecosystem"], package["name"], package["version"]
            by_id = {v["id"]: v for v in entry.get("vulnerabilities") or []}
            groups = entry.get("groups") or [{"ids": [i]} for i in by_id]
            for group in groups:
                ids = [str(i) for i in group["ids"]]
                vulns = [by_id[i] for i in ids if i in by_id]
                first = next((i for i in ids if i.startswith("GHSA-")), ids[0])
                summary = next(
                    (str(v.get("summary") or "").strip() for v in vulns if v["id"] == first), ""
                )
                fixes = [first_fix(v, ecosystem, name, version) for v in vulns]
                found.append(
                    Finding(
                        lockfile=lockfile,
                        ecosystem=ecosystem,
                        package=name,
                        version=version,
                        id=first,
                        aliases=frozenset(ids) | frozenset(group.get("aliases") or []),
                        fixed=highest(fixes, ecosystem),
                        summary=summary,
                    )
                )
    return sorted(found, key=lambda f: (f.lockfile, f.package, f.version, f.id))


# --- the allowlist ------------------------------------------------------------------------


@dataclass(frozen=True)
class Accept:
    number: int  # its place in the file, from 1
    id: str
    package: str
    reason: str
    expires: datetime.date

    def label(self) -> str:
        return f"entry {self.number} ({self.id}, {self.package})"


def load_allow(path: Path) -> tuple[list[Accept], list[str]]:
    """The allowlist's entries, and what is wrong with any that cannot be one."""
    if not path.exists():
        return [], []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        return [], [f"{path.name} is not TOML: {e}"]
    accepts: list[Accept] = []
    problems: list[str] = []
    unknown_tables = sorted(set(data) - {"accept"})
    if unknown_tables:
        problems.append(f"{path.name}: unknown table(s) {unknown_tables}; entries are [[accept]]")
    for number, raw in enumerate(data.get("accept") or [], 1):
        where = f"{path.name} entry {number}"
        missing = [f for f in ALLOW_FIELDS if not raw.get(f)]
        extra = sorted(set(raw) - set(ALLOW_FIELDS))
        if missing or extra:
            what = []
            if missing:
                what.append(f"lacks {', '.join(missing)}")
            if extra:
                what.append(f"has unknown field(s) {', '.join(extra)}")
            problems.append(f"{where} {' and '.join(what)}; an entry is {', '.join(ALLOW_FIELDS)}")
            continue
        expires = raw["expires"]
        if isinstance(expires, str):
            try:
                expires = datetime.date.fromisoformat(expires)
            except ValueError:
                expires = None
        if isinstance(expires, datetime.datetime) or not isinstance(expires, datetime.date):
            problems.append(f"{where}: expires must be a date, as in `expires = 2027-01-31`")
            continue
        accepts.append(
            Accept(number, str(raw["id"]), str(raw["package"]), str(raw["reason"]), expires)
        )
    return accepts, problems


@dataclass
class Verdict:
    open: list[Finding]
    accepted: list[tuple[Finding, Accept]]
    problems: list[str]


def evaluate(found: list[Finding], accepts: list[Accept], today: datetime.date) -> Verdict:
    """Each finding is open unless an entry for its package, naming one of its ids, has not
    expired. An expired entry and one that matches nothing are problems."""
    verdict = Verdict(open=[], accepted=[], problems=[])
    matched: set[int] = set()
    for finding in found:
        mine = [
            a
            for a in accepts
            if a.id in finding.aliases
            and normalized(finding.ecosystem, a.package)
            == normalized(finding.ecosystem, finding.package)
        ]
        matched.update(a.number for a in mine)
        live = [a for a in mine if a.expires >= today]
        if live:
            verdict.accepted.append((finding, live[0]))
        else:
            verdict.open.append(finding)
    for a in accepts:
        if a.expires < today:
            verdict.problems.append(
                f"{a.label()} expired on {a.expires}: fix the finding, or renew the entry"
                " with a reason that holds today and a new date"
            )
        elif a.number not in matched:
            verdict.problems.append(f"{a.label()} matches no finding: remove it")
    return verdict


# --- fixes --------------------------------------------------------------------------------


def direct_npm(package_json: Path) -> set[str]:
    try:
        manifest = json.loads(package_json.read_text())
    except (OSError, ValueError):
        return set()
    sections = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
    return {name for s in sections for name in manifest.get(s) or {}}


def fix(finding: Finding, root: Path) -> str:
    """The command that moves the lockfile to a fixed version, or what to do instead."""
    if not finding.fixed:
        return (
            "no fixed version is published: drop the dependency, or accept the finding in the"
            " allowlist with a reason and an expiry date"
        )
    lock = Path(finding.lockfile)
    where = lock.parent.as_posix()
    if lock.name == "uv.lock":
        return f"(cd {where} && uv lock --upgrade-package {finding.package})"
    if lock.name == "bun.lock":
        if finding.package in direct_npm(root / where / "package.json"):
            return f"(cd {where} && bun update {finding.package})"
        return (
            f"{finding.package} is not a direct dependency of {where}/package.json:"
            f" `bun why {finding.package}` there names what requires it, and that is what"
            " to update (`bun update <it>`)"
        )
    return f"upgrade {finding.package} to {target(finding.fixed)} in {finding.lockfile}"


# --- the scan -----------------------------------------------------------------------------


def scan(lockfiles: list[Path]) -> dict[str, Any]:
    scanner = os.environ.get("OSV_SCANNER") or "osv-scanner"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.json"
        command = [scanner, "scan", "source", "--format", "json", "--output-file", str(out)]
        command += ["--verbosity", "error"]
        for lockfile in lockfiles:
            command += ["--lockfile", str(lockfile)]
        try:
            ran = subprocess.run(command, capture_output=True, text=True)
        except FileNotFoundError:
            raise ScanError(
                f"{scanner} cannot be run: install osv-scanner on PATH (CONTRIBUTING.md, Tools)"
            ) from None
        # 0: nothing found; 1: vulnerabilities found. Anything else is the scan failing.
        if ran.returncode not in (0, 1) or not out.exists():
            raise ScanError(f"{scanner} exited {ran.returncode}: {ran.stderr.strip()}")
        return read_report(out)


def read_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ScanError(f"the report {path} cannot be read: {e}") from None
    if not isinstance(report, dict):
        raise ScanError(f"the report {path} is not an osv-scanner JSON report")
    return report


# --- the gate -----------------------------------------------------------------------------


def render(verdict: Verdict, root: Path, allow: str) -> list[str]:
    lines: list[str] = []
    if verdict.open:
        packages = {(f.lockfile, f.package, f.version) for f in verdict.open}
        lines.append(
            f"audit: {len(verdict.open)} known vulnerability(ies) in {len(packages)}"
            f" locked package(s), none accepted in {allow}"
        )
        for lockfile, package, version in sorted(packages):
            key = (lockfile, package, version)
            mine = [f for f in verdict.open if (f.lockfile, f.package, f.version) == key]
            lines.append(f"  {lockfile}: {package} {version}")
            for f in mine:
                lines.append(f"    {f.id}  {fixed_in(f.fixed)}  {f.summary}".rstrip())
            fixable = [f for f in mine if f.fixed]
            if fixable:
                to = target(highest([f.fixed for f in fixable], fixable[0].ecosystem))
                lines.append(f"    fix (to {to}): {fix(fixable[0], root)}")
            unfixable = [f for f in mine if not f.fixed]
            if unfixable:
                lines.append(
                    f"    fix ({', '.join(f.id for f in unfixable)}): {fix(unfixable[0], root)}"
                )
    if verdict.accepted:
        lines.append(f"audit: {len(verdict.accepted)} accepted in {allow}, each until its date")
        for f, a in verdict.accepted:
            lines.append(f"  {f.lockfile}: {f.package} {f.version}  {f.id}  until {a.expires}")
    if verdict.problems:
        lines.append(f"audit: {len(verdict.problems)} problem(s) in {allow}")
        lines.extend(f"  {p}" for p in verdict.problems)
    if not verdict.open and not verdict.problems:
        lines.append("audit: ok, no known vulnerability is open")
    return lines


def main(argv: list[str], today: datetime.date | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "audit").splitlines()[0])
    parser.add_argument("lockfiles", nargs="+", type=Path)
    parser.add_argument("--allow", type=Path, required=True, help="the allowlist (TOML)")
    parser.add_argument("--osv-json", type=Path, help="read this report instead of scanning")
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    lockfiles = [p.resolve() for p in args.lockfiles]
    for lockfile in lockfiles:
        if not lockfile.is_file():
            print(f"audit: no lockfile at {lockfile}", file=sys.stderr)
            return 2
    accepts, problems = load_allow(args.allow)
    for directory in sorted({p.parent for p in lockfiles}):
        if (directory / SCANNER_CONFIG).exists():
            problems.append(
                f"{(directory / SCANNER_CONFIG).relative_to(root)} would hide findings from"
                f" this gate: move what it ignores into {args.allow.name}, then remove it"
            )
    try:
        report = read_report(args.osv_json) if args.osv_json else scan(lockfiles)
    except ScanError as e:
        print(f"audit: {e}", file=sys.stderr)
        return 2
    verdict = evaluate(findings(report, root), accepts, today or datetime.date.today())
    verdict.problems[:0] = problems
    print("\n".join(render(verdict, root, args.allow.name)))
    return 1 if verdict.open or verdict.problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
