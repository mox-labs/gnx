---
title: Install a plugin
section: guides
mode: how-to
status: shipped
register: public
fidelity: tarmac
---

# Install a plugin

This is the one gnx path that runs today. The plugins install through Claude Code's own marketplace mechanism — clone the catalog, register the marketplace, pick a plugin, enable it.

---

## Step 1 — Clone the catalog

Clone the catalog repository. Its root holds the marketplace manifest at `.claude-plugin/marketplace.json`:

```sh
git clone https://github.com/mox-labs/gnx.git
```

---

## Step 2 — Register the marketplace

Open `~/.claude/settings.json` and add an `extraKnownMarketplaces` entry pointing at your clone:

```json
{
  "extraKnownMarketplaces": {
    "gnx": {
      "source": { "source": "directory", "path": "/path/to/gnx" },
      "autoUpdate": true
    }
  }
}
```

The key — `"gnx"` here — is yours to choose; it becomes the marketplace identifier and the suffix in `enabledPlugins`: `intent-hardening@gnx`, `rational-inquiry@gnx`. Two source types work: a `directory` source reads a local path (used above), and a `github` source (`"source": "github", "repo": "<owner>/<repo>"`) clones a public repo once it is published.

---

## Step 3 — Pick a plugin

Two plugins are projected and installable today:

| Plugin | What it gives you |
|--------|-------------------|
| `intent-hardening` | Hardens a loose ask into something the catalog can act on — an adhikaraṇa-structured exchange that reports how far the intent can honestly sharpen |
| `rational-inquiry` | The inference-validity gate — tests whether a "therefore" holds against named defeat conditions |

The wider set — craft-rhetoric, ci-scaffolds, guild-arch, and more — is being ported into the catalog as components. Current installed versions live in **[what's real vs planned](/docs/status)**, so this page never drifts from that record.

---

## Step 4 — Enable it

In `~/.claude/settings.json`, add to `enabledPlugins`:

```json
{
  "enabledPlugins": {
    "intent-hardening@gnx": true
  }
}
```

Claude Code copies the plugin subdirectory into a versioned cache at `~/.claude/plugins/cache/gnx/intent-hardening/<version>/`. The plugin is self-contained — no build step, no traversal outside its directory. Restart Claude Code and it is active. Install several by adding each:

```json
{
  "enabledPlugins": {
    "intent-hardening@gnx": true,
    "rational-inquiry@gnx": true
  }
}
```

---

## Step 5 — Verify

Run `/plugin` in Claude Code and confirm the `gnx` marketplace and the plugin appear. If the marketplace fails to register, the `extraKnownMarketplaces` path is wrong, or `.claude-plugin/marketplace.json` is missing at the clone root.

---

## How Claude Code activates a plugin

Claude Code loads the plugin's skills — each a `SKILL.md` file. A plugin's `description` (in its `plugin.json`) is what Claude Code matches against to decide *when* to activate it; it loads the matching skill and drives the behaviour from there. The skill is the interface — not a command, not a menu. This is the shipped end of a general rule: an agent reaches a component through the skill that travels inside it.

---

## Where to go next

- **[How components work](/docs/how-components-work)** — the grammar behind what you installed: the kinds, the manifest, `provides` and `requires`.
- **[Compose components](/docs/compose-components)** — assemble installed pieces into a pipeline from their declared surfaces alone.
