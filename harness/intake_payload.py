#!/usr/bin/env python3
"""Payload intake — translate a cix plugin unit into a gnx component payload.

Bench step 3 ("pristine pass") made repeatable. This tool does the *mechanical*
half of the translation the claim-extraction exemplar did by hand:

  1. Normalise frontmatter to the fields Claude Code documents
     (name, description, model, color, tools) — see plugin-dev/agent-development.
  2. Relocate `<example>` / `<commentary>` blocks out of `description` and into a
     "## When to invoke" body section as prose bullets, then leave `description` as
     a prose trigger summary that points at it. This is the format plugin-dev's
     agent-development skill documents as current; cix authored the older XML-in-
     description form.
  3. Repair the defects the survey found: colors outside the documented palette,
     and a missing required `model`.
  4. Record — never silently drop — every plugin-relative dependency, so the
     manifests (deferred until slick lands) can be generated from a ledger instead
     of re-derived by reading 66 payloads again.

What it deliberately does NOT do: rewrite prose. The lead sentence, the worked
scenarios, and the whole system prompt travel verbatim. Voice is the author's;
this tool only moves text and fixes enumerated fields.

Usage:
  intake_payload.py --plan            # print what would change, write nothing
  intake_payload.py --apply           # write components/ + the ledger

Exit 0 = clean, 1 = a unit could not be translated (nothing written for it).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

GNX = Path(__file__).resolve().parent.parent
CIX = Path.home() / "mox" / "products" / "cix" / "plugins"

# The palette Claude Code documents. cix used six colors outside it; each maps to
# the documented color closest in *role*, per plugin-dev's own semantics
# (blue/cyan = analysis+review, green = success, yellow = caution+validation,
# red = critical+security, magenta = creative+generation).
COLOR_REPAIR = {
    "amber": "yellow",    # audit, ebert — both are validation gates
    "indigo": "magenta",  # elicit — generative discourse
    "orange": "blue",     # jobs — experience analysis
    "teal": "cyan",       # magellan — survey/analysis
    "purple": "magenta",  # vyasa — generative architecture
}
VALID_COLORS = {"blue", "cyan", "green", "yellow", "magenta", "red"}
VALID_MODELS = {"inherit", "sonnet", "opus", "haiku"}

# Plugins that carry gen-0 units. `_radix.parked-while-tuning` is excluded: radix is
# a declarative-only Capability that already landed, and cix itself parked it.
PLUGINS = [
    "antifragile",
    "ci-scaffolds",
    "craft-evals",
    "craft-extensions",
    "craft-research",
    "craft-rhetoric",
    "guild-arch",
]

# Already translated by hand as the bench exemplar (2026-07-23) — do not overwrite.
ALREADY_PORTED = {("craft-research", "extract")}

FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
EXAMPLE = re.compile(r"<example>(.*?)</example>", re.S)
COMMENTARY = re.compile(r"<commentary>(.*?)</commentary>", re.S)


class Unit:
    """One cix unit and its translated form."""

    def __init__(self, plugin: str, kind: str, name: str, path: Path):
        self.plugin, self.kind, self.name, self.path = plugin, kind, name, path
        self.notes: list[str] = []
        self.repairs: list[str] = []
        self.deps: list[str] = []
        self.siblings: list[str] = []
        self.fm: dict = {}
        self.body: str = ""
        self.error: str | None = None


def split_frontmatter(text: str) -> tuple[dict, str] | None:
    m = FRONTMATTER.match(text)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return fm, m.group(2)


def parse_examples(desc: str) -> tuple[str, list[dict]]:
    """Split a description into (lead prose, [scenario, ...]).

    A scenario keeps every line cix wrote — context, the user/assistant exchange,
    and the commentary — so relocating it loses nothing.
    """
    lead = desc[: desc.find("<example>")] if "<example>" in desc else desc
    scenarios = []
    for raw in EXAMPLE.findall(desc):
        commentary = ""
        cm = COMMENTARY.search(raw)
        if cm:
            commentary = " ".join(cm.group(1).split())
        stripped = COMMENTARY.sub("", raw)
        fields: dict[str, str] = {}
        order: list[str] = []
        current = None
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(Context|user|assistant)\s*:\s*(.*)$", line)
            if m:
                current = m.group(1)
                if current not in fields:
                    order.append(current)
                fields[current] = m.group(2).strip()
            elif current:
                fields[current] = (fields[current] + " " + line).strip()
        if fields or commentary:
            scenarios.append({"fields": fields, "order": order, "commentary": commentary})
    return lead.strip(), scenarios


def condense(context: str) -> str:
    """A Context line -> a clause usable inside 'Typical triggers include ...'."""
    s = " ".join(context.split()).rstrip(".")
    # Drop a leading article so the clauses read as a list.
    s = re.sub(r"^(The|A|An)\s+", "", s)
    if not s:
        return s
    # Lowercase the lead word only when it is ordinary prose. Acronyms and proper
    # nouns must survive: "A PR adds ..." -> "PR adds ...", never "pR adds ...".
    lead = s.split(" ", 1)[0].rstrip(",;:")
    if len(lead) > 1 and lead[1].isupper():
        return s
    return s[0].lower() + s[1:]


def join_clauses(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def build_description(lead: str, scenarios: list[dict]) -> str:
    """Prose trigger summary + pointer. The lead travels verbatim."""
    text = lead
    # The validator and the guidance both want the "use this agent when" pattern.
    # cix wrote "Use when:" — the smallest faithful edit reaches the documented form.
    if not re.search(r"use this agent when", text, re.I):
        if re.search(r"\bUse when\b", text):
            text = re.sub(r"\bUse when\b", "Use this agent when", text, count=1)
        else:
            text = text.rstrip()
            joiner = " " if text.endswith((".", "!", "?")) else ". "
            text = f"{text}{joiner}Use this agent when the situations below apply."
    clauses = [condense(s["fields"].get("Context", "")) for s in scenarios]
    clauses = [c for c in clauses if c]
    parts = [" ".join(text.split())]
    if clauses:
        # cix's Context lines are full sentences, not noun phrases, so plugin-dev's
        # "Typical triggers include X and Y" template produces ungrammatical text
        # ("include code review shows questionable naming"). A colon-list carries the
        # same information and stays grammatical whatever shape the clause is.
        parts.append("Typical triggers: " + "; ".join(clauses) + ".")
    if scenarios:
        parts.append('See "When to invoke" in the agent body for worked scenarios.')
    return " ".join(parts)


def build_when_to_invoke(scenarios: list[dict]) -> str:
    """The relocated scenarios, as prose bullets. Nothing is discarded."""
    if not scenarios:
        return ""
    lines = ["## When to invoke", ""]
    for s in scenarios:
        f, order = s["fields"], s["order"]
        ctx = " ".join(f.get("Context", "").split()).rstrip(".")
        head = ctx or "Scenario"
        detail = []
        for key in order:
            if key == "Context":
                continue
            val = " ".join(f[key].split())
            detail.append(f'{key.capitalize()}: {val}')
        tail = " ".join(detail)
        bullet = f"- **{head}.**"
        if tail:
            bullet += f" {tail}"
        if s["commentary"]:
            bullet += f" *{s['commentary']}*"
        lines.append(bullet)
    lines.append("")
    return "\n".join(lines)


def translate(unit: Unit) -> None:
    parsed = split_frontmatter(unit.path.read_text())
    if parsed is None:
        unit.error = "unparseable frontmatter"
        return
    fm, body = parsed

    name = fm.get("name") or unit.name
    desc = str(fm.get("description", "") or "")
    lead, scenarios = parse_examples(desc)

    # --- model: required; cix left it off one unit.
    model = fm.get("model")
    if model is None:
        model = "inherit"
        unit.repairs.append("added missing required `model: inherit`")
    elif model not in VALID_MODELS:
        unit.notes.append(f"model {model!r} outside documented set {sorted(VALID_MODELS)}")

    # --- color: repair the six outside the documented palette.
    color = fm.get("color")
    if color in COLOR_REPAIR:
        unit.repairs.append(f"color {color!r} -> {COLOR_REPAIR[color]!r} (outside documented palette)")
        color = COLOR_REPAIR[color]
    elif color is None:
        color = "blue"
        unit.repairs.append("added missing required `color: blue`")
    elif color not in VALID_COLORS:
        unit.notes.append(f"color {color!r} outside documented palette — left as authored")

    # --- tools: keep as authored. Absent means all tools, which is legal and, for
    #     mudge, intentional (its own description calls for full access).
    tools = fm.get("tools")
    if tools is None:
        unit.notes.append("no `tools` field — inherits all tools (as authored)")

    # --- skills: not a documented Claude Code field. It is cix's plugin-relative
    #     dependency pointer. Preserved (dropping it would destroy provenance) AND
    #     recorded in the ledger so manifest relations can be generated later.
    #     cix authored this field inconsistently: a YAML list on some units, a bare
    #     comma-separated string on others ("research, auditing"). Normalise to a list
    #     so the ledger records two dependencies, not one named "research, auditing".
    skills = fm.get("skills")
    if skills:
        if isinstance(skills, str):
            skills = [s.strip() for s in skills.split(",") if s.strip()]
        else:
            skills = [str(s).strip() for s in skills if str(s).strip()]
        unit.deps = skills

    new_desc = build_description(lead, scenarios)
    when = build_when_to_invoke(scenarios)

    out_fm: dict = {"name": name, "description": new_desc, "model": model, "color": color}
    if tools is not None:
        out_fm["tools"] = tools
    if skills:
        out_fm["skills"] = skills

    body = body.lstrip("\n")
    if when:
        # Place "When to invoke" after the opening paragraph so the persona still
        # lands first, which is how these prompts are written to read.
        paras = body.split("\n\n", 1)
        if len(paras) == 2:
            body = f"{paras[0]}\n\n{when}\n{paras[1]}"
        else:
            body = f"{body}\n\n{when}"

    unit.fm, unit.body = out_fm, body


class _Dumper(yaml.SafeDumper):
    """Emit long single-paragraph strings as folded blocks.

    Descriptions run to several hundred characters. Default SafeDumper quotes and
    hard-wraps them, which is valid YAML but unreadable in review. Folded style
    (`>-`) keeps one logical line while wrapping visually.
    """


def _str_representer(dumper: yaml.Dumper, data: str):
    style = ">" if len(data) > 120 and "\n" not in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_representer)


def render(unit: Unit) -> str:
    fm_text = yaml.dump(
        unit.fm,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=96,
    )
    return f"---\n{fm_text}---\n\n{unit.body.strip()}\n"


def translate_skill(unit: Unit) -> None:
    """Skills need far less repair than agents: cix already authored third-person
    descriptions with trigger phrases, and there is no model/color/tools surface.

    Body length is *reported*, never rewritten. plugin-dev targets 1,500-2,000 words
    with detail pushed into references/; several cix skills exceed it. Restructuring
    authored prose is a content decision, not a mechanical translation, so this tool
    surfaces the deviation and leaves the text alone.
    """
    parsed = split_frontmatter(unit.path.read_text())
    if parsed is None:
        unit.error = "unparseable frontmatter"
        return
    fm, body = parsed

    if not fm.get("name"):
        unit.error = "missing required `name`"
        return
    if not fm.get("description"):
        unit.error = "missing required `description`"
        return

    desc = str(fm["description"])
    if not re.search(r"this skill should be used", desc, re.I):
        # cix wrote "Use when the user asks to ..." on two units — second person.
        # The documented form is third person; this is the smallest faithful edit.
        if re.match(r"^\s*Use when\b", desc):
            desc = re.sub(r"^\s*Use when\b", "This skill should be used when", desc, count=1)
            fm = {**fm, "description": desc}
            unit.repairs.append("description 'Use when ...' -> third-person "
                                "'This skill should be used when ...'")
        else:
            unit.notes.append("description is not in the documented third-person form")

    words = len(body.split())
    if words > 3000:
        unit.notes.append(f"body ~{words} words — over plugin-dev's 3,000-word ceiling; "
                          "candidate for references/ extraction (not done here)")
    elif words > 2000:
        unit.notes.append(f"body ~{words} words — over the 1,500-2,000 target, under the ceiling")

    unit.fm, unit.body = fm, body.lstrip("\n")


def known_unit_names() -> set[str]:
    """Every gen-0 unit name, for sibling-reference detection."""
    names = set()
    for plugin in PLUGINS:
        for p in (CIX / plugin / "agents").glob("*.md"):
            names.add(p.stem)
        for p in (CIX / plugin / "skills").glob("*/SKILL.md"):
            names.add(p.parent.name)
    return names


def find_sibling_refs(body: str, self_name: str, names: set[str]) -> list[str]:
    """Backticked references to other gen-0 units.

    Inside a cix plugin these resolved implicitly by co-location. As independent gnx
    components they are cross-component edges, so they get recorded for the manifest
    pass instead of quietly becoming prose that points at nothing.
    """
    found = {
        tok for tok in re.findall(r"`([A-Za-z][A-Za-z0-9-]*)`", body)
        if tok in names and tok != self_name
    }
    return sorted(found)


def collect() -> list[Unit]:
    units: list[Unit] = []
    for plugin in PLUGINS:
        for path in sorted((CIX / plugin / "agents").glob("*.md")):
            if (plugin, path.stem) in ALREADY_PORTED:
                continue
            units.append(Unit(plugin, "agents", path.stem, path))
        for path in sorted((CIX / plugin / "skills").glob("*/SKILL.md")):
            units.append(Unit(plugin, "skills", path.parent.name, path))
    return units


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="report only, write nothing")
    g.add_argument("--apply", action="store_true", help="write payloads + ledger")
    args = ap.parse_args(argv[1:])

    units = collect()
    names = known_unit_names()
    for u in units:
        if u.kind == "skills":
            translate_skill(u)
        else:
            translate(u)
        if not u.error:
            u.siblings = find_sibling_refs(u.body, u.name, names)

    failed = [u for u in units if u.error]
    ok = [u for u in units if not u.error]

    print(f"{len(units)} units collected from cix ({len(failed)} unparseable)\n")
    for u in ok:
        bits = []
        if u.repairs:
            bits += [f"REPAIR {r}" for r in u.repairs]
        if u.deps:
            bits.append(f"deps -> {', '.join(u.deps)}")
        if u.siblings:
            bits.append(f"refs siblings -> {', '.join(u.siblings)}")
        if u.notes:
            bits += [f"note {n}" for n in u.notes]
        flag = "  " if not u.repairs else "* "
        print(f"{flag}{u.plugin}/{u.name}")
        for b in bits:
            print(f"      {b}")
    for u in failed:
        print(f"!! {u.plugin}/{u.name}: {u.error}")

    if args.plan:
        print("\n--plan: nothing written")
        return 1 if failed else 0

    ledger: dict = {"agents": {}, "skills": {}}
    for u in ok:
        dest = GNX / "components" / u.kind / u.name
        dest.mkdir(parents=True, exist_ok=True)
        entry = {
            "source": f"cix/plugins/{u.plugin}/{u.kind}/{u.name}"
                      + (".md" if u.kind == "agents" else "/SKILL.md"),
            "plugin": u.plugin,
            "repairs": u.repairs,
            "notes": u.notes,
            "references_siblings": u.siblings,
        }
        if u.kind == "agents":
            (dest / "agent.md").write_text(render(u))
            entry["skills_dependency"] = u.deps
            ledger["agents"][u.name] = entry
        else:
            (dest / "SKILL.md").write_text(render(u))
            # Bundled resources travel with the skill: references/, examples/,
            # scripts/, assets/. Copied verbatim — they are the payload.
            carried = []
            for child in sorted(u.path.parent.iterdir()):
                if child.name == "SKILL.md":
                    continue
                target = dest / child.name
                if child.is_dir():
                    shutil.copytree(child, target, dirs_exist_ok=True)
                    carried.append(f"{child.name}/")
                else:
                    shutil.copy2(child, target)
                    carried.append(child.name)
            entry["carried"] = carried
            ledger["skills"][u.name] = entry
    (GNX / "harness" / "intake-ledger.yaml").write_text(
        "# Generated by harness/intake_payload.py --apply.\n"
        "# The record bench step 5 asks for, in a form manifests can be generated from\n"
        "# once slick lands. `skills_dependency` is cix's plugin-relative pointer: each\n"
        "# entry names a skill that is now its own gnx component, so these become\n"
        "# manifest relations rather than bundled copies (INTAKE.md bench finding).\n"
        + yaml.safe_dump(ledger, sort_keys=True, allow_unicode=True, width=100)
    )
    print(f"\nwrote {len(ok)} payloads + harness/intake-ledger.yaml")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
