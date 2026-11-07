# Contributing to gnx

gnx is a set of Python tools and libraries for working with agents (ix, recon, matrix,
hardline, matrix-hardline, ix-cosmic-ray, dao), the `gnx` CLI that builds its Claude Code
plugins, and the skills those plugins ship. [README.md](README.md) says what each is. This file
is the contract for working in the repository; [AGENTS.md](AGENTS.md) is its short form for an
agent.

## Who reviews, and how to contribute

yzavyas owns the project, reviews every pull request, and merges to `main`. Most changes are
written by Claude under the owner's rulings ([how gnx is
built](docs/content/start/2-how-gnx-is-built.md)).

From outside the project, **open an issue first**: describe the problem or the change, and wait
for a reply before writing a pull request. There are no issue templates, no CLA and no DCO
sign-off. A vulnerability goes through [SECURITY.md](SECURITY.md), never an issue.

## Workflow: GitHub Flow

- `main` is always releasable. Never commit to it directly.
- Branch off `main` for every change: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`.
- Open a PR. CI must be green. Merge by **squash** — one logical change becomes
  one commit on `main`.
- Keep a PR to one logical change. Small PRs review faster and revert cleaner.

## Commits: Conventional Commits

Every commit message follows `<type>(<scope>): <description>`:

```
feat(recon): recon 0.16.0 — a crawl that resumes
fix(gnx): a skill's overview.md stays out of its plugin, like its manifest
docs(ix): name the child-process selection gap
```

The scope is the package or area the change is in. A commit Claude wrote ends with
`Co-Authored-By: Claude …` and a `Claude-Session:` link to the session.

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`,
`build`, `style`, `revert`. The `commit-msg` hook and the CI `commit-lint` job
enforce this — they share the same pattern, so they cannot disagree.

## Gates: the justfile is the single source of truth

The hook and CI both call `just` (`just check` and `just ci`), so a local check
and the CI check cannot drift.

```sh
just              # list every gate
just check        # what every commit must pass: the docsite, the docs' shape, versions
                  # and links, a staged secret scan, the identity grammar, component
                  # payloads, the plugin projection, and the capability packages' lint
                  # and format
just ci           # the full CI gate: also the CHANGELOG check, the capability packages'
                  # tests, their strict typecheck, each installed alone from its wheel and
                  # smoke-tested, and the lab
just docs-sync    # the docs gate alone (below)
just lab          # validate and plan the lab's experiments; nothing is run
```

The capability packages (`components/capabilities/`) are one uv workspace. Run their tests
from there, or through `just capabilities-test`:

```sh
uv --project components/capabilities run pytest components/capabilities/ix/tests -q
```

No test runs a live model session, and the test process reaches no host but this machine:
`components/capabilities/conftest.py` refuses every call where an agent session or a model call
would spend money, every socket connection or datagram in the test process that is not to
loopback, every lookup through `socket.getaddrinfo`, `gethostbyname` or `gethostbyname_ex` of a
name other than `localhost`, and a `k6` started directly with a target that is not loopback, and
fails the test that made it. It does not see a child process other than `k6` (`git`, `uv`,
`curl`, a container), nor `k6` started through a shell; its docstring lists each route it does
not cover, and a test that takes one is reviewed for it. Use matrix's `mock` runtime, a fake, or
a server of the test's own on 127.0.0.1 instead. A test that calls third-party hosts on purpose
is marked `network` and runs only when asked, as recon's live tests do (`just recon-live`).

`just capabilities-test` lists every skip with its reason, and fails on a skip that
conftest.py's `EXPECTED_SKIPS` does not name: a test that cannot run here is either made to
run or named there with why. So it fails on a machine without `k6` on `PATH`, or without a
docker daemon running with `alpine:3.20` pulled: ix's end-to-end k6 tests and its live
compose test need them (see Tools).

## Docs ship with the code

Each component owns its docs, in its own directory, so they change in the same commit as its
code.

| File | What it holds |
|---|---|
| `README.md` | the entry: what it is, install, a first run, where next |
| `docs/explanation/overview.md` | the explanation the docsite renders at `/components/<name>`; a skill's or an agent's sits beside its `SKILL.md` or `agent.md` as `overview.md` |
| `CHANGELOG.md` | what changed and why, per release |

An overview opens with front matter: `title` (the component's directory name), `summary` (one
sentence) and `kind` (`capability`, `skill`, `agent` or `plugin`). Its `##` sections are these,
in order: What it is, The problem, How it works, Try it, What it gives an agent, What it does
not do, Next. It is written to [docs/VOICE.md](docs/VOICE.md).

`just docs-sync` holds all of this. Each problem it prints names the file, what is wrong, and
the fix.

| Check | Fails when |
|---|---|
| overviews | a component under `components/` has no overview, or one out of shape; `incubator/` is exempt |
| versions | an install line's `gnx@<pkg>-v<version>` tag is not that package's `pyproject.toml` version |
| links | a relative link, or its `#anchor`, does not resolve in a README, CONTRIBUTING or SECURITY file, or a page under `docs/content/` or `components/` |
| changelog | a package's surface changed since the merge-base with `origin/main`, and its `CHANGELOG.md` did not |

A package's surface is its `CONTRACT.md`, the tests that pin it (those its `CONTRACT.md` cites,
and any `test_*contract*`, `test_*layering*`, `test_extension*` or `test_surface*`), and the
skill text `<tool> --skill` prints. CHANGELOGs and `docs/design/` records are history and are
not read.

`just check` runs the first three. The changelog check reads the branch's commits, which a
pre-commit hook has not got yet, so `just ci` runs it; set `DOCS_SYNC_BASE` to compare with a
branch other than `origin/main`.

When a surface change needs no entry, say why in a `Docs-Unchanged:` trailer on any commit in
the branch, with the message's other trailers. The gate prints each one it honours.

```
test(ix): name the layering test's fixture

Docs-Unchanged: renames a helper in a pinned test; no guarantee moved
```

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
- [k6](https://k6.io) — ix's service experiments (`ix run` on an `http-load` probe), and
  ix's end-to-end tests in `just ci`.
- [docker](https://docs.docker.com/compose/) with compose — ix's `compose` environment, and
  its live test in `just ci`, which needs `alpine:3.20` pulled.
