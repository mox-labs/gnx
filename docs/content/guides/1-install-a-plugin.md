---
title: Install a plugin
section: guides
mode: how-to
status: shipped
register: public
fidelity: tarmac
---

# Install a plugin

**This runs today.** No gnx CLI required — the marketplace mechanism is Claude Code's own feature; gnx supplies the content. The cix plugin family installs now. `gnx init`, `gnx add`, and `gnx build` are designed but unbuilt — they do not appear in these steps.

---

## Step 1 — Add the marketplace

Open `~/.claude/settings.json` and add an `extraKnownMarketplaces` entry pointing at a local clone of the catalog:

```json
{
  "extraKnownMarketplaces": {
    "cix": {
      "source": { "source": "directory", "path": "/path/to/cix" },
      "autoUpdate": true
    }
  }
}
```

The key (`"cix"`) becomes the marketplace identifier — Claude Code uses it as the namespace suffix in `enabledPlugins`: `"craft-rhetoric@cix"`, `"ci-scaffolds@cix"`, and so on.

Two source types work. A `directory` source reads a local path — the verified path today, pointing at a clone of the repo. A `github` source (`"source": "github", "repo": "<owner>/<repo>"`) clones a public repo by the same mechanism, once the repo is published. The example uses a directory source, which runs now.

---

## Step 2 — See what's available

The cix marketplace ships these plugins today:

| Plugin | Version | What it gives you |
|--------|---------|-------------------|
| `craft-rhetoric` | 0.3.0 | Comprehension, rhetoric, explanation — docs, tutorials, discourse, diagrams |
| `ci-scaffolds` | 0.6.0 | Collaboration scaffolds — claim verification, decision frameworks, mastery-oriented review |
| `guild-arch` | 0.2.0 | Architectural reasoning — design review, threat modeling, security-by-design |
| `antifragile` | 0.1.0 | ACES boundary review — adaptability, composability, extensibility checks |
| `craft-extensions` | 0.1.0 | Lexicon extension — vocabulary and concept growth |
| `craft-evals` | 0.3.0 | Evaluation — activation suites, methodology rubrics |
| `craft-research` | 0.3.0 | Research synthesis — literature analysis, evidence-grounded claims |
| `recon` | 0.7.0 | Reconnaissance — surface a codebase or system before you act on it |

Versions are the installed (`plugin.json`) versions — the ones Claude Code caches and resolves. These are the catalog's first cognitive extensions.

---

## Step 3 — Install one

In `~/.claude/settings.json`, add to `enabledPlugins`:

```json
{
  "enabledPlugins": {
    "craft-rhetoric@cix": true
  }
}
```

Claude Code copies the plugin subdirectory into a versioned cache at `~/.claude/plugins/cache/cix/craft-rhetoric/0.3.0/`. The plugin is self-contained — no build step, no traversal outside its directory. Restart Claude Code and the plugin is active.

Install several from the same marketplace by adding each:

```json
{
  "enabledPlugins": {
    "craft-rhetoric@cix": true,
    "ci-scaffolds@cix": true,
    "guild-arch@cix": true
  }
}
```

---

## Claude Code drives a plugin through its skills

Claude Code loads the plugin's skills — for craft-rhetoric, `rhetoric`, `discourse`, `voicing`, `evaluating`, and others. Each is a SKILL.md file Claude reads at activation.

A plugin's `description` (in `plugin.json`) is what Claude Code matches against to decide *when* to activate it — craft-rhetoric's is *"Crafts content that teaches and persuades… Use when: user asks to 'write docs', 'explain this', 'create a tutorial'…"*. Claude loads the skill and drives the behavior from it. The skill is the interface — not a command, not a menu. Every plugin in the catalog follows that pattern.

---

## What does not run yet

```
gnx add craft-rhetoric      # planned — gnx CLI not built
gnx init                    # planned — project genesis not built
gnx build                   # planned — projection CLI not built
gnx search                  # planned — discovery CLI not built
```

The gnx CLI is designed. Until it ships, the `extraKnownMarketplaces` entry in `settings.json` is the install path — and it's all you need to install what exists today.

---

## Where to go next

- **[How components work](/docs/how-components-work)** — the grammar behind what you installed: four kinds, the manifest, `provides` and `requires`.
- **[Compose components](/docs/compose-components)** — assemble installed pieces into a pipeline from their declared surfaces alone.
