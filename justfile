# gnx — gate single-source-of-truth.
# The git hook and CI both call `just`, so a local check and the CI check
# cannot drift (the vaani pattern). cix shipped gates as ad-hoc bash and as
# .githooks that were never wired; gnx fixes both — see CONTRIBUTING.md.

set shell := ["bash", "-uc"]

# Everything a commit must pass. The pre-commit hook calls this.
check: docs-check secrets

# The CI gate (mirrors the hook; slow gates are added here as the CLI lands).
ci: docs-check secrets-all

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
