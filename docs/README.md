# docs/

The docsite and the records behind it. Only two folders here are docs for users; the rest are
working records, kept for history and for the people building the site. A component's own docs
live in its directory under `components/`, not here.

## The docs for users

| Folder | What it is |
|---|---|
| [content/](content/) | The docsite's pages: `start/` and `guides/`. Published at [mox-labs.github.io/gnx](https://mox-labs.github.io/gnx). |
| [experience/](experience/) | The docsite itself (SvelteKit). It renders `content/` and each component's `overview.md`. |

## Writing instruments

| File | What it is |
|---|---|
| [VOICE.md](VOICE.md) | The voice the docs are written in, and the check for text that drifts from it. |
| [RUBRIC.md](RUBRIC.md) | The rubric a page is scored against before it ships. |

## Working records

None of these is a description of what ships. Where one disagrees with a README, the README is
right.

| Path | What it is |
|---|---|
| [ground-truth.md](ground-truth.md) | The design discourse of June 2026 that the first site was written from. |
| [decisions/log.md](decisions/log.md) | The docsite build's decision ledger, from June 2026. Package decisions are in each package's `docs/design/`. |
| [_handoff/](_handoff/) | Prompts and plans handed from one working session to the next. |
| [_deferred/](_deferred/) | Drafts and findings set aside for later. |
| [convergence/](convergence/), [catalog-map/](catalog-map/) | The internal design map and a diagram of the first catalog. |
| [feedback/](feedback/), `evaluation.json`, `reviews.json` | Review threads and scorecards from earlier passes over the site. |

The public build of the site leaves every internal page out.
