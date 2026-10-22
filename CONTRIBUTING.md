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
drift.

```sh
just              # list every gate
just check        # what every commit must pass: the docsite, a staged secret scan,
                  # the identity grammar, component payloads, the plugin projection,
                  # and the capability packages' lint and format
just ci           # the full CI gate: also the capability packages' tests, lint,
                  # strict typecheck and standalone install, and the lab
just lab          # validate and plan the lab's experiments; nothing is run
```

The capability packages (`components/capabilities/`) are one uv workspace. Run their tests
from there, or through `just capabilities-test`:

```sh
uv --project components/capabilities run pytest components/capabilities/ix/tests -q
```

No test reaches a live model: `components/capabilities/conftest.py` refuses every call
where an agent session or a model call would spend money, and fails the test that made it.
Use matrix's `mock` runtime, or a fake, instead.

## One-time setup: wire the hooks

Git does not use `.githooks/` unless you point it there. Run this once after
cloning:

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
- [uv](https://docs.astral.sh/uv/) — the capability packages' workspace.
- [k6](https://k6.io) — only to run ix's service experiments (`ix run` on an
  `http-load` probe); no gate needs it.
