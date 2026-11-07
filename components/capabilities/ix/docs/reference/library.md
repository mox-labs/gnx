# The library

The commands are an adapter over the library; `import ix` gives the same thing. Each
function takes a path or an `ix.Experiment` built in memory.

```python
import ix

result = ix.validate("examples/proxy-streaming")  # ix.Validation: valid, complete, problems
plan = ix.plan("examples/proxy-streaming")  # ix.Plan; raises ix.InvalidExperimentError
design = ix.resolve("examples/proxy-streaming")  # ix.Design: every value, with its origin
genres, kinds = ix.genres(), ix.kinds()  # ix.Genres, ix.Kinds

slower = "examples/delay-server/slower.yaml"
ix.register(slower)  # ix.Registered; ix.RegistrationConflictError if the file changed since
found = ix.deviations(slower)  # ix.Deviations; ix.NotFoundError if it is not registered
report = ix.run(slower)  # ix.Report; blocks until the run ends
again = ix.report(report.location)  # the same report, from the record
listed = ix.runs(slower)  # ix.Runs

started = ix.start(slower)  # ix.Status, once its own process has opened the record
ix.status(started.location).status  # "running", then how it ended
for event in ix.events(started.location, follow=True):  # until the run ends
    print(event["name"])
ix.stop(started.location)  # ix.Status; a run that ended is left as it is

experiment = ix.load("examples/proxy-streaming")  # ix.Experiment
ix.plan(experiment.model_copy(update={"repeats": 9})).trials.total  # 243
```

`ix.__all__` is the declared API: `__version__`, `load`, `validate`, `resolve`, `plan`,
`register`, `deviations`, `run`, `start`, `status`, `events`, `stop`, `report`, `runs`,
`genres`, `kinds`, `skill`, `configure_telemetry`, `error_document`; `mutants`, the module of
mutation testing's provisional functions ([Testing a test suite](../how-to/test-a-test-suite.md)); the documents they
return (`Validation`, `Problem`, `Design`, `Plan`, `Registered`, `Deviations`, `Report`,
`Status`, `Runs`, `Genres`, `Kinds`); the three records an author or an extension writes
(`Experiment`, `Genre`, `Kind`, the union of a kind's four family records); the errors
(`IxError`, `ConfigError`, `NotFoundError`, `InvalidExperimentError`,
`RegistrationConflictError`, `UnregisteredError`, `UsageError`); and the tables
`PROBLEM_KINDS`, `ERROR_KINDS`, `EXIT_CODES`, `EXIT_MEANINGS` and `EXIT_NEXT`. Every part of
those records (`SubjectConfig`, `Margin`, `Decision`, `Interval`, ...) is exported from
`ix.domain`, the one public path to them; the modules inside it are not public. What an
extension implements and is handed (the ports, the needs, `Target`, `SubjectError`,
`HarnessError`) is `ix.extension`'s.

`ix.run` returns a report for every run that started, however it ended: `Report.status` is
`completed`, `interrupted` or `failed`, and `Report.exit_code` is what a command should exit
with. It raises only for what stops a run before it starts. It runs its own event loop, so
call it from synchronous code; `on_event=` receives progress events, which are telemetry.

Importing `ix` loads no host runtime: matrix is imported when a function is first called.
Only ix's composition root and its `matrix_*` adapters name matrix.

A program that wires its own adapters builds an `ix.composition.Root` over its own registry
(`Root(registry=...)`; `tracer=` takes where ix's spans go), and `with
ix.composition.using(root):` runs the library functions and the `ix` command on it.

## What a caller may rely on

Before 1.0 a minor release may change anything, said in the changelog. Beyond the declared
API above, these are behaviours a caller may rely on, each pinned by a test, and what is
not:

| behaviour | contract | pinned by |
|---|---|---|
| a run is a directory `<experiment directory>/.ix/runs/<run id>/` (or under the settings' `runs`), its id `<experiment>-<UTC time>-<4 hex>` | yes | `test_run_path.py::TestWhatCallersRelyOn` |
| its tables `ix.v1.run`, `ix.v1.trial`, `ix.v1.bindings`, `ix.v1.interaction`, `ix.v1.reading`, each `<kind>.jsonl`, beside matrix's (`matrix.v1.session`, `matrix.v1.ledger`, `run.json`, whose layout is matrix's contract) | yes; a row gains fields only by addition | the same |
| the events a run hands `on_event`: `ix.environment.up` (`kind`, `services`), `ix.trial.start` (`seq`, `total`, `subject`, `role`, `probe`, `cell`), `ix.trial.end` (`seq`, `total`, `subject`, `role`, `failure`, `duration_ms`), `ix.sense.end` (`readings`, `interactions`) | no: an event is telemetry, and telemetry names are not contract (owner ruling 2026-10-22); an event may be dropped, so nothing reads a result from one | |
| the span names `ix.plan`, `ix.run`, `ix.environment`, `ix.trial`, `ix.sensor` under the instrumentation scope `ix`, span attributes, and the GenAI conventions' event and attribute names (`gen_ai.evaluation.result`, `error.type`) | no: telemetry names are not contract (owner ruling 2026-10-22); the conventions are Development status, and ix follows them as they move | |
| the exit kinds and codes in [the table](output.md#exit-codes) | yes; the shared ones are every gnx capability's | `test_ix_cli.py::test_the_agreed_kinds_keep_their_agreed_codes`, `test_the_exit_codes_of_a_run_are_in_the_one_table` |
| the human text of `ix` commands | no: read `--json` | |
