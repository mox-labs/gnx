# gnx

The catalog CLI — turns authored components into installable Claude Code plugins.

## What this is

gnx holds a catalog of **components** (a skill, an agent, a runnable capability — one per
directory) and projects them into **plugins**, which is the shape Claude Code installs.

Those are different granularities on purpose. A component is the unit of authorship; a
plugin is a bundle someone installs. Keeping them separate is what lets one component ship
in several plugins, and what lets a bundle be re-cut without touching the components in it.

The regrouping is **declared**, in `components/bundles.yaml`, not hardcoded in this tool.
Adding a plugin is a data change.

### Why the projection is committed

`plugins/` is generated output that is nevertheless checked in, and that is forced by the
platform rather than chosen: **no install-time build hook exists** in Claude Code — hooks
are session-lifecycle only. Nothing can run at install time to build a plugin, so the built
form has to be in the repository before publish.

That makes drift the real risk, which is why `gnx build --check` is a commit gate. It fails
if the committed projection does not match `components/`.

### What it is not

gnx does not install anything yet. The registry and installer surface — `gnx add`,
`gnx search`, `gnx validate` — is designed, not built. Today the tool projects and lists.

## Using it

```bash
gnx build          # project components/ → plugins/ + .claude-plugin/marketplace.json
gnx build --check  # verify the committed projection matches; non-zero on drift
gnx list           # the registered components
```

`gnx build` writes to a staging directory and swaps atomically, so an interrupted build
cannot leave a half-written plugin tree. It also prunes: a plugin removed from
`bundles.yaml` has its directory deleted rather than left behind as a stale install target.

## Install

gnx is the one package in this repository that publishes. Until a release:

```bash
uv tool install "gnx @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/gnx"
```
