<!-- Produced by the `skill-authoring-research` workflow, run wf_fb31fa78-2fc, 2026-08-17.
     9 agents, 4 sweep lanes each independently re-verified (re-fetch + quote check) before
     synthesis; 73 claims survived. The synthesis agent was blocked from writing this file by
     harness policy (subagents may not write report .md files) and returned it inline; the
     main loop wrote it here. Raw per-agent results: the workflow's journal.jsonl. -->

# Authoring Claude Skills — synthesis for catalog maintainers

**Audience:** engineers maintaining ~35 Claude Skills as a catalog.
**Compiled:** 2026-08-17. **Retrieval date for all web sources:** 2026-08-17 unless stated.

**Source classes used, never blended:**

| Class | Meaning |
|---|---|
| `OFFICIAL` | Anthropic-operated domain (platform.claude.com, code.claude.com, claude.com/blog, anthropic.com) |
| `OFFICIAL-REPO` | github.com/anthropics/skills — Anthropic-owned code, measured directly |
| `OFFICIAL-STANDARD` | agentskills.io / github.com/agentskills — Anthropic-*originated*, vendor-neutral. **Not an Anthropic publication.** |
| `ACADEMIC-PREPRINT` | arXiv, not peer reviewed |
| `PRACTITIONER` | named individual or vendor engineering blog |
| `COMMUNITY` | issue trackers, gists |

**Standing caveat on dates.** Every Anthropic *documentation* page in this synthesis carries **DATE UNKNOWN** — no dateline, no `datePublished`/`dateModified` in markup, no `Last-Modified` header. Only the blogs, the changelog, agentskills.io (JSON-LD) and git commits are datable. Anything pre-2026 is labelled **HISTORICAL**.

**Host migration (read this before fixing any internal link).** `docs.claude.com/en/docs/agents-and-tools/agent-skills/*` now redirects (302) to `platform.claude.com/docs/en/agents-and-tools/agent-skills/*`; `/en/docs/claude-code/skills` returns 301 to `code.claude.com/docs/en/skills`; `/en/api/skills-guide` returns 301 to platform.claude.com. `docs.anthropic.com` emits a 301 to a malformed doubled path but still resolves 200. Reproduced by direct HTTP 2026-08-17. *This is a tool observation, not published text.*

---

## 1. What is settled

Claims where official sources agree, or where a single official source states a rule no other official source contradicts.

### 1.1 The frontmatter contract

**S1 — Two required fields, hard validation limits.** `name`: max 64 chars, lowercase letters/numbers/hyphens only, no XML tags, must not contain the reserved words `anthropic` or `claude`. `description`: non-empty, max 1,024 chars, no XML tags.
`OFFICIAL` — https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices — DATE UNKNOWN. Corroborated on the `overview` page and the API skills-guide.
Scope correction: "requires exactly two fields" is true of the **platform/API surface**. Claude Code does not repeat the reserved-word rule and treats `name` as optional (see D7).

**S2 — The allowed key set is closed, and enforced in code.** Anthropic's own validator hard-fails on any unrecognized top-level key:
```python
ALLOWED_PROPERTIES = {'name', 'description', 'license', 'allowed-tools', 'metadata', 'compatibility'}
```
It also rejects angle brackets anywhere in the description: `if '<' in description or '>' in description`.
`OFFICIAL-REPO` — https://github.com/anthropics/skills/blob/main/skills/skill-creator/scripts/quick_validate.py — file last modified **2026-02-06** (commit `1ed29a03`). Only two commits have ever touched this file.
Neither the closed-set statement nor the angle-bracket rule appears anywhere on agentskills.io/specification (verified by grep of live page HTML). The validator is ~6 months behind the spec page and is **not guaranteed to track it**.

**S3 — The spec's six fields are the normative list.** `name` (req), `description` (req), `license`, `compatibility` (max 500 chars), `metadata` (string→string map), `allowed-tools` (space-separated, **experimental**).
`OFFICIAL-STANDARD` — https://agentskills.io/specification — **dateModified 2026-08-04T22:53:14.470Z** (embedded JSON-LD; this page is *not* undated). Spec source `docs/specification.mdx` last committed 2026-08-04T22:52:17Z.
The spec adds **four** fields beyond the two the Anthropic docs enumerate.

**S4 — The spec's `name` rules are stricter than the Anthropic docs page.** Must be 1–64 chars, lowercase alphanumeric + hyphens, **must not start or end with a hyphen**, **must not contain consecutive hyphens (`--`)**, **must match the parent directory name**.
`OFFICIAL-STANDARD` — https://agentskills.io/specification — 2026-08-04.
Confirmed negatively: grepping the platform best-practices page for "directory name", "parent directory", "consecutive hyphen" returns zero hits. The docs page would pass `-pdf` and `pdf--processing`.
Linter trap: the spec's own **summary table states only** "must not start or end with a hyphen". The consecutive-hyphen and directory-match rules live in the prose section below the table. A linter author reading the table alone misses two of five rules.

**S5 — `metadata` is the only escape hatch.** The spec frames it as the place for "additional properties not defined by the Agent Skills spec". Put `version:`, `author:`, `tags:`, `updated:` there — never at top level (S2 fails them).
`OFFICIAL-STANDARD` — https://agentskills.io/specification — 2026-08-04.

**S6 — Portability is a hard error, not a graceful ignore.** "If you include any field the spec doesn't allow, packaging or upload fails with a hard error instead of ignoring the field." Affected paths: claude.ai skill uploads, the Skills API, and `package_skill.py` from anthropics/skills. Observed error text: `Unexpected key(s) in SKILL.md frontmatter: argument-hint. Allowed properties are: allowed-tools, compatibility, description, license, metadata, name`.
`OFFICIAL` — https://code.claude.com/docs/en/skills — DATE UNKNOWN (documents behavior up to Claude Code v2.1.220).
**Trap the docs bury:** enabling a personal skill for Cowork/cloud sessions *uploads it to claude.ai*, "so the same rules apply" — the six-field restriction bites skills you never consciously exported.

### 1.2 Progressive disclosure and size

**S7 — Three tiers, three budgets.** Level 1 metadata: always loaded at startup, **~100 tokens per skill** (`name` + `description`). Level 2 instructions: on trigger, **under 5k tokens** (SKILL.md body). Level 3+ resources: **none until accessed** — reference files load when read; "Scripts run through bash, and only their output enters context."
`OFFICIAL` — https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview — DATE UNKNOWN. Independently corroborated `OFFICIAL-STANDARD` at agentskills.io/specification (2026-08-04), which frames 5k as "recommended" where Anthropic states it flatly.
Design consequence stated in the source: prefer bundling a script the agent **executes** over prose it must **read**.

**S8 — The 500-line SKILL.md figure appears on all three official surfaces — but the sentences differ and none of them is enforcement.**
- best-practices: "Keep SKILL.md body under 500 lines for optimal performance" + "Split content into separate files when approaching this limit"
- agentskills.io/specification (2026-08-04): "Keep your main `SKILL.md` under 500 lines. Move detailed reference material to separate files."
- code.claude.com/docs/en/skills: "Keep `SKILL.md` under 500 lines. Move detailed reference material to separate files."

`OFFICIAL` + `OFFICIAL-STANDARD` — DATE UNKNOWN on the two Anthropic pages.
**No surface reports a hard error at 500 lines.** All three phrase it as guidance. A CI gate is your policy choice, not a documented limit. The only hard numeric limits stated anywhere are the frontmatter validation ones in S1.
Correction to an earlier framing: "the single most-repeated authoring limit" is unsourced editorializing — the 1,024-char description cap appears on just as many surfaces.

**S9 — References must be one level deep, and the mechanism is stated.** "Claude may partially read files when they're referenced from other referenced files. When encountering nested references, Claude might use commands like `head -100` to preview content rather than reading entire files, resulting in incomplete information. **Keep references one level deep from SKILL.md**."
`OFFICIAL` — platform best-practices — DATE UNKNOWN. Bad example is the docs' own: `SKILL.md → advanced.md → details.md`. Recurs as shipping-checklist item "File references are one level deep".
The spec states the rule without the `head -100` mechanism, so the mechanism is attributable to Anthropic only.

**S10 — A second, separate line limit: reference files over 100 lines need a table of contents.** "For reference files longer than 100 lines, include a table of contents at the top. This ensures Claude can see the full scope of available information even when previewing with partial reads."
`OFFICIAL` — platform best-practices — DATE UNKNOWN. **Found on the Anthropic page only** — absent from the spec and from the Claude Code page. The source says "include" (guidance), not "must".

**S11 — Every body line is a recurring cost.** "Keep the body itself concise. Once a skill loads, its content stays in context across turns, so every line is a recurring token cost. State what to do rather than narrating how or why."
`OFFICIAL` — https://code.claude.com/docs/en/skills — DATE UNKNOWN.

### 1.3 Claude Code lifecycle and precedence

**S12 — Highest-value lifecycle fact: content persists for the session and is never re-read.** "the rendered `SKILL.md` content enters the conversation as a single message and stays there for the rest of the session… Claude Code does not re-read the skill file on later turns, so write guidance that should apply throughout a task as **standing instructions rather than one-time steps**."
`OFFICIAL` — code.claude.com/docs/en/skills — DATE UNKNOWN.
This invalidates the common step-1/step-2/step-3 body template for anything spanning multiple turns.

**S13 — Permissions do *not* persist.** An `allowed-tools` grant "clears when you send your next message"; `disallowed-tools` behaves identically ("The restriction clears when you send your next message"). A skill cannot durably fence a tool off either.
`OFFICIAL` — code.claude.com/docs/en/skills — DATE UNKNOWN.

**S14 — `allowed-tools` is not gated by workspace trust.** "Claude Code applies a project skill's `allowed-tools` whenever you or Claude invoke the skill, including in a `-p` run in a folder you've never trusted. A skill can grant itself broad tool access, so review the `allowed-tools` of skills checked into a repository before you run Claude Code there."
`OFFICIAL` — code.claude.com/docs/en/skills — DATE UNKNOWN. Paired with the platform overview's warning that untrusted skills "could lead to data exfiltration, unauthorized system access, or other security risks."
Pair with S12/S13: the grant expires at the next message, but the *instructions* persist all session — the durable risk is the body, not the permission.

**S15 — Precedence is counterintuitive: personal overrides project.** "Across levels, enterprise overrides personal, and personal overrides project… with a `deploy` skill in both `~/.claude/skills/` and your project's `.claude/skills/`, `/deploy` runs the personal one."
Three additions from the same page: (a) **plugin skills are exempt** — they carry a `plugin-name:skill-name` namespace, so plugin packaging is the real fix for shadowing; (b) a local skill overrides a bundled skill's name but **not its aliases** (project `code-review` takes `/code-review`, but bundled `/review` still runs Anthropic's); (c) name matching is aggressive — Claude Code "ignores case, spacing, and invisible characters, and treats compatibility forms such as fullwidth letters and dash variants as their plain equivalents", so near-miss names collide.
**The folder name `synced` is reserved** in enterprise, personal, and project locations, "in any capitalization", and a skill authored at that name is skipped.
`OFFICIAL` — code.claude.com/docs/en/skills — DATE UNKNOWN.

**S16 — The listing display cap is 1,536 chars, combined across two fields, and configurable.** "the combined `description` and `when_to_use` text is truncated at 1,536 characters in the skill listing to reduce context usage"; `when_to_use` "counts toward the 1,536-character cap". "The cap is configurable with `skillListingMaxDescChars`." A separate collection-level budget is controlled by `skillListingBudgetFraction` / `SLASH_COMMAND_TOOL_CHAR_BUDGET`.
`OFFICIAL` — code.claude.com/docs/en/skills — DATE UNKNOWN.
Changelog origin: "raised the listing cap from 250 to 1,536 characters and added a startup warning when descriptions are truncated" — `OFFICIAL` https://code.claude.com/docs/en/changelog, **v2.1.105, April 13, 2026** (located inside the dated `<Update label="2.1.105">` block).
**1,024 is your authoring ceiling (S1 validation); 1,536 is only a display cap.** Do not author to 1,536.

**S17 — Claude Code extends frontmatter to 20 fields, all optional.** `name, description, when_to_use, argument-hint, arguments, disable-model-invocation, user-invocable, allowed-tools, disallowed-tools, model, effort, context, agent, background, hooks, paths, shell, metadata, license, compatibility` — 14 beyond the spec's six. "All fields are optional. Only `description` is recommended."
`OFFICIAL` — code.claude.com/docs/en/skills — DATE UNKNOWN.

**S18 — `context: fork` is wrong for reference-style skills.** "`context: fork` only makes sense for skills with explicit instructions. If your skill contains guidelines like 'use these API conventions' without a task, the subagent receives the guidelines but no actionable prompt, and returns without meaningful output." Mechanism: "The skill content becomes the prompt that drives the subagent. It won't have access to your conversation history."
`OFFICIAL` — code.claude.com/docs/en/skills — DATE UNKNOWN (stated in a `<Warning>` callout).

**S19 — Forked skills background by default since v2.1.218.** "Changed skills with `context: fork` to run in the background by default; opt out per skill with `background: false`."
`OFFICIAL` — code.claude.com/docs/en/changelog, **v2.1.218, July 22, 2026** (version→date mapping confirmed inside the dated block).
Corroborated in the skills docs: a backgrounded fork "runs with the narrower tool set that applies to background subagents… If your skill's steps depend on a tool outside that set, set `background: false`". And: "A forked skill that runs in the background applies its edits outside your session's checkpoints, so `/rewind` doesn't undo them; use git to revert them." Four cases where Claude Code waits anyway: non-interactive/`-p`, `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, concurrent invocation of the same skill, scheduled tasks.

### 1.4 The description is a trigger specification

**S20 — Descriptions are written for the model's selection pass, not for humans.** "When Claude Code starts a session, it builds a listing of every available skill with its description. This listing is what Claude scans to decide 'is there a skill for this request?' Which means the description field is not a summary, it's a description of when to trigger this skill. It's helpful to include triggers for the skill, like 'babysit,' in its description."
`OFFICIAL` — https://claude.com/blog/lessons-from-building-claude-code-how-we-use-skills — **June 3, 2026**.
*Note:* this article says nothing about third person. A search of the Claude Code skills reference for "third person" also returns zero hits.

**S21 — The observed failure mode is undertriggering, so be deliberately pushy.** "**description**: When to trigger, what it does. This is the primary triggering mechanism — include both what the skill does AND specific contexts for when to use it. All 'when to use' info goes here, not in the body. Note: currently Claude has a tendency to 'undertrigger' skills — to not use them when they'd be useful. To combat this, please make the skill descriptions a little bit 'pushy'."
`OFFICIAL-REPO` — https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md (line 67) — file last modified **2026-03-06** (commit `b0cbd3df`). ~5.5 months old.
The source's own worked rewrite: from "How to build a simple fast dashboard to display internal Anthropic data." to "…Make sure to use this skill whenever the user mentions dashboards, data visualization, internal metrics, or wants to display any kind of company data, even if they don't explicitly ask for a 'dashboard.'"
Corroborated `OFFICIAL-STANDARD` at agentskills.io/skill-creation/optimizing-descriptions: "**Err on the side of being pushy.** Explicitly list contexts where the skill applies, including cases where the user doesn't name the domain directly."

**S22 — There is a failure mode no description can fix.** "agents typically only consult skills for tasks that require knowledge or capabilities beyond what they can handle alone. A simple, one-step request like 'read this PDF' may not trigger a PDF skill even if the description matches perfectly, because the agent can handle it with basic tools."
`OFFICIAL-STANDARD` — https://agentskills.io/skill-creation/optimizing-descriptions — DATE UNKNOWN.
Do not tune prose against trivially-handleable requests. Target specialized-knowledge tasks.

**S23 — Description precision is a two-sided error problem that worsens with catalog size.** "As your skill count grows, description precision becomes critical: too broad and you get false triggers, too narrow and it never fires. Skill-creator now helps you tune descriptions… We ran it across our document-creation skills and saw improved triggering on 5 out of 6 public skills."
`OFFICIAL` — https://claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills — **March 3, 2026**.

### 1.5 Evaluation

**S24 — Evals come before documentation. Five steps.** "**Create evaluations BEFORE writing extensive documentation.** This ensures your Skill solves real problems rather than documenting imagined ones." 1. Identify gaps by running Claude without the skill; 2. Build three scenarios testing those gaps; 3. Establish baseline without the skill; 4. Write minimal instructions; 5. Iterate.
`OFFICIAL` — platform best-practices — DATE UNKNOWN. The source says "Build three scenarios", **not** "a minimum of three".
Same page: "There is not currently a built-in way to run these evaluations. Users can create their own evaluation system. Evaluations are your source of truth for measuring Skill effectiveness." (See D8 — this is in tension with shipped tooling.)
HISTORICAL ancestor, same principle: "Start with evaluation: Identify specific gaps in your agents' capabilities by running them on representative tasks and observing where they struggle… Then build skills incrementally to address these shortcomings." — `OFFICIAL` https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills, **Published Oct 16, 2025 (HISTORICAL)**.

**S25 — A quantified trigger-eval protocol exists.** ~20 queries (8–10 should-trigger, 8–10 should-not); run each **3 times**; compute trigger rate; a should-trigger query passes above **0.5**; split **~60% train / ~40% validation**; select the best iteration by validation pass rate. "With 20 queries at 3 runs each, that's 60 invocations." Cap: "Five iterations is usually enough", and the best description may not be the last one produced.
`OFFICIAL-STANDARD` — https://agentskills.io/skill-creation/optimizing-descriptions — DATE UNKNOWN.
Two supporting rules: reject easy negatives ("'Write a fibonacci function' — obviously irrelevant, tests nothing"); and never keyword-stuff — "Avoid adding specific keywords from failed queries — that's overfitting. Instead, find the general category or concept those queries represent."
**Every figure is hedged in the source** ("about 20", "3 is a reasonable starting point", "0.5 is a reasonable default"). Treat as calibrated starting points, not requirements.

**S26 — Skills have expiry conditions, detectable by eval.** "Capability uplift skills **may** become less necessary as models improve. Evals tell you when that's happened. Encoded preference skills are more durable, but only as valuable as their fidelity to your actual workflow… If the base model starts passing your evals without the skill loaded, that's a signal the skill's techniques may have been incorporated into the model's default behavior. The skill isn't broken; it's just no longer necessary."
`OFFICIAL` — https://claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills — **March 3, 2026**.
Correction to an earlier framing: the source hedges with "may" twice, and says evals *are the stated way* to detect it — not that they are the *only* way.

### 1.6 Content rules

**S27 — Don't state the obvious.** "Don't state the obvious — Claude already knows how to code and can read your codebase. A skill that restates what Claude would do by default adds context without adding value. If you're publishing a skill that is primarily about knowledge, focus on information that pushes Claude out of its normal way of thinking."
`OFFICIAL` — claude.com/blog/lessons-from-building-claude-code-how-we-use-skills — **June 3, 2026**. Context: "hundreds of them in active use" at Anthropic.
Correction: this is the **first of ~10 tips**, not "the primary anti-pattern" — the article never ranks anti-patterns.

**S28 — Gotchas are the highest-signal content, and are grown, not written.** "The highest-signal content in any skill is the Gotchas section. These sections should be built up from common failure points that Claude runs into when using your skill. Ideally, you will update your skill over time to capture these gotchas." Example given is a concrete state trap: "The subscriptions table is append-only. The row you want is the one with the highest version, not the most recent created_at." Closing line: "Most of our best skills began as a few lines and a single gotcha."
`OFFICIAL` — same article — June 3, 2026. This is the only superlative the article awards.

**S29 — Avoid railroading.** "Avoid railroading Claude — Claude will generally try to stick to your instructions, and because skills are so reusable you'll want to be careful of being too specific in your instructions. Give Claude the information it needs, but give it the flexibility to adapt to the situation."
`OFFICIAL` — same article — June 3, 2026 (section heading is literally "Avoid railroading Claude").

**S30 — Exhaustive coverage is harmful, not merely wasteful.** "Overly comprehensive skills can hurt more than they help — the agent struggles to extract what's relevant and may pursue unproductive paths triggered by instructions that don't apply to the current task. Concise, stepwise guidance with a working example tends to outperform exhaustive documentation. When you find yourself covering every edge case, consider whether most are better handled by the agent's judgment."
`OFFICIAL-STANDARD` — https://agentskills.io/skill-creation/best-practices — **dateModified 2026-04-22** (JSON-LD). Section heading: "Aim for moderate detail". Same page's content test: "Would the agent get this wrong without this instruction?"

**S31 — Four named anti-patterns, each verified individually.**
- *Windows paths:* "Always use forward slashes in file paths, even on Windows" / "Avoid: `scripts\helper.py`". Restated: "Claude navigates your skill directory like a filesystem. Use forward slashes (`reference/guide.md`), not backslashes."
- *Time-sensitive info:* section "Avoid time-sensitive information", with an `## Old patterns` example wrapped in `<details>`; checklist item "No time-sensitive information (or in 'old patterns' section)".
- *Option menus:* "Don't present multiple approaches unless necessary" — bad example "You can use pypdf, or pdfplumber, or PyMuPDF…" against "Provide a default (with escape hatch)". (Source says "unless necessary" — softer than a flat ban.)
- *MCP tool names:* "**Format:** `ServerName:tool_name`" — "Without the server prefix, Claude may fail to locate the tool, especially when multiple MCP servers are available." Scoped to the "Advanced: Skills with executable code" section.

`OFFICIAL` — platform best-practices — DATE UNKNOWN.

**S32 — The context window is framed as a shared resource, with three challenge questions.** "The context window is a public good. Your Skill shares the context window with everything else Claude needs to know, including: the system prompt, conversation history, other Skills' metadata, your actual request." Challenge each paragraph with: "Does Claude really need this explanation?", "Can I assume Claude knows this?", "Does this paragraph justify its token cost?"
`OFFICIAL` — platform best-practices — DATE UNKNOWN. *(Re-check note: "context window" is a hyperlink in the source, so the raw text reads `The [context window](…) is a public good` — a substring search on rendered text fails.)*

**S33 — Naming: gerund-first, advisory.** "Consider using **gerund form** (verb + -ing) for Skill names" — `processing-pdfs`, `analyzing-spreadsheets`, `managing-databases`, `testing-code`, `writing-documentation`. Acceptable alternatives: noun phrases (`pdf-processing`), action-oriented (`process-pdfs`). Avoid: vague (`helper`, `utils`, `tools`), overly generic (`documents`, `data`, `files`), reserved words (`anthropic-helper`, `claude-tools`), "Inconsistent patterns within your skill collection".
`OFFICIAL` — platform best-practices — DATE UNKNOWN.
Two honest limits: the page has three tiers (Good / Acceptable / Avoid) but does **not rank** the two alternatives, so "ranked list" overstates it; and gerund-first is advisory ("Consider using") while Anthropic's own canonical examples elsewhere use noun phrases (`pdf-processing`). **Only the reserved-word line is machine-enforced (S1).**

### 1.7 Boundaries with adjacent mechanisms

**S34 — Skills vs subagents.** "If multiple agents or conversations need the same expertise—like security review procedures or data analysis methods—create a Skill rather than building that knowledge into individual subagents. Skills are portable and reusable, while subagents are purpose-built for specific workflows. Use Skills to teach expertise that any agent can apply; use subagents when you need independent task execution with specific tool permissions and context isolation."
`OFFICIAL` — https://claude.com/blog/skills-explained — **March 5, 2026**.

**S35 — Skills vs MCP: connectivity vs procedure, used together.** "MCP connects Claude to data; Skills teach Claude what to do with that data… Use both together: MCP for connectivity, Skills for procedural knowledge."
`OFFICIAL` — claude.com/blog/skills-explained — March 5, 2026.
Placement rule, from a different URL: "The rule of thumb: MCP instructions cover how to use the server and its tools correctly. Skill instructions cover how to use them for a given process or in a multiserver workflow." — `OFFICIAL` https://claude.com/blog/extending-claude-capabilities-with-skills-mcp-servers, **December 19, 2025 (HISTORICAL)**. Worked example: a Salesforce MCP server specifies query syntax and API formats; the skill specifies which records to check first and how to cross-reference them.
Named failure mode, same source: "If your MCP server says to return JSON and your skill says to format as markdown tables, Claude has to guess which one is right. Let MCP handle connectivity, and let skills handle presentation, sequencing, and workflow logic."

**S36 — Promotion threshold for prompt → skill.** "If you find yourself typing the same prompt repeatedly across multiple conversations, it's time to create a Skill."
`OFFICIAL` — claude.com/blog/skills-explained — March 5, 2026. No number is given; any "second or third time" rule is your operationalization, not Anthropic's.

**S37 — Move workflow instructions out of CLAUDE.md into skills.** "Keep CLAUDE.md to specific instructions and move workflow-specific ones into skills, which only get loaded when they're used." Preceding sentence: "run `/context` in a fresh session to see what's in there before you've typed anything."
`OFFICIAL` — https://claude.com/blog/maximizing-the-value-of-your-claude-code-sessions — **August 14, 2026** (article Date field). *Most recent official source in this synthesis.*

**S38 — No native dependency mechanism between skills.** "This sort of dependency management is not natively built into marketplaces or skills yet, but you can just reference other skills by name, and the model will invoke them if they are installed."
`OFFICIAL` — claude.com/blog/lessons-from-building-claude-code-how-we-use-skills — June 3, 2026. Note the "yet" — current-state limitation, not design stance.

**S39 — Composition for skills you don't own is a wrapper, never an edit.** "Embedded only works on skills you can edit… Built-in skills and plugin-managed skills (the kind that get overwritten on update) are off-limits for this pattern; for those, chain instead… Chaining is also how you add verification to a skill you can't modify: build a custom wrapper skill that invokes the original, then invokes your verification skill." The article's example body is two lines: "Run /simplify on the current diff first. When /simplify finishes, invoke /verify-no-public-api-changes."
`OFFICIAL` — https://claude.com/blog/building-verification-loops-in-claude-code-with-skills — **July 22, 2026** (authored by Delba de Oliveira, Claude Code team).

**S40 — Four deliberate placements, with an explicit don't-chain condition.** Section headings: Standalone / Embedded / Chained / On every PR, under "Match the check to where it runs". "You can skip chaining when the steps are independent enough that you sometimes want to run one without the others; chaining trades flexibility for automation. Chained verification loops can increase token spend, so it's best to test these loops before deploying them broadly." Also: "The signal that you've outgrown standalone is when you're running it after every change", and "Hold off on PR-wide gates while the chain is still in flux."
`OFFICIAL` — same article — July 22, 2026.

**S41 — Nine categories; good skills occupy exactly one.** "After cataloging all of our internal skills at Anthropic, we noticed they cluster into nine categories. The best skills fit cleanly into one; the ones that try to do too much straddle several and confuse the agent. This isn't a definitive list, but it is a useful framework for identifying gaps in your own skills library."
1. Library and API reference · 2. Product verification · 3. Data fetching and analysis · 4. Business process and team automation · 5. Code scaffolding and templates · 6. Code quality and review · 7. CI/CD and deployment · 8. Runbooks · 9. Infrastructure operations.
`OFFICIAL` — claude.com/blog/lessons-from-building-claude-code-how-we-use-skills — June 3, 2026. Category 2 is flagged highest-leverage: "Verification skills have had the most measurable impact on Claude's output quality internally." Self-limited as "not a definitive list" — a gap-finding framework, not a schema.

**S42 — Granularity: scope a skill like a function.** "Deciding what a skill should cover is like deciding what a function should do: you want it to encapsulate a coherent unit of work that composes well with other skills. Skills scoped too narrowly force multiple skills to load for a single task, risking overhead and conflicting instructions. Skills scoped too broadly become hard to activate precisely. A skill for querying a database and formatting the results may be one coherent unit, while a skill that also covers database administration is probably trying to do too much."
`OFFICIAL-STANDARD` — https://agentskills.io/skill-creation/best-practices — dateModified **2026-04-22**.

**S43 — Claude A / Claude B, no meta-skill required.** "Work with one instance of Claude ('Claude A') to create a Skill that is used by other instances ('Claude B'). Claude A helps you design and refine instructions, while Claude B tests them in real tasks." And: "Claude models understand the Skill format and structure natively. You don't need special system prompts or a 'writing skills' skill to get Claude to help create Skills."
`OFFICIAL` — platform best-practices — DATE UNKNOWN.
**Narrow reading only.** The source says such scaffolding isn't needed *for format correctness*. Anthropic itself ships `skill-creator`, which does eval splitting, trigger-rate measurement and description optimization. The correct inference: don't build scaffolding whose only job is teaching Claude the SKILL.md shape.

**S44 — API-side hard limits.** "Total upload size must be under 30 MB (uncompressed)" (stated twice; also "**Maximum Skill upload size:** 30 MB (all files combined, uncompressed)"). "You can include up to 8 Skills for each request." Beta headers: `code-execution-2025-08-25` ("required for Skills"), `skills-2025-10-02` ("Enables Skills API"), `files-api-2025-04-14` ("Required only when you use the Files API"). Every code sample sends the first two together.
`OFFICIAL` — https://platform.claude.com/docs/en/build-with-claude/skills-guide — DATE UNKNOWN; the beta identifiers themselves are dated 2025-10-02, 2025-08-25, 2025-04-14.

### 1.8 Origin (HISTORICAL)

**S45 — Progressive disclosure is the stated core design principle.** "Progressive disclosure is the core design principle that makes Agent Skills flexible and scalable. Like a well-organized manual that starts with a table of contents, then specific chapters, and finally a detailed appendix, skills let Claude load information only as needed." And: "the amount of context that can be bundled into a skill is effectively unbounded."
`OFFICIAL` — https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills — **Published Oct 16, 2025. HISTORICAL, ~10 months old.**
Cite for *rationale only*, never for current field lists or limits — nothing about the six-field surface appears here.

**S46 — Standardization date.** The same post carries a dated update: "Update: We've published Agent Skills as an open standard for cross-platform portability. (December 18, 2025)". Independently consistent with `github.com/agentskills/agentskills` created 2025-12-16T15:47:19Z.
`OFFICIAL` — anthropic.com — **December 18, 2025. HISTORICAL.**
**Provenance discipline:** agentskills.io says the format "was originally developed by Anthropic, released as an open standard, and has been adopted by a growing number of agent products. The standard is open to contributions from the broader ecosystem." That supports "Anthropic-originated" and "open to contributions" — it does **not** support "ecosystem-governed": no governance body, steering group, or transfer of stewardship is named.

**S47 — Adoption roster lives at /clients, not /specification.** https://agentskills.io/clients lists **46 client entries, two of which are Anthropic's own** (Claude, Claude Code) — so **44 third-party**, not "~45+ other". Confirmed present: Gemini CLI, OpenCode, Cursor, Goose, GitHub Copilot, VS Code, ChatGPT & Codex, Junie, Amp, Kiro, Qodo, Roo Code, Mistral AI Vibe, Tabnine, Spring AI, Databricks Genie Code, Snowflake Cortex Code, Pulumi Neo, and others.
`OFFICIAL-STANDARD` — DATE UNKNOWN.

---

## 2. What practitioners add

Field-tested detail the docs omit. Attributed by name. Every measurement here is narrower in scope than the official guidance it supplements — the scope limits are stated, not buried.

### 2.1 The listing budget — the finding that matters most at ~35 skills

**P1 — Alexey Pelykh: skill metadata competes for a hard cumulative budget, and past ~42 skills descriptions are silently dropped.**
`PRACTITIONER` — https://gist.github.com/alexey-pelykh/faa3c304f731d6a962efc5fa2a43abe1 — gist created **2025-12-04**, updated **2026-06-28**; research conducted Dec 4–5, 2025 (dates confirmed against the GitHub gists API).

> **21 skills (33%) were completely hidden** from the agent—it couldn't discover or invoke them.

Measured figures, all verbatim from the raw gist: empirical budget **~15,500–16,000 characters**; per-skill overhead **~109** characters (`Total per skill = description_length + 109`); at the observed average of **263 chars** per description, capacity is **~42 skills**. Recommendation: "**Target**: ≤130 characters per description for collections of 60+ skills."

**The sharpest point, and the one that changes behavior:** "Hidden skills had nearly identical average description length (262 vs 264 chars). This proves truncation is based on **cumulative total**, not individual description length." Trimming one verbose description does not rescue a skill — total collection size is what matters.

Self-declared limits, verbatim: "Based on single observation (42/63 split)", "No source code access for verification", "Is the budget configurable? No evidence found". Measured on **Claude Opus 4.5, December 2025**. The "not configurable" finding is now definitively overtaken by P2.

**P2 — The budget is now surfaced and configurable, and the drop policy favours most-used skills.**
`COMMUNITY` — https://github.com/anthropics/claude-code/issues/56966 — created **2026-05-07**, closed 2026-05-11. Verified via `gh api` (WebFetch 404s on this issue).

> Skill listing will be truncated
>       49 descriptions dropped (full descriptions kept for most-used skills) (2.3%/1% of context):
>     impeccable:impeccable, imagegen-frontend-web, imagegen-frontend-mobile, +46 more
>         run /skills to disable some, or raise skillListingBudgetFraction (currently 1%) in settings.json

Reporter: `weebHarsh`, Claude Code v2.1.132, win32. The issue body continues: "Opting in would cost ~5k tokens for skills every session and uses rate limits faster" — the real tradeoff of raising the fraction.

**Authority caveat:** closed as a **duplicate by `github-actions[bot]` and locked. No Anthropic maintainer confirmed anything.** The only authority is the pasted CLI string. Corroborated by four sibling reports of the same truncation (anthropics/claude-code issues 56710, 57599, 60560, 64606).

**The cold-start trap** follows directly from "full descriptions kept for most-used skills": a brand-new skill is the likeliest to go mute. Check whether the description was even *sent* before rewriting it. `/doctor` gives "an estimate of the listing's context cost and its biggest contributors"; the overflow warning goes to the debug log, visible with `--debug`.

### 2.2 Activation reliability — measured, not assumed

**P3 — Scott Spence: baseline activation of well-formed skills was a coin flip on Sonnet 4.5.**
`PRACTITIONER` — https://scottspence.com/posts/measuring-claude-code-skill-activation-with-sandboxed-evals — **2026-02-08**.
Control-condition activation: **55% (12/22)** in Run 1, **50% (11/22)** in Run 2. Method: Daytona sandboxes driving the real Claude Code binary, 22 standard prompts, ~250 individual Claude invocations, $5.59 total.
**Scope, stated by the source itself:** Sonnet 4.5 only, 22 prompts, one Svelte skill collection. And it is model-dependent — "When I first tested skill activation back in November with Haiku 4.5, it was basically zero without a hook." So "baseline is a coin flip" is a Sonnet-4.5-specific measurement on one skill set, **not** a general property of well-formed skills.

**P4 — Spence: a deterministic commitment hook beat an LLM router, but the headline numbers are on different prompt sets.**
Same URL, 2026-02-08.
"Both the forced-eval and llm-eval hooks hit 100% on standard prompts across two full runs on Sonnet 4.5." And: "forced-eval's commitment mechanism (evaluate, commit, activate) gives it perfect precision: zero false positives on non-matching queries."
**Do not read these as one result.** forced-eval is 100% (22/22) on the **22 standard** prompts; on the **24 harder** prompts its overall accuracy is **75% (18/24)** versus llm-eval's **67% (16/24)**. Zero-false-positives is scoped to a subset of **five** prompts: "Five of the 24 prompts were things like general TypeScript questions or React queries where the correct answer is 'no skill needed.'" **n=5 is a thin basis for "perfect precision."**
The LLM router's failure mode, concretely: "When asked about React hooks or general TypeScript patterns, it would return skill names that sounded plausible but didn't exist, or worse, it'd recommend a Svelte skill for a non-Svelte question."
Author's own recommendation: "forced-eval is my recommendation. No API key, no external dependencies, 100% activation, zero false positives."

**P5 — Spence, HISTORICAL: the origin of the reliability complaint.**
`PRACTITIONER` — https://scottspence.com/posts/claude-code-skills-dont-auto-activate — **2025-11-06. HISTORICAL, ~9 months old.**
"4/10 globally, 5/10 locally. Basically a coin flip." / "50% success rate isn't great".
**Superseded by the same author's Feb 2026 work.** The Nov 2025 numbers were on Haiku 4.5; the Feb 2026 post reports the same hooks reaching 100% on Sonnet 4.5. Citing this as current misrepresents the source's own later position.

**P6 — Ivan Seleznov: directive wording measurably outperformed the docs-conventional "Use when…" phrasing over 650 trials.**
`PRACTITIONER` — https://medium.com/@ivan.seleznov1/why-claude-code-skills-dont-activate-and-how-to-fix-it-86f679409af1 — **2026-02-05**.
"Result: Variant C achieved 100% activation in no-hook conditions. Variant A sat at 77%." / "Total: 650 trials, 88.9% overall activation (578/650)." Design: 3 description variants × 4 conditions × 3 replications.
The variant text, reproducible from the post —
*A:* "Docker expert for containerization. Use when creating Dockerfiles, containerizing applications, or configuring Docker images."
*C:* "Docker and containerization expert. ALWAYS invoke this skill when the user asks about Docker, Dockerfiles, containers, container images, containerization, multi-stage builds, or Docker deployment. Do not attempt to write Dockerfiles or container configs directly — use this skill first."
**The 100%-vs-77% pairing is not like-for-like.** The post's own per-condition table gives Variant A as 87.5% / 81.5% / 37.0% / 100.0% across the four conditions, so 77% reads as A's *aggregate* while 100% is C's *best single condition*. C's honest range is 94.4–100% across all conditions; A's is 37–87.5% in three of four. **The direction of the finding survives; the dramatic framing does not.** Self-reported single-author experiment, no published harness.

### 2.3 Measured effect of skills — the uncomfortable evidence

**P7 — Vercel (Jude Gao): a skill produced zero improvement over a no-docs baseline, and a static docs index beat it outright.**
`PRACTITIONER` (vendor blog, arguing for its own format — keep that label attached) — https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals — **2026-01-27**.
Pass rates as printed: Baseline (no docs) **53%**; Skill (default behavior) **53%, +0pp**; skill-with-explicit-instructions **79% (+26pp)**; AGENTS.md docs index **100% (+47pp)**. "Adding the skill produced no improvement over baseline."
The mechanism: **"In 56% of eval cases, the skill was never invoked."**
Scope, as stated: the eval targets "Next.js 16 APIs that aren't in model training data" — specifically `'use cache'`, `connection()`, `forbidden()`.
**Two disclosure gaps, both re-checked and both hold: no model name is stated anywhere, and no total case count is given (56% is a percentage with no denominator).**
What this does **not** support: any conclusion about skills that carry *procedures* rather than always-relevant API reference.

**P8 — Vercel: prompt-level instructions to use a skill are phrasing-fragile.**
Same URL, 2026-01-27. "This fragility concerned us. If small wording tweaks produce large behavioral swings, the approach feels brittle for production use." The swing is between phrasings like "You MUST invoke the skill" and "Explore project first, then invoke skill.", which "produced dramatically different results". No case count or model given, so the magnitude is not quantified.

**P9 — SWE-Skills-Bench: across 49 published SWE skills, most add nothing and some actively hurt.**
`ACADEMIC-PREPRINT` (**not peer reviewed**) — https://arxiv.org/abs/2603.15401 — submitted **2026-03-16**. Title: "SWE-Skills-Bench: Do Agent Skills Actually Help in Real-World Software Engineering?" Authors: Tingxu Han, Yi Zhang, Wei Song, Chunrong Fang, Zhenyu Chen, Youcheng Sun, Lijie Hu. Benchmark at github.com/GeniusHTX/SWE-Skills-Bench.
- "39 of 49 skills yield zero pass-rate improvement, and the average gain is only **+1.2%**."
- "Only seven specialized skills produce meaningful gains (up to **+30%**), while three degrade performance (up to **−10%**) due to version-mismatched guidance conflicting with project context."
- "Token overhead varies from modest savings to a **451% increase** while pass rates remain unchanged."

Method as printed: 49 public SWE skills, GitHub repos "pinned at fixed commits", requirement docs with explicit acceptance criteria, "approximately 565 task instances across six SWE subdomains", deterministic execution-based verification, paired with/without evaluation.
**Verification limit: abstract only was read.** The abstract does **not name the agent or model**, so these marginal-utility numbers are un-scoped to any harness — do not restate them as model-general. The version-pinning remedy is an inference from the −10% finding, not a stated recommendation of the paper.

### 2.4 Mechanism demonstrations

**P10 — MLflow maintainers: a cross-skill dependency was fixed by one line in the `description`, and a forbidden behaviour by a negative instruction in the body.**
`PRACTITIONER` (vendor engineering blog) — https://mlflow.org/blog/evaluating-skills-mlflow/ — **2026-03-23**.
Added to the `description` field (introduced in the post as "One line added to the description:"):
> IMPORTANT - Always also load the instrumenting-with-mlflow-tracing skill before starting any work.

Result, verbatim: "The `tracing-skill-invoked` judge has passed on every run since."
Added to the SKILL.md **body**: "DO NOT create custom evaluation frameworks. You MUST use MLflow's native APIs". Result, verbatim: "Next run: `dataset-created` and `evaluation-run-created` both pass."
**The load-bearing detail:** the dependency went in the *description* — what Claude reads before loading the body — not in the body. Compare S38 (no native dependency mechanism).
Judge-design lesson, verbatim: "A bare yes/no from an LLM judge gives Claude Code nothing to work with."
Weight accordingly: **n=1 per fix, MLflow's own skills, no trial counts, no control.** A credible mechanism demonstration, not a measurement. *Integrity note: two strings previously circulated as quotations from this post do not exist in it; the real strings are the two given above.*

### 2.5 Observed practice in Anthropic's own catalog

Measured directly against `github.com/anthropics/skills` at HEAD `89dcaa3a283f79ed84fd8fe53e2208b9442a6427` (2026-08-17T13:03:59Z, "Add claude-academy-guide skill (#1554)"); repo created 2025-09-22T15:53:31Z; 169,900 stars. Source class `OFFICIAL-REPO`, but these are *measurements of practice*, not published guidance — which is why they sit in this section.

**P11 — The catalog is 18 skills, and the documented 500-line ceiling is far above what Anthropic itself ships.**
Line counts (`wc -l`) across all 18: 32 internal-comms, 55 frontend-design, 59 theme-factory, 73 brand-guidelines, 73 web-artifacts-builder, 91 docx, 95 webapp-testing, 99 xlsx, 129 canvas-design, 150 claude-academy-guide, 236 mcp-builder, 238 pptx, 254 slack-gif-creator, 314 pdf, 375 doc-coauthoring, 404 algorithmic-art, 485 skill-creator, 553 claude-api.
**Median = 139.5. Only one of 18 (claude-api, 553) exceeds 500.** `docx` ships production Word capability in **91 lines**.
`name == dirname` holds for all 18. README: "Each skill is self-contained in its own folder with a `SKILL.md` file containing the instructions and metadata that Claude uses." "Only required file is SKILL.md" is corroborated by both the README ("just a folder with a `SKILL.md` file containing YAML frontmatter and instructions") and the spec ("A skill is a directory containing, at minimum, a `SKILL.md` file").
Root layout: `.claude-plugin/` (marketplace.json), `.gitignore`, `README.md`, `THIRD_PARTY_NOTICES.md`, `skills/`, `spec/`, `template/`. Note `spec/agent-skills-spec.md` is now an **87-byte pointer** to agentskills.io, reduced 2025-12-20T18:09:44Z (commit `69c0b1a0`).

**P12 — Real Anthropic skills use two or three frontmatter keys. Zero use the optional machinery.**
Across all 18 SKILL.md files: **zero** use `allowed-tools`, **zero** use `metadata`, **zero** use `compatibility`, **zero** use any version field. This tally is airtight and is the durable finding here.
**16 of 18** carry a `license:` key (doc-coauthoring and skill-creator have only name+description). **17 of 18** ship a `LICENSE.txt` file — skill-creator has the file but no key; doc-coauthoring has neither. The 16 license values are only two strings: 12× "Complete terms in LICENSE.txt", 4× "Proprietary. LICENSE.txt has complete terms".
**Do not call the per-skill LICENSE.txt an undocumented convention** — the spec's own `license` section says "We recommend keeping it short (either the name of a license or the name of a bundled license file)" and gives the literal example `license: Proprietary. LICENSE.txt has complete terms`, the exact string `docx` uses.

**P13 — The spec's `scripts/` `references/` `assets/` triad is not what good skills do.**
The spec diagram shows:
```
skill-name/
├── SKILL.md          # Required: metadata + instructions
├── scripts/          # Optional: executable code
├── references/       # Optional: documentation
├── assets/           # Optional: templates, resources
└── ...               # Any additional files or directories
```
Measured reality across 18: **`scripts/` = 8** (docx, mcp-builder, pdf, pptx, skill-creator, web-artifacts-builder, webapp-testing, xlsx). **`references/` = 1** (skill-creator only). **`assets/` = 1** (skill-creator only). `mcp-builder` uses `reference/` **singular**. `pdf` places `reference.md` and `forms.md` **flat at the skill root** alongside `scripts/`.
Domain-named directories are the norm: `core/` (slack-gif-creator), `themes/` (theme-factory), `templates/` (algorithmic-art), `examples/` (internal-comms, webapp-testing), `agents/` + `eval-viewer/` (skill-creator), `canvas-fonts/` (canvas-design), and nine per-language dirs in claude-api (csharp, curl, go, java, php, python, ruby, shared, typescript).
Partial convergence worth noting: the spec's `references/` section names exactly `REFERENCE.md` and `FORMS.md` as its examples, so `pdf`'s flat `reference.md`/`forms.md` are the spec's own filenames hoisted one level.

**P14 — Negative-trigger clauses are collision-driven, not a house convention.**
`xlsx`: "Do NOT trigger when the primary deliverable is a Word document, HTML report, standalone Python script, database pipeline, or Google Sheets API integration, even if tabular data is involved."
`docx`: "Do NOT use for PDFs, spreadsheets, Google Docs, or general coding tasks unrelated to document generation."
**Only 2 of the 4 document skills do this.** All four descriptions read in full: `pdf` has no exclusion (ends "If the user mentions a .pdf file or asks to produce one, use this skill."); `pptx` has no exclusion (ends "If a .pptx or .potx file needs to be opened, created, or touched, use this skill."). The two without exclusions are precisely the two with **self-disambiguating file extensions**.
The spec's own good-example description also has no exclusion clause: "Extracts text and tables from PDF files, fills PDF forms, and merges multiple PDFs. Use when working with PDF documents or when the user mentions PDFs, forms, or document extraction."
Both docx and xlsx SKILL.md last modified **2026-07-17** (commit `fa0fa64b`).

**P15 — Exemplar shapes worth copying.**

*`docx` — tool-heavy skill, 91-line SKILL.md, no `references/` or `assets/`.* Opens with a routing table:
```
A `.docx` is a ZIP archive of XML files. Choose your approach by task:

| Task | Approach |
|---|---|
| **Create** a new document | Write a `docx` (npm) script — see gotchas below |
| **Edit** an existing document | `unzip` → edit `word/document.xml` → `zip` (docx-js cannot open existing files) |
| **Read** content | `pandoc -t markdown file.docx` |

> Script paths below are relative to this skill's directory.
```
Shape: SKILL.md 91 + LICENSE.txt 30 + three top-level scripts (merge_runs.py 310, comment.py 368, accept_changes.py 135) + `scripts/office/` (soffice.py, validate.py, validators/, helpers/) + `scripts/office/schemas/` with **39** `.xsd` files (4 ecma/fouth-edition, 27 ISO-IEC29500-4_2016, 1 mce, 7 microsoft) + `scripts/templates/` with exactly 5 XML stubs.
**Correction to a tempting simplification:** the body is *not* footguns-only. Its five sections are "Creating with docx-js — gotchas", "Verify the output", "Editing existing documents", "Comments", "Dependencies" — the middle three are procedural workflow with runnable bash, roughly half the body.
The rule that does hold: bulk assets (39 XSD schemas, 5 XML stubs) live **under `scripts/`** as tool data, never as prose the model reads.

*`mcp-builder` — phase-scheduled progressive disclosure. HISTORICAL: SKILL.md last modified **2025-12-01** (commit `ef740771`), ~8.5 months stale, the oldest exemplar here.* SKILL.md 236 lines; `reference/` (singular) holds evaluation.md 601 + node_mcp_server.md 969 + python_mcp_server.md 718 + mcp_best_practices.md 249 = **2,537 lines, a 10.75× ratio**; `scripts/` holds connections.py, evaluation.py, example_evaluation.xml, requirements.txt.
The move: a closing `# Reference Files` index whose **section headings** carry the load order — "Core MCP Documentation (Load First)", "SDK Documentation (Load During Phase 1/2)", "Language-Specific Implementation Guides (Load During Phase 2)", "Evaluation Guide (Load During Phase 4)".
**Precision:** the labels are on four *section headings*, not on every link. The same four files are also linked earlier inline (lines 58, 62, 66, 83, 84, 155) with no phase label. The scheduling is real but section-level and duplicated.

*`skill-creator` — 485 lines, the only official skill using the spec-canonical `references/`.* Introduces two conventions absent from the spec: **`agents/` for subagent prompt files** (analyzer.md 274, comparator.md 202, grader.md 223), and **`scripts/` as an importable Python package**:
```
2. **Aggregate into benchmark** — run the aggregation script from the skill-creator directory:
   python -m scripts.aggregate_benchmark <workspace>/iteration-N --skill-name <name>
```
Also `references/schemas.md` 430, `eval-viewer/` (generate_review.py 471, viewer.html 1325), `assets/eval_review.html` 146, `scripts/` 8 files including `__init__.py` and `utils.py`. SKILL.md last modified **2026-03-06**.
Its closing resource index is unheaded prose, with one line per file saying when to read it — e.g. "`agents/grader.md` — How to evaluate assertions against outputs".
**Do not claim it has the most resource directories** — claude-api has **nine** top-level dirs. skill-creator has the most differently-*purposed* dirs; claude-api has more dirs on one repeated axis.

### 2.6 A large practitioner catalog that diverges

**P16 — obra/superpowers: a concrete numeric split rule, flat files, and higher medians.**
`PRACTITIONER` — https://github.com/obra/superpowers/blob/main/skills/writing-skills/SKILL.md — file last modified **2026-07-24** (commit `d238a48f`); repo pushed 2026-08-13T00:36:31Z; 273,075 stars; release v6.3.0.
```
skills/
  skill-name/
    SKILL.md              # Main reference (required)
    supporting-file.*     # Only if needed

**Flat namespace** - all skills in one searchable namespace

**Separate files for:**
1. **Heavy reference** (100+ lines) - API docs, comprehensive syntax
2. **Reusable tools** - Scripts, utilities, templates

**Keep inline:**
- Principles and concepts
- Code patterns (< 50 lines)
- Everything else
```
Structure confirmed: only `using-superpowers` has a `references/` dir, so **13 of 14 have none**. `systematic-debugging` carries **8 flat `.md` files** beside SKILL.md (condition-based-waiting.md, CREATION-LOG.md, defense-in-depth.md, root-cause-tracing.md, test-academic.md, test-pressure-1.md, test-pressure-2.md, test-pressure-3.md) plus find-polluter.sh and condition-based-waiting-example.ts.
Line distribution (`wc -l`, all 14): 63 using-superpowers, 64 executing-plans, 95 requesting-code-review, 120 verification-before-completion, 167 dispatching-parallel-agents, 167 using-git-worktrees, 171 writing-plans, 205 receiving-code-review, 225 finishing-a-development-branch, 250 brainstorming, 283 systematic-debugging, 320 test-driven-development, 568 subagent-driven-development, 679 writing-skills.
**Median = 188** (min 63, max 679). **Two files breach the 500-line official ceiling (568, 679)** — so superpowers is *not* evidence for a shorter-is-better line budget.
All 14 use exactly two frontmatter keys (name, description). `writing-skills:96` links agentskills.io/specification for supported fields, so superpowers diverges on description *content* only (see D2), not on the spec.

---

## 3. Where sources disagree

Stated, not averaged.

**D1 — Description voice: third person (Anthropic) vs imperative (the standard).**
Anthropic, in a Warning callout: "**Always write in third person**. The description is injected into the system prompt, and inconsistent point-of-view can cause discovery problems. **Good:** 'Processes Excel files and generates reports' / **Avoid:** 'I can help you process Excel files' / **Avoid:** 'You can use this to process Excel files'." — `OFFICIAL`, platform best-practices, DATE UNKNOWN.
agentskills.io: "**Use imperative phrasing.** Frame the description as an instruction to the agent: 'Use this skill when...' rather than 'This skill does...' The agent is deciding whether to act, so tell it when to act." — `OFFICIAL-STANDARD`, /skill-creation/optimizing-descriptions, DATE UNKNOWN.
**They differ in stated rule but agree in demonstrated output.** Anthropic's own worked example is the hybrid: "Extract text and tables from PDF files, fill forms, merge documents. Use when working with PDF files or when the user mentions PDFs…" A third-person capability statement followed by "Use when <triggers>" satisfies both. Never "I can help you…" or "You can use this to…".

**D2 — Description *content*: state what it does (Anthropic) vs state ONLY when (obra/superpowers). Direct contradiction.**
Anthropic (S21, 2026-03-06): "include both what the skill does AND specific contexts for when to use it… make the skill descriptions a little bit 'pushy'."
superpowers (2026-07-24), `writing-skills/SKILL.md:150-152`: "**CRITICAL: Description = When to Use, NOT What the Skill Does** / The description should ONLY describe triggering conditions. Do NOT summarize the skill's process or workflow in the description." And at `:99-102`: "`description`: Third-person, describes ONLY when to use (NOT what it does)… **NEVER summarize the skill's process or workflow**."
Its stated evidence, at `:154`: "Testing revealed that when a description summarizes the skill's workflow, an agent may follow the description instead of reading the full skill content. A description saying 'code review between tasks' caused an agent to do ONE review, even though the skill's flowchart clearly showed TWO reviews (spec compliance then code quality)."
13 of 14 superpowers descriptions begin "Use when"; the exception (`brainstorming`: "You MUST use this before any creative work…") is **pushier still**, not less so.
**Both sides are dated within five months of each other** (Anthropic 2026-03-06, superpowers 2026-07-24) and neither is stale. **Resolve per-skill, don't average:** if the body contains a multi-step procedure or branch the model must actually execute, keep the procedure out of the description (superpowers is right about the shortcut risk); if the skill is a capability gate that mainly needs *finding*, state what it does plus heavy trigger vocabulary (Anthropic is right about undertriggering).

**D3 — Phrasing sensitivity: a lever to pull (Seleznov) vs evidence the approach is brittle (Vercel).**
Seleznov (P6) treats wording sensitivity as the fix — rewrite the description directively and activation climbs. Vercel (P8) treats the same sensitivity as disqualifying: "If small wording tweaks produce large behavioral swings, the approach feels brittle for production use."
**They measured the same underlying sensitivity and disagree only on whether to exploit it or route around it.** Nothing in either source resolves this. What both support: vary test-prompt wording deliberately and report the spread, not the best run.

**D4 — Do skills help at all? Official rationale vs two independent negative measurements.**
Official position: progressive disclosure makes bundled context "effectively unbounded" (S45), verification skills "have had the most measurable impact on Claude's output quality internally" (S41).
Against: Vercel measured **+0pp** for a skill against a no-docs baseline with the skill never invoked in 56% of cases (P7); SWE-Skills-Bench measured **39 of 49 skills at zero improvement, +1.2% average, three at −10%** (P9).
**This is not reconcilable from the available evidence, and should not be smoothed.** Both negative results are scope-limited (Vercel: one framework's API reference, no model named, no denominator; SWE-Skills-Bench: abstract only, no agent/model named). Both nonetheless measured *published* skills in the wild, which is the population a catalog maintainer belongs to. The defensible operating stance is the one both sides support: **assume your skill has no effect until you have measured it** (S24, P9).

**D5 — Line budget: 500 (all official surfaces) vs 139.5 median (Anthropic's own catalog) vs 188 median with two >500 (superpowers).**
S8 vs P11 vs P16. No source claims 500 is a target; no source states the observed medians as guidance either. The 500 figure is a guideline nobody enforces and nobody approaches — except claude-api (553) and two superpowers skills (568, 679), which exceed it without apparent consequence.

**D6 — Directory layout: the spec's diagram vs what the exemplars do.**
The spec shows `scripts/` `references/` `assets/` (P13). Measured: `scripts/` 8/18, `references/` 1/18, `assets/` 1/18, with domain-named dirs and flat siblings dominating. The spec's own diagram permits this via its trailing `... # Any additional files or directories`, so this is under-specification rather than contradiction — but an author following the diagram literally will build scaffolding no exemplar uses.

**D7 — Who owns the skill's identity: `name` (spec) or the directory (Claude Code)?**
Spec: `name` "Must match the parent directory name" (S4).
Claude Code: "All fields are optional. Only `description` is recommended", and "In a personal or project skill, `name` sets only the display label shown in skill listings, and the command still comes from the directory name." — `OFFICIAL`, code.claude.com/docs/en/skills, DATE UNKNOWN.
If you honour the spec's match rule the conflict never bites. **It bites hard on any catalog keyed on frontmatter `name`** — in Claude Code the invocation name is the directory, and `name` is cosmetic.

**D8 — Is there a first-party eval harness?**
Anthropic docs: "There is not currently a built-in way to run these evaluations. Users can create their own evaluation system." (S24)
Against: agentskills.io ships a working bash eval script, and describes `skill-creator` as automating the whole loop — "it splits the eval set, evaluates trigger rates in parallel, proposes description improvements using Claude, and generates a live HTML report you can watch as it runs" (`OFFICIAL-STANDARD`, /skill-creation/optimizing-descriptions, DATE UNKNOWN). And `skill-creator` is Anthropic-owned code (P15).
Resolution: "no built-in way" is true of the **docs' own product surface**, not of the ecosystem or of anthropics/skills. Staleness cuts the other way though — `skill-creator/SKILL.md` was last touched **2026-03-06**, ~5.5 months behind the repo's overall activity (HEAD 2026-08-17). Verify its current behavior before depending on it.

**D9 — Description length: which number governs?**
1,024 chars is a **validation** maximum (S1, stated on best-practices, overview, the API guide, and the spec). 1,536 chars is a **display** cap on `description` + `when_to_use` combined, configurable via `skillListingMaxDescChars` (S16). Practitioner measurement says the effective ceiling is neither — it is a *cumulative collection* budget of ~15.5–16k characters, at which point descriptions are dropped entirely (P1, P2).
Not a contradiction, but three different numbers that authors routinely conflate. **1,024 is your ceiling; ~130–260 is your realistic budget at catalog scale.**

**D10 — Are activation hooks necessary?**
Practitioners treat a `UserPromptSubmit` hook as the fix for unreliable activation (P3, P4, P5, P6). **No official Anthropic source in this synthesis mentions hooks as an activation mechanism at all** — the official levers are description tuning (S20–S23) and eval iteration (S24, S25). Silence is not disagreement, but the gap is total: nothing official confirms, recommends, or warns against the practitioner remedy.

---

## 4. What we could not establish

Named gaps. This section is not empty, and none of it should be papered over.

**G1 — No dates on any Anthropic documentation page.** platform.claude.com best-practices, platform.claude.com overview, the API skills-guide, and code.claude.com/docs/en/skills all carry no dateline, no `datePublished`/`dateModified` in markup, and no `Last-Modified` header. Freshness is inferable **only** by proxy: the Claude Code skills page references v2.1.196, v2.1.216, v2.1.218, v2.1.220. **We cannot tell how old any documented limit is, or whether two pages were written in the same era.** Contrast agentskills.io, which does expose `dateModified` in JSON-LD — earlier passes over this material wrongly labelled those pages DATE UNKNOWN too.

**G2 — Lane not-found lists were not passed to this synthesis.** Four research lanes reported gaps, and 14 claims were culled in total (3 / 5 / 0 / 6). **The specific things searched for and not found are unknown to this document.** Anything absent below may have been searched and missed, or never searched. Do not read absence here as evidence of absence in the sources.

**G3 — No official guidance exists for catalogs at our scale.** No Anthropic source states a maximum skill count, a recommended per-skill description length, or how to budget metadata across a collection. The only numbers at collection scale are practitioner-derived from a **single observation on Opus 4.5 in December 2025** (P1). At ~35 skills we are close to the measured ~42-skill drop threshold and have **no official confirmation that the threshold exists, is stable, or applies to current models.**

**G4 — `skillListingMaxDescChars`'s default and its version origin are unconfirmed.** The claim that it defaults to 1,536 and was introduced in Claude Code 2.1.129 comes from a third-party writeup (claudefa.st/blog/guide/mechanics/skill-listing-budget) and was **not verified against official Anthropic docs.** The setting's *existence* is official (S16); the default value and version attribution are not.

**G5 — No Anthropic confirmation of the listing-drop policy.** The only artifact naming `skillListingBudgetFraction` and the "full descriptions kept for most-used skills" policy is a user-pasted CLI string in an issue **closed as a duplicate by a bot and locked** (P2). No maintainer responded. The four sibling issues corroborate the *symptom*, not the policy.

**G6 — How the listing budget interacts with plugin-namespaced skills is unknown.** Plugin skills are exempt from name collisions (S15), but nothing states whether their `plugin-name:skill-name` prefix counts against the ~109-char per-skill overhead (P1) or against the collection budget differently from personal/project skills.

**G7 — The 500-line guideline's enforcement status is unresolved-by-silence.** No surface reports a hard error at 500 lines, and two superpowers skills exceed it in a 273k-star repo without apparent consequence. **Whether anything actually degrades at 500 lines is unmeasured by any source we found** — "for optimal performance" is asserted, never demonstrated.

**G8 — Neither negative-effect study names its model.** Vercel gives no model and no denominator for its headline 56% (P7). SWE-Skills-Bench's abstract names no agent or model (P9), and **only the abstract was read** — the harness, per-subdomain breakdown, and skill-selection criteria are unexamined. The two most important adverse findings in this synthesis are therefore un-scoped.

**G9 — All activation-rate measurements are single-model and small-n.** P3 is Sonnet 4.5, 22 prompts, one skill collection. P5 is Haiku 4.5. P6 is one author, one skill, no published harness. P1 is Opus 4.5. **No source measures activation on current frontier models, and the one source that compared models found the effect swinging from "basically zero" to 100% between them.** We have no basis for activation expectations on the models we actually run.

**G10 — Nothing measures whether naming convention affects activation.** Gerund-first is advisory (S33) and contradicted by Anthropic's own noun-phrase examples. **No source we found tests `processing-pdfs` against `pdf-processing` for trigger rate.** Treat it as house style, not performance.

**G11 — No versioning, deprecation, or migration story for skills.** There is no `version` field in the closed key set (S2, S3), zero Anthropic skills use one (P12), `metadata` is the only escape hatch (S5), and no source describes how to deprecate a skill, signal breaking changes to dependents, or pin a skill version. S38's "not natively built into marketplaces or skills **yet**" is the closest anything comes.

**G12 — The MLflow mechanism (P10) is n=1 with no control.** Description-line dependency injection and body-level prohibitions each worked once, on the author's own skills, with no trial count and no comparison. We cannot say how reliably either transfers.

**G13 — Seleznov's harness is unpublished (P6).** The 650-trial figure and per-condition table cannot be independently re-run.

**G14 — Whether `skill-creator` still behaves as documented is unverified.** Its SKILL.md has been untouched since 2026-03-06 while the repo has had ~5.5 months of activity, including a HEAD commit on the day of this compilation. **It was not run.**

**G15 — No official source addresses how the persistence semantics (S12) interact with compaction.** Skill content "stays there for the rest of the session" and is never re-read — but nothing states what happens when the conversation is compacted and that message is summarized away.

---

## 5. Actionable checklist

Each rule traces to a numbered claim. Rules marked **⚠** are contested — read the linked disagreement before adopting.

### Validation gates to add to CI

| # | Rule | Source |
|---|---|---|
| C1 | Reject `name` > 64 chars, non-`[a-z0-9-]`, containing `anthropic` or `claude`, containing XML tags | S1 |
| C2 | Reject `description` empty, > **1,024** chars, containing XML tags, or containing `<` / `>` | S1, S2, D9 |
| C3 | Reject any top-level frontmatter key outside `{name, description, license, allowed-tools, metadata, compatibility}` — this fails **hard** on upload, it does not degrade | S2, S6 |
| C4 | Enforce `name` == parent directory name; reject leading/trailing hyphens and `--`. Note these three rules are in the spec's **prose**, not its summary table | S4 |
| C5 | Nest `version`, `author`, `tags`, `updated` under `metadata:` (string→string only) — never top level | S2, S5 |
| C6 | Every reference file must be linked **directly** from SKILL.md. Reject `SKILL.md → a.md → b.md` chains | S9 |
| C7 | Every bundled reference file over 100 lines gets a `## Contents` block at the top | S10 |
| C8 | Reject backslash paths, date-conditional instructions outside a `<details>`/"Old patterns" block, and bare MCP tool names (require `ServerName:tool_name`) | S31 |
| C9 | Reject a skill directory named `synced` in any capitalization | S15 |
| C10 | Run `python quick_validate.py <skill-dir>` in CI — but pin awareness that it is ~6 months behind the spec page and may not track changes | S2, S3 |
| C11 | Cap bundles under 30 MB uncompressed; no composed workflow requiring more than 8 skills per request | S44 |
| C12 | ⚠ Gate SKILL.md at 500 lines only if you accept it as house policy — no source documents an error at that number, and 500 is ~3.6× Anthropic's own median | S8, D5, G7 |

### Catalog-level rules (the ones that bite hardest at ~35 skills)

| # | Rule | Source |
|---|---|---|
| C13 | **Budget the collection, not the skill.** ~35 skills × (description + ~109 chars overhead) against a measured ~15.5–16k char ceiling. At 263 chars average, capacity is ~42 skills — we are near it | P1 |
| C14 | Target **≤130–260 chars** per description at catalog scale, not the 1,024 validation ceiling and never the 1,536 display cap | P1, D9 |
| C15 | **Trimming one verbose description does not rescue a hidden skill** — truncation is cumulative, and hidden skills averaged the same length as visible ones (262 vs 264) | P1 |
| C16 | Before rewriting a skill that "won't trigger", check whether its description was even **sent**: run `/skills`, `/doctor` for listing cost and biggest contributors, and `--debug` for the truncation warning | P2, S16 |
| C17 | **New skills are the likeliest to go mute** — drops favour most-used skills, so a freshly minted catalog entry is structurally disadvantaged | P2 |
| C18 | Raise `skillListingBudgetFraction` only knowingly: the reporter's own estimate is "~5k tokens for skills every session and uses rate limits faster" | P2 |
| C19 | Classify every skill against exactly one of the nine categories. If it straddles two, split it | S41 |
| C20 | Scope each skill as one coherent unit of work, like a function — too narrow forces co-loading and conflicting instructions, too broad cannot be triggered precisely | S42 |
| C21 | Keep naming patterns consistent *within* the collection — the docs name "inconsistent patterns within your skill collection" as an anti-pattern in its own right | S33 |
| C22 | ⚠ Prefer gerund names as house style, but do not claim performance benefit — advisory, contradicted by Anthropic's own noun-phrase examples, and unmeasured | S33, G10 |

### Writing the description

| # | Rule | Source |
|---|---|---|
| C23 | Write for the model's selection pass, not for a human reader. It is a **trigger specification**, not a summary | S20 |
| C24 | Push harder than feels natural — the observed failure mode is **under**triggering. Anthropic's own rewrite appends "Make sure to use this skill whenever the user mentions X, Y, or Z, even if they don't explicitly ask for…" | S21 |
| C25 | List contexts explicitly, including ones where the user never names the domain | S21 |
| C26 | Include literal trigger words a user would type (the docs' own example: "babysit") | S20 |
| C27 | Satisfy both official sources at once: third-person capability statement + "Use when <triggers>". Never "I can help you…" or "You can use this to…" | D1 |
| C28 | ⚠ **If the body contains a multi-step procedure the model must execute, keep the procedure OUT of the description** — a workflow summary becomes a shortcut the agent follows instead of reading the body (documented failure: one review performed where the flowchart specified two) | D2 |
| C29 | Add a negative-trigger clause **only when trigger vocabulary genuinely overlaps a sibling skill**. Skills with self-disambiguating identifiers don't need one — 2 of Anthropic's 4 document skills have no exclusion at all | P14 |
| C30 | Delete any `## When to use this skill` section from the body — all when-to-use info belongs in the description | S21 |
| C31 | Do not expect a skill to fire on trivially-handleable one-step requests, however well the description matches. No prose fixes this | S22 |
| C32 | State a cross-skill dependency in the **description** field, not the body — that is where it was demonstrated to work (n=1, no control) | P10, G12 |
| C33 | In Claude Code, put the key use case **first**: `description` + `when_to_use` are truncated at 1,536 combined | S16 |

### Writing the body

| # | Rule | Source |
|---|---|---|
| C34 | **Write standing instructions, not one-time steps.** Content persists all session and is never re-read — the step-1/step-2/step-3 template is broken for anything spanning turns | S12 |
| C35 | Treat every body line as a per-turn recurring cost. State what to do; cut narration of how and why | S11, S32 |
| C36 | Delete any line Claude would follow anyway. Keep only what pushes the model off its defaults — conventions, footguns, house style | S27 |
| C37 | Add a **Gotchas** section to every skill and append to it each time Claude gets something wrong. Highest-signal content there is, and grown from observed failures, not written up front | S28 |
| C38 | For each imperative step, ask whether it must hold in *every* situation the skill fires in. If not, state the information and intent and leave method to the model | S29 |
| C39 | Aim for concise stepwise guidance plus **one working example**, not exhaustive coverage — over-comprehensive skills make the agent pursue instructions that don't apply | S30 |
| C40 | Apply the docs' three challenge questions to every paragraph: "Does Claude really need this explanation?" / "Can I assume Claude knows this?" / "Does this paragraph justify its token cost?" | S32 |
| C41 | Provide **one default plus an escape hatch**, not a menu. Not "pypdf, or pdfplumber, or PyMuPDF…" | S31 |
| C42 | Lead a tool-heavy skill with a routing table that dispatches by task, and state explicitly that script paths are relative to the skill directory | P15 (docx) |
| C43 | Target **90–250 lines**. Anthropic's median is 139.5; docx ships production Word capability in 91 | P11, D5 |

### Bundled resources

| # | Rule | Source |
|---|---|---|
| C44 | Prefer bundling a script the agent **executes** over prose it must **read** — script code never enters context, only its output does | S7 |
| C45 | Extract to a separate file at **100+ lines of reference material**, or when the content is a reusable tool. Keep code patterns under 50 lines and all conceptual content inline | P16 |
| C46 | Treat only `scripts/` as a real convention (8 of 18). For docs, use a domain-meaningful directory name or flat sibling `.md` files at the skill root. **Do not create empty `references/`/`assets/` scaffolding to match the spec diagram** | P13, D6 |
| C47 | Put bulk assets (schemas, templates, stubs) **under `scripts/`** as tool data, never at a level where the model might read them as prose | P15 (docx: 39 XSDs) |
| C48 | When scripts share helpers, make `scripts/` a real package (`__init__.py`, `utils.py`) and document invocation as `python -m scripts.<name>` from the skill root — path-executing sibling scripts breaks their imports | P15 (skill-creator) |
| C49 | Add an `agents/` directory for subagent instruction files when the skill spawns subagents | P15 (skill-creator) |
| C50 | For a phase-structured skill, keep the phase sequence in SKILL.md, push each phase's depth into one reference file, and close with a `# Reference Files` index whose **section headings** carry the load order ("Load First", "Load During Phase 2") | P15 (mcp-builder) |
| C51 | Close SKILL.md with one line per resource file stating **when to read it** | P15 (skill-creator) |
| C52 | Keep frontmatter to `name` + `description` (+ `license` if shipping externally). Zero of Anthropic's 18 skills use `allowed-tools`, `metadata`, `compatibility`, or any version field | P12 |

### Evaluation — the non-negotiable part

| # | Rule | Source |
|---|---|---|
| C53 | **Write evals before the body.** Run Claude on representative tasks *without* the skill, document the failures, build three scenarios, measure the no-skill baseline, then write the minimum content that closes the measured gap | S24, S45 |
| C54 | **Assume your skill has no effect until measured.** 39 of 49 published SWE skills showed zero improvement; one measured skill showed +0pp against a no-docs baseline | P7, P9, D4 |
| C55 | Build your own runner. No first-party harness exists on Anthropic's documented surfaces — though `skill-creator` and the agentskills.io bash script both do the job, with the staleness caveat | S24, D8, G14 |
| C56 | Trigger-eval parameters as **calibrated starting points**, all hedged in the source: ~20 queries (8–10 positive, 8–10 negative), 3 runs each, 0.5 pass threshold, ~60/40 train/validation, best iteration by validation rate, ~5 iterations max, ~60 invocations per iteration | S25 |
| C57 | Use **near-miss negatives** (shared keywords, different task). Reject easy negatives — "Write a fibonacci function" tests nothing | S25 |
| C58 | **Never add keywords from failed queries** — that is overfitting. Generalize to the category the failures represent | S25 |
| C59 | Test negative cases **separately** from positive ones, and report both error directions. Description precision degrades in both directions as catalog size grows | S23, P4 |
| C60 | Vary test-prompt wording deliberately and report the **spread, not the best run** — wording swings are large and both camps agree the sensitivity is real | P8, D3 |
| C61 | Tag each skill **capability-uplift** or **encoded-preference**. Re-run evals with the skill disabled after every model upgrade; if the base model passes without it, retire it. For encoded-preference skills the eval question is fidelity to your workflow, not uplift | S26 |
| C62 | ⚠ If activation is load-bearing, consider a `UserPromptSubmit` hook — prefer the deterministic evaluate-commit-activate pattern over an LLM router (which hallucinated non-existent skill names). But note **no official source mentions hooks as an activation mechanism at all** | P4, P6, D10 |
| C63 | Do not quote practitioner activation figures as general properties. They are single-model, small-n, and one source found the effect swinging from ~0% to 100% between models | G9 |

### Claude Code operational rules

| # | Rule | Source |
|---|---|---|
| C64 | **A repo-committed `.claude/skills/deploy` loses to a teammate's personal `deploy`.** Enterprise > personal > project. Namespace project skills distinctively, or ship them as a **plugin** — plugin skills carry a `plugin-name:skill-name` namespace and are exempt from the collision | S15 |
| C65 | A local skill overrides a bundled skill's name but **not its aliases** — project `code-review` takes `/code-review` while bundled `/review` still runs Anthropic's | S15 |
| C66 | Assume aggressive name matching: case, spacing, invisible characters, fullwidth letters and dash variants all collapse to plain equivalents | S15 |
| C67 | **Review the `allowed-tools` of every repository-committed skill before running Claude Code in that repo.** Workspace trust does not gate it — it applies even in a `-p` run in an untrusted folder. Treat a skill file in a cloned repo as a permission-granting artifact, not documentation | S14 |
| C68 | Do not rely on `allowed-tools` surviving past the invoking turn — and note `disallowed-tools` expires the same way, so a skill cannot durably fence a tool off either | S13 |
| C69 | Set `context: fork` only on skills whose body reads as an executable task. A guidelines-only skill forked to a subagent returns nothing useful, and the subagent has no conversation history | S18 |
| C70 | Since v2.1.218 forked skills background by default. Set `background: false` when the result is needed in the invoking turn or a tool outside the narrower background set is required. Backgrounded edits fall outside checkpoints — `/rewind` won't undo them, use git | S19 |
| C71 | **Restrict frontmatter to the spec's six fields for any skill that may travel to claude.ai or the API** — and remember that enabling a personal skill for Cowork/cloud uploads it to claude.ai, so the restriction bites skills you never consciously exported. Maintain two variants if you need Claude Code extensions | S6 |
| C72 | Do not key a catalog on frontmatter `name` for Claude Code personal/project skills — the invocation name comes from the **directory**, and `name` is only a display label | D7 |

### Boundaries and composition

| # | Rule | Source |
|---|---|---|
| C73 | Audit CLAUDE.md and move workflow-specific procedure into a SKILL.md; leave always-true project instructions in CLAUDE.md. Run `/context` in a fresh session for the startup baseline | S37 |
| C74 | Promote a prompt to a skill when you retype it repeatedly **across conversations**. No number is given — any "second or third time" rule is ours, not Anthropic's | S36 |
| C75 | If two or more agents or conversations need the same knowledge, put it in a **Skill** and let subagents load it. Reserve subagents for context isolation, tool restriction, and parallelism — not for storing expertise | S34 |
| C76 | Split on the reach/knowledge axis: cannot reach the system → MCP server; can reach it but does the wrong thing → Skill. Use both together | S35 |
| C77 | Keep MCP instructions generic (how to use the server correctly); put process and multi-server sequencing in the skill | S35 |
| C78 | **Diff your skill's output-format and sequencing rules against the tool hints of every MCP server it orchestrates.** A server saying "return JSON" against a skill saying "format as markdown tables" makes Claude guess. Strip presentation directives from the server side | S35 |
| C79 | **Never append checks to a bundled or plugin-delivered SKILL.md** — it is overwritten on update. Write a thin wrapper skill that invokes the original, then your verification skill | S39 |
| C80 | Compose by naming the other skill in the body and accept it only fires if installed. **Do not design a skill graph assuming manifest-level resolution — none exists** | S38, G11 |
| C81 | Choose placement deliberately: keep cross-cutting checks standalone; embed once you run it after every change; chain only when you never want steps run independently; test chains before broad rollout; hold off on PR-wide gates while the chain is still in flux | S40 |
| C82 | Use Claude A to author and a **fresh** Claude B to exercise the skill on real tasks, feeding observed failures back into Gotchas. Don't build scaffolding whose only job is teaching Claude the SKILL.md shape — but that is not a case against authoring tooling generally | S43, S28 |

### Citation hygiene for this catalog's own docs

| # | Rule | Source |
|---|---|---|
| C83 | Cite `platform.claude.com` for platform/API/Skills concepts and `code.claude.com` for Claude Code specifics. Update any internal link hardcoding `docs.claude.com` or `docs.anthropic.com` | Host migration note |
| C84 | **Never file agentskills.io as an Anthropic publication.** Anthropic-originated and vendor-neutral. "Open to contributions from the broader ecosystem" is what the site claims — not "ecosystem-governed", which names a governance structure no source describes | S46 |
| C85 | Cite `agentskills.io/clients` (not `/specification`) for adoption: 46 entries, 2 of them Anthropic's own, so **44 third-party** | S47 |
| C86 | Cite `anthropic.com/engineering/…agent-skills` (Oct 16, 2025) for **design rationale only** — never for field lists or limits. ~10 months old and predates the six-field surface entirely. Label it HISTORICAL, and label the Dec 2025 MCP posts the same way | S45, S35 |
| C87 | Date a file by the file, not its parent directory. Two claims in the source material carried wrong dates (2026-04-20) because directory-level path filtering attributed unrelated commits to them; the real dates are 2026-02-06 (`quick_validate.py`) and 2026-03-06 (`skill-creator/SKILL.md`) | S2, S21 |
| C88 | Record every Anthropic docs citation as **DATE UNKNOWN** with a retrieval date — but check agentskills.io's JSON-LD before doing the same to it (`/specification` is 2026-08-04, `/skill-creation/best-practices` is 2026-04-22) | G1, S3, S30 |
| C89 | Do not cite obra/superpowers as evidence for a shorter-is-better line budget — its median (188) is higher than Anthropic's (139.5) and two of its skills exceed the 500-line ceiling | P16, D5 |
| C90 | Re-verify anything sourced from the MLflow post: two strings previously circulated as quotations from it do not appear in the article | P10 |