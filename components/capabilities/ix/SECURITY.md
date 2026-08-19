# Security — ix

ix runs experiments: it sends probes to agent runtimes and measures what comes back. One
of its sensors **executes model-generated code in this process**. That is the intended
behaviour of a code-generation benchmark and the single most important thing on this page.

Audience: anyone running `ix run` against an experiment they did not author.

## Blast radius

`ix run` will, depending on the experiment's sensors:

- spawn agent runtimes through matrix (see matrix's `SECURITY.md` — the agent's authority
  is decided there, by config that lives in *your* `experiment.yaml`);
- **execute untrusted Python in the ix process** (`FunctionTestSensor`);
- write results under the lab directory.

## Trust boundaries

### 1. Model-generated code → this process (`FunctionTestSensor`)

The sensor extracts Python from the subject's response, writes it to a temp file, imports
it, and calls the named function. The imported module runs **in this process, with this
process's privileges** — filesystem, network and subprocess access included.

This is deliberate. A function-test benchmark cannot grade code without running it. It is
written down because an implicit trust boundary is one nobody reviews.

What constrains it:

- `self._timeout` bounds runaway execution via `SIGALRM`.
- The temp file is created with `mkstemp` — atomically, mode 0600 — and unlinked in a
  `finally`.

What does **not** constrain it:

- **`SIGALRM` does not exist on Windows.** There, `_exec_with_timeout` degrades to a plain
  call and there is *no timeout at all*. This is logged at WARNING rather than left silent,
  because the absence of the bound is the thing worth knowing.
- Nothing sandboxes filesystem, network, or subprocess access. Import-time side effects run
  before any timeout logic can inspect the module.

**Run this against code from a source you are willing to execute, or run ix in a
container.** There is no middle setting.

### 2. Experiment directory → sensor configuration

An experiment is a directory of YAML and Markdown. `graders_module` is resolved relative
to the experiment directory and imported. `experiment.yaml`'s `agent:` block becomes
matrix's agent kwargs, so **an experiment.yaml chooses the agent's permission mode**.

Installing someone else's experiment is equivalent to installing their code.

### 3. Probe frontmatter → sensor state

Frontmatter is untyped YAML and reaches sensors as `metadata`. Values are coerced at the
boundary (`filesystem_store.py`, `sensors.py`) rather than trusted — see I-3 below. This
is a correctness boundary, not a trust boundary; a malformed probe mis-scores a run, it
does not gain authority.

### 4. Optional third-party evaluators

`DeepEvalSensor` is behind an optional extra. When enabled it routes judge calls through a
matrix `Agent` adapter and hands prompts to deepeval. deepeval's own network behaviour is
outside ix's control.

## Findings

### I-1 — `mktemp` race in `FunctionTestSensor._load_function` (fixed 2026-08-17, CWE-377)

The sensor used `tempfile.mktemp()`, which only *reserves a name*. Between the name being
returned and `write_text()` opening it, another local process could create a symlink at
that path — pointing the write anywhere the ix process can write, and pointing the
subsequent import at a file the attacker controls.

Fixed: `tempfile.mkstemp()`, which creates the file atomically with mode 0600 and returns
an open fd. The write now goes through `os.fdopen(fd, "w")` — the path is never re-opened
by name.

### I-2 — unguarded `spec` / `spec.loader` (fixed 2026-08-17)

`importlib.util.spec_from_file_location` can return `None`, and `spec.loader` can be
`None`. Unguarded, both surfaced as a bare `AttributeError` that said nothing about what
failed. Now raises `ImportError` naming the file. Robustness, not a vulnerability.

### I-3 — aggregation crashed on a third-party sensor (fixed 2026-08-18)

`Reading.score` is optional and `Sensor` is a Protocol, so a sensor outside this package
may report only `passed`. `aggregate_readings` summed the raw tuple and raised `TypeError`
— **after every trial in the run had already been paid for**. Now falls back to the
boolean, which is the documented contract ("the sensor is the grader").

Regression: `ix/tests/ix/test_strict_regressions.py`.

### I-4 — `expected_skill: null` silently un-scored a probe (fixed 2026-08-18)

`ActivationSensor.from_config`'s comprehension guarded on
`p.metadata.get(...) or config.expected_skill` but stored `p.metadata.get(..., default)`.
A probe carrying an explicit empty `expected_skill:` passed the guard on the *config's*
truthiness and then stored its own `None`. The sensor read that back as "no expectation"
and scored the probe against nothing while still reporting a verdict for it — a silently
wrong measurement, which in an eval harness is the worst kind of bug.

Regression: `ix/tests/ix/test_strict_regressions.py`.

### I-5 — a fractional score overrode the sensor's verdict (fixed 2026-08-18)

`aggregate_readings` set `ProbeResult.passed = score > 0.5`, discarding `Reading.passed`
entirely. For a binary sensor the two agree. For `FunctionTestSensor` they diverge: a
submission failing 1 of 4 test cases scores 0.75 and was **reported as PASS** while the
sensor had already ruled it incorrect.

In a benchmark a wrong verdict is worse than a crash — a crash gets noticed. Now the
verdict is the sensor's (majority of trials passed) and aggregation only counts, which is
what its own docstring says it does.

Found by `lab/sensor-integrity` on its first run, not by review. Regression:
`ix/tests/ix/test_strict_regressions.py`.

## Not covered

- ix does not sandbox generated code. See boundary 1.
- ix does not verify experiment provenance. There is no signature on an experiment
  directory.
- `--mock` uses a seeded PRNG for run reproducibility. It is not a security control and
  its seed is not a secret.

## Reporting

Open an issue on the gnx repository. Pre-gen-0 component; no embargo channel.
