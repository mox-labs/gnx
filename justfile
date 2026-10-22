# gnx — gate single-source-of-truth.
# The git hook and CI both call `just`, so a local check and the CI check
# cannot drift. See CONTRIBUTING.md.

set shell := ["bash", "-uc"]

# The pre-commit hook calls this. Its lint and format gates are CI's own, so a line CI would
# refuse cannot be committed.
#
# Everything a commit must pass.
check: docs-check secrets grammar payloads projection capabilities-lint

# The hook's gates, the secret scan over all of history, and the slow ones. The capability
# tests need k6 on PATH and a docker daemon with alpine:3.20 pulled; without them, the tests
# that use them skip and the run fails.
#
# The CI gate: CI runs this recipe as it stands.
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

# The workspace's own Python (the tests' shared helpers in _testing, and conftest.py) is
# linted and formatted with the packages.
#
# Lint the capability packages (the puma/x.uma house set: + B, SIM, TC) and check their
# formatting, the Python blocks in their Markdown included.
capabilities-lint:
    uv --directory components/capabilities run ruff check hardline matrix ix recon dao gnx _testing conftest.py
    uv --directory components/capabilities run ruff format --check hardline matrix ix recon dao gnx _testing conftest.py

# Typecheck each capability FROM ITS OWN DIRECTORY. mypy resolves config from the
# invocation rootdir, so running it from the workspace root silently applies the root
# [tool.mypy] and ignores each package's scoped overrides (recon needs one for xmltodict,
# matrix for the OTLP exporters — neither ships py.typed).
# puma's gate does `cd puma && mypy src/xuma` for exactly this reason.
#
# Every package's source is checked under --strict, and so are the tests of each package in
# the second list: a test's stand-in that drifts from the port it stands for is then a type
# error, not a test that passes against a shape the code no longer has. Each list is the
# gate, not a wish: adding a package to it is how it stays clean. MYPYPATH is the workspace,
# where the tests' shared helpers (_testing) live; the workspace's own Python (those helpers
# and conftest.py) is checked from there.
#
# Typecheck every capability package under mypy --strict, and the tests that are typed.
capabilities-typecheck:
    #!/usr/bin/env bash
    set -uo pipefail
    fail=0
    for p in hardline matrix ix recon dao gnx; do
      paths=(src/$p)
      case " hardline matrix ix recon dao " in *" $p "*) paths+=(tests) ;; esac
      ( cd components/capabilities/$p && \
        MYPYPATH=.. ../.venv/bin/mypy --strict "${paths[@]}" ) || fail=1
    done
    ( cd components/capabilities && .venv/bin/mypy --strict _testing conftest.py ) || fail=1
    exit $fail

# Every package's but the gnx CLI's, which has none, and the workspace guard's own (_testing).
# A skip components/capabilities/conftest.py does not expect fails the run, and every test is
# bounded in time (pytest-timeout). No test reaches a live model or any host but this machine:
# conftest.py refuses it.
#
# The capability packages' tests, each skip listed with its reason.
capabilities-test:
    uv --directory components/capabilities run --group dev \
        python -m pytest hardline/tests matrix/tests ix/tests recon/tests dao/tests _testing \
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

# Every capability, built as the wheel a user installs and installed ALONE into a fresh venv
# from only its own declared dependencies (the `uv tool install` / `uvx` path), then driven
# through a real smoke test outside the repository.
#
# `just ci` runs inside the workspace, where the dev group installs every package and the
# UNION of their dependencies, and imports each from its checkout. So an under-declared
# dependency, an entry point the wheel's metadata lacks, or a file the wheel leaves out is
# invisible to every other gate. `--help` sees only what the CLI imports at start; a command
# reaches what a user's first use does. No package has extras, so each install is the plain
# one, and it carries every runtime and telemetry: a check that builds each runtime with the
# OTEL_* variables set, and expects no warning, fails on one an install lacks.
#
#   hardline  `models` and `check` of a registry with a local and a hosted row; `complete` of
#             a row on the mock backend, a real call through the runtime that calls no model
#   matrix    `check` and `plan` of a config with a mock agent and an agent step; `check` of
#             one with every runtime (claude-sdk, pydantic-ai, hardline), asked for OTLP export;
#             a `run` of a model step on hardline's mock backend, read back from the store
#   ix        `validate`, `plan` and a dry run of examples/harness-integrity; `plan` of an
#             agent session on claude-sdk, asked for console export; an exploratory `run` of a
#             model-call experiment on a hardline row of the mock backend
#   recon     a survey of a cli collector, then a query of the timestamp it collected
#   gnx, dao  `--help`: their commands read this repository
#
# ix depends on this repository's matrix, never PyPI's unrelated `matrix`: its venv is given
# matrix's wheel beside its own, so that dependency is this repository's and ix's other
# imports are still held to what ix declares. Each venv is Python 3.12, the floor the packages
# declare, and HOME is a fresh directory, so no user config reaches a command.
#
# Every capability installed alone from its wheel, then driven through a smoke test.
# matrix depends on hardline and ix on matrix: PyPI has unrelated packages of both names, so
# each is installed with this repository's wheels of what it depends on.
capabilities-standalone:
    #!/usr/bin/env bash
    set -uo pipefail
    tmp="$(mktemp -d)"; trap 'rip "$tmp" 2>/dev/null || true' EXIT
    examples="$PWD/components/capabilities/ix/examples"
    packages=(hardline matrix ix recon gnx dao)
    # expect EXPRESSION: the JSON document on stdin makes EXPRESSION (over `d`) true.
    expect() { python -c 'import json, sys; d = json.load(sys.stdin); assert eval(sys.argv[1]), d' "$1"; }
    smoke_hardline() {
      printf '%s\n' 'default: local' 'models:' \
        '  local: {backend: openai-compat, base_url: "http://127.0.0.1:9/v1", model: m, family: qwen}' \
        '  hosted: {backend: anthropic, model: claude-haiku-4-5, family: claude, api_key: "env:SMOKE_KEY"}' \
        > hardline.yaml
      SMOKE_KEY=unused hardline models --json | expect '{m["name"] for m in d["models"]} == {"local", "hosted"}'
      SMOKE_KEY=unused hardline check --json | expect 'd["ok"]'
      printf '%s\n' 'models:' '  echo: {backend: mock, model: m, family: mock, options: {default: hello}}' \
        > hardline.yaml
      hardline complete echo "Say hello." --json \
        | expect 'd["text"] == "hello" and d["finish_reason"] == "stop" and d["backend"] == "mock"'
    }
    smoke_matrix() {
      printf '%s\n' 'matrix:' '  agent_runtimes: {offline: {type: mock}}' \
        '  agents: {greeter: {runtime: offline, system_prompt: "Say hello."}}' \
        '  inputs: [smoke.v1.name]' '  components:' \
        '    - {name: greet, type: matrix.v1.agent-step,' \
        '       config: {agent: greeter, inputs: {name: smoke.v1.name}, output: smoke.v1.greeting}}' \
        > matrix.yaml
      matrix check --only matrix.yaml --json | expect 'd["ok"]'
      matrix plan --only matrix.yaml --json | expect 'd["ok"] and d["stages"] == [["greet"]]'
      printf '%s\n' 'matrix:' \
        '  agent_runtimes: {claude: {type: claude-sdk}, loop: {type: pydantic-ai}}' \
        '  model_runtimes: {calls: {type: hardline}}' \
        '  models: {models: {local: {backend: openai-compat, base_url: "http://127.0.0.1:9/v1", model: m, family: qwen}}}' \
        > runtimes.yaml
      OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:9 matrix check --only runtimes.yaml --json \
        | expect 'd["ok"] and d["warnings"] == [] and d["agent_runtimes"] == ["claude", "loop"]'
      printf '%s\n' 'matrix:' '  model_runtimes: {calls: {type: hardline}}' \
        '  models: {models: {echo: {backend: mock, model: m, family: mock, options: {default: hello}}}}' \
        '  inputs: [smoke.v1.name]' '  components:' \
        '    - {name: greet, type: matrix.v1.model-step,' \
        '       config: {runtime: calls, model: echo, inputs: {name: smoke.v1.name}, prompt: "Greet {name}."}}' \
        > model.yaml
      echo '"Ada"' > names.jsonl
      matrix run --only model.yaml --input smoke.v1.name=names.jsonl --json > run.json
      expect 'd["status"] == "completed" and d["tables"]["matrix.v1.model-call"]["rows"] == 1' < run.json
      # The row the step recorded, read back from the run's store.
      expect '[json.loads(row)["text"] for row in open(d["location"] + "/matrix.v1.model-call.jsonl")] == ["hello"]' < run.json
    }
    smoke_ix() {
      cp -R "$examples/harness-integrity" .
      ix validate harness-integrity --json | expect 'd["valid"]'
      ix plan harness-integrity --json | expect 'd["runnable"]'
      ix run harness-integrity --dry-run --json | expect 'd["status"] == "completed"'
      mkdir on-claude
      printf '%s\n' 'name: on-claude' 'question: Does the agent name a fruit?' 'genre: evaluate' \
        'subjects:' '  agent: {kind: agent-session, runtime: claude-sdk, runtime_options: {setting_sources: []}}' \
        'probes:' '  - {id: ask, kind: prompt, prompt: "Name a fruit."}' \
        'sensors:' '  - {kind: text-match, contains: banana}' \
        'primary: {metric: text_matched}' 'threshold: {op: ">=", value: 0.5}' > on-claude/experiment.yaml
      OTEL_TRACES_EXPORTER=console OTEL_METRICS_EXPORTER=none ix plan on-claude --json \
        | expect 'd["runnable"] and d["warnings"] == []'
      printf '%s\n' 'models:' '  echo: {backend: mock, model: m, family: mock, options: {default: hello}}' \
        > hardline.yaml
      mkdir on-model
      printf '%s\n' 'name: on-model' 'question: Does the model say hello?' 'genre: evaluate' \
        'subjects:' '  it: {kind: model-call, runtime: hardline, model: echo}' \
        'probes:' '  - {id: ask, kind: prompt, prompt: "Say hello."}' \
        'sensors:' '  - {kind: text-match, contains: hello}' \
        'primary: {metric: text_matched}' 'threshold: {op: ">=", value: 0.5}' > on-model/experiment.yaml
      ix run on-model --exploratory --json | expect 'd["status"] == "completed" and d["simulated"] == "none" and [(e["metric"], e["value"]) for e in d["analysis"]["estimates"]] == [("text_matched", 1.0)]'
    }
    smoke_recon() {
      printf '%s\n' 'collectors:' '  - name: releases' '    type: cli' '    run: |' \
        "      echo '{\"tag\": \"v1\", \"published\": \"2026-03-15T18:42:31Z\"}'" > mission.yaml
      recon init smoke -c mission.yaml --json | expect 'd["outcome"] == "created"'
      recon survey smoke --json | expect 'd["status"] == "ok" and d["tables"][0]["records"] == 1'
      recon query smoke "SELECT typeof(published) AS t FROM releases" --json \
        | expect 'd["rows"] == [{"t": "TIMESTAMP"}]'
    }
    smoke_gnx() { gnx --help >/dev/null; }
    smoke_dao() { dao --help >/dev/null; }
    for p in "${packages[@]}"; do
      uv build --quiet --directory components/capabilities --package "$p" --wheel --out-dir "$tmp/dist" \
        || { echo "  the $p wheel did not build"; exit 1; }
    done
    fail=0
    for p in "${packages[@]}"; do
      work="$tmp/$p"; mkdir -p "$work/home"
      wheels=("$tmp/dist/$p"-*.whl)
      if [ "$p" = matrix ]; then wheels+=("$tmp/dist"/hardline-*.whl); fi
      if [ "$p" = ix ]; then wheels+=("$tmp/dist"/matrix-*.whl "$tmp/dist"/hardline-*.whl); fi
      # Not under `if`: there bash ignores the subshell's `set -e`, and a failed step goes on.
      ( set -euo pipefail
        uv venv --quiet --python 3.12 "$work/venv"
        uv pip install --quiet --python "$work/venv/bin/python" "${wheels[@]}"
        cd "$work"; export HOME="$work/home" PATH="$work/venv/bin:$PATH"
        "smoke_$p" ) > "$work/log" 2>&1
      if [ $? -eq 0 ]; then
        echo "  $p standalone OK"
      else
        cat "$work/log"
        echo "  $p standalone FAIL: installed alone from its wheel, it does not work (above)"; fail=1
      fi
    done
    exit $fail
