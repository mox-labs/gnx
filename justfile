# gnx — gate single-source-of-truth.
# The git hook and CI both call `just`, so a local check and the CI check
# cannot drift (the vaani pattern). cix shipped gates as ad-hoc bash and as
# .githooks that were never wired; gnx fixes both — see CONTRIBUTING.md.

set shell := ["bash", "-uc"]

# Everything a commit must pass. The pre-commit hook calls this.
check: docs-check secrets grammar payloads projection

# The CI gate (mirrors the hook; slow gates are added here as the CLI lands).
ci: docs-check secrets-all grammar payloads projection capabilities-test capabilities-lint capabilities-typecheck

# The committed plugin projection must match components/ (gnx build --check).
# `--project` not `--directory`: build reads ./components from the CWD, and
# --directory would move the CWD into the workspace.
projection:
    uv --project components/capabilities run gnx build --check

# The gnx identity grammar over every manifest (GEP-0001/0002/0003).
grammar:
    uv --directory components/capabilities run --group dev \
        python {{justfile_directory()}}/harness/check.py {{justfile_directory()}}/components

# Component payloads against Claude Code's documented agent/skill shape.
# Replaces plugin-dev's validate-agent.sh, which cannot parse a block-scalar
# description and exits mid-run under `set -e` — see the header of the script.
payloads:
    uv --directory components/capabilities run --group dev \
        python {{justfile_directory()}}/harness/validate_payload.py \
        {{justfile_directory()}}/components/agents {{justfile_directory()}}/components/skills

# Lint the capability packages (the puma/x.uma house set: + B, SIM, TC).
capabilities-lint:
    uv --directory components/capabilities run ruff check matrix ix recon dao gnx

# Typecheck each capability FROM ITS OWN DIRECTORY. mypy resolves config from the
# invocation rootdir, so running it from the workspace root silently applies the root
# [tool.mypy] and ignores each package's scoped overrides (recon needs them for glom +
# xmltodict, which ship no py.typed). puma's gate does `cd puma && mypy src/xuma` for
# exactly this reason. matrix and ix are not yet clean and are listed separately below.
capabilities-typecheck:
    #!/usr/bin/env bash
    set -uo pipefail
    fail=0
    for p in recon dao gnx; do
      ( cd components/capabilities/$p && \
        ../.venv/bin/mypy --strict src/$p ) || fail=1
    done
    exit $fail

# The capability packages: matrix, ix, recon, dao (gnx CLI has no tests yet).
capabilities-test:
    uv --directory components/capabilities run --group dev \
        python -m pytest matrix/tests ix/tests recon/tests dao/tests -q

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
