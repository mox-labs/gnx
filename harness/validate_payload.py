#!/usr/bin/env python3
"""Payload validator — checks component payloads against Claude Code's documented shape.

Companion to check.py. check.py validates *manifests* (the gnx identity grammar);
this validates *payloads* (the agent.md / SKILL.md that a plugin projection ships).

Why this exists rather than plugin-dev's own `scripts/validate-agent.sh`: that script
cannot validate any file whose description is a YAML block scalar. It extracts fields
with `grep '^description:' | sed`, so a `description: |` block yields the single
character "|", which then trips its own "too short" warning. And because it runs under
`set -euo pipefail`, the first `((warning_count++))` on a zero-valued counter evaluates
to 0, which bash reads as false, and the script exits 1 mid-run — before it ever checks
model, color, tools, or the system prompt. Verified 2026-08-17 against
plugin-dev@claude-plugins-official (cache timestamp 2026-08-17T12:15Z).

The *rules* below are plugin-dev's, from skills/agent-development/SKILL.md. Only the
implementation differs: real YAML parsing, and it does not stop at the first finding.

Usage: validate_payload.py <dir-or-file> [...]
Exit 0 = no errors (warnings allowed), 1 = errors, 2 = usage/parse failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
VALID_MODELS = {"inherit", "sonnet", "opus", "haiku"}
VALID_COLORS = {"blue", "cyan", "green", "yellow", "magenta", "red"}
GENERIC_NAMES = {"helper", "assistant", "agent", "tool", "skill"}

# Names that deviate from plugin-dev's convention and are kept anyway, each with a
# reason. An entry here downgrades the length/format finding to a note — the deviation
# stays visible in every run instead of being silently whitelisted away.
#
# `k` is a single character, below plugin-dev's 3-char floor. That floor is plugin-dev's
# own convention, not a Claude Code constraint: `guild-arch:k` loads and dispatches in
# Claude Code today. The name is the persona's identity (the strategic advisor), so
# renaming it to satisfy a convention would cost more than it buys. Owed: a ruling from
# the principal — karman, the naming steward, is the right seat for it.
NAME_EXCEPTIONS = {
    "k": "single-char persona name; works in Claude Code today, ruling owed",
}
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)

# plugin-dev/agent-development: name 3-50, description 10-5000, prompt 20-10000.
NAME_MIN, NAME_MAX = 3, 50
DESC_MIN, DESC_MAX = 10, 5000
PROMPT_MIN, PROMPT_MAX = 20, 10000
# plugin-dev/skill-development budgets SKILL.md in words: 1,500-2,000 ideal,
# 3,000 ceiling, 5,000 hard max, overflow into references/.
SKILL_WORDS_TARGET, SKILL_WORDS_CEIL, SKILL_WORDS_MAX = 2000, 3000, 5000
# dao-corpus/corpora/agent-craft/distillation/RUBRIC.md, A1 — trigger surface scoring.
A1_SCENARIOS_MIN, A1_SCENARIOS_MAX = 2, 5

# --- Platform limits, from docs/_deferred/skill-authoring/FINDINGS.md -----------------
# Researched 2026-08-17 against platform.claude.com + Anthropic's own validator. These are
# HARD limits enforced on upload, distinct from plugin-dev's authoring guidance: a skill
# outside them is rejected, not degraded.
SKILL_NAME_MAX = 64                 # C1
SKILL_DESC_MAX = 1024               # C2
SKILL_RESERVED_WORDS = ("anthropic", "claude")   # C1 — forbidden inside `name`
# C3: the top-level frontmatter key set is CLOSED and fails hard on an unknown key.
SKILL_ALLOWED_KEYS = frozenset(
    {"name", "description", "license", "allowed-tools", "metadata", "compatibility"}
)
# C5: these belong under `metadata:`, never at top level.
SKILL_METADATA_KEYS = ("version", "author", "tags", "updated")

# C13/C14 — the collection-level budget, which is what actually bites at catalog scale.
# Every skill's description is concatenated into one listing; past the ceiling, entries are
# truncated away and those skills can never trigger. Truncation is CUMULATIVE (C15), so
# this is a property of the catalog, not of any one skill.
LISTING_ENTRY_OVERHEAD = 109        # measured per-entry cost beyond the description
LISTING_CEILING = 15500             # low end of the measured 15.5-16k ceiling
DESC_TARGET_MAX = 260               # C14 target at catalog scale, not the 1024 hard cap


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")

    def note(self, where: str, msg: str) -> None:
        self.notes.append(f"{where}: {msg}")


def split_frontmatter(text: str):
    m = FRONTMATTER.match(text)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        return ("PARSE", str(e))
    if not isinstance(fm, dict):
        return None
    return fm, m.group(2)


def check_name(fm: dict, where: str, rep: Report, expected: str | None) -> None:
    name = fm.get("name")
    if not isinstance(name, str) or not name:
        rep.err(where, "missing required field `name`")
        return
    excused = NAME_EXCEPTIONS.get(name)
    report = rep.note if excused else rep.err
    suffix = f" — allowed exception: {excused}" if excused else ""
    # A single-character name cannot satisfy NAME_RE (it needs a start AND an end char),
    # so check the charset separately from the shape to keep the message honest.
    if not re.fullmatch(r"[a-z0-9-]+", name):
        rep.err(where, f"name {name!r}: only lowercase letters, digits and hyphens allowed")
    elif not NAME_RE.match(name):
        report(where, f"name {name!r}: must start and end with an alphanumeric{suffix}")
    if not (NAME_MIN <= len(name) <= NAME_MAX):
        report(where, f"name {name!r}: length {len(name)} outside "
                      f"{NAME_MIN}-{NAME_MAX}{suffix}")
    if name in GENERIC_NAMES:
        rep.warn(where, f"name {name!r} is too generic")
    if expected and name != expected:
        # Not an error: the CC-facing name and the component directory are allowed to
        # diverge (claim-extraction ships an agent named `extract`). Worth surfacing.
        rep.note(where, f"name {name!r} differs from directory {expected!r}")


def check_description(fm: dict, where: str, rep: Report, kind: str) -> None:
    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        rep.err(where, "missing required field `description`")
        return
    n = len(desc)
    if n < DESC_MIN:
        rep.err(where, f"description too short ({n} chars, minimum {DESC_MIN})")
    elif n > DESC_MAX:
        rep.warn(where, f"description very long ({n} chars, over {DESC_MAX})")
    if kind == "skill":
        # Platform-hard limits (FINDINGS C1/C2) — distinct from the authoring guidance above.
        if n > SKILL_DESC_MAX:
            rep.err(where, f"description {n} chars — over the platform maximum of "
                           f"{SKILL_DESC_MAX}; upload rejects this")
        if "<" in desc or ">" in desc:
            rep.err(where, "description contains angle brackets — XML tags are rejected (C2)")
        if n > DESC_TARGET_MAX:
            rep.note(where, f"description {n} chars — over the {DESC_TARGET_MAX}-char "
                            "catalog-scale target (C14); it consumes shared listing budget")
    if kind == "agent":
        if not re.search(r"use this agent when|use when", desc, re.I):
            rep.warn(where, "description should contain the 'Use this agent when ...' trigger pattern")
        if "<example>" in desc:
            rep.warn(where, "description still carries <example> blocks — current guidance "
                            "puts worked scenarios in a 'When to invoke' body section")
        # dao-corpus agent-craft RUBRIC, A1 (trigger surface): 2-5 concrete scenarios phrased
        # as practitioner intent scores 3; fewer than 2, or more than 5, scores 1. The count is
        # checkable even though the quality of each scenario is not.
        triggers = re.search(r"Typical triggers?:?\s*(.+?)(?:\s*See \"When to invoke\"|$)",
                             desc, re.S | re.I)
        if triggers:
            n = len([c for c in re.split(r";|,\s*and\s+", triggers.group(1)) if c.strip()])
            if n < A1_SCENARIOS_MIN:
                rep.warn(where, f"only {n} trigger scenario(s) — RUBRIC A1 wants "
                                f"{A1_SCENARIOS_MIN}-{A1_SCENARIOS_MAX} concrete scenarios")
            elif n > A1_SCENARIOS_MAX:
                rep.warn(where, f"{n} trigger scenarios — RUBRIC A1 scores >{A1_SCENARIOS_MAX} "
                                "as a keyword list, not a trigger surface")
        # RUBRIC A1 scores 0 when "the figure's name [is] used as the trigger": a description
        # whose WHEN clause is the seat's own name tells a reader nothing they did not already
        # need to know. Flagged when the name appears inside the trigger clause itself.
        name = str(fm.get("name", "")).strip()
        if name and triggers and re.search(rf"\b{re.escape(name)}\b", triggers.group(1), re.I):
            rep.warn(where, f"the seat's own name {name!r} appears in its trigger clause — "
                            "RUBRIC A1 scores name-as-trigger at 0; triggers name situations")


def check_model_color(fm: dict, where: str, rep: Report) -> None:
    model = fm.get("model")
    if model is None:
        rep.err(where, "missing required field `model`")
    elif model not in VALID_MODELS:
        rep.warn(where, f"model {model!r} outside {sorted(VALID_MODELS)}")

    color = fm.get("color")
    if color is None:
        rep.err(where, "missing required field `color`")
    elif color not in VALID_COLORS:
        rep.err(where, f"color {color!r} outside the documented palette {sorted(VALID_COLORS)}")


def check_tools(fm: dict, where: str, rep: Report) -> None:
    tools = fm.get("tools")
    if tools is None:
        rep.note(where, "no `tools` field — agent inherits all tools")
        return
    if isinstance(tools, str):
        rep.warn(where, "`tools` is a string; the documented form is a list")
        return
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        rep.err(where, "`tools` must be a list of tool-name strings")


def check_body(body: str, where: str, rep: Report, kind: str) -> None:
    text = body.strip()
    if not text:
        rep.err(where, "body (system prompt) is empty")
        return
    n = len(text)
    if n < PROMPT_MIN:
        rep.err(where, f"body too short ({n} chars, minimum {PROMPT_MIN})")
    if kind == "agent":
        # plugin-dev/agent-development budgets the system prompt in characters.
        if n > PROMPT_MAX:
            rep.warn(where, f"body very long ({n} chars, over {PROMPT_MAX})")
    else:
        # plugin-dev/skill-development budgets SKILL.md in WORDS, not characters:
        # 1,500-2,000 ideal, 3,000 ceiling, 5,000 hard max, with the overflow moved
        # into references/. Measuring skills against the agent char limit reports the
        # wrong rule.
        words = len(text.split())
        if words > SKILL_WORDS_MAX:
            rep.warn(where, f"SKILL.md ~{words} words — over the {SKILL_WORDS_MAX}-word "
                            "hard max; extract detail into references/")
        elif words > SKILL_WORDS_CEIL:
            rep.warn(where, f"SKILL.md ~{words} words — over the {SKILL_WORDS_CEIL}-word "
                            "ceiling; candidate for references/ extraction")
        elif words > SKILL_WORDS_TARGET:
            rep.note(where, f"SKILL.md ~{words} words — over the "
                            f"{SKILL_WORDS_TARGET}-word target, under the ceiling")
    if kind == "agent":
        if not re.search(r"\bYou are\b|\bYou will\b|\bYour\b|\bYou\b", text):
            rep.warn(where, "body should address the agent in second person")
        if "## When to invoke" not in text:
            rep.warn(where, "body has no 'When to invoke' section")


def check_skill_frontmatter(fm: dict, where: str, rep: Report) -> None:
    """The platform's closed key set and name rules (FINDINGS C1/C3/C4/C5)."""
    name = fm.get("name")
    if isinstance(name, str):
        if len(name) > SKILL_NAME_MAX:
            rep.err(where, f"name {len(name)} chars — over the platform maximum of {SKILL_NAME_MAX}")
        for word in SKILL_RESERVED_WORDS:
            if word in name.lower():
                rep.err(where, f"name contains the reserved word {word!r} (C1)")
        if name.startswith("-") or name.endswith("-") or "--" in name:
            rep.err(where, "name has a leading/trailing hyphen or a double hyphen (C4)")
    unknown = sorted(set(fm) - SKILL_ALLOWED_KEYS)
    for key in unknown:
        if key in SKILL_METADATA_KEYS:
            rep.err(where, f"`{key}` is a top-level key — it belongs under `metadata:` (C5); "
                           "the top-level key set is closed and upload fails hard (C3)")
        else:
            rep.err(where, f"`{key}` is outside the closed top-level key set "
                           f"{sorted(SKILL_ALLOWED_KEYS)} — upload fails hard (C3)")


def check_listing_budget(paths: list[Path], rep: Report) -> None:
    """The catalog-level budget (C13/C15). Reported once, not per skill.

    Truncation is cumulative and favours dropping the *newest* skills (C17), so a catalog
    over budget has entries that silently never trigger. This cannot be diagnosed one
    skill at a time, which is why it is a collection-level check.
    """
    entries: list[tuple[str, int]] = []
    for p in paths:
        if p.name != "SKILL.md":
            continue
        parsed = split_frontmatter(p.read_text())
        if not isinstance(parsed, tuple) or parsed[0] == "PARSE":
            continue
        fm = parsed[0]
        if isinstance(fm, dict):
            entries.append((p.parent.name, len(str(fm.get("description", "")))))
    if not entries:
        return
    total = sum(d + LISTING_ENTRY_OVERHEAD for _, d in entries)
    where = f"<catalog: {len(entries)} skills>"
    if total > LISTING_CEILING:
        worst = ", ".join(f"{n} ({d})" for n, d in sorted(entries, key=lambda x: -x[1])[:4])
        rep.err(where, f"skill listing is {total:,} chars against a ~{LISTING_CEILING:,} "
                       f"ceiling — over by {total - LISTING_CEILING:,}. Entries past the "
                       f"ceiling are truncated away and can never trigger. Largest: {worst}")
    else:
        rep.note(where, f"skill listing {total:,} / ~{LISTING_CEILING:,} chars "
                        f"({LISTING_CEILING - total:,} of headroom)")


def check_dangling(body: str, fm: dict, where: str, rep: Report) -> None:
    """Pointers that only resolved inside the cix plugin tree.

    Bench step 3 forbids a payload that still reaches into cix. `skills:` is cix's
    plugin-relative dependency field and is recorded in harness/intake-ledger.yaml
    for manifest generation, so it is a note here, not a finding.
    """
    if fm.get("skills"):
        # NOT an undocumented accident, though plugin-dev does not list it: the dao registry
        # defines it deliberately — "the registry is the repertoire; the frontmatter `skills:`
        # is the preload subset (preload ⊂ repertoire)". Recorded so manifest relations can be
        # generated from the ledger later, and so a preload naming a practice the component
        # does not ship stays visible.
        rep.note(where, "`skills` preload subset declared (preload ⊂ repertoire) — "
                        "recorded in intake-ledger.yaml for the manifest pass")
    # A plugin-root reference that walks UP escapes into a sibling plugin. Install
    # copies only the plugin subdirectory, so the target does not exist on the installed
    # machine (verified install mechanics). Matches braced and unbraced forms — the first
    # real instance of this bug used `$CLAUDE_PLUGIN_ROOT/../`, which a braces-only
    # pattern silently passed.
    for m in re.finditer(r"\$\{?CLAUDE_PLUGIN_ROOT\}?(/\.\.)+", body):
        rep.err(where, f"plugin-root reference escapes into a sibling plugin: {m.group(0)!r} "
                       "— install copies only this plugin's subdirectory")
    for m in re.finditer(r"\bcix/(?:plugins|tools)/", body):
        rep.err(where, f"dangling path into cix: {m.group(0)!r}")
    # Relative links that climb out of the component directory. These are NOT findings
    # here: a link like ../eliciting/SKILL.md is broken relative to components/skills/,
    # but resolves correctly once both skills land in the same projected plugin
    # (skills/research/ -> skills/eliciting/). Whether it actually resolves is a property
    # of the projection, so it is checked there — see `gnx build`'s link check — and only
    # recorded here.
    climbs = {m.group(1) for m in re.finditer(r"\]\(((?:\.\./)+[^)\s]+)\)", body)}
    if climbs:
        rep.note(where, f"{len(climbs)} bundle-relative link(s) — resolved at projection, "
                        f"not here: {', '.join(sorted(climbs)[:3])}"
                        + (" …" if len(climbs) > 3 else ""))


def validate_file(path: Path, root: Path, rep: Report) -> None:
    kind = "agent" if path.name == "agent.md" else "skill"
    resolved = path.resolve()
    try:
        where = str(resolved.relative_to(root.resolve()))
    except ValueError:
        where = str(path)
    parsed = split_frontmatter(path.read_text())
    if parsed is None:
        rep.err(where, "no YAML frontmatter")
        return
    if isinstance(parsed, tuple) and parsed[0] == "PARSE":
        rep.err(where, f"frontmatter is not valid YAML: {parsed[1]}")
        return
    fm, body = parsed
    expected = path.parent.name
    check_name(fm, where, rep, expected)
    check_description(fm, where, rep, kind)
    if kind == "skill":
        check_skill_frontmatter(fm, where, rep)
    if kind == "agent":
        check_model_color(fm, where, rep)
        check_tools(fm, where, rep)
    check_body(body, where, rep, kind)
    check_dangling(body, fm, where, rep)


def collect(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out += sorted(p.rglob("agent.md")) + sorted(p.rglob("SKILL.md"))
        elif p.is_file():
            out.append(p)
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    files = collect(argv[1:])
    if not files:
        print("no agent.md / SKILL.md found in the given paths", file=sys.stderr)
        return 2
    root = Path.cwd()
    rep = Report()
    for f in files:
        validate_file(f, root, rep)
    check_listing_budget(files, rep)

    for n in rep.notes:
        print(f"note  {n}")
    for w in rep.warnings:
        print(f"warn  {w}")
    for e in rep.errors:
        print(f"ERROR {e}")
    print(f"\n{len(files)} payloads, {len(rep.errors)} errors, "
          f"{len(rep.warnings)} warnings, {len(rep.notes)} notes")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
