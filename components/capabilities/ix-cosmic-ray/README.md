# ix-cosmic-ray

[cosmic-ray](https://github.com/sixty-north/cosmic-ray)'s mutation operators as an
[ix](../ix/README.md) mutant source.

ix measures whether a test suite fails when the code is wrong, as an estimate over sampled
mutants (the `mutation-score` and `mutation-regress` genres). It makes no mutant itself: a mutant
source does, at ix's `ix.mutant-source` extension point. This package is one.

## Install

Install it beside ix, and `ix mutants sample` draws its mutants from cosmic-ray's operators.
With `uv tool install`, run ix's install again with `--with`:

```sh
uv tool install "git+https://github.com/mox-labs/gnx@ix-v0.9.0#subdirectory=components/capabilities/ix" --with "git+https://github.com/mox-labs/gnx@ix-cosmic-ray-v0.1.0#subdirectory=components/capabilities/ix-cosmic-ray"
```

It is on no package index: a package named `ix-cosmic-ray` there is not this one. From a
clone, the capabilities workspace has it with ix:

```sh
uv --project components/capabilities run ix mutants sample <experiment> --n 100 --seed 1
```

[Testing a test suite](../ix/docs/how-to/test-a-test-suite.md) walks through a first run on
a small package, offline, and reading its survivors.

cosmic-ray is used as a generator library only: its operators say where they can change a file,
and `mutate_code` makes one mutant as text. Its `init`, its work database, its runner and its
distributors are never used: ix applies each mutant to a fresh copy of the tree and runs the
tests. cosmic-ray's dependencies (aiohttp, sqlalchemy, gitpython, stevedore and more) come with
this package, never with ix.

## What it gives

- **Every argument-free operator, over the whole file.** `core/VariableReplacer` and
  `core/VariableInserter` take arguments and are not asked.
- **A key per mutant**, `<operator>:<occurrence>` (`core/NumberReplacer:3`), counted over the
  whole file as `mutate_code` counts it. ix's id is `<file>:<key>`, pinned to the file's digest.
- **The node's span and the text now in it.** Where an operator rewrites more than its node, the
  span widens to cover every change.
- **What it could not make**, by operator and with what it raised (`core/ExceptionReplacer`
  raises on some files): ix counts it in the sample's record.

ix keeps a mutant only when its text parses and changes the code, and counts the rest. A
buy-check on ix's own 56 source files (2026-11-06) found 19% of cosmic-ray's mutants do not
parse: its binary-operator replacers mistake a signature's `*` and `/` markers, and `**`, for
operators. On recon's `items.py`, 97 of 548 did not (18%), and ix left out another 110 that
change only a type annotation.

`core/ExceptionReplacer` puts `CosmicRayTestingException` where the caught type was, as cosmic-ray
itself does. Nothing defines that name in the code under test, so the handler raises `NameError`
when an exception reaches it.

## Next

| Page | For |
|---|---|
| [What ix-cosmic-ray is](docs/explanation/overview.md) | the mental model, and what it gives an agent |
| [Testing a test suite](../ix/docs/how-to/test-a-test-suite.md) | the flow, from sampling to the survivors |
| [CONTRACT.md](CONTRACT.md) | what each surface hides, its defaults and its failures |
| [CHANGELOG.md](CHANGELOG.md), [SECURITY.md](SECURITY.md) | what changed; what it runs |
