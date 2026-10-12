# Changelog

All notable changes to matrix. Versions follow semver; before 1.0 a minor may break.

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
