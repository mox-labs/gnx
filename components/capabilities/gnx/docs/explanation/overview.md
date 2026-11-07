---
title: gnx
summary: The catalog CLI that projects this repository's components into Claude Code plugins and checks the committed projection.
kind: capability
---

# gnx

## What it is

The tool that turns the components in this repository into the plugins Claude Code installs,
and fails a commit when the two disagree.

## The problem

You edit a skill under `components/skills/`. Claude Code doesn't install from there: it installs
from `plugins/`, a copy, committed to the repository. Nothing runs at install time to rebuild
the copy, so if you forget, users install yesterday's skill. Nothing tells you either, until
someone notices the fix they were promised is not in their install.

## How it works

A **component** is one skill, agent or capability, in its own directory under `components/`.
It is the unit you write.

A **plugin** is what a user installs: a bundle of components. One component can ship in
several plugins, and a bundle can be re-cut without touching its components.

The **bundles file**, `components/bundles.yaml`, declares which components go in which plugin.
Adding a plugin is a data change, not a code change.

The **projection** is what `gnx build` writes from those two: `plugins/` and
`.claude-plugin/marketplace.json`. It is committed because Claude Code has no install-time
build hook: the built form has to be in the repository before anyone installs it.

**Drift** is a committed projection that differs from a fresh build. `gnx build --check` fails
on it, and the pre-commit gate runs it.

## Try it

From the root of a clone:

```
$ uv --project components/capabilities run gnx list
┃ kind  ┃ type_url                    ┃ provides                                                   ┃
│ Skill │ gnx.dev.v1.intent-hardening │ intent.hardening, intent.adhikarana-protocol,              │
│       │                             │ intent.triguna-halt, intent.gradient                       │
│ Skill │ gnx.dev.v1.rational-inquiry │ inquiry.inference-validity, inquiry.fallacy-gate,          │
│       │                             │ inquiry.defeat-conditions                                  │
$ uv --project components/capabilities run gnx build --check
clean — 3 plugin(s); committed projection matches components/
```

Edit a projected skill, and the same check names the file and exits 1:

```
$ echo >> components/skills/rational-inquiry/SKILL.md
$ uv --project components/capabilities run gnx build --check
drift — 1 difference(s) vs the committed projection:
  changed: rational-inquiry/skills/rational-inquiry/SKILL.md
```

`gnx build` rewrites the projection, and the commit carries both.

## What it gives an agent

| What an agent can do | Why it can trust it | The command |
|---|---|---|
| See what the catalog holds | read from the manifests on disk, with any component filed under the wrong kind's directory flagged | `gnx list` |
| Know the plugins match their sources before committing | a fresh build is compared file by file with the committed tree | `gnx build --check` |
| Rebuild the plugins without leaving a broken tree | it builds into a staging directory and swaps; a plugin no longer declared is removed | `gnx build` |
| Catch a link that would break once installed | every relative link in a built plugin must resolve inside that plugin | `gnx build` |

## What it does not do

- **It installs nothing.** There is no `gnx add`, `gnx search` or `gnx validate`; users
  install plugins through Claude Code.
- **It works only in this repository.** It reads `./components` and writes `./plugins`, from the
  directory it runs in.
- **It projects skills and agents only.** A capability's Python package is installed with `uv`;
  only a skill a package carries (recon's) is projected.
- **It does not judge what it publishes.** Whatever `bundles.yaml` names is copied as written,
  scripts included, and nothing signs the result. See [SECURITY.md](../../SECURITY.md).
- **It has no `--skill` and no machine-readable output.**

## Next

- [README](../../README.md): running it, and the install line.
- `gnx --help`.
- [SECURITY.md](../../SECURITY.md): the write path, and what is not covered.
