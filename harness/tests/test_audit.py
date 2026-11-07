"""audit: an open finding fails the gate, the allowlist accepts only what it names until its
date, and every finding says how to fix it.

The reports here are osv-scanner's JSON shape (`scan source --format json`), cut down to the
fields the gate reads. No test runs the scanner or reaches the network.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

import audit  # noqa: E402

TODAY = datetime.date(2026, 11, 7)


def advisory(
    vid: str, ecosystem: str, name: str, *events: dict[str, str], summary: str = ""
) -> dict[str, Any]:
    return {
        "id": vid,
        "summary": summary or f"{name}: something bad",
        "affected": [
            {
                "package": {"ecosystem": ecosystem, "name": name},
                "ranges": [{"type": "ECOSYSTEM", "events": list(events)}],
            }
        ],
    }


def report(root: Path, lockfile: str, *packages: dict[str, Any]) -> dict[str, Any]:
    return {
        "results": [
            {
                "source": {"path": str(root / lockfile), "type": "lockfile"},
                "packages": list(packages),
            }
        ]
    }


def locked(
    ecosystem: str, name: str, version: str, vulns: list[dict[str, Any]], groups: list[list[str]]
) -> dict[str, Any]:
    return {
        "package": {"name": name, "version": version, "ecosystem": ecosystem},
        "vulnerabilities": vulns,
        "groups": [{"ids": ids, "aliases": [*ids, f"CVE-{ids[0]}"]} for ids in groups],
    }


def pillow(root: Path) -> dict[str, Any]:
    """pillow 12.3.0 in a uv.lock, with one advisory under two ids (PYSEC and GHSA)."""
    pysec = advisory("PYSEC-1", "PyPI", "Pillow", {"introduced": "0"}, {"fixed": "13.0.0"})
    ghsa = advisory(
        "GHSA-aaaa", "PyPI", "pillow", {"introduced": "0"}, {"fixed": "12.4.0"}, summary="overflow"
    )
    return report(
        root,
        "py/uv.lock",
        locked("PyPI", "pillow", "12.3.0", [pysec, ghsa], [["PYSEC-1", "GHSA-aaaa"]]),
    )


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- versions -----------------------------------------------------------------------------


def test_pep440_orders_dev_pre_release_and_post() -> None:
    order = ["1.0.dev0", "1.0a1", "1.0b2", "1.0rc1", "1.0", "1.0.post1", "1.0.1", "1.10", "2!0.1"]
    keys = [audit.pep440_key(v) for v in order]
    assert all(k is not None for k in keys)
    assert keys == sorted(keys)  # type: ignore[type-var]
    assert audit.pep440_key("1.0") == audit.pep440_key("1.0.0")


def test_semver_orders_pre_releases_field_by_field() -> None:
    order = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta.2", "1.0.0-beta.11"]
    order += ["1.0.0-rc.1", "1.0.0", "2.9.0", "2.10.0"]
    keys = [audit.semver_key(v) for v in order]
    assert all(k is not None for k in keys)
    assert keys == sorted(keys)  # type: ignore[type-var]


def test_the_fix_is_the_lowest_fixed_version_above_the_locked_one() -> None:
    vuln = advisory(
        "GHSA-x",
        "npm",
        "nanoid",
        {"introduced": "0"},
        {"fixed": "3.3.8"},
        {"introduced": "4.0.0"},
        {"fixed": "5.0.9"},
    )
    assert audit.first_fix(vuln, "npm", "nanoid", "3.3.3") == "3.3.8"
    assert audit.first_fix(vuln, "npm", "nanoid", "4.0.2") == "5.0.9"


def test_an_advisory_that_names_only_the_last_affected_version_says_so() -> None:
    vuln = advisory(
        "GHSA-y", "PyPI", "pyjwt", {"introduced": "2.11.0"}, {"last_affected": "2.13.0"}
    )
    assert audit.first_fix(vuln, "PyPI", "pyjwt", "2.13.0") == ">2.13.0"
    assert audit.fixed_in(">2.13.0") == "last affected 2.13.0"
    assert audit.highest([">2.13.0", "2.15.0"], "PyPI") == "2.15.0"


def test_an_advisory_with_no_fixed_version_has_none() -> None:
    vuln = advisory("GHSA-z", "npm", "deepmerge", {"introduced": "0"})
    assert audit.first_fix(vuln, "npm", "deepmerge", "4.3.1") == ""
    assert audit.fixed_in("") == "no fix published"


# --- findings -----------------------------------------------------------------------------


def test_one_advisory_under_two_ids_is_one_finding_named_by_its_ghsa(tmp_path: Path) -> None:
    [finding] = audit.findings(pillow(tmp_path), tmp_path)
    assert (finding.lockfile, finding.package, finding.version) == (
        "py/uv.lock",
        "pillow",
        "12.3.0",
    )
    assert finding.id == "GHSA-aaaa"
    assert {"PYSEC-1", "GHSA-aaaa", "CVE-PYSEC-1"} <= finding.aliases
    assert finding.fixed == "13.0.0"  # the version that fixes both of the group's records
    assert finding.summary == "overflow"


# --- the allowlist ------------------------------------------------------------------------


def accept(id: str, package: str, expires: datetime.date, number: int = 1) -> audit.Accept:
    return audit.Accept(number, id, package, "a reason", expires)


def test_an_entry_accepts_the_finding_by_any_alias_until_its_date(tmp_path: Path) -> None:
    found = audit.findings(pillow(tmp_path), tmp_path)
    verdict = audit.evaluate(found, [accept("CVE-PYSEC-1", "Pillow", TODAY)], TODAY)
    assert (verdict.open, verdict.problems) == ([], [])
    assert [f.id for f, _ in verdict.accepted] == ["GHSA-aaaa"]


def test_an_entry_for_another_package_accepts_nothing(tmp_path: Path) -> None:
    found = audit.findings(pillow(tmp_path), tmp_path)
    verdict = audit.evaluate(found, [accept("GHSA-aaaa", "pyjwt", TODAY)], TODAY)
    assert [f.id for f in verdict.open] == ["GHSA-aaaa"]
    assert verdict.problems == ["entry 1 (GHSA-aaaa, pyjwt) matches no finding: remove it"]


def test_an_expired_entry_fails_the_gate_and_accepts_nothing(tmp_path: Path) -> None:
    found = audit.findings(pillow(tmp_path), tmp_path)
    yesterday = TODAY - datetime.timedelta(days=1)
    verdict = audit.evaluate(found, [accept("GHSA-aaaa", "pillow", yesterday)], TODAY)
    assert [f.id for f in verdict.open] == ["GHSA-aaaa"]
    assert len(verdict.problems) == 1
    assert "expired on 2026-11-06" in verdict.problems[0]


def test_an_entry_that_matches_no_finding_fails_the_gate() -> None:
    verdict = audit.evaluate([], [accept("GHSA-gone", "pillow", TODAY)], TODAY)
    assert verdict.problems == ["entry 1 (GHSA-gone, pillow) matches no finding: remove it"]


def test_an_entry_must_have_every_field_and_no_other(tmp_path: Path) -> None:
    allow = write(
        tmp_path / "allow.toml",
        '[[accept]]\nid = "GHSA-a"\npackage = "p"\nexpires = 2027-01-01\n\n'
        '[[accept]]\nid = "GHSA-b"\npackage = "p"\nreason = "r"\nexpiry = 2027-01-01\n\n'
        '[[accept]]\nid = "GHSA-c"\npackage = "p"\nreason = "r"\nexpires = "soon"\n\n'
        '[[accept]]\nid = "GHSA-d"\npackage = "p"\nreason = "r"\nexpires = 2027-01-01\n',
    )
    accepts, problems = audit.load_allow(allow)
    assert [a.id for a in accepts] == ["GHSA-d"]
    assert accepts[0].expires == datetime.date(2027, 1, 1)
    assert "entry 1 lacks reason" in problems[0]
    assert "entry 2 lacks expires and has unknown field(s) expiry" in problems[1]
    assert "entry 3: expires must be a date" in problems[2]


# --- fixes --------------------------------------------------------------------------------


def finding(lockfile: str, package: str, fixed: str = "1.2.3") -> audit.Finding:
    return audit.Finding(
        lockfile, "npm", package, "1.0.0", "GHSA-x", frozenset({"GHSA-x"}), fixed, ""
    )


def test_a_uv_finding_is_fixed_by_upgrading_that_package_in_the_lock(tmp_path: Path) -> None:
    assert audit.fix(finding("components/capabilities/uv.lock", "pillow"), tmp_path) == (
        "(cd components/capabilities && uv lock --upgrade-package pillow)"
    )


def test_a_bun_finding_names_bun_update_only_for_a_direct_dependency(tmp_path: Path) -> None:
    write(tmp_path / "site/package.json", json.dumps({"devDependencies": {"vite": "^8.0.0"}}))
    assert audit.fix(finding("site/bun.lock", "vite"), tmp_path) == "(cd site && bun update vite)"
    transitive = audit.fix(finding("site/bun.lock", "nanoid"), tmp_path)
    assert transitive.startswith("nanoid is not a direct dependency of site/package.json")
    assert "`bun why nanoid`" in transitive


def test_a_finding_with_no_fix_says_what_to_do_instead(tmp_path: Path) -> None:
    assert audit.fix(finding("site/bun.lock", "deepmerge", fixed=""), tmp_path).startswith(
        "no fixed version is published"
    )


# --- the gate -----------------------------------------------------------------------------


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    write(tmp_path / "py/uv.lock", "version = 1\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def gate(tree: Path, data: dict[str, Any], allow: str = "") -> int:
    write(tree / "report.json", json.dumps(data))
    write(tree / "allow.toml", allow)
    return audit.main(
        ["--allow", "allow.toml", "--osv-json", "report.json", "py/uv.lock"], today=TODAY
    )


def test_an_open_finding_fails_and_prints_what_fixes_it(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert gate(tree, pillow(tree)) == 1
    out = capsys.readouterr().out
    assert "py/uv.lock: pillow 12.3.0" in out
    assert "GHSA-aaaa  fixed in 13.0.0  overflow" in out
    assert "fix (to 13.0.0): (cd py && uv lock --upgrade-package pillow)" in out


def test_a_clean_report_passes(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert gate(tree, {"results": []}) == 0
    assert "audit: ok" in capsys.readouterr().out


def test_an_accepted_finding_passes_and_is_still_listed(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allow = '[[accept]]\nid = "GHSA-aaaa"\npackage = "pillow"\nreason = "r"\nexpires = 2026-11-07\n'
    assert gate(tree, pillow(tree), allow) == 0
    assert "py/uv.lock: pillow 12.3.0  GHSA-aaaa  until 2026-11-07" in capsys.readouterr().out


def test_an_osv_scanner_ignore_file_beside_a_lockfile_fails_the_gate(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tree / "py/osv-scanner.toml", '[[IgnoredVulns]]\nid = "GHSA-aaaa"\n')
    assert gate(tree, {"results": []}) == 1
    assert "py/osv-scanner.toml would hide findings" in capsys.readouterr().out


def test_a_scanner_that_is_not_there_is_an_error_not_a_pass(
    tree: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OSV_SCANNER", str(tree / "no-such-scanner"))
    write(tree / "allow.toml", "")
    assert audit.main(["--allow", "allow.toml", "py/uv.lock"], today=TODAY) == 2
    assert "cannot be run" in capsys.readouterr().err
