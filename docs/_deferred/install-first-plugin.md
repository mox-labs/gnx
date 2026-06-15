---
title: Install a plugin today
section: start
mode: how-to
status: shipped
register: public
fidelity: tarmac
---

# Install a plugin today

**Status: shipped.** These steps run now. No gnx CLI required — the marketplace mechanism is Claude Code's own feature; gnx supplies the content.

craft-rhetoric (v0.3.0) and the cix plugin family are installable today. `gnx init`, `gnx add`, and `gnx build` are designed but do not run yet — they do not appear here.

---

## Step 1 — Add the marketplace

Open `~/.claude/settings.json`. Add an `extraKnownMarketplaces` entry:

```json
{
  "extraKnownMarketplaces": {
    "cix": {
      "source": {
        "source": "directory",
        "path": "/path/to/cix"
      },
      "autoUpdate": true
    }
  }
}
```

The key (`"cix"`) becomes the marketplace identifier. Claude Code uses it as the namespace suffix in `enabledPlugins` — `"craft-rhetoric@cix"`, `"ci-scaffolds@cix"`, and so on.

Two source types work. A `"directory"` source reads a local path — this is how cix is consumed today, pointing at a local clone of the repo. A `"github"` source (`"source": "github", "repo": "<owner>/<repo>"`) clones a public repo — the planned public distribution path. The example above uses a directory source against a local clone, which runs now.

---

## Step 2 — Browse what's available

The cix marketplace ships these plugins today:

| Plugin | Version | What it provides |
|--------|---------|-----------------|
| `craft-rhetoric` | 0.3.0 | Comprehension, rhetoric, explanation — documentation, tutorials, discourse, diagrams |
| `ci-scaffolds` | 0.6.0 | Collaboration scaffolds — claim verification, decision frameworks, mastery-oriented review |
| `guild-arch` | 0.2.0 | Architectural reasoning — design review, threat modeling, security-by-design |
| `antifragile` | — | ACES boundary review — adaptability, composability, extensibility checks |
| `craft-extensions` | 0.1.0 | Lexicon extension — vocabulary and concept growth |
| `craft-evals` | 0.3.0 | Evaluation — activation suites, methodology rubrics |
| `craft-research` | 0.3.0 | Research synthesis |
| `radix` | 0.3.4 | Multimodal input handling |

These are the gnx planned marketplace plugins installable through the cix source now. The gnx marketplace projection (a separate generated `marketplace.json` at the gnx repo root) is the designed shape — same content, different entry point, planned.

---

## Step 3 — Install craft-rhetoric

In `~/.claude/settings.json`, add to `enabledPlugins`:

```json
{
  "enabledPlugins": {
    "craft-rhetoric@cix": true
  }
}
```

Claude Code copies the plugin subdirectory into a versioned cache at `~/.claude/plugins/cache/cix/craft-rhetoric/0.3.0/`. The plugin is self-contained — no build step, no traversal outside its directory. Once the entry is written and Claude Code restarts, the plugin is active.

To install multiple plugins from the same marketplace, add each one:

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

## What Claude Code does with a plugin after install

Claude Code loads skills from the installed plugin directory. For craft-rhetoric, those skills include `rhetoric`, `discovering`, `discourse`, `voicing`, `evaluating`, and others. Each is a SKILL.md file Claude reads at activation.

A plugin's description field (in `plugin.json`) is what Claude Code uses to decide when to activate it. craft-rhetoric's: *"Crafts content that teaches and persuades — documentation, tutorials, research papers, diagrams, animations. Use when: user asks to 'write docs', 'explain this', 'create a tutorial'..."*

Claude Code loads the skill and drives the behavior from it. The skill is the interface — not a command, not a menu. That is the design pattern every plugin in this catalog follows.

---

## What NOT to do yet

These commands do not run today:

```
gnx add craft-rhetoric      # planned — gnx CLI not built
gnx init                    # planned — project genesis CLI not built
gnx build                   # planned — projection CLI not built
gnx search                  # planned — discovery CLI not built
```

The gnx CLI is designed (ground-truth §3 sketches the full command surface). Build starts now. Until it ships, the `extraKnownMarketplaces` entry in `settings.json` is the install path.

---

## Where to go next

[how-components-work](/docs/how-components-work) — what you just installed. The four kinds, the manifest, `provides` and `requires`. The mental model for what a plugin is and why it composes the way it does.
