"""The document ``ix --skill`` prints: how to write, check, plan and register an experiment.

The genre sections are generated from the catalogue, so an installed third-party genre is
taught the same way as ix's own. The worked examples are checked by the test suite: each one
validates and plans.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ix.domain import schema
from ix.domain.experiment import STATISTICS
from ix.domain.problem import PROBLEM_KINDS

if TYPE_CHECKING:
    from ix.domain.genre import Catalogue, Genre

_HEAD = """\
# ix: writing and checking an experiment

ix answers a question about a system with an experiment. You write one YAML file; ix checks
it, shows what a run would do, and freezes the decision before anything runs.

## The loop

```
ix validate <path> --json     # every problem at once, each with path, kind, message, fix
ix plan <path> --json         # what a run would do: runs nothing, spends nothing
ix register <path> --json     # freeze the decision parameters beside the file
ix register <path> --check    # what has changed since registration
ix genres --json              # the genres installed, and any genre file ix refused
```

`<path>` is the experiment file, or a directory holding `experiment.yaml`. Fix every problem
`validate` reports, then read the plan before asking for a run: it names the control, counts
the trials and lists the effects.

These commands read the experiment model described here. `ix run`, `ix results`,
`ix compare`, `ix experiment ...` and `ix lab ...` still drive the earlier agent-eval
harness, which reads its own `experiment.yaml` format (`sensors:`, `trials:`, `engine:`).
`ix validate` says so when it is given one of those files.

## Choose the genre first

A genre is the kind of decision the answer must equip. Pick it by the decision, not by the
system under test.
"""

_FILE = """\
## The experiment file

```yaml
name: <one-token-name>
question: <what you want to know, one sentence>
hypothesis: <what you expect, in a sentence that could turn out false>
genre: <genre>
subjects:                      # the variants under test, by name
  <name>: {kind: <subject kind>, <that kind's options>, effects: [<what starting it does>]}
control: <subject name>        # only where the genre has a control; default: the first
probes:                        # the stimulus: inline, or a source
  - {kind: <probe kind>, <that kind's options>}
factors:                       # sweep axes; every combination is a cell
  <name>: [<level>, <level>]
environment: {provider: none}
metrics:                       # every metric a decision reads is declared
  - {name: <metric>, unit: <unit>, direction: higher|lower|neither, shape: <shape>, judged: false}
primary: {metric: <metric>, statistic: <statistic>}
bar: {op: ">=", value: 0.9}    # where the genre decides against a bar
rule: <rule>                   # where the genre decides between subjects
margin: 5%                     # 5% relative, 10pp percentage points, or a bare number
guards:                        # metrics that must also not get worse
  - {metric: <metric>, margin: 5%}
repeats: 5
seed: 1
spend: {ceiling: 5.00, per_trial: 0.02, currency: USD}
```

Only `name`, `question`, `genre`, `subjects` and `probes` are always required; the genre
says what else is. An unknown key is refused, so a typo cannot pass silently.

- **Subjects and probes bind a kind.** The keys beside `kind` are that kind's own options.
  In an inline probe, `{name}` refers to a factor or to an option every subject sets.
- **Probes from files:** `probes: {source: directory, path: probes}` reads one `.md` file
  per probe: frontmatter is the probe's truth, the body is the prompt.
- **Metrics:** shapes and their statistics are
SHAPES
  With no `statistic`, ix reads the first one listed for the shape.
- **A model-judged metric never decides a gate.** Declare a metric a model scores as
  `judged: true`. It is refused as `primary` and as a guard in any genre with a verdict.
- **Effects** are what the file declares under `effects:`; the plan lists them. State what
  starts a process, reaches the network, runs tools or spends money.
- **Spend:** with `per_trial` and `ceiling`, validate refuses a plan estimated over the
  ceiling.

## Registration

`ix register` writes `<file-stem>.registration.json` beside the experiment: the digests of
the file and of the probe set, the genre, the primary metric, the margin, the repeats, the
seed and the spend ceiling. Register once the plan is right and before any run. A change
after that is a deviation; `ix register <path> --check` lists them, and `ix register` on a
changed experiment is refused with exit 3 so the earlier record is never silently replaced.

## Output and exit codes

`--json` prints one document on stdout with a `schema` field: `ix.v2.validation`,
`ix.v2.plan`, `ix.v1.registered`, `ix.v1.deviations`, `ix.v1.genres`. With `--json`, an
error is one JSON line on stderr: `{"error": {"kind", "message", "fix"}}`.

| Exit | Meaning |
|------|---------|
| 0 | done; for `validate`, the experiment is valid |
| 2 | usage: a bad flag or argument |
| 3 | config: the experiment is invalid, or `register` met a changed experiment |
| 4 | not found: no experiment file, or no registration to check |

## Problem kinds

KINDS
"""

#: A worked example per built-in genre, by short name. Each validates and plans as written.
EXAMPLES: dict[str, str] = {
    "evaluate": """\
name: search-slo
question: Does the search endpoint answer within 300 ms at the 95th percentile?
genre: evaluate
subjects:
  search: {kind: http, base_url: "http://127.0.0.1:8080", effects: ["network to 127.0.0.1:8080"]}
probes:
  - {kind: http-load, target: "{base_url}/search?q=foo", load_model: open, rate: 20/s, duration: 2m}
metrics:
  - {name: latency, unit: ms, direction: lower, shape: histogram}
primary: {metric: latency, statistic: p95}
bar: {op: "<", value: 300ms}
""",
    "compare": """\
name: skill-description
question: Does the rewritten description route better than the current one?
hypothesis: rewritten is better than current on activated_correctly
genre: compare
subjects:
  current:   {kind: agent, plugins: [plugins/current], effects: [spends money, runs tools]}
  rewritten: {kind: agent, plugins: [plugins/rewritten], effects: [spends money, runs tools]}
probes:
  - {id: real-ask, kind: prompt, prompt: "Review this pull request for security problems."}
  - {id: decoy, kind: prompt, prompt: "What is the capital of France?"}
metrics:
  - {name: activated_correctly, direction: higher, shape: boolean}
primary: {metric: activated_correctly}
rule: superiority
spend: {ceiling: 5.00, per_trial: 0.02}
""",
    "characterise": """\
name: proxy-memory-curve
question: How does peak memory grow with artifact size and concurrency?
genre: characterise
subjects:
  proxy: {kind: process, command: [./proxy], effects: [starts process ./proxy]}
factors:
  size: [1MiB, 64MiB, 1GiB]
  concurrency: [1, 8, 64]
probes:
  - kind: http-load
    target: "http://127.0.0.1:8080/blobs/{size}"
    load_model: open
    rate: "{concurrency}0/s"
    duration: 60s
metrics:
  - {name: memory_peak, unit: bytes, direction: lower, shape: scalar}
primary: {metric: memory_peak, statistic: max}
""",
    "regress": """\
name: proxy-gate
question: May this build ship against the last release?
genre: regress
subjects:
  baseline:  {kind: process, command: [./build/release/proxy], effects: [starts process]}
  candidate: {kind: process, command: [./build/candidate/proxy], effects: [starts process]}
probes:
  - kind: http-load
    target: "http://127.0.0.1:8080/blobs/64MiB"
    load_model: open
    rate: 80/s
    duration: 60s
metrics:
  - {name: ttfb, unit: ms, direction: lower, shape: histogram}
primary: {metric: ttfb, statistic: p99}
margin: 5%
""",
}


def _genre_section(genre: Genre) -> str:
    label = schema.short(genre.name)
    roles = ", ".join(
        f"{role.name} ({role.min}"
        + ("" if role.max == role.min else f" to {role.max}" if role.max else " or more")
        + ")"
        for role in genre.subjects
    )
    defaults = genre.defaults
    supplied = [
        f"repeats {defaults.repeats.value} (at least {defaults.min_repeats.value})",
        "subjects interleaved" if defaults.interleave.value else "subjects in order",
    ]
    if defaults.aa.value:
        supplied.append("an A/A calibration on the control")
    if defaults.rule is not None:
        supplied.append(f"rule {defaults.rule.value}")
    lines = [
        f"### {label}" + (f" (narrows {schema.short(genre.extends)})" if genre.extends else ""),
        "",
        f"*{genre.question}*",
        "",
        f"- Subjects: {roles}.",
        f"- Factors: {genre.factors}.",
        f"- You must set: {', '.join(genre.requires) or 'only the always-required keys'}.",
        f"- Rules: {', '.join(genre.rules) or 'none; do not set `rule`'}.",
        f"- Verdict: {'holds, fails or inconclusive' if genre.gated else 'none; estimates only'}.",
        f"- ix supplies: {'; '.join(supplied)}. These defaults are provisional.",
        "",
        genre.skill.rstrip(),
    ]
    example = EXAMPLES.get(label)
    if example is not None:
        lines += ["", "```yaml", example.rstrip(), "```"]
    return "\n".join(lines)


def render(catalogue: Catalogue) -> str:
    """The skill document for the genres in ``catalogue``."""
    shapes = "\n".join(
        f"  `{shape}`: {', '.join(statistics) or 'no statistic; it cannot decide'}."
        for shape, statistics in STATISTICS.items()
    )
    kinds = ", ".join(f"`{kind}`" for kind in sorted(PROBLEM_KINDS))
    sections = "\n\n".join(_genre_section(genre) for genre in catalogue.genres.values())
    body = _FILE.replace("SHAPES", shapes).replace("KINDS", kinds + ".")
    return f"{_HEAD}\n{sections}\n\n{body}"
