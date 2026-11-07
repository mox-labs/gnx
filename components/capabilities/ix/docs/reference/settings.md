# Settings and telemetry

ix's settings are the `ix:` section of matrix's config tiers, later tiers winning:
`~/.matrix/config.yaml`, `~/.ix/config.yaml`, `./matrix.yaml`, `./ix.yaml`, then the files
`$MATRIX_CONFIG` and `$IX_CONFIG` name. They hold what varies by where ix runs, not by
experiment:

```yaml
ix:
  runs: ~/ix-runs       # where every run is kept; absent: .ix/runs beside each experiment
```

An unknown key is refused, by its path and file. The same read gives the `matrix:` section:
the observers of a run, and the named model runtimes (`matrix.model_runtimes`) a
`pydantic-ai` session's `model_runtime` names. A model runtime's own models are its config's:
a model-call subject's or a judge's `runtime_options.models` for hardline.

ix makes spans through the OpenTelemetry API, under the instrumentation scope `ix`:
`ix.plan`, `ix.run`, `ix.environment`, `ix.trial` and `ix.sensor`, with each reading as a
`gen_ai.evaluation.result` event and a failure's reason as `error.type`. The agent sessions inside a trial are matrix's spans. The
`ix` command installs the SDK, which comes with it, from the standard `OTEL_*` variables; in
an install that lacks it, every `--json` document carries a warning instead. A program that
imports ix configures OpenTelemetry itself, or calls `ix.configure_telemetry()`.

The `evaluator` sensor reads a trial's spans in this process, from the SDK's tracer
provider. A run with an evaluator that reads spans (`ToolCorrectness`, `MaxToolCalls`) puts
matrix's `otel` observer on itself, and uses the tracer provider installed, exporters and
all; when none is, it installs one with no exporter, so the spans stay in the process. That is
the one provider ix installs; a program that installs its own does so before it runs an
experiment, since OpenTelemetry lets a process install one. A tool
call's arguments are on its span only with `capture_content`
(`observers: [{type: otel, capture_content: true}]`), which `ArgumentCorrectness` reads.
Without the spans its reading fails and says why; it never reads as an agent that called no
tool.
