# Rubric extension — register profiles for the multi-audience site

Merge this into `docs/RUBRIC.md` in Phase 0. The existing rubric (criteria A–G, the
verdict grammar) was written for one register: internal design docs. The polished site
has four registers with different bars. This adds three criteria (H, I, J) and a
**register-profile table** that says which criteria are hard gates for which register.

Keep the living-instrument header: when a doc surfaces a failure mode the rubric doesn't
catch, add the criterion. (Criterion G was born that way from "grounded in Frames.")

---

## New criteria

### H. Maturity Honesty  — HARD GATE on User Guide and Dev Guide

**Checks.** Every capability claim carries a maturity marker — **shipped / planned /
proposed** — and nothing aspirational is stated as if it works today. `gnx init` does
not exist; a sentence that says "run `gnx init` and it scaffolds your project" without a
*planned* marker is a violation. Maps to the design register's established/decided/open.

| 5 | Every claim marked; shipped vs planned vs proposed unambiguous; no aspirational-as-real |
| 3 | Mostly marked; one or two claims read as real that are actually planned |
| 1 | The guide reads as documentation of a working tool that does not exist |

### I. Task Orientation  — HARD GATE on User Guide

**Checks.** Every User-Guide page answers a concrete "how do I X." Examples are
copy-pasteable and real (commands, file contents), not "imagine a component." The path
is progressive: orient → first win → compose → extend. No page that only explains
without enabling an action.

| 5 | Every page is a task; examples runnable; progressive arc intact |
| 3 | Mostly task-shaped; one page explains without enabling; one example is illustrative not runnable |
| 1 | Reference dumped as prose; no task a reader can complete |

### J. Grammar Accuracy  — HARD GATE on Dev Guide and Reference

**Checks.** Every claim about the component model matches the **shipped slick source**
(`~/mox/packages/slick/src/manifest.rs` = 5 fields: type_url, source, requires, provides,
relations; `registry.rs` = TypedStruct + TypedRegistry; no kind/apiVersion/protocols in
shipped code — those are proto-draft doctrine). Dev-Guide manifest examples must be valid
against the real schema. Doctrine (4 kinds, apiVersion, Protocol, api/ schemas) is marked
as proto-target, not shipped. Verify against source, do not trust memory.

| 5 | Every grammar claim matches slick source; shipped vs doctrine marked; examples valid |
| 3 | Mostly accurate; one field or example drifts from the shipped schema |
| 1 | Invented fields, stale 8-kind list, or doctrine presented as shipped |

---

## Register profiles — which criteria are hard gates where

A doc declares its register (a `register:` key in its frontmatter, or by its nav section).
The gemini eval applies the profile for that register. `~` = applies, advisory.

| Criterion | User Guide | Dev Guide | Design | The Build | Reference |
|---|---|---|---|---|---|
| A Voice | gate | gate | gate | gate | ~ |
| B No LLM tells | ~ | ~ | ~ | ~ | ~ |
| C No register-announcing | gate | gate | gate | gate | ~ |
| D Factual fidelity | gate | gate | gate | gate | gate |
| E Propagation | gate | ~ | ~ | ~ | ~ |
| F Tightness | ~ | ~ | ~ | ~ | ~ |
| G Legibility (no internal jargon in value claims) | **gate** | ~ (mechanism is the subject) | gate on abstracts | ~ | ~ |
| H Maturity honesty | **gate** | **gate** | gate | ~ | gate |
| I Task orientation | **gate** | ~ | n/a | n/a | n/a |
| J Grammar accuracy | ~ | **gate** | ~ | n/a | **gate** |

Verdict grammar is unchanged (SHIP / TIGHTEN / RETURN), evaluated against the *applicable*
hard gates for the doc's register. The gemini eval prompt must be told the doc's register
so it loads the right profile.
