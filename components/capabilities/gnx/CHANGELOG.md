# Changelog — gnx

## 0.1.0 — unreleased

The first version. It has been in this repository since 2026-08-18 and has no release tag yet.

### What it holds

- `gnx list`: the components under `components/`, with each one's kind, type URL and what it
  provides, and any component whose declared kind disagrees with its directory.
- `gnx build`: projects the components into Claude Code plugins, `plugins/` and
  `.claude-plugin/marketplace.json`, as `components/bundles.yaml` declares. It builds into a
  staging directory and swaps, so an interrupted build leaves no half-written tree, and it
  removes the directory of a plugin no longer declared. Skills are copied whole, and every
  relative link in a built plugin must resolve inside that plugin.
- `gnx build --check`: exits 1 when the committed projection differs from a fresh build. It is
  a commit gate (`just projection`).

### Changed since it arrived

- **The help text and the package description say what ships** (2026-11-07): a CLI that lists
  the components and projects them into plugins. They named a registry and an agentic CLI,
  which are not built.
- **A copy of the repository's LICENSE ships in the package** (2026-11-07).
- **`gnx build` works in a linked git worktree** (2026-10-09). It staged under `.git`, which in
  a worktree is a file, and failed.
- **SECURITY.md** (2026-08-21): gnx runs no model and holds no credential, but it writes a
  plugin tree other people install, and `bundles.yaml` decides what that tree contains.
