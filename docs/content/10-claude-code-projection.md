# The Claude Code projection

> **Design register.** gnx is pre-build (NAMED — doctrine shaped, build starting). Present tense below states the design, not shipped behavior. What runs today: [status](/docs/status).

The Claude Code marketplace is not a runtime target — it is a projection surface. gnx writes to it at authoring time, commits the output, and enforces freshness in CI.

---

## The repo is the catalog and the marketplace at once

One tree holds both faces (§8a): the authored catalog and the Claude Code marketplace projected from it. The directory layout mirrors the apiVersion namespace, so where a component sits on disk *is* its vendor scope.

```
gnx/
├── api/                     # type_url → schema: data types, component configs, protocol configs
│   └── slick.dev/v1/…
├── components/              # vendor-neutral vocabulary — the 4 core kinds (slick.dev/v1)
│   ├── capabilities/ agents/ skills/ flows/
├── extensions/              # vendor-namespaced kinds + concrete vendor facade impls
│   └── claude-code/         #   hooks.claude.anthropic.com/v1, commands.…/v1
├── .claude-plugin/marketplace.json   # GENERATED (root — CC requires it here)
└── plugins/                # GENERATED (commit policy open) — Target-side projections
```

The split is the load-bearing line: **`api/` + `components/` + `extensions/` are authored; `marketplace.json` + `plugins/` are generated and committed.** A plugin references components by type_url; `gnx build` resolves them into a self-contained plugin dir, because Claude Code copies each subdir in isolation and bans traversal (below). Plugins pull from the catalog; they own nothing — which is the structural cure for cix's per-plugin duplication.

The `components/` ↔ `extensions/` boundary is not cosmetic. It is the enforcement surface for degeneration-watch #2: a vendor-specific field on a core schema or core component breaks the namespace-as-vendor-scope invariant and corrupts the registry's portability computation. Keeping vendor surfaces in `extensions/<target>/` is what keeps the core vendor-neutral *by structure*.

Adapter capability negotiation runs over this layout: each Target adapter declares the namespaces it supports (Claude Code: `slick.dev/*` + `*.claude.anthropic.com/*`), and `gnx build` projects the matching slice — core components plus that target's extensions — into the target's expected shape. Same catalog, namespace-filtered projection per Target. (How protocol transports are absorbed across this surface is doc 13.)

---

## Projection happens at authoring time

Claude Code's install mechanics determine the design, not the other way around.

When a user adds a marketplace via the Marketplace tab, Claude Code (CC) clones the entire repo. When a user installs a plugin, CC copies only the plugin subdirectory into a versioned cache. Two hard constraints follow:

1. **No install-time build hook exists.** There is no lifecycle event analogous to `npm postinstall` where gnx could generate files. Whatever lands in the plugin subdir at clone time is what CC sees.
2. **Path traversal out of a plugin dir is banned.** A plugin cannot read `../shared/` or anywhere above its subdir root. Symlinks within a marketplace dereference into copies at install. A plugin that symlinks to shared content gets a copy, not a reference.

The `directory`-source marketplace path (how yzavyas consumes his own marketplace locally) introduces a further ambiguity: symlink behavior for directory-source marketplaces is not specified, and the whole mechanism is Windows-hostile regardless.

The conclusion is not "we should use a different install mechanism." The conclusion is: **projection happens at authoring time; output is generated and (policy TBD, see open questions) committed.** `gnx build` writes the plugin dirs. `gnx build --check` runs in CI to verify the committed state is current.

---

## One source, two generated manifests

Claude Code's plugin resolution involves two manifest layers:

- `marketplace.json` at the repo root (under `.claude-plugin/`) — the discovery surface CC reads to enumerate available plugins
- `plugin.json` inside each plugin subdir — the install-time manifest CC uses when a user installs that plugin; version here is the cache key

These can drift. In cix they did: dual-manifest version drift was chronic. The versions could be in sync by convention, but convention fails under churn.

`gnx build` generates both from a single component/distribution source. Version lives in one place. The plugin.json entry wins silently in CC's resolution when they diverge. Making gnx the authoritative writer of both, from one input, makes "wins silently" irrelevant. There is nothing to silently win against.

`gnx build --check` re-generates and diffs. If the committed output doesn't match what build would produce, CI fails. This turns single-source-of-truth from a convention into a structural property.

An explicit bump to the source version = a release. The commit of the generated output is the release artifact.

![Projection mechanics](/diagrams/projection.svg)

---

## Self-contained plugin directories, no traversals

```
<repo-root>/
├── .claude-plugin/
│   └── marketplace.json          # generated by `gnx build`; discovery surface
├── plugins/
│   ├── slick/
│   │   └── plugin.json           # generated; cache key = version here
│   ├── aboot/
│   │   └── plugin.json
│   └── ci-scaffolds/
│       └── plugin.json
└── components/
    └── skills/
        └── <name>/
            └── manifest.yaml     # source (format leaning, §10.3); `gnx build` reads this
```

The on-disk manifest format isn't settled — `manifest.yaml` is the lean, not a decision (§10.3). The projection mechanics below hold regardless of what the source file is finally called.

Each plugin subdir is self-contained because it must be: CC's install copies the subdir in isolation. No `..` references resolve out of it.

The component source (`manifest.yaml`, the skill, tests) lives in `components/`. The `plugins/` tree is generated. Generated directories belong in `.gitignore` during development and are committed only via `gnx build` through CI or an explicit release step — or committed directly when the intent is "this is the shipped state." [open: exact policy on when generated output is committed vs gitignored during development]

---

## Commands are legacy

The ground truth is a direct citation from Claude Code's own documentation: **"use skills for new plugins."** Commands are not deprecated, but they are the old pattern. gnx's plugin projection generates skills, not commands, for any new catalog component.

The distinction matters for the `gnx component init [kind]` scaffold: the output template should default to skill structure, and should not generate a `commands/` entry unless the component explicitly targets legacy compatibility. This is not gnx's opinion — it is CC's stated direction.

---

## Three cix failure modes absorbed structurally

Three failure modes from cix that gnx absorbs structurally:

**Publish state in data, not directory renames.** cix used directory structure (moving things in/out of `archive/`, renaming paths) to represent publish lifecycle state. That breaks CI path references silently. gnx keeps lifecycle state in manifest data — a field, not a location.

**Validation first-class in the CLI.** cix delegated validation to bash scripts that lived adjacent to the tool but weren't the tool. `gnx validate` is a first-class command, not a sourced script. The registration gate calls it; CI calls it; the developer calls it. Same code path every time.

**Name things once.** Every rename in cix left stale CI paths, stale import paths, and stale documentation. The cost accumulated. In gnx, the plugin subdir name and the component namespace are set at `gnx component init` time and don't move. If the name is wrong, fix it before first registration — not after.

---

## Three standing degeneration conditions

Three standing conditions (established 2026-05-25):

**#1 — Manifest core accreting vendor semantics.** The `slick.dev/v1` core namespace must not grow Claude Code-specific fields. aboot is the sharpest test: it has a universal core (continuity artifacts, session lifecycle) and a vendor binding (`hooks.claude.anthropic.com/v1` for SessionStart/SessionEnd/PreCompact/Stop). The vendor binding is in a separate apiVersion. The core stays portable. If gnx's build logic starts depending on the presence of CC-specific fields to function, that's the signal.

**#2 — The CC Target adapter becoming de-facto spec. This is the live risk.** CC is currently the only Target that ships. When there is only one concrete Target, every projection decision gets made against that Target's behavior. Over time, those decisions accumulate as implicit assumptions about what a "Target" is. The second Target never ships because the first Target's assumptions are now embedded. The architecture becomes Claude-Code-specific in practice even though it was designed to be vendor-neutral. The mitigation is deliberate: treat the CC projection as a plugin of a Target interface, not as the Target interface itself. The TargetPort protocol in the hexagonal core is where this boundary lives — it must have a named interface and at least a stub second implementation before the CC implementation calculates its final shape.

**#3 — Accreditation encoding vendor trust policy.** gnx ships correctness (structure, port satisfaction, namespace scope); x.uma + geist-edge ship trust (FIS, autonomy ceilings). If accreditation records start carrying Claude Code-specific trust judgments — "this plugin is safe for CC's permission model" — the catalog becomes vendor-locked at the trust layer, which is harder to unwind than a field name.

---

## Open questions

- **When is generated output committed?** The doc describes committing the `gnx build` output as the release artifact. The exact workflow — whether `plugins/` is gitignored during feature development and committed only on release, or always committed — is not in the ground truth. This affects `gnx build --check` CI policy and the PR review experience.

- **The TargetPort stub second implementation.** Degeneration watch #2 names this as the structural mitigation. The ground truth does not name a second Target candidate or a timeline. Without a second Target, the watch condition has no tripwire beyond code review.

---

Comments most wanted on degeneration watch #2: the TargetPort stub second implementation is the structural mitigation, and it has no named candidate or tripwire yet.

