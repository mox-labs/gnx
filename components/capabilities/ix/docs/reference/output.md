# Output and exit codes

`--json` prints one document with a `schema` field: `ix.v1.validation`, `ix.v1.plan`,
`ix.v1.registered`, `ix.v1.deviations`, `ix.v1.report`, `ix.v1.status` (`ix status`, `ix stop`,
`ix run --detach`), `ix.v1.runs`, `ix.v1.genres`, `ix.v1.kinds`; `ix mutants sample`,
`map` and `read` print `ix.v1.mutation-sample`, `ix.v1.mutation-maps` and
`ix.v1.mutants-reading`. `ix events --json` prints one `matrix.v1.event` document a line.

A command that fails prints one envelope on stderr, as one JSON line under `--json`:

```json
{"schema": "ix.v1.error", "error": {"kind": "config", "message": "...", "fix": "...", "complete": true, "details": {}, "problems": [], "deviations": []}}
```

A problem is `{path, kind, message, fix, allowed, got, source}`. `allowed` lists the legal
values where the set is closed, and `got` is what was written, so a caller need not parse
the message. `"complete": false`, in a validation or an error, means checking stopped at
the file's shape: fix those problems and check again. Warnings travel inside the document
(`warnings` in `ix.v1.validation` and `ix.v1.plan`). `ix validate` on an invalid file,
`ix deviations` that finds any, and `ix run` or `ix report` of a run that fails, is
inconclusive, was not held to a registration it moved away from, was answered by a stand-in
where a gate was asked for, was stopped, could not finish or is still running are not
failures: each prints its own document on stdout and
exits non-zero. The envelope and the shared exit codes are every gnx capability's; ix's own
kinds take 10 and up. `error.kind` is one of `unknown`, `usage`, `config`, `conflict`,
`not_found`, `unregistered` or `interrupted`. `details` holds the wrong value (`value`) and
where it is (`path`) when there is one, and a matrix error's own details when ix carries one
over.

## Exit codes

| Exit | Kind | Meaning |
|------|------|---------|
| 0 | `ok` | done; for validate, the experiment is valid; for run and report, the verdict holds, or there is no verdict to give and none was asked for (exploratory or dry); for status, events and stop, the run completed, which is no verdict |
| 1 | `unknown` | a failure ix did not classify |
| 2 | `usage` | a bad flag or argument |
| 3 | `config` | the experiment is invalid, or it cannot be carried out here as written (a kind with no adapter, a subject that cannot start, an unsupported analysis): read the message and the fix |
| 4 | `not_found` | no experiment file, no registration, or no run |
| 7 | `conflict` | register met an experiment that changed since its registration: restore it, or amend with a reason |
| 8 | `run_failed` | the harness could not finish the run; the record is kept |
| 10 | `fails` | the verdict is fails |
| 11 | `inconclusive` | the verdict is inconclusive |
| 12 | `run_interrupted` | the run stopped before it finished; the record is kept and it gives no verdict |
| 13 | `running` | the run has not finished yet (from `ix stop`: it is still releasing what it started); ask `ix status` again, or `ix events --follow` until it ends |
| 14 | `deviations` | the experiment differs from its registration (ix deviations); a run of it gave no gate result |
| 15 | `unregistered` | ix run needs a registration, or --exploratory |
| 16 | `simulated` | a stand-in (a mock runtime) answered a run held to its registration: it measured the harness, not the subject, and gives no gate result |
| 130 | `interrupted` | the command itself was interrupted (Ctrl-C) before it finished: run it again |

`run_interrupted` (12) is a run that was stopped and reported: `ix run` stopped by Ctrl-C
or SIGTERM exits 12 with its report. `interrupted` (130) is any other command
cut short by Ctrl-C, which prints the error envelope instead.
