# gnx — gate single-source-of-truth.
# The git hook and CI both call `just`, so a local check and the CI check
# cannot drift. See CONTRIBUTING.md.

set shell := ["bash", "-uc"]

# The pre-commit hook calls this. Its lint and format gates are CI's own, so a line CI would
# refuse cannot be committed.
#
# Everything a commit must pass.
check: docs-check secrets grammar payloads projection capabilities-lint

# The CI gate (mirrors the hook; slow gates are added here as the CLI lands).
ci: docs-check secrets-all grammar payloads projection recon-skill capabilities-test capabilities-lint capabilities-typecheck capabilities-standalone lab

# `--project` not `--directory`: build reads ./components from the CWD, and
# --directory would move the CWD into the workspace.
#
# The committed plugin projection must match components/ (gnx build --check).
projection:
    uv --project components/capabilities run gnx build --check

recon-skill:
    uv --project components/capabilities run python \
        components/capabilities/recon/scripts/plugin_skill.py plugins/recon/skills/recon --check

# recon against the live APIs it collects from; not in ci, since it depends on third parties.
recon-live:
    RECON_LIVE=1 uv --directory components/capabilities run --group dev \
        python -m pytest recon/tests/test_integration.py -q

# The gnx identity grammar over every manifest (GEP-0001/0002/0003).
grammar:
    uv --directory components/capabilities run --group dev \
        python {{justfile_directory()}}/harness/check.py {{justfile_directory()}}/components

# Replaces plugin-dev's validate-agent.sh, which cannot parse a block-scalar
# description and exits mid-run under `set -e` — see the header of the script.
#
# Component payloads against Claude Code's documented agent/skill shape.
payloads:
    uv --directory components/capabilities run --group dev \
        python {{justfile_directory()}}/harness/validate_payload.py \
        {{justfile_directory()}}/components/agents {{justfile_directory()}}/components/skills

# Lint the capability packages (the puma/x.uma house set: + B, SIM, TC) and check their
# formatting, the Python blocks in their Markdown included.
capabilities-lint:
    uv --directory components/capabilities run ruff check hardline matrix ix recon dao gnx
    uv --directory components/capabilities run ruff format --check hardline matrix ix recon dao gnx

# Typecheck each capability FROM ITS OWN DIRECTORY. mypy resolves config from the
# invocation rootdir, so running it from the workspace root silently applies the root
# [tool.mypy] and ignores each package's scoped overrides (recon needs one for xmltodict,
# matrix for the OTLP exporters — neither ships py.typed).
# puma's gate does `cd puma && mypy src/xuma` for exactly this reason.
#
# Every package's source is checked under --strict, and so are the tests of each package in
# the second list: a test's stand-in that drifts from the port it stands for is then a type
# error, not a test that passes against a shape the code no longer has. matrix's and recon's
# tests are not annotated yet. Each list is the gate, not a wish: adding a package to it is
# how it stays clean.
#
# Typecheck every capability package under mypy --strict, and the tests that are typed.
capabilities-typecheck:
    #!/usr/bin/env bash
    set -uo pipefail
    fail=0
    for p in hardline matrix ix recon dao gnx; do
      paths=(src/$p)
      case " hardline ix dao " in *" $p "*) paths+=(tests) ;; esac
      ( cd components/capabilities/$p && \
        ../.venv/bin/mypy --strict "${paths[@]}" ) || fail=1
    done
    exit $fail

# Every package's but the gnx CLI's, which has none. A skip components/capabilities/conftest.py
# does not expect fails the run, and every test is bounded in time (pytest-timeout). No test
# reaches a live model: conftest.py refuses it.
#
# The capability packages' tests, each skip listed with its reason.
capabilities-test:
    uv --directory components/capabilities run --group dev \
        python -m pytest hardline/tests matrix/tests ix/tests recon/tests dao/tests \
        -q -rs --strict-skips

# The docsite: svelte-kit sync + svelte-check + tsc --noEmit.
docs-check:
    cd docs/experience && bun run check

# Secret scan over staged changes — fast; blocks a commit that stages a secret.
secrets:
    gitleaks protect --staged --no-banner --redact

# Full-history secret scan — run in CI and on demand.
secrets-all:
    gitleaks detect --no-banner --redact

# Validate and plan every experiment in the lab. Nothing runs: this checks that each
# experiment is well-formed against the installed genres and kinds and that the installed
# CLI resolves them through its entry points. A run of a lab experiment starts real agent
# sessions and spends money, so it is never part of a gate.
lab:
    uv --project components/capabilities run ix validate lab/catalog-routing
    uv --project components/capabilities run ix plan lab/catalog-routing

# Every capability, installed ALONE from only its own declared dependencies —
# the `uv tool install` / `uvx` path, which nothing else here exercises.
#
# `just ci` runs inside the workspace, where the dev group installs all five
# packages and the UNION of their dependencies. An under-declared dependency is
# therefore invisible to every other gate: dao imported pydantic in its domain
# layer and declared only click and rich, and every gate passed while
# `uv tool install dao` died on import (fixed 2026-09-06).
#
# The copy to a temp dir is load-bearing, not hygiene: uv discovers the workspace
# from the package path and applies the root's [tool.uv.sources], so testing in
# place resolves workspace-internal deps and gives a false pass.
#
# This models the real distribution path — `uv tool install`/`uvx` against a
# git+subdirectory URL — for every package with no workspace-internal dependency.
# uv clones the whole repo to build a subdirectory, so it sees the workspace root
# and its [tool.uv.sources]; what it does NOT do is widen a package's own declared
# dependency set. Confirmed against origin/main: dao installed from a git URL failed
# on pydantic exactly as the local install did.
#
# A package that depends on a workspace-internal one (`matrix`) cannot be in this
# list: the repository resolves that dependency on every supported path because the
# clone carries the workspace root, and severing the package from the repo — what
# this recipe does on purpose — lets PyPI's unrelated `matrix` 3.0.0 fill the gap.
# ix is the one package with such a dependency: its run path is matrix components. It is
# checked with matrix copied beside it and named explicitly, so the gap is filled by this
# repository's matrix and ix's other imports are still held to what ix declares.
capabilities-standalone:
    #!/usr/bin/env bash
    set -uo pipefail
    tmp="$(mktemp -d)"; trap 'rip "$tmp" 2>/dev/null || true' EXIT
    fail=0
    for p in hardline matrix ix recon gnx dao; do
      cp -R components/capabilities/$p "$tmp/$p"
      probe=("$p" --help)
      with=(--with ./$p)
      if [ "$p" = ix ]; then with=(--with ./matrix --with ./ix); fi
      if ( cd "$tmp" && uv run --isolated --no-project --quiet "${with[@]}" "${probe[@]}" >/dev/null ); then
        echo "  $p standalone OK"
      else
        echo "  $p standalone FAIL — an import is not in [project.dependencies]"; fail=1
      fi
    done
    exit $fail
