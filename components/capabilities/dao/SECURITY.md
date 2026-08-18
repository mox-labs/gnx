# Security — dao

dao is the smallest surface of the four capabilities: it reads a corpus catalog, reads a
project's `.claude/` directory, and prints a report. It executes nothing, fetches nothing,
and writes nothing.

Audience: anyone running `dao check` against a repository they did not write.

## Blast radius

Read-only. `dao spec`, `dao check`, `dao figures`, and `dao policy` all terminate in a
console table. There is deliberately **no `dao init`** — see below.

## Trust boundaries

### 1. Project directory → audit report

`dao check <project>` walks `.claude/` and `CLAUDE.md` and reports PRESENT / PARTIAL /
ABSENT. Files are read as text and never executed, imported, or rendered as a template.
Agent and skill bodies are examined for substance, not evaluated.

The report is the only output. A hostile project can make the audit *wrong* — a padded
agent file can pass a length-based substantive test — but it cannot make the audit
*dangerous*.

### 2. Corpus catalog → seat grounding

`dao figures` reads `dao-corpus/corpora/agent-craft`'s CATALOG. The catalog's `informs`
edges decide which domains are offered as grounded seats for a project.

**The honesty property is the security property here.** A domain marked `RAW_GATHERED` has
sources collected but not distilled; presenting it as grounded would launder unreviewed
material into a project's bench. `dao figures` prints the count explicitly and
`CorpusDomain.is_grounded` returns true only for `DISTILLED`.

### 3. No generation

There is no `dao init`. Scaffolding eleven plausible files is exactly the artifact-shaped
hole the audit exists to catch, and the load-bearing part — the method prose — is a
judgment task. The skill holds the judgment; the code holds the checking. This is a
design ruling, but it is also why dao has no file-writing surface to secure.

## Findings

None. No security-relevant defect was found in dao during the 2026-08 pass.

Two adjacent design properties worth recording, since both would be security-relevant if
reversed:

- **`GateVerdict.out_of_family` travels with the verdict**, not with the gate
  (`domain/models.py`). A stored or forwarded verdict cannot lose the one fact that
  determines its weight — M1-H2 measured a same-family reviewer as adding nothing over a
  cold re-read, so a verdict that shed its provenance could later be mistaken for the
  stronger kind.
- **Audit thresholds live in a declared `POLICY` map**, surfaced by `dao policy`
  (`application/audit.py`). M2's finding: a threshold buried in code is a governance
  decision hidden from governance.

## Not covered

- dao does not verify corpus provenance. It trusts the CATALOG's own grounding labels.
- dao's substantive tests are heuristics. PARTIAL is a signal to look, not a proof.

## Reporting

Open an issue on the gnx repository. Pre-gen-0 component; no embargo channel.
