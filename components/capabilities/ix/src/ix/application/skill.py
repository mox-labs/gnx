"""The document ``ix --skill`` prints: how to write, check, plan and register an experiment.

The genre and kind sections are generated from the installed records, so a third party's
genre or kind is taught the same way as ix's own, and what the document says must be set is
what ``validate`` enforces. The worked examples are minimal (they restate no default) and
the test suite validates and plans each one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ix.domain import schema
from ix.domain.errors import EXIT_CODES
from ix.domain.experiment import ESTIMATORS, STATISTICS
from ix.domain.kind import EFFECTS, KIND_FAMILIES, NOTE_PREFIX
from ix.domain.plan import VERDICTS
from ix.domain.problem import PROBLEM_KINDS

if TYPE_CHECKING:
    from ix.domain.genre import Genre, Genres
    from ix.domain.kind import Kind, Kinds

#: The commands ix has, in the order an author uses them.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("ix validate <path>", "every problem at once, each with path, kind, message, fix"),
    ("ix plan <path>", "the decision a run would equip and what it would cost; runs nothing"),
    ("ix register <path>", "freeze the design beside the file, before any run"),
    ("ix deviations <path>", "what changed since registration; exit 9 when anything did"),
    ("ix genres", "the genres installed, and any genre file ix refused"),
    ("ix kinds", "the subject kinds, probe kinds, sensors and environment kinds installed"),
)

_HEAD = """\
# ix: writing and checking an experiment

ix answers a question about a system with an experiment. You write one YAML file; ix checks
it, shows the decision a run would equip and what it would cost, and freezes the design
before anything runs. **ix has no `run` command yet**: it checks, plans and registers.

## The loop

```
COMMANDS
```

`<path>` is the experiment file, or a directory holding `experiment.yaml`. Every command
takes `--json`. Fix every problem `validate` reports, read the plan (it states the decision
first), then register.

## Choose the genre first

A genre is the kind of decision the answer must equip. Pick it by the decision, not by the
system under test.
"""

_FILE = """\
## The experiment file

```yaml
name: <one-token-name>
question: <what you want to know, one sentence>
rationale: <why you expect what you expect; optional>
genre: <genre>
subjects:                      # a mapping: name -> {kind, that kind's options}
  <name>: {kind: <subject kind>, <option>: <value>}
control: <subject name>        # only where the genre has a control; default: the first
probes:                        # a list of probes, or one source
  - {kind: <probe kind>, <option>: <value>, truth: {<key>: <value>}}
factors:                       # conditions varied on purpose; every combination is a cell
  <name>: [<level>, <level>]
environment: {kind: <environment kind>}
primary: {metric: <metric>, statistic: <statistic>, estimator: <estimator>}
threshold: {op: ">=", value: 0.9}   # where the genre decides against a threshold
comparison: <comparison>            # where the genre compares subjects
margin: 5%                          # 5% relative, 10pp percentage points, or a bare number
guardrails:                         # metrics that must also hold: a margin or a threshold
  - {metric: <metric>, margin: 5%}
metrics: [<metric>]                 # further metrics to measure and report
sensors:                            # only to give a sensor options, or to choose between two
  - {kind: <sensor>, <option>: <value>}
repeats: <n>
seed: <n>
spend: {ceiling: 5.00, per_trial: 0.02, currency: USD}
```

Only `name`, `question`, `genre`, `subjects` and `probes` are always required; the genre
says what else is, and supplies the rest. Leave out what you do not mean to change: the plan
shows every value and whether it came from the experiment, the genre or ix.

- **Every key is checked.** An unknown key is refused, at the top of the file and beside
  every `kind`: a kind declares its options, and `validate` reports `option_unknown`,
  `option_missing` and `option_invalid`. A key starting with `NOTE` is a note and is ignored.
- **The hypothesis is rendered** from the genre, the metric and the margin or threshold, so
  you do not write one. The plan prints it as `Decides:`.
- **Metrics come from sensors.** You name a metric; ix selects the one sensor that provides
  it and that the subjects and probes can feed. No sensor: `metric_unprovided`. Two:
  `metric_ambiguous`, settled by listing one under `sensors:`.
- **A model-judged metric never decides.** Whether a metric is judged is declared by its
  sensor. A judged metric is refused as `primary` and as a guardrail in any genre with a
  verdict (`judged_gate`); it can still be reported under `metrics:`.
- **Two reductions.** `statistic` reduces one trial's reading to a number; `estimator`
  combines the trials. By the shape of a reading:
SHAPES
  With no `statistic`, ix takes the first listed. With no `estimator`, a boolean combines as
  `rate` and anything else as the genre's estimator.
- **Placeholders.** In an inline probe, `{name}` is a factor, or an option every subject
  sets; anything else is `unbound_placeholder`.
- **Probes from files.** `probes: {source: directory, path: probes}` reads one `.md` file
  per probe: the body is the prompt, the frontmatter is the probe's truth (reference data a
  sensor measures against; it never reaches the subject). A truth key no sensor in use
  reads is `truth_unclaimed`. Sources: SOURCES.
- **Effects** come from the kinds. `effects:` beside a `kind` adds to them and cannot remove
  one. A run that spends money needs `spend.ceiling` (`spend_unbounded`).

## Kinds

Subjects and probes must fit: a probe kind needs facets, and every subject's kind must offer
them (`kind_unfit`). A sensor needs facets from the subjects or from what the probes leave
behind. Required options are marked `*`.

KINDS

Effects: EFFECTS

## Registration

`ix register <path>` writes `<file-stem>.registration.json` beside the experiment. It holds
the design as resolved (what you chose and what the genre supplied: comparison, margin,
threshold, guardrails, control, repeats, seed, order, calibration, estimators, the analysis
and its parameters, the sensors), and the digests of the probe set, the experiment and the
plan you read.

Digests are over meaning: a comment, key order or a default written out is not a change. A
reworded `question` or `rationale` changes the experiment's digest and not the plan's.

`ix deviations <path>` lists what differs from the registration and exits 9 when anything
does. `ix register` on a changed experiment is refused (exit 3) and the record is kept. To
replace it on purpose: `ix register <path> --amend --reason "<why>"`, which keeps the
superseded record and the reason inside the new one.

## Output and exit codes

`--json` prints one document on stdout with a `schema` field: `ix.v1.validation`,
`ix.v1.plan`, `ix.v1.registered`, `ix.v1.deviations`, `ix.v1.genres`, `ix.v1.kinds`.

A command that fails prints one envelope on stderr, as one JSON line under `--json`:

```json
{"schema": "ix.v1.error",
 "error": {"kind": "...", "message": "...", "fix": "...", "problems": [], "deviations": []}}
```

`error.kind` is `config`, `conflict`, `not_found` or `failure`. `problems` and `deviations`
are always present. `ix validate` on an invalid file and `ix deviations` that finds any are
not failures: each prints its own document on stdout and exits non-zero.

A problem is `{path, kind, message, fix, allowed, got, source}`: `allowed` lists the legal
values where the set is closed, `got` is what was written. A validation with
`"complete": false` stopped at the file's shape; fix those problems and validate again.

| Exit | Meaning |
|------|---------|
EXITS

## Problem kinds

PROBLEMS

Tolerate a kind you do not know: the set grows.
"""

#: A minimal example per built-in genre, by short name. None restates a default; the test
#: suite validates and plans each one as written.
EXAMPLES: dict[str, str] = {
    "evaluate": """\
name: skill-routing
question: Does the review skill fire for a real ask and stay quiet for a decoy?
genre: evaluate
subjects:
  catalog: {kind: agent, max_turns: 1, plugins: [plugins/review]}
probes:
  - id: real-ask
    kind: prompt
    prompt: Review this pull request for security problems.
    truth: {expectation: must_trigger, expected_skill: security-review}
  - id: decoy
    kind: prompt
    prompt: What is the capital of France?
    truth: {expectation: should_not_trigger, expected_skill: security-review}
primary: {metric: activated_correctly}
threshold: {op: ">=", value: 0.9}
spend: {ceiling: 2.00, per_trial: 0.05}
""",
    "compare": """\
name: proxy-streaming
question: Does streaming keep time to first byte without costing memory?
genre: compare
subjects:
  buffered:  {kind: process, command: [./proxy, --buffered], url: "http://127.0.0.1:8080"}
  streaming: {kind: process, command: [./proxy, --streaming], url: "http://127.0.0.1:8080"}
probes:
  - kind: http-load
    target: "{url}/blobs/64MiB"
    load_model: open
    rate: 80/s
    duration: 60s
primary: {metric: ttfb, statistic: p99}
comparison: non-inferiority
margin: 5%
guardrails:
  - {metric: memory_peak, margin: 0%}
""",
    "characterize": """\
name: proxy-memory-curve
question: How does peak memory grow with artifact size and request rate?
genre: characterize
subjects:
  proxy: {kind: process, command: [./proxy], url: "http://127.0.0.1:8080"}
factors:
  size: [1MiB, 64MiB, 1GiB]
  rate: [10/s, 80/s, 640/s]
probes:
  - kind: http-load
    target: "{url}/blobs/{size}"
    load_model: open
    rate: "{rate}"
    duration: 60s
primary: {metric: memory_peak, estimator: max}
""",
    "regress": """\
name: proxy-gate
question: May this build ship against the last release?
genre: regress
subjects:
  baseline:  {kind: process, command: [./build/release/proxy], url: "http://127.0.0.1:8080"}
  candidate: {kind: process, command: [./build/candidate/proxy], url: "http://127.0.0.1:8080"}
probes:
  - kind: http-load
    target: "{url}/blobs/64MiB"
    load_model: open
    rate: 80/s
    duration: 60s
primary: {metric: ttfb, statistic: p99}
margin: 5%
""",
}


def _count(low: int, high: int | None) -> str:
    if high == low:
        return str(low)
    return f"{low} or more" if high is None else f"{low} to {high}"


def _genre_section(genre: Genre) -> str:
    label = schema.short(genre.name)
    roles = ", ".join(f"{role.name} ({_count(role.min, role.max)})" for role in genre.roles)
    design = genre.design
    supplied = [
        f"repeats {design.repeats.default} (at least {design.repeats.min})",
        f"order {design.order}",
        f"estimator {design.estimator}",
    ]
    if design.calibration != "none":
        supplied.append(f"calibration {design.calibration} (the control against itself)")
    if genre.default_comparison is not None:
        supplied.append(f"comparison {genre.default_comparison}")
    lines = [
        f"### {label}"
        + (f" (narrows {schema.short(genre.extends)})" if genre.extends else "")
        + (f"  `{genre.name}`" if label == genre.name else ""),
        "",
        f"*{genre.question}*",
        "",
        f"- Subjects: {roles}.",
        f"- Factors: {genre.factors}.",
        f"- You must set: {', '.join(genre.author_must_set) or 'only the always-required keys'}.",
    ]
    if genre.comparisons:
        lines.append("- Comparisons:")
        lines += [f"  - `{name}`: {form}" for name, form in genre.comparisons.items()]
    else:
        lines.append(f"- Decides: {genre.hypothesis_form}")
        lines.append("- Comparisons: none; do not set `comparison`.")
    lines += [
        f"- Verdict: {VERDICTS[genre.verdict_form]}.",
        f"- The genre supplies: {'; '.join(supplied)}. {design.note.capitalize()}.",
        "",
        genre.guidance.rstrip(),
    ]
    example = EXAMPLES.get(label)
    if example is not None:
        lines += ["", "```yaml", example.rstrip(), "```"]
    return "\n".join(lines)


def _kind_line(kind: Kind) -> str:
    options = ", ".join(
        f"`{name}`{'*' if option.required else ''}"
        + (f" ({'|'.join(option.allowed)})" if option.allowed else f" ({option.type})")
        for name, option in kind.options.items()
    )
    parts = [f"- **`{schema.short(kind.name)}`**: {kind.summary}"]
    parts.append(f"  options: {options or 'none'}")
    facets = []
    if kind.needs:
        facets.append(f"needs {', '.join(kind.needs)}")
    if kind.offers:
        facets.append(f"offers {', '.join(kind.offers)}")
    if kind.effects:
        facets.append(f"effects {', '.join(kind.effects)}")
    if kind.truth:
        facets.append(f"reads truth {', '.join(kind.truth)}")
    if facets:
        parts.append(f"  {'; '.join(facets)}")
    for metric in kind.metrics:
        traits = [metric.shape, f"{metric.direction} is better"]
        if metric.unit:
            traits.insert(1, metric.unit)
        if metric.judged:
            traits.append("JUDGED by a model")
        parts.append(f"  metric `{metric.name}` ({', '.join(traits)}): {metric.description}")
    return "\n".join(parts)


def _kinds_section(kinds: Kinds) -> str:
    titles = {
        "subject": "Subject kinds (`subjects.<name>.kind`)",
        "probe": "Probe kinds (`probes[].kind`)",
        "sensor": "Sensors (selected by metric; `sensors[].kind` to name one)",
        "environment": "Environment kinds (`environment.kind`)",
    }
    blocks = []
    for family in KIND_FAMILIES:
        lines = "\n".join(_kind_line(kind) for kind in kinds.of(family)) or "- (none installed)"
        blocks.append(f"### {titles[family]}\n\n{lines}")
    return "\n\n".join(blocks)


def render(genres: Genres, kinds: Kinds, probe_sources: list[str]) -> str:
    """The skill document for the installed genres, kinds and probe sources."""
    width = max(len(command) for command, _ in COMMANDS)
    commands = "\n".join(f"{command:<{width}}   # {what}" for command, what in COMMANDS)
    shapes = "\n".join(
        f"  - `{shape}`: statistic {', '.join(STATISTICS[shape])}; "
        f"estimator {', '.join(ESTIMATORS[shape])}"
        for shape in STATISTICS
    )
    body = (
        _FILE.replace("SHAPES", shapes)
        .replace("NOTE", NOTE_PREFIX)
        .replace("SOURCES", ", ".join(f"`{name}`" for name in probe_sources) or "(none)")
        .replace("KINDS", _kinds_section(kinds))
        .replace("EFFECTS", "; ".join(f"`{name}` {what}" for name, what in EFFECTS.items()) + ".")
        .replace("EXITS", "\n".join(f"| {code} | {what} |" for code, what in EXIT_CODES.items()))
        .replace("PROBLEMS", ", ".join(f"`{kind}`" for kind in sorted(PROBLEM_KINDS)) + ".")
    )
    sections = "\n\n".join(_genre_section(genre) for genre in genres.genres.values())
    return f"{_HEAD.replace('COMMANDS', commands)}\n{sections}\n\n{body}"
