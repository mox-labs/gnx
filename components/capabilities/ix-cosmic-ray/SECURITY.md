# Security — ix-cosmic-ray

ix-cosmic-ray makes mutants of text it is given. It reads no file, starts no process, opens no
connection and writes nothing; ix reads the scope files, applies the mutants to copies and runs the
tests (ix's [SECURITY.md](../ix/SECURITY.md)).

## What runs when the source is built

- **cosmic-ray, and the operator providers installed beside it.** Building the source imports
  `cosmic_ray.plugins`, which loads every `cosmic_ray.operator_providers` entry point of the
  environment through stevedore, a third party's providers included, and asks each of its
  operators. An installed provider is code that runs in ix's process: install only providers you
  would run. cosmic-ray's own operators are the `core/` ones.
- **Only when asked.** Loading this package's registration (any ix session that reads the
  `ix.mutant-source` point) imports no cosmic-ray; `ix mutants sample` builds the source.

## What a mutant can do

A mutant is a change to a copy of your code, which ix's trial then runs under your test suite: the
suite runs mutated code with whatever access the suite has. cosmic-ray's operators change operators,
constants, conditions, decorators and loops; they add no import and no call of their own, except
`core/ExceptionReplacer`'s name `CosmicRayTestingException`, which nothing defines.
