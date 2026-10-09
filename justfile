# gnx — gate single-source-of-truth.
# The git hook and CI both call `just`, so a local check and the CI check
# cannot drift (the vaani pattern). cix shipped gates as ad-hoc bash and as
# .githooks that were never wired; gnx fixes both — see CONTRIBUTING.md.

set shell := ["bash", "-uc"]

# Everything a commit must pass. The pre-commit hook calls this.
check: docs-check secrets grammar payloads projection

# The CI gate (mirrors the hook; slow gates are added here as the CLI lands).
ci: docs-check secrets-all grammar payloads projection capabilities-test capabilities-lint capabilities-typecheck capabilities-standalone

# `--project` not `--directory`: build reads ./components from the CWD, and
# --directory would move the CWD into the workspace.
#
# The committed plugin projection must match components/ (gnx build --check).
projection:
    uv --project components/capabilities run gnx build --check

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

# Lint the capability packages (the puma/x.uma house set: + B, SIM, TC).
capabilities-lint:
    uv --directory components/capabilities run ruff check hardline matrix ix recon dao gnx

# Typecheck each capability FROM ITS OWN DIRECTORY. mypy resolves config from the
# invocation rootdir, so running it from the workspace root silently applies the root
# [tool.mypy] and ignores each package's scoped overrides (recon needs them for glom +
# xmltodict, matrix for opentelemetry, ix for deepeval — none of which ship py.typed).
# puma's gate does `cd puma && mypy src/xuma` for exactly this reason.
#
# All six are clean under --strict as of 2026-09-24 (hardline joined). Adding a package here is how it
# stays that way: the list is the gate, not a wish.
#
# Typecheck every capability package under mypy --strict.
capabilities-typecheck:
    #!/usr/bin/env bash
    set -uo pipefail
    fail=0
    for p in hardline matrix ix recon dao gnx; do
      ( cd components/capabilities/$p && \
        ../.venv/bin/mypy --strict src/$p ) || fail=1
    done
    exit $fail

# The capability packages: hardline, matrix, ix, recon, dao (gnx CLI has no tests yet).
capabilities-test:
    uv --directory components/capabilities run --group dev \
        python -m pytest hardline/tests matrix/tests ix/tests recon/tests dao/tests -q

# The docsite: svelte-kit sync + svelte-check + tsc --noEmit.
docs-check:
    cd docs/experience && bun run check

# Secret scan over staged changes — fast; blocks a commit that stages a secret.
secrets:
    gitleaks protect --staged --no-banner --redact

# Full-history secret scan — run in CI and on demand.
secrets-all:
    gitleaks detect --no-banner --redact

# --- The gnx CLI is Python (leaning, cix-hexagonal inheritance), not yet built.
# As it lands, its gates join `check` here, mirrored by CI, so nothing drifts:
#
# lint:
#     uv run ruff check && uv run ruff format --check
# typecheck:
#     uv run mypy --strict src
# test:
#     uv run pytest

# The simulated subject proves the harness end to end without credentials; the live one
# needs ANTHROPIC_API_KEY and measures the catalog rather than the plumbing. See
# lab/README.md for why those are different claims.
#
# Run the lab's experiments offline, on both engines: the native matrix DAG and Inspect AI.
# Same experiment, same readings — the engine parity test asserts it.
evals:
    uv --project components/capabilities run ix experiment validate catalog-routing --lab lab
    uv --project components/capabilities run ix run catalog-routing --lab lab --subject catalog-simulated --seed 42
    uv --project components/capabilities run ix run sensor-integrity --lab lab
    uv --project components/capabilities run ix run sensor-integrity --lab lab --engine inspect

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
# ix is NOT in this list, and its absence is not a defect. ix depends on `matrix`,
# which the repository resolves on every supported path because the clone carries
# the workspace root. Severing the package from the repo — what this recipe does on
# purpose — is the one condition under which ix legitimately cannot resolve, and
# PyPI's unrelated `matrix` 3.0.0 then fills the gap. See ix/SECURITY.md I-6; ix is
# checked on the git path instead.
capabilities-standalone:
    #!/usr/bin/env bash
    set -uo pipefail
    tmp="$(mktemp -d)"; trap 'rip "$tmp" 2>/dev/null || true' EXIT
    fail=0
    for p in hardline matrix recon gnx dao; do
      cp -R components/capabilities/$p "$tmp/$p"
      probe=("$p" --help)
      if ( cd "$tmp" && uv run --isolated --no-project --quiet --with ./$p "${probe[@]}" >/dev/null ); then
        echo "  $p standalone OK"
      else
        echo "  $p standalone FAIL — an import is not in [project.dependencies]"; fail=1
      fi
    done
    exit $fail
