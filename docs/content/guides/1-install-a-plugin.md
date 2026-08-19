---
title: Install a plugin
section: guides
mode: how-to
status: shipped
register: public
---

# Install a plugin

Two commands, in Claude Code:

```
/plugin marketplace add mox-labs/gnx
/plugin install dao@gnx
```

The first registers the marketplace — Claude Code clones the repository and reads
`.claude-plugin/marketplace.json` from its root. The second installs one plugin. Both were
run against this repository to confirm they work as written.

The same thing from a terminal:

```
claude plugin marketplace add mox-labs/gnx
claude plugin install dao@gnx
claude plugin list
```

## What you can install

Eleven plugins. [The catalog](/catalog) lists them with what each contains, and lists every
component with the plugins that ship it.

Swap `dao` for any plugin name: `guild-arch`, `craft-research`, `craft-rhetoric`,
`ci-scaffolds`, `antifragile`, `craft-extensions`, `craft-evals`, `intent-hardening`,
`rational-inquiry`, `recon`.

## Using it

Skills activate on their own when a request matches what they describe — there is nothing
to invoke. Agents are dispatched by Claude Code when a task fits their role, and can be
asked for by name.

One honest caveat: **activation is not guaranteed.** A measurement of this catalog found
that a skill whose description lists a trigger phrase does not always fire on it. The
`dao` skill, for instance, reliably activates on "set up a dao" and not on "set up the
harness for this project", which its own description names as a trigger. If a skill does
not engage, naming it directly works.

## If it does not appear

Run `/plugin` and check the `gnx` marketplace is listed. If registration failed, the repo
was unreachable or `.claude-plugin/marketplace.json` was not found at its root.

Installed plugins are copied to `~/.claude/plugins/cache/gnx/<plugin>/<version>/`. The
version is the cache key, so an update lands when the version is bumped.

## Removing

```
claude plugin uninstall dao@gnx
claude plugin marketplace remove gnx
```
