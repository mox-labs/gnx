# Security — gnx

gnx reads a catalog and writes a plugin tree. It runs no model, opens no socket, and holds no
credential. Its whole security surface is **what it writes, where, and on whose say-so** —
and the output it writes is a directory that other people install and execute.

Audience: anyone running `gnx build`, and anyone reviewing a `bundles.yaml` change.

## Blast radius

`gnx build` writes `plugins/` and `.claude-plugin/marketplace.json` under the repository
root. Nothing else. It executes nothing it reads.

But the output is a **plugin tree that other people install**, so a defect here is a supply
problem for them, not a correctness problem for you. That is the asymmetry worth keeping in
mind: gnx's blast radius is downstream of gnx.

## Trust boundaries

### 1. `bundles.yaml` → what gets published

The bundling declaration decides which components land in which plugin, and the projector
copies whatever it names. **A `bundles.yaml` change is a publishing decision**, and it is the
one file in the repository where a one-line edit changes what a stranger installs.

What constrains it: `gnx build --check` is a commit gate, so the committed projection cannot
silently diverge from `components/`. A component added to a bundle shows up as a projection
diff in the same commit.

What does not: nothing validates that a component *should* be published. There is no
maturity gate on projection — `bundles.yaml` is trusted completely.

### 2. Component content → the plugin tree

Component bodies are copied, not interpreted. gnx does not execute skills, agents, or scripts
it projects, and does not evaluate templates in them. A malicious component is copied
faithfully and becomes a malicious plugin — gnx is a conduit here and does not pretend to be
a filter.

`scripts/` inside a projected component is copied with its mode bits. An executable script in
a component is an executable script in the installed plugin.

### 3. The write path

`gnx build` stages into a temporary directory and swaps atomically, so an interrupted build
cannot leave a half-written plugin tree that someone then installs. It also **prunes**: a
plugin removed from `bundles.yaml` has its directory deleted rather than left behind as a
stale install target — a stale plugin directory is worse than a missing one, because it still
resolves.

Paths derive from bundle names and component directory names, both from the repository. gnx
does not accept an output path from the environment or a flag.

## Why the projection is committed at all

Claude Code has **no install-time build hook** — hooks are session-lifecycle only. Nothing can
run at install time to build a plugin from source, so the built form has to be in the
repository before publish.

That is a platform constraint, not a preference, and it is the reason the drift check exists:
generated output that is committed will drift from its source unless something fails when it
does.

## Findings

None. No security-relevant defect was found in gnx during the 2026-08 pass.

## Not covered

- gnx does not sign or verify anything it publishes. There is no attestation on a projected
  plugin, and no way for an installer to check that `plugins/` was produced by `gnx build`
  from the committed `components/` rather than edited by hand.
- No maturity or review gate stands between a component and publication.
- `gnx list` and `gnx build` read every component body; a pathological file (enormous, or a
  symlink loop) is a denial-of-service against the build, not against a consumer.

## Reporting

Open an issue on the gnx repository. Pre-gen-0 component; no embargo channel.
