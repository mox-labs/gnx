# Testing a test suite

Does a test suite fail when the code is wrong? ix seeds faults (mutants: `<` for `<=`, `and`
for `or`, a constant moved by one) into one scope, applies each once to a fresh copy of the
tree, and runs the tests that ran its statement. The share killed is the suite's power to
show these faults, never a proof that the code is correct. This page runs it on a small
package, then shows what each step does. It calls no model.

## What you need

- **A mutant source.** ix makes no mutant itself; [ix-cosmic-ray](../../../ix-cosmic-ray/README.md)
  provides cosmic-ray's operators. It is on no package index: install it beside ix from this
  repository at its release's tag. Installed with `uv tool install`, run it again with
  `--with`:

  ```bash
  uv tool install "git+https://github.com/mox-labs/gnx@ix-v0.9.0#subdirectory=components/capabilities/ix" --with "git+https://github.com/mox-labs/gnx@ix-cosmic-ray-v0.1.0#subdirectory=components/capabilities/ix-cosmic-ray"
  ```

  From a clone, `uv --project components/capabilities run ix …` has it already.
- **pytest and pytest-cov in the suite's own environment**, not ix's. In an environment
  `uv venv` made, which has no pip, add them with `uv`'s pip interface, naming the suite's
  Python with `--python`; `ix mutants sample` refuses without them and prints the command.
- **A baseline that passes.** Deselect a test that fails, or fix it.

The package under test need not be installed (its dependencies must be): the folder its
scope files import from (`src/` in a src layout) is first on `PYTHONPATH` in every run.

## A first run

A package `calc` with two functions in `src/calc/ops.py`, four tests in `tests/`, and its own
`.venv` with pytest and pytest-cov. The experiment, `experiment.yaml` at the tree's root:

```yaml
name: calc-mutation
question: Do the tests fail when calc.ops is wrong?
genre: mutation-score
subjects:
  tests:
    kind: test-suite
    root: .
    command: [.venv/bin/python, -m, pytest, tests, -q]
    tests: [tests]
    scope: [src/calc/ops.py]
    map: mutation/tests.map.json
primary: {metric: killed}
```

Draw the mutants, then validate, register and run:

```bash
ix mutants sample . --n 20 --seed 1
ix validate . && ix register .
ix run .
ix mutants read .
```

```
Sampled 20 of 23 mutants from ix-cosmic-ray.v1.mutant-source
Drawn with:   n 20 (given), seed 1 (given), timeout_s 3600.0 (default)
By operator:  core/AddNot 2, core/NumberReplacer 2, core/ReplaceComparisonOperator_GtE_Eq 1, …, and 16 more (--json lists every one)
Left out:     none
Subject tests: baseline passed: 4 tests in 0.232s; 0 drawn mutant(s) no test runs (confirm: uncovered); about 4.5s of test time to run
Next: ix validate ., then ix plan, ix register, ix run
```

The run gives an estimate with its interval, never a verdict, and points to the survivors:

```
Verdict:      none: the genre gives estimates only
…
│ tests   │ subject │ -    │ killed value (rate) │ 0.8      │ [0.584, 0.9193] │ 20 │
…
Next: this genre gives estimates, not a verdict: `ix mutants read <run directory>` names each survivor and the tests to strengthen
```

```
tests (subject): killed 16 of 20 read (80.0%); unread none
Survivors (4)
  src/calc/ops.py:3  `<` -> `<=`  core/ReplaceComparisonOperator_Lt_LtE  assertion_gap: strengthen tests/test_ops.py::test_clamp_above, tests/test_ops.py::test_clamp_below, tests/test_ops.py::test_clamp_inside  m-78740f0070a5
  …
  src/calc/ops.py:11  `18` -> `17`  core/NumberReplacer  assertion_gap: strengthen tests/test_ops.py::test_adult  m-70f3fafaa873
```

Each survivor names a boundary no test pins: no test calls `clamp` at `low` exactly, or
`is_adult(18)`.

The repository's `lab/mutation/recon-items` is a larger one: recon's `items.py` under recon's
own suite, run from the capabilities workspace. Its 100 mutants are checked in, so sampling it
again is refused; from a clone, `ix mutants map lab/mutation/recon-items` makes its selection
map, and then it registers and runs (a long run: `ix run --detach`, then `ix status`).

```yaml
subjects:
  recon:
    kind: test-suite
    root: ../../../components/capabilities
    command: [.venv/bin/python, -m, pytest, recon/tests, -q, -p, no:cacheprovider]
    tests: [../../../components/capabilities/recon/tests]
    scope: [../../../components/capabilities/recon/src/recon/core/application/items.py]
    map: mutation/recon.map.json
```

## What `ix mutants sample` does

- It asks the mutant source for every mutant of each scope file, and keeps those that parse
  and change the code. It counts the rest by why: unparsable, unchanged, an annotation only,
  a `TYPE_CHECKING` block, a line marked `# ix: no mutate`.
- It draws `--n` of them from `--seed`, and pins each as `probes/m-<id>.yaml`.
- It runs each suite's baseline once under coverage.py's per-test contexts and writes the
  selection map.
- It refuses a `probes/` folder that already holds files: sample into a new experiment.

**`ix mutants map`** makes the map alone, for probe files another tool wrote: a `mutant` probe
file is the boundary any generator speaks, and it runs with no source installed.

## What a trial does

- The copy is first on `PYTHONPATH`, ahead of an editable install of the tree. Every run
  checks where each scope module was imported from: anywhere else is the harness's failure
  (`not_the_mutant`), never a survivor.
- A test that failed on the mutant kills it only if it fails again run alone on the mutant,
  and passes run alone on the same copy with the original restored. Each run of the suite
  has a temporary directory of its own.
- A survivor no test ran is run against every test (`confirm: uncovered`, the default). A run
  past its time limit is unread (`timeout`), never killed.
- Coverage records no test against a line a child process runs, so a test that drives the
  scope's code through a subprocess (a CLI test starting the tool) is never selected for it.
  For such a suite set `confirm: all`: every survivor then runs against every test.
- `--trials-in-flight N` runs N mutants at once, each in its own copy.

## Reading the survivors

**`ix mutants read`** names each survivor as an assertion gap (the tests that ran its
statement are listed: strengthen what they assert) or a coverage gap (no test ran it: write
one). It also gives the **pruning candidates**: the tests a greedy keep set leaves out of the
kill matrix, with the `deselect` list a variant needs. They are only candidates. Confirm them
with a `mutation-regress` on a fresh sample, disjoint from the first:

```sh
ix mutants sample <new experiment> --seed 2 --exclude lab/mutation/recon-items
```

The two suites meet the same mutants, so the difference is paired (Tango's score interval):

- With no mutant on which they disagree, 100 mutants bound a loss near 3.7 points, and 400
  near 1.
- ix removes no test.

[CONTRACT.md](../../CONTRACT.md) has every option, outcome and refusal; `ix --skill -r mutation`
teaches the flow to an agent.
