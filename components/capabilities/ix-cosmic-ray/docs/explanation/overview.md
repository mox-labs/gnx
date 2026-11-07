---
title: ix-cosmic-ray
summary: cosmic-ray's mutation operators as the mutant source ix draws from when it tests a test suite.
kind: capability
---

## What it is

ix-cosmic-ray gives ix its mutants. Installed beside ix, it is what `ix mutants sample` asks
for every small fault (`<` for `<=`, a constant moved by one) that could be seeded into a
file.

## The problem

ix measures whether a test suite fails when the code is wrong, by seeding faults and running
the tests. Something has to make the faults. Writing them by hand is slow and biased toward
the faults you already thought of. cosmic-ray has a set of mutation operators, but it also
brings its own runner, database and distributors, which would run the tests a second way,
beside ix's.

## How it works

A **mutant source** is the plug-in point where ix asks for mutants: given a file's text, it
returns every change it can make. ix-cosmic-ray is one, registered at ix's
`ix.mutant-source` point.

An **operator** is one kind of change, named as cosmic-ray names it
(`core/ReplaceComparisonOperator_Lt_LtE`). ix-cosmic-ray asks every operator that takes no
arguments, over the whole file.

Each mutant gets a **key**, `<operator>:<occurrence>`, counted over the whole file. The same
text and the same cosmic-ray give the same keys, so ix can pin a mutant to its file's digest
and draw the same sample again from the same seed.

Only cosmic-ray's operators and `mutate_code` are used. Applying a mutant, running the tests
and deciding a kill are ix's.

## Try it

Offline, on a small package `calc` with four tests (the walk-through is
[Testing a test suite](../../../ix/docs/how-to/test-a-test-suite.md)). From a clone, the
capabilities workspace has ix-cosmic-ray installed; `--json` shows what the source made:

```bash
uv --project components/capabilities run ix mutants sample <experiment> --n 5 --seed 1 --json
```

```
{"schema": "ix.v1.mutation-sample", "sampled": 5, "population": 23, "census": false,
 "source": {"type_url": "ix-cosmic-ray.v1.mutant-source", "drives": {"cosmic-ray": "…"}},
 "dropped": {"unparsable": 0, "unchanged": 0, "annotation": 0, "type_checking": 0, …},
 "unmade": {}, …}
```

Each drawn mutant is pinned as a probe file:

```yaml
kind: mutant
options:
  file: "src/calc/ops.py"
  start: [11, 18]
  end: [11, 20]
  original: "18"
  replacement: "17"
  operator: "core/NumberReplacer"
  mutant: "src/calc/ops.py:core/NumberReplacer:1"
  file_digest: "sha256:3bd31e2dd55e…"
```

## What it gives an agent

| What an agent can do | Why it can trust it | The command |
|---|---|---|
| Draw the faults to test a suite with | every argument-free operator over the whole file, the same keys for the same text, drawn from a stated seed | `ix mutants sample <path> --n 100 --seed 1` |
| See what was left out, and why | ix counts every mutant it did not keep by reason (unparsable, unchanged, an annotation only), and every occurrence an operator raised on, by operator | `ix mutants sample <path> --json` (`dropped`, `unmade`) |
| Know which generator made a result | the population records the source's type URL and the cosmic-ray version it drove | `source.drives` in the sample's JSON |
| Plan without loading cosmic-ray | the registration imports no cosmic-ray: only sampling builds the source | `ix plan <path>` |
| Use another generator instead | a `mutant` probe file is the boundary; ix runs probe files any tool wrote, with no source installed | `ix mutants map <path>` |

## What it does not do

- **It asks no operator that takes arguments.** `core/VariableReplacer` and
  `core/VariableInserter` are never asked.
- **Not every mutant it makes parses.** cosmic-ray's binary-operator replacers mistake a
  signature's `*` and `/` markers, and `**`, for operators. ix keeps only mutants that parse
  and change the code, and counts the rest.
- **`core/ExceptionReplacer` names an exception nothing defines**
  (`CosmicRayTestingException`), as cosmic-ray does, so the handler raises `NameError` when an
  exception reaches it.
- **It runs no test and reads no file.** It is given text and returns changes.
- **It loads every cosmic-ray operator provider installed** in ix's environment, a third
  party's included, as code in ix's process ([SECURITY.md](../../SECURITY.md)).

## Next

- [README](../../README.md): install, and what the source gives.
- [Testing a test suite](../../../ix/docs/how-to/test-a-test-suite.md): the whole flow, from
  sampling to reading the survivors.
- `ix --skill -r mutation`: the flow as an agent reads it.
- [CONTRACT.md](../../CONTRACT.md): what the source promises, its defaults and its failures.
