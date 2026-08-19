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
/plugin install rational-inquiry@gnx
```

The first registers the marketplace — Claude Code clones the repository and reads
`.claude-plugin/marketplace.json` from its root. The second installs one plugin. Both were
run against this repository to confirm they work as written.

The same thing from a terminal:

```
claude plugin marketplace add mox-labs/gnx
claude plugin install rational-inquiry@gnx
claude plugin list
```

## What you can install

Three plugins: `intent-hardening`, `rational-inquiry`, and `recon`. [The catalog](/catalog)
lists what each contains.

That is a deliberately small set — see [what gnx is](/docs/what-is-gnx) for why 69
components sit in `incubator/` rather than here.

## Using it

Skills activate on their own when a request matches what they describe — there is nothing
to invoke. Agents are dispatched by Claude Code when a task fits their role, and can be
asked for by name.

One honest caveat: **activation is not guaranteed.** A live measurement of this catalog
found skills that did not fire on trigger phrases quoted in their own descriptions — one
reason most components are in `incubator/` rather than here. If a skill does not engage,
naming it directly works.

## If it does not appear

Run `/plugin` and check the `gnx` marketplace is listed. If registration failed, the repo
was unreachable or `.claude-plugin/marketplace.json` was not found at its root.

Installed plugins are copied to `~/.claude/plugins/cache/gnx/<plugin>/<version>/`. The
version is the cache key, so an update lands when the version is bumped.

## Removing

```
claude plugin uninstall rational-inquiry@gnx
claude plugin marketplace remove gnx
```
