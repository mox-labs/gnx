#!/usr/bin/env python3
"""gnx grammar checker — executable form of the ratified rules.

Implements:
  GEP-0001 (accepted)  — the identity grammar: dotted, version-medial, kebab-case
                         resource, no kind segments, namespace extraction.
  GEP-0002 (accepted)  — the provides shape rule: parses-as-type_url => port,
                         dotted-lowercase phrase => tag; near-misses warn.
  GEP-0003 (PROPOSED)  — Flow compile checks: members resolve, topology derived
                         from ports, missing/duplicate producer, reject-all-cycles.
                         Marked provisional in output; the GEP is unratified.

Usage: check.py <dir-or-manifest.yaml> [...]
Exit 0 = no errors (warnings allowed), 1 = errors, 2 = usage/parse failure.

Harness-local tool. Lives in scratch/ on purpose: nothing here mints identity.
"""

import re
import sys
from pathlib import Path

import yaml

VER = re.compile(r"^v[0-9]+$")
NS_SEG = re.compile(r"^[a-z][a-z0-9]*$")
KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TAG = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
KIND_WORDS = {"skill", "agent", "capability", "flow", "kind"}
# D25 (2026-07-25) ruled the kind vocabulary OPEN and namespaced — so this is the known
# core, not a closed enum: an unknown kind is a note, not a warning, because a vendor kind
# is legal by construction.
#
# The core is FOUR (ruled 2026-08-02, revising D25's "core set grows by two"): Processor and
# View were rung errors of the same class D25 caught in Shell and Observable. A processor is
# a Capability that runs in the data plane; a View is a Capability whose payload renders.
# Both are Capability shapes, not kinds — per D25's own cited lineage, "Tool and Resource
# were merged into Capability with labels, not taxonomy."
CORE_KINDS = {"Skill", "Agent", "Capability", "Flow"}


def parse_type_url(s):
    """GEP-0001 §1/§4. Returns (namespace, ver, resource) or raises ValueError."""
    if "/" in s:
        raise ValueError("slash-form is rejected (GEP-0001 §5): dots only")
    segs = s.split(".")
    vers = [i for i, seg in enumerate(segs) if VER.match(seg)]
    if not vers:
        raise ValueError("no version segment (v[0-9]+)")
    vi = vers[0]  # §4: the FIRST v-segment splits namespace from resource
    if vi == 0:
        raise ValueError("empty namespace: version segment is first")
    if vi == len(segs) - 1:
        raise ValueError("version-terminal form is not adopted (GEP-0001 §6)")
    if vi != len(segs) - 2:
        raise ValueError("resource must be exactly one segment after the version")
    ns, resource = segs[:vi], segs[-1]
    for seg in ns:
        if not NS_SEG.match(seg):
            raise ValueError(f"bad namespace segment {seg!r} (dotted lowercase)")
    if not KEBAB.match(resource):
        raise ValueError(f"resource {resource!r} is not kebab-case")
    return ".".join(ns), segs[vi], resource


def is_type_url(s):
    try:
        parse_type_url(s)
        return True
    except ValueError:
        return False


def near_miss(s):
    """An entry that is *almost* a type_url is more likely a typo'd port than an
    exotic tag (GEP-0002 §1)."""
    return any(VER.match(seg) for seg in str(s).split(".")) and not is_type_url(s)


class Report:
    """Three severities. `note` exists because D25's open vocabulary made "unrecognised"
    a legal state — reporting it as a warning would train readers to ignore warnings."""

    def __init__(self):
        self.errors, self.warnings, self.notes = [], [], []

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}")

    def note(self, where, msg):
        self.notes.append(f"{where}: {msg}")


def check_identity(m, where, rep):
    tu = m.get("type_url")
    if not isinstance(tu, str):
        rep.err(where, "missing type_url")
        return None
    try:
        ns, _ver, _res = parse_type_url(tu)
    except ValueError as e:
        rep.err(where, f"type_url {tu!r}: {e}")
        return None
    for seg in tu.split("."):
        if seg in KIND_WORDS:
            rep.warn(where, f"type_url segment {seg!r} looks kind-derived "
                            "(GEP-0001 §3: kind never appears in the identity)")
    return tu


def classify(entry):
    """GEP-0002: port | tag | near-miss | odd."""
    s = str(entry)
    if is_type_url(s):
        return "port"
    if near_miss(s):
        return "near-miss"
    if TAG.match(s):
        return "tag"
    return "odd"


def check_ports(m, where, rep):
    ports_out, ports_in = [], []
    for entry in m.get("provides") or []:
        c = classify(entry)
        if c == "port":
            ports_out.append(str(entry))
        elif c == "near-miss":
            rep.warn(where, f"provides {entry!r}: almost a type_url — typo'd port?")
        elif c == "odd":
            rep.warn(where, f"provides {entry!r}: neither port nor dotted tag")
    for entry in m.get("requires") or []:
        c = classify(entry)
        if c == "port":
            ports_in.append(str(entry))
        elif c == "near-miss":
            rep.err(where, f"requires {entry!r}: almost a type_url — requires "
                           "entries are ports (GEP-0002)")
        else:
            rep.err(where, f"requires {entry!r}: not a port — requires matches "
                           "provided type_urls by string equality (GEP-0002)")
    return ports_out, ports_in


def check_flow(m, where, catalog, rep):
    """GEP-0003 (PROPOSED) compile checks. catalog: type_url -> manifest."""
    members = m.get("members")
    if not isinstance(members, list) or not members:
        rep.err(where, "[GEP-0003 provisional] Flow has no members list")
        return
    resolved = []
    for i, mem in enumerate(members):
        mtu = (mem or {}).get("type_url")
        mwhere = f"{where} members[{i}]"
        if not isinstance(mtu, str) or not is_type_url(mtu):
            rep.err(mwhere, f"[GEP-0003 provisional] bad member type_url {mtu!r}")
            continue
        if mtu not in catalog:
            rep.err(mwhere, f"[GEP-0003 provisional] unresolved member {mtu!r}")
            continue
        resolved.append(mtu)

    # Derived topology over member ports (provided -> required, string equality).
    provided = {}
    for mtu in resolved:
        for p in (catalog[mtu].get("provides") or []):
            if classify(p) == "port":
                provided.setdefault(str(p), []).append(mtu)
    for port, producers in provided.items():
        if len(producers) > 1:
            rep.err(where, f"[GEP-0003 provisional] duplicate producer for {port}: "
                           f"{', '.join(producers)} (single-producer is per-composition)")
    edges = {mtu: set() for mtu in resolved}
    for mtu in resolved:
        for r in (catalog[mtu].get("requires") or []):
            r = str(r)
            producers = provided.get(r, [])
            if not producers:
                rep.err(where, f"[GEP-0003 provisional] missing producer for {r} "
                               f"(required by {mtu}; closed-world inputs)")
            for p in producers:
                if p != mtu:
                    edges[p].add(mtu)

    # Reject-all-cycles baseline (GEP-0003 unresolved: leashes unrecovered).
    seen, done = set(), set()

    def dfs(n, path):
        seen.add(n)
        path.append(n)
        for nxt in edges[n]:
            if nxt in path:
                cyc = path[path.index(nxt):] + [nxt]
                rep.err(where, "[GEP-0003 provisional] cycle: " + " -> ".join(cyc))
                continue
            if nxt not in done:
                dfs(nxt, path)
        path.pop()
        done.add(n)

    for n in resolved:
        if n not in done:
            dfs(n, [])


def load_manifests(paths):
    """Collect manifests. A directory scan is *selective*; an explicit file is not.

    components/ now holds payloads as well as manifests, and payload YAML is not ours
    to validate: semgrep rule files, recon collector configs, skill examples. Two
    consequences for a directory walk — a file that fails to parse is skipped with a
    warning rather than aborting the run, and a file that parses but carries no
    type_url is not a manifest and is ignored.

    A path named explicitly on the command line is always treated as a manifest, so
    the harness/drafts/ workflow (draft manifests named per-component, not
    manifest.yaml) keeps its strict behaviour: a broken draft still fails loudly.
    """
    explicit, scanned = [], []
    for p in map(Path, paths):
        if p.is_dir():
            scanned += sorted(p.rglob("*.yaml")) + sorted(p.rglob("*.yml"))
        else:
            explicit.append(p)

    out = []
    for f in explicit:
        try:
            doc = yaml.safe_load(f.read_text())
        except yaml.YAMLError as e:
            print(f"PARSE FAIL {f}: {e}", file=sys.stderr)
            sys.exit(2)
        if isinstance(doc, dict):
            out.append((f, doc))

    for f in scanned:
        if ".venv" in f.parts or "node_modules" in f.parts:
            continue
        try:
            doc = yaml.safe_load(f.read_text())
        except yaml.YAMLError as e:
            first = str(e).splitlines()[0]
            print(f"skip  {f}: not parseable as YAML ({first})", file=sys.stderr)
            continue
        # type_url is the identity and the join key — no manifest exists without it.
        if isinstance(doc, dict) and "type_url" in doc:
            out.append((f, doc))
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    manifests = load_manifests(argv[1:])
    rep = Report()
    catalog = {}
    for f, m in manifests:
        tu = check_identity(m, f.name, rep)
        if tu:
            if tu in catalog:
                rep.err(f.name, f"duplicate type_url {tu} (identity is the join key)")
            else:
                catalog[tu] = m
    for f, m in manifests:
        where = f.name
        check_ports(m, where, rep)
        kind = m.get("kind")
        if kind is None:
            rep.warn(where, "no kind (overlay field; GEP-0009 territory)")
        elif kind not in CORE_KINDS:
            rep.note(where, f"kind {kind!r} outside the core set — legal under D25's open vocabulary")
    for f, m in manifests:
        if m.get("kind") == "Flow":
            check_flow(m, f.name, catalog, rep)

    for n in rep.notes:
        print(f"note  {n}")
    for w in rep.warnings:
        print(f"warn  {w}")
    for e in rep.errors:
        print(f"ERROR {e}")
    print(f"\n{len(manifests)} manifests, {len(catalog)} identities, "
          f"{len(rep.errors)} errors, {len(rep.warnings)} warnings, {len(rep.notes)} notes")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
