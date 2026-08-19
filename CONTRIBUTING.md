# Contributing to gnx

gnx is the open-source component of the mox stack — a registry, a Claude Code
marketplace, and an agentic CLI for composable components. This file is the
contract for working in the repo.

## Workflow: GitHub Flow

- `main` is always releasable. Never commit to it directly.
- Branch off `main` for every change: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`.
- Open a PR. CI must be green. Merge by **squash** — one logical change becomes
  one commit on `main`.
- Keep a PR to one logical change. Small PRs review faster and revert cleaner.

## Commits: Conventional Commits

Every commit message follows `<type>(<scope>): <description>`:

```
docs(start): re-register the Start pages to pragmatics
feat(cli): scaffold gnx component init
fix(content): correct the slick manifest field count
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`,
`build`, `style`, `revert`. The `commit-msg` hook and the CI `commit-lint` job
enforce this — they share the same pattern, so they cannot disagree.

## Gates: the justfile is the single source of truth

The hook and CI both call `just`, so a local check and the CI check cannot
drift. This is deliberate — cix's gates were ad-hoc bash that fell out of sync
with CI, and its `.githooks` were never wired at all. gnx fixes both.

```sh
just check        # what every commit must pass: docsite check + staged secret scan
just docs-check   # svelte-kit sync + svelte-check + tsc --noEmit
just secrets      # gitleaks over staged changes
just ci           # the full CI gate
```

## One-time setup: wire the hooks

Git does not use `.githooks/` unless you point it there. Run this once after
cloning (the cix lesson — committed hooks that nothing ever ran):

```sh
git config core.hooksPath .githooks
chmod +x .githooks/*
```

After that, `pre-commit` runs `just check` and `commit-msg` validates the
message on every commit. CI mirrors both.

## Tools

- [bun](https://bun.com) — the docsite (`docs/experience`) runs on it.
- [just](https://just.systems) — the gate runner.
- [gitleaks](https://github.com/gitleaks/gitleaks) — the secret scanner.

The gnx CLI itself (Python, leaning) is designed, not yet built; its lint /
typecheck / test gates join `just check` as it lands. See
[the status page](docs/content/reference/3-status.md).
