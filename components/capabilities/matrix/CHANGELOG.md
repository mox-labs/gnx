# Changelog

All notable changes to matrix. Versions follow semver; before 1.0 a minor may break.

## 0.4.0

matrix is components wired by kind, a registry and config. The flow grammar is gone, a row is
the record itself, and rows land in the store while a component is still running.

### Removed

- **The flow grammar**: `Flow`, `Member`, `Port`, bindings, topics, `after`, `many`,
  `optional`, `compile_flow`, `CompiledFlow`, levels, and `Container.compile`. Nothing
  replaces them one for one; see "Added".
- **`Artifact`**, and with it `Construct.values`, `Construct.by_type`, `Construct.topics` and
  `Run.outputs`. A table's rows are the records.
- **`episode`**, on `Run`, `RunContext` and rows. Repeating is the caller's.
- `ConstructStore.save`. A run is written as it goes, not afterwards.
- `Run.flow`, `inputs.context`, and the `flow` field of run events.

### Added

- **Components wired by kind.** A component declares `requires` and `provides` as sets of
  kinds (type URLs) and implements `run(inputs, ctx)`. `wire(components, inputs=...)` infers
  the wiring and returns a `Wiring`: a component depends on the provider of each kind it
  requires, each kind has one provider, and the kinds the caller supplies are run inputs. It
  reports every problem at once: a kind nobody provides, two providers, a cycle.
- **`ctx.provide(kind, row)`** hands a row over while the component runs. When it returns the
  row is validated, in the record, and durable in the store. A component may also return rows,
  as before.
- **Readiness scheduling.** A component starts when the providers of what it requires have
  finished; unrelated components run side by side.
- **The ledger.** Provenance is a table of its own, kind `matrix.v1.ledger` (`LedgerEntry`:
  `seq`, `kind`, `row`, `component`, `time`). An entry points at its row by `(kind, row)` and
  is written first, so a kill leaves at worst an entry with no row, which a reader reports in
  `Construct.torn`.
- **A store that is written as the run goes.** `ConstructStore.open(run) -> RunWriter`,
  `RunWriter.append(kind, row)`, `RunWriter.finish(run)`; `Executor.run(..., store=)`. A run
  can be read while it is running and after it was killed: `describe` reports `running`, or
  `interrupted` when the writing process is gone.
- **`StoreError`**, error kind `store` (exit 1). A store that cannot keep a row, or finish a
  run, fails the run.
- **`Container.wire`** builds `{name, type, config}` entries through the registry and wires
  them; `matrix.components` and `matrix.inputs` carry them in config, and `matrix check` wires
  them.
- **`matrix.v1.ref`** (`Ref`): a row that stands for a value too large to be one.
- `Run.rows`, `Run.document`, `Construct.provenance`, `Construct.kinds`, `Construct.restore`,
  `Inputs.one`, `Wiring.order`.

### Changed

- **`Component.run` takes `(inputs, ctx)`**, and `Inputs` maps each required kind to a tuple
  of its rows.
- **Renamed, member to component**: `Run.members` is `Run.components`; `Limits.members` and
  `MemberLimits` are `Limits.components` and `ComponentLimits`; `MemberStatus` is
  `ComponentStatus`; `RunContext.member`, `Event.member` and `RunError.member` are
  `.component`; failure and release records say `component`; the events are
  `matrix.component.start`, `.stopping`, `.end` and `.late`.
- `matrix.artifact.append` is `matrix.row.append`, with fields `kind`, `row`, `seq`.
  `matrix.run.start` carries `components`; `matrix.run.end` carries `rows`.
- `Limits.concurrency` defaults to `None` (no cap); it was 1.
- The record keeps its own copy of each row, taken when the row is handed over.
- `matrix.v1.agent-step`: `inputs` maps placeholders to the kinds it requires; the reply is
  provided under the kind `response` names (two agent steps in one run each name their own);
  `output` is a kind.
- Opening a run id that exists is a `StoreError`; it was a `ConfigError` from `save`.
- On disk, a table file is named for its kind and each line is the row itself; `run.json`
  gains `components` and `releases` and drops `flow` and `episode`. Records written by 0.3 or
  earlier are not readable.
- OTel: `matrix.member.*` instruments and attributes are `matrix.component.*`;
  `matrix.run.duration` no longer has a `matrix.flow` attribute.

## 0.3.0

A run now always ends, and always cleans up; and it can be watched without being slowed.

### Added

- **One stop path.** A member is stopped the same way whatever the cause (its deadline,
  another member's failure, cancellation, SIGTERM, Ctrl-C): stop request, grace, cancel.
  `ctx.stopping` is the request; a member that returns within `grace_s` keeps what it returns.
- **Deferred releases.** `ctx.defer(fn)` registers something to undo. Every release is called
  when the run ends, last registered first, each bounded by `cleanup_s`, whether the run
  completed, failed or was cancelled. One that raises or overruns is in `Run.releases`.
- **Terminal states.** `Run.members` records how each member ended: `completed`, `failed`,
  `cancelled` or `skipped`, with the reason. A member that swallows cancellation is abandoned
  after `cleanup_s` and marked `abandoned`.
- **Cancelled runs.** Cancelling the task that awaits `Executor.run` raises `RunCancelled`
  (a `CancelledError`) carrying the partial `Run`, after the members stopped and the releases
  ran. `Run.status` may be `cancelled`.
- **Signals.** While a run is in flight, a SIGTERM or SIGINT that nothing else handles
  cancels it. `Executor(signals=False)` opts out.
- **`Limits`**: `deadline_s` per member, `Limits.members` (`MemberLimits`) to override it by
  alias, `grace_s`, `cleanup_s`, and `on_failure: "fast" | "isolate"`.
- **`matrix.process.spawn`** starts a child in its own process group and defers killing the
  group.
- **Component telemetry.** `ctx.emit`, `ctx.measure` and `ctx.span`.
- **Observers.** `console` (progress on stderr) and `jsonl` (a file or stderr); `otel` records
  metrics as well as spans. `matrix.observers` accepts `{type, ...options}`.
- `Event.member`, `Event.document()`, `EventPolicy`, `Run.events_dropped`.

### Changed

- **A failure stops the run by default.** With `on_failure="fast"` the first failure asks
  every running member to stop and skips the rest; before, the rest of the level ran to its
  end. `on_failure="isolate"` skips only the members that depend on the failed one.
- **Events are delivered from a bounded queue**, not on the emitting task. An observer can no
  longer stall a run; under load events are dropped and counted.
- **Event names are namespaced**: `run.start` is `matrix.run.start`, and likewise
  `matrix.run.end`, `matrix.member.start`, `matrix.member.end`, `matrix.artifact.append`,
  `matrix.agent.start`, `matrix.agent.end`. `matrix.member.end` reports the member's terminal
  state in `status` (it was `ok` or an error kind), and the error kind in `kind`.
- A member past its deadline gets a stop request and a grace before it is cancelled.
- `RunContext` is no longer a frozen dataclass.
- `RecordingObserver` keeps its last 10 000 events rather than all of them.
- An invalid flow input is refused before the run starts, so no events are emitted for it.
- matrix depends on `rich`. The `otel` extra needs opentelemetry 1.23 or later.

### Removed

- `Limits.member_timeout_s`: use `deadline_s`.
- The `EVENTS` tuple in `matrix.domain.observer`.
