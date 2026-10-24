# Errors, problems and warnings: every kind

recon classifies everything that goes wrong. A command that fails prints a `recon.v1.error` envelope on stderr and exits with its kind's code; a config it cannot use is a `config` error whose `problems` each name a path, a kind and a fix; a run that goes on despite something is a `warnings` list of the same problem records in its document. This page lists every kind. `recon --skill` has the exit-code table, what each error kind means and what to do next.

## Error kinds

`error.kind` is one of `unknown`, `usage`, `config`, `not_found`, `transient`, `auth`, `conflict`, `run_failed`, `store`, `query`, `busy`, `stale`, `interrupted`. Each has its own exit code; act on the code, then on the error's `fix`.

## Problem kinds

A problem stops the command: the plan is invalid until it is fixed. A problem's `kind` is one of `yaml_syntax`, `wrong_shape`, `unknown_key`, `missing_key`, `invalid_value`, `name_unknown`, `name_taken`, `unresolved`.

## Warning kinds

A warning is a problem record in a document's `warnings` list (`recon.v1.plan`, `recon.v1.survey`, and every other document that has one): it does not stop the command, and its `fix` says what to do if the run should not have gone that way. A warning's `kind` is one of `env_unset`, `plugin_broken`, `otel_unavailable`, `telemetry_failed`, `extract_missing`, `table_unreadable`, `archive_unfinished`, `extract_unresolved`, `records_not_objects`, `captures_unmatched`, `result_empty`, `events_dropped`, `telemetry_stuck`, `archive_abandoned`, `records_dropped`, `source_stuck`, `retain_unset`, `windows_many`, `paginate_pages`, `paginate_loop`, `paginate_empty`, `deadline_short`, `deletes_withheld`, `changes_failed`, `config_changed`, `missed_ignored`, `schedule_overrun`, `mission_exposed`, `env_unscheduled`, `package_unpinned`.

## A kind not listed here

Both sets grow. A reader handles an error kind it does not know by its exit code, and a problem or warning kind it does not know by its `message` and `fix`.
