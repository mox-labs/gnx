# Schedules: a mission that runs itself

A mission that should run every ten minutes, unattended, declares `schedule:`. `recon schedule install` projects it onto the operating system's own scheduler, and that scheduler starts each tick: there is no recon daemon. Each tick is one survey of the mission, held to the config that was installed, logged beside the mission. This page is the contract: the key, every command and flag, each default, and every refusal with its fix.

## Contents

- [When to use a schedule](#when-to-use-a-schedule)
- [The key](#the-key)
- [Installing, listing, removing](#installing-listing-removing)
- [What is projected](#what-is-projected)
- [A tick](#a-tick)
- [Credentials in a tick](#credentials-in-a-tick)
- [What a tick runs](#what-a-tick-runs)
- [The log, and status](#the-log-and-status)
- [Refusals and warnings](#refusals-and-warnings)
- [In a program](#in-a-program)

## When to use a schedule

Use one to snapshot a source on a cadence (an inbox, a release feed, an API) while nobody is there to run `recon survey`. Each tick writes an ordinary archive, so the archives of a schedule are read, queried and compared as any survey's are. Use a stream (`recon --skill -r streaming`) for a source that pushes or never ends; a schedule asks a source that answers and is done.

## The key

```yaml
schedule:
  every: 600      # seconds; required in the block
  missed: once    # once | skip
collectors:
  - name: inbox
    type: cli
    run: "..."
```

| Key | Holds | Default |
|---|---|---|
| `schedule` | the mission's schedule | none: the mission runs when it is told to |
| `schedule.every` | the seconds between ticks: at least 60, and whole minutes that divide a day (60, 120, 180, ..., 600, ..., 3600, 5400, 7200, ..., 86400), so the ticks fall on one daily grid every scheduler keeps; another value is a plan problem whose fix names the nearest two | required |
| `schedule.missed` | what ticks missed while the host was asleep or off become: `once`, one run when it is back (the missed ticks coalesced, never caught up one by one); `skip`, none | `once` |
| `deadline` | a scheduled mission's survey time, so a tick ends before the next is due | 0.8 × `every` unless set |

`recon survey <mission> --dry-run --json` reports each value and where it came from, in `provenance`: `schedule.every` (`mission`), `schedule.missed` (`mission`, or `recon` when left out) and `deadline` (`recon`, with a note, when it is the default). The ticks fall at `offset + k × every` from local midnight, where the offset is `hash(mission) mod every` in whole minutes: missions due at the same moment are spread over the interval, the same on every host. The grid is wall-clock time, as each scheduler keeps it: across a daylight-saving change, one day's ticks shift by the hour the clock does. How late a tick is, and so whether `missed: skip` skips it, is the seconds that passed since the instant its grid time named, in UTC, never the difference of two wall-clock readings.

## Installing, listing, removing

```
recon schedule install <mission> [--platform launchd|systemd|cron] [--json]
recon schedule list [--platform ...] [--json]
recon schedule remove <mission> [--platform ...] [--json]
recon schedule status [<mission>] [--platform ...] [--json]
```

`--platform` defaults to the host's scheduler: `launchd` on macOS; `systemd` on Linux where the user's systemd manager runs (`systemctl` on PATH and `XDG_RUNTIME_DIR` set); `cron` elsewhere. Every document says which, as `platform`.

`install` plans the mission (an invalid config exits 3), writes the scheduler's files and has the scheduler load them. It prints `recon.v1.schedule`: `mission`, `platform`, `label` (`recon.<8 hex of the project's path digest>.<mission>`, one schedule per mission of a project), `outcome`, `every`, `missed`, `offset_s`, `config_sha256` (the digest each tick holds the config to), `argv` (what each tick runs) and `files` (where the files are; `crontab` for cron). `outcome` is `installed`; `unchanged` when the same config is already installed, which changes and runs nothing; or `replaced` when a different one was. What it put in place is kept in `.recon/<mission>/schedule.json`.

`list` prints `recon.v1.schedules`: the schedules installed for this project's missions, read from the scheduler's own directory or crontab. `remove` unloads the schedule and removes its files (`outcome: removed`), or says there was none (`absent`); the mission, its archives and its log stay. `status` is described below.

## What is projected

| Platform | Files | Commands run |
|---|---|---|
| `launchd` | `~/Library/LaunchAgents/<label>.plist`: `ProgramArguments`, `WorkingDirectory`, `EnvironmentVariables` (`PATH`), `StartCalendarInterval`, `StandardOutPath` and `StandardErrorPath` `/dev/null` | `launchctl bootout gui/<uid>/<label>`, then `launchctl bootstrap gui/<uid> <plist>` |
| `systemd` | `$XDG_CONFIG_HOME/systemd/user/<label>.service` (`Type=oneshot`, `WorkingDirectory`, `Environment=PATH=...`, `ExecStart`, `StandardOutput=null`) and `<label>.timer` (`OnCalendar=` lines, `Persistent=true` for `missed: once`, `false` for `skip`, `AccuracySec=1s`) | `systemctl --user daemon-reload`, `enable <label>.timer`, `restart <label>.timer` |
| `cron` | an entry in recon's block of the user's crontab, between `# BEGIN recon schedules` and `# END recon schedules`; every line outside the block is kept as it was, and the block goes with its last entry | `crontab -l`, then `crontab -` |

Each tick runs `recon schedule tick <mission> --project <project> --config-sha256 <digest>`: recon by the absolute path of the installation that installed it, as an argument list (cron alone hands its line to `/bin/sh`; there every argument is quoted whole and `PATH` is set by `/usr/bin/env`), from the project's directory, with an explicit `PATH`: the installer's, kept to absolute directories that exist, that no other user can write (owned by you or by root, and neither group- nor world-writable) and that are not inside the project (no `bin/` of a checkout, no `node_modules/.bin`; no `.`, nothing relative). The files are written `0600`, and only into the user's own domain: no LaunchDaemon, no system unit, no `/etc/cron.d`, no sudo. They hold no secret and no `OTEL_EXPORTER_OTLP_HEADERS`: nothing of the environment but that `PATH`.

`missed` per platform: launchd coalesces calendar ticks missed in sleep into one on wake; systemd runs one missed tick when `Persistent=true`; cron runs none, whatever `missed: once` asks (a `missed_ignored` warning at install). Under `skip`, the tick itself declines a late start (below), so launchd's wake-up run is skipped too.

## A tick

`recon schedule tick` is what the scheduler starts; run by hand, it is one tick. In order:

1. **The config is the one installed.** Its sha256 must be `--config-sha256`: the digest of the very bytes the survey plans and runs, read once while the tick holds the mission, so a config swapped after an earlier read never runs. Another is refused, `stale` (exit 12), logged `skipped_by: config_changed`, and so is every tick after until `recon schedule install` runs again: a scheduled config runs its commands unattended, so a change to it is read before it runs.
2. **A late start under `missed: skip`.** A tick that starts more than 60 s after it was due (the grid's grain) runs no survey: `skipped_by: missed`, exit 0.
3. **One survey at a time.** The tick surveys the mission under its lock. Another survey of the mission running (a slow tick, a survey by hand) refuses it `busy` (exit 11), logged `skipped_by: busy`; `details.skips` counts the busy skips in a row, and from the second the error is an overrun: the survey takes longer than `every`, and the fix is to raise `every` or lower `deadline:`. The default `deadline:` of 0.8 × `every` bounds each survey so the next tick finds the mission free.
4. **The survey.** As `recon survey` runs it, plus two keys in its archive's `meta.yaml`: `scheduled_at`, when the tick was due (local time), and `late_s`, how late it started. Its exit code is the survey's. It prints `recon.v1.tick`: `mission`, `scheduled_at`, `late_s`, `skipped_by`, the `survey` document, `warnings`.

## Credentials in a tick

A tick's environment is the scheduler's: the installed `PATH`, and what the scheduler itself sets, never your shell's. A variable a credential names as `env:NAME`, set in your shell, is unset in a tick, and its requests go without it. The plan of a scheduled mission warns `env_unscheduled` for each such reference a source it reads makes, at its path (`catalog.<i>.auth.token`, an `mcp` source's `catalog.<i>.token` or `catalog.<i>.env.<NAME>`), even when the variable is set where you plan; `env_unset` is only for one unset there too.

Give a tick's credentials a form it resolves itself, the same grammar for every source kind:

```yaml
# in an api source: a token from a file only you can read
auth: {token: "file:mail.token", header: Authorization, prefix: "Bearer "}
# or every header from a command
auth: {headers_helper: "/usr/bin/security find-generic-password -w -s mail-headers"}
```

- `auth.token: file:PATH` reads the credential from a file, a relative path being relative to the mission's directory. The file must be readable by you alone (`chmod 600`): one its group or others can read is refused before anything is sent, `config`, with the `chmod` that fixes it.
- `auth.headers_helper` names a command, run once a survey within the source's `timeout`, whose standard output is a JSON object of the headers to send: a keychain read, a cloud CLI's token. Name it by an absolute path, since a tick's `PATH` is the installed one.
- An `mcp` source takes the same: `token: file:PATH` or `headers_helper` for a remote server, `env: {NAME: file:PATH}` for what a stdio server is given; or a stdio server that holds its own token.

Never put a secret in the config, or in the scheduler's files, which recon never does.

## What a tick runs

A tick runs the config that was installed, and the commands in it run as they are written. `npx` and `uvx` fetch the package they are given each time they start unless it names an exact version, so a stdio server or a `cli` command written `npx -y @scope/server` or `uvx some-server` runs, every tick, whatever was published last. The plan of a scheduled mission warns `package_unpinned` for each, with `got` the package; pin it, `npx -y @scope/server@1.4.2` or `uvx some-server==2025.4.7` (`uvx --from 'some-server==2025.4.7' its-command` when the command has another name), and install again.

## The log, and status

Each tick is one JSON line of `.recon/<mission>/schedule.log`, `0600`:

| Field | Holds |
|---|---|
| `scheduled` | when the tick was due: the latest grid time at or before it started, local time |
| `started`, `ended` | when it ran |
| `exit`, `kind` | its exit code and its kind (`ok`, or the error kind the code maps to) |
| `archive` | the survey's archive id; null when it wrote none |
| `skipped_by` | why it ran no survey: `busy`, `missed` or `config_changed`; null when it ran one |
| `late_s` | how late it started, in seconds |

The log is bounded: at 1 MiB it becomes `schedule.log.1`, replacing the one before, so a schedule's log never passes 2 MiB.

`recon schedule status [<mission>] --json` prints `recon.v1.schedules`, one entry a mission that has a schedule or an installed one: `installed` (its files are in place as `install` wrote them; one edited or removed by hand reads false), `config_changed` (the config differs from the one installed, so its ticks are refused; null when no install is recorded), `every`, `missed`, `next_at` (the next tick, local time), `last_tick` (the log's last line), `late_s` (the seconds since the tick due last, when it has not run; 0 when it has) and `skips` (the ticks skipped in a row at the log's end). Its `warnings` hold a `config_changed` for each mission whose config changed, and a `schedule_overrun` for each whose last two or more ticks were skipped busy.

## Refusals and warnings

| What | Kind, exit | Fix |
|---|---|---|
| `install` of a mission with no `schedule:` | `config` (3), at path `schedule`, `missing_key` | add `schedule: {every: 600}`, then install |
| an `every` no scheduler keeps (`every: 420`) | plan problem, `config` (3) | the fix names the nearest: `every: 360 or every: 480` |
| no such mission | `not_found` (4) | `recon init` it, or `recon schedule remove` a schedule whose mission is gone |
| this recon is not installed as a command | `usage` (2) | install recon as a command, then install the schedule with it |
| a path no scheduler file can hold (a line break; a `%` for cron) | `usage` (2) | move it, or choose another `--platform` |
| the scheduler refuses (`launchctl`, `systemctl`, `crontab` exit non-zero) | `run_failed` (8), its message quoted | fix what it says; the files are in place, so `install` again loads them |
| a tick's config is not the one installed | `stale` (12) | read the change, then `recon schedule install <mission>` |
| a tick while another survey holds the mission | `busy` (11), `details.skips` | let it run; from the second skip in a row, raise `every` or lower `deadline:` |
| `missed: once` under cron | `missed_ignored` warning | install for launchd or systemd, or write `missed: skip` |
| a credential named `env:NAME` in a scheduled mission | `env_unscheduled` warning in the plan, at the reference's path | name it as `file:PATH` (mode 0600), or `headers_helper` for a token, then install again |
| an `npx` or `uvx` package with no exact version, in a stdio server or a `cli` command | `package_unpinned` warning in the plan, at `catalog.<i>` or `collectors.<i>` | pin it, `pkg@<version>` (npx) or `pkg==<version>` (uvx), then install again |
| a config changed since install | `config_changed` warning in `status` | read it, then install again |
| two or more busy skips in a row | `schedule_overrun` warning in `status` | raise `every`, or lower `deadline:` |

## In a program

`recon.schedule_install(name, platform=None)`, `recon.schedule_list(platform=None)`, `recon.schedule_remove(name, platform=None)`, `recon.schedule_status(mission=None, platform=None)` and `recon.schedule_tick(name, config_sha256=...)` are the commands, each with `root=` for the project, returning `recon.ScheduleDocument`, `recon.SchedulesDocument` and `recon.TickDocument`. `recon.composition.root(schedulers=..., schedule_host=..., local=...)` takes the scheduler of each platform, what a tick runs, and the host's local time: a test's root gives a scheduler whose commands are recorded and never run.
