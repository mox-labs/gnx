# Changelog — ix

## 0.3.0 — 2026-10-12

ix gains a model of the experiment itself: one file that names its genre, and commands that check it, plan it and freeze its decision before anything runs. Nothing in this release starts a subject. The agent-eval harness (`ix run`, `ix results`, `ix compare`, `ix experiment`, `ix lab`) is unchanged and keeps its own file format.

### Added

- **The experiment file** (`ix.v2.experiment`): `name`, `question`, `hypothesis`, `genre`, `subjects` (a map of name to a binding with a `kind`), `control`, `probes` (inline, or `{source: directory, path: ...}`), `factors`, `environment`, `metrics`, `primary`, `bar`, `rule`, `margin`, `guards`, `repeats`, `seed`, `spend`. Unknown keys are refused.
- **Genres as data** (`ix.v1.genre`): `evaluate`, `compare`, `characterise` and `regress` (which extends `compare`) are YAML records in `ix/genres/`. A genre declares subject roles and their cardinality, whether factors are forbidden, allowed or required, the fields an experiment must set, the rules it may name, design defaults, the analysis by id, the verdict kind and the report sections. Every default carries a note; the built-in ones are marked provisional pending the experimentation canon.
- **Extension by narrowing.** A genre extends at most one other, one level deep, and may only tighten it. A file that loosens its parent, changes the analysis or the verdict kind, or adds a role is refused with one `genre_narrowing` problem per violation; a deeper chain is refused with `genre_depth`.
- **The `ix.genre` entry point.** A package registers a package of genre YAML files there, and ix registers its own the same way; the entry point is read and never called. A `genres/` directory beside an experiment adds that lab's genres. Two files declaring one name are both refused; a file that cannot be read does not affect the others.
- **`ix validate <path>`**: every problem in one batch, each with `path`, `kind`, `message` and `fix`; exit 3 when invalid. `ix.PROBLEM_KINDS` is the vocabulary.
- **A model-judged metric never decides a gate.** A metric declared `judged: true` is refused as `primary` and as a guard in any genre with a verdict (`judged_gate`).
- **`ix plan <path>`**: the subjects with their roles and which is the control, the cells, the probes, the repeats, the A/A calibration pass, the order rule and seed, the declared effects, the estimated spend, and every design value with its source (the experiment, the genre, or ix). Pure: the same file gives the same digest.
- **`ix register <path>`**: writes `<file-stem>.registration.json` beside the experiment with the file digest, the probe-set digest, the genre, the primary metric, the margin, the repeats, the seed and the spend ceiling. `--check` lists deviations from it and writes nothing. Registering an experiment that has changed since is refused (kind `conflict`, exit 3) and the record is kept.
- **`ix genres`**: the catalogue, and each refused genre file with its problems.
- **`ix --skill`**: how to write an experiment for each installed genre, with an example per built-in genre that the test suite validates and plans.
- **A declared library API**: `ix.__all__` names `load`, `validate`, `resolve`, `plan`, `register`, `deviations`, `catalogue`, `skill`, the records and the errors. The new commands call only these. `import ix` loads no host runtime.
- Example experiments: `examples/proxy-streaming` (a service comparison over a 3 x 3 sweep) and `examples/catalog-routing` (an agent evaluation with probes in files).
- A test that scans imports: `ix.domain` and `ix.application` import no adapter, no host and no I/O or tool library. Three modules of the harness's domain are listed as exemptions, and the list can only shrink.

### Changed

- `ix --help` lists the experiment-model commands beside the harness commands and says which is which.
- Where a new document shares a name with a harness document, it takes a new version instead of a second shape under one id: `ix.v2.experiment`, `ix.v2.validation`, `ix.v2.plan`. The harness still prints `ix.v1.*`.

### Notes

- `ix run` reads neither the experiment model nor a registration. `ix validate` given a harness `experiment.yaml` reports `earlier_format` and names `ix experiment validate`.
- The options a subject, probe or environment gives beside `kind` are stored and not checked: no kind adapter is attached. The plan's effects are those the file declares, and the plan says so.
