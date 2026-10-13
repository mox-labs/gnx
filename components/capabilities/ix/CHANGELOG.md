# Changelog — ix

## 0.3.0 — 2026-10-13

ix is now the experiment model and nothing else: one file that names its genre, and commands that check it, plan it and register it. The agent-eval harness is removed. **ix does not run an experiment in this release**; there is no `ix run`.

### Removed (breaking)

- **The agent-eval harness**: the commands `ix run`, `ix results`, `ix compare`, `ix experiment` and `ix lab`; the engines (native and Inspect AI), the sensors' implementations, the simulated runtime, the results store, the config tiers (`ix.yaml`, `~/.ix/config.yaml`), and its documents (`ix.v1.results`, `ix.v1.results-list`, `ix.v1.comparison`, `ix.v1.labs`, `ix.v1.init`).
- The dependencies on `matrix`, `hardline` and `claude-agent-sdk`, the `inspect` and `deepeval` extras, and the `matrix.extensions` entry point.
- The earlier `experiment.yaml` format (`sensors:`/`sensor:`, `trials:`, `engine:`, `subjects:` as a list). A file in that format is refused key by key like any other unknown key; nothing converts it.

### Added

- **The experiment file** (`ix.v1.experiment`): `name`, `question`, `rationale`, `genre`, `subjects` (name to `{kind, options}`), `control`, `probes` (a list, or `{source: directory, path}`), `factors`, `environment`, `sensors`, `metrics`, `primary`, `threshold`, `comparison`, `margin`, `guardrails`, `repeats`, `seed`, `spend`. Unknown keys are refused at the top of the file and beside every `kind`.
- **Genres as data** (`ix.v1.genre`): `evaluate`, `compare`, `characterize` and `regress` (which extends `compare`). A genre declares `roles`, the factor policy, `must_set`, `comparisons` (each with the sentence it claims), `design` (`repeats: {default, min}`, `order`, `calibration`, `estimator`, and a note on its standing), the analysis, `verdict_form`, the report sections and `guidance`. Extension is narrowing only, one level deep; a child cannot add or change an analysis parameter.
- **Kinds as data** (`ix.v1.kind`): subject kinds `process`, `http`, `agent`; probe kinds `prompt`, `http-load`, `inspect-task`; sensors `timing`, `memory`, `activation`, `function-test`, `usage`, `judge`; environment kinds `none`, `process`, `compose`. Each declares its options (types, required), effects, the facets it offers and needs, and for a sensor its metrics (with whether a model judges them) and the probe truth it reads. They are declarations: nothing here starts or measures anything.
- **Entry points `ix.genre` and `ix.kind`**: a package registers a package of YAML files; ix registers its own the same way. A lab keeps `genres/` and `kinds/` beside its experiment. Two files declaring one name are both refused.
- **`ix validate`**: every problem in one batch, each with `path`, `kind`, `message`, `fix`, `allowed` and `got`. It checks the experiment against its genre; every kind's options (`kind_unknown`, `option_unknown`, `option_missing`, `option_invalid`); that probe kinds fit subject kinds (`kind_unfit`); that every named metric has exactly one provider (`metric_unprovided`, `metric_ambiguous`, `metric_mismatch`); probe truth no sensor reads (`truth_unclaimed`); and spend (`spend_unbounded`, `spend_over_ceiling`). `complete: false` when the file did not load and the later checks did not run.
- **A model-judged metric never decides.** `judged` is declared by the sensor and is not a key of the experiment file. A judged metric is refused as `primary` and as a guardrail in any genre with a verdict (`judged_gate`).
- **Two reductions**: `statistic` reduces one trial's reading (`p99` of a histogram); `estimator` combines the trials (`mean`; `rate` for a boolean), defaulted by the genre.
- **`ix plan`**: the question, the hypothesis rendered from the genre (`Decides:`), the guardrails and the form of the verdict; then subjects and roles, the trial arithmetic, the estimated duration where probe kinds declare one, the order, the sensors, the effects (from the kinds, plus any the author added), the spend, the registration status, and every design value with its source. The digest is over meaning: stable against a comment, key order, a default written out, reworded prose and a field a later ix adds.
- **`ix register`**: writes `<file-stem>.registration.json` with the design as resolved (what the file sets and what the genre supplies) and the digests of the probe set, the experiment and the plan. A changed experiment is refused (exit 3). `--amend --reason "<why>"` replaces the record and keeps the superseded one, with the reason and the deviations accepted, inside it. A stored record tolerates fields this ix does not know.
- **`ix deviations`**: lists each frozen value that changed; exits 9 when any did.
- **`ix genres`, `ix kinds`, `ix --skill`**: the installed genres and kinds, and a document generated from them that teaches how to write an experiment, with a minimal example per built-in genre.
- **One error envelope** (`ix.v1.error`) for every failing command, usage errors included, always with `problems` and `deviations`. One exit-code table (`ix.EXIT_CODES`): 0, 1, 2 usage, 3 config, 4 not found, 9 deviations.
- **A declared library API** (`ix.__all__`): `load`, `validate`, `resolve`, `plan`, `register`, `deviations`, `genres`, `kinds`, `skill`, `error_document`, the documents they return, `Experiment`, `Genre`, `Kind`, the errors, `PROBLEM_KINDS` and `EXIT_CODES`. Each function takes a path or an `Experiment` built in memory.
- Examples: `examples/proxy-streaming` (a service comparison) and `examples/catalog-routing` (an agent evaluation with probes in files).
- Tests that scan imports: `ix.domain` and `ix.application` import only from an allowlist; only `ix.composition` imports an outbound adapter; the command line imports only `ix`; nothing imports a host.

### Changed

- Every document id is `ix.v1.*`.
- SECURITY.md describes what ix now reads and writes.
