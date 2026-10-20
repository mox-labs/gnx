# What recon does for Claude

> Recon extends Claude's effective context through structured indirection.

That sentence is the whole idea. The rest of this doc unpacks what it means and why the design choices follow from it.

## The problem recon solves

Claude's context window is finite. If the task is to reason over a thousand records, loading a thousand records into the prompt doesn't work — the ones that fit eat budget that would be better spent on thinking. And interpreting each record costs inference: parsing the JSON, reading nested fields, remembering which entity was which, re-reading responses after retries.

Claude *can* do this work. It's just the wrong work for an LLM. Fetching, parsing, retrying, rate-limiting, normalizing — none of it is reasoning. It's mechanical. Every cycle spent on it is a cycle not spent on the things only Claude can do: deciding what matters, spotting anomalies, connecting findings across sources that nobody wrote down as connected.

The gap is capability-shaped, not effort-shaped. Having Claude try harder at parsing API responses doesn't fix it. Having Claude not do that work at all does.

## Convergent versus divergent

A useful distinction between two kinds of work an LLM gets asked to do:

**Convergent work** is deterministic. There's a right answer, or at least a clearly-better one. Parse this response. Extract this field. Retry this 429 with backoff. Reshape this nested payload into flat columns. LLMs can do convergent work, but they're overkill: the cost is inference, and the failure modes are strange — hallucinated field names, JSON structure lost in paraphrase, silent drops of records that didn't fit a mental model.

**Divergent work** is generative. There is no single right answer, only better and worse judgments. Which findings matter for this question? What pattern is emerging across these records? Where is the hole in this argument? What's suspicious about this result? This is the work LLMs uniquely enable, and it's what the human wanted help with in the first place.

Recon takes the convergent work off Claude's critical path. A declarative config says *what to fetch* and *how to reshape it*. Recon runs it mechanically — no LLM inference during execution. Claude reads the normalized JSONL and spends its inference on the divergent work.

The split is structural, not just efficient. LLMs are convergent engines — they regress toward the mean of the training distribution. Ask an LLM to "parse these API responses and summarize" and you get a fluent summary that silently lost half the structure. Ask recon to do the same reshape and you get deterministic, auditable JSONL. The tool is honest about its output in a way the LLM can't be, because the tool doesn't interpret.

## Structured indirection

Here is the non-obvious part. A recon archive with five thousand records isn't in Claude's context. It's in a file Claude can *query*.

```sql
SELECT title, year, citations
FROM papers
WHERE citations > 100
ORDER BY year DESC
LIMIT 10
```

Claude sees ten rows. The other four thousand nine hundred and ninety never enter the prompt. This is structured indirection: Claude reasons about *what to ask*, recon holds the data, Claude reasons about *what came back*.

Effective reach now scales with the archive size, not with the context window. A mission with fifty thousand records works the same as a mission with fifty — Claude asks targeted questions against structured data instead of scrolling through unstructured text. The conversation stays light, the data stays rich, and the reasoning stays focused.

## What this unlocks

Six concrete capabilities follow from the one thesis.

**Work over datasets larger than the context window.** Query, don't load. The archive holds the data; Claude holds the questions. A literature review across four hundred papers is the same shape as one across forty — the difference is how often Claude refines the query.

**Reproducibility and audit.** The config is a frozen, reviewable artifact. Anyone reading `.recon/<mission>/config.yaml` can see exactly what was collected, from where, with what parameters, at what time. There is no "Claude made a judgment call at step forty-seven that nobody can reconstruct." The judgment calls are in the config, signed by whoever wrote it.

**Temporal awareness.** Each run produces a timestamped archive alongside earlier ones. Diff two runs to see what changed. Monitor a GitHub repo's releases week over week. Track an API's state between deploys. The archives are the memory; Claude does the diffing.

**Cross-source composition.** Normalize heterogeneous sources to one schema, then query across them. Academic papers from Semantic Scholar, issues from `gh`, release notes from RSS, dependency trees from `cargo` — all become JSONL, all queryable from one SQL interface. Questions that span sources become one query instead of a manual join.

**Scheduled and offline execution.** Because recon is mechanical, it runs without Claude awake. Cron it. Run it overnight. A scheduled agent runs `recon survey` hourly and Claude reads the latest archive in the morning, reasoning about what's new. The collection loop and the reasoning loop can be decoupled.

**Claude's inference stays focused.** No cycles burned on retry logic, rate-limit arithmetic, response parsing, pagination math, or authentication ceremony. The inference budget goes to thinking. This is the capability the other five rest on.

## When recon is the right tool

Reach for recon when:

- There is data *out there* Claude needs to reason over, and it's more than what fits comfortably in the prompt
- The data source has a predictable response shape — even if messy, there's a schema you can probe
- The same thing will be collected more than once, or across more than one source
- You want the collection to be reproducible or auditable

Reach for something else when:

- You need one-off ad-hoc exploration — `curl` piped into the chat is faster
- You need live streaming or pub/sub — recon does snapshots, not streams
- You want an autonomous research agent that decides what to fetch — recon runs the config you wrote, it doesn't decide

In a literature review recon is the first link: it collects, and extracting claims per source, verifying them and synthesizing happen downstream, over the tables it wrote. Recon is useful anywhere Claude needs structured indirection over external data, research or not.

## Why recon does not decide what to fetch

Recon runs the config you wrote. It does not choose the sources, and it does not write the report. That is deliberate: the divergent work, deciding what to collect and what the result means, stays with the human and Claude working together, because that is where new connections come from and where the human learns something. Recon removes the mechanical ceremony around that work and leaves the thinking where it is.

If the goal is a finished report with no involvement, recon is the wrong tool. If the goal is a queryable dataset that the human and Claude can reason over together (noticing patterns, questioning outliers, following threads across sources), recon is the bridge.

## The one-sentence version

Recon is mechanical so Claude can be generative. Configuration is the contract between them.
