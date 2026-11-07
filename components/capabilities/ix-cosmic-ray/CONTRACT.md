# ix-cosmic-ray's contracts

What each published surface of ix-cosmic-ray hides, what it consists of, its defaults, how it fails,
and how far it is promised. The [README](README.md) shows how to use it; this file is what a change to
one of them must keep. `tests/test_source.py` holds them.

Stability is one of two words. **Public**: may change in a minor release, said in the CHANGELOG.
**Not contract**: may change without notice.

The package depends on `ix` (bounded to the minor it is built against) and `cosmic-ray==8.7.*`; ix
does not depend on it.

## The mutant source: `ix-cosmic-ray.v1.mutant-source`

**Stability: public**, and provisional while ix's `ix.mutant-source` point is (ix's CONTRACT.md).
Registered at that point from this distribution's `matrix.extensions` entry point (`"ix.mutant-source"
= "ix_cosmic_ray.source:register"`), as any third party's source is, with `drives: [cosmic-ray]`, so a
population records cosmic-ray's installed version.

**The decision it hides.** Which mutants exist: cosmic-ray's operator set, how its occurrences are
counted and how its output becomes ix's spans. A change of operator set or of cosmic-ray is this
package's release, never ix's.

**Members.** It keeps ix's `MutantSource` port: `mutations(text) -> Mutations`.

| | What |
|---|---|
| `made` | one `Mutation` per occurrence of every argument-free operator over the whole text, in cosmic-ray's operator order, then occurrence order |
| `key` | `<operator>:<occurrence>`, the occurrence counted from 0 over the whole text: the same for the same text and the same cosmic-ray |
| `operator` | cosmic-ray's name (`core/ReplaceComparisonOperator_Lt_LtE`) |
| `start`, `end` | `(line, column)`, the line from 1 and the column in characters: the mutated node's span, widened to every changed character where the operator rewrote more |
| `replacement` | the text cosmic-ray put in that span |
| `unmade` | per operator, the occurrences it raised on or changed nothing at, with the first reason |

**Defaults.** None to set. Operators that take arguments are not asked: there is no configuration to
give them.

**Failure behaviour.** A text cosmic-ray's parser (parso) cannot read raises, and `ix mutants sample`
refuses the file and says so. An operator that raises on an occurrence is counted in `unmade` and the
rest go on. Building the source imports cosmic-ray; loading the registration does not.

**What it does not do.** It checks no mutant: ix keeps those that parse and change the code. It reads
no file, starts no process and writes nothing.

## The package's names

**Stability: public.** `ix_cosmic_ray.__all__`: `TYPE_URL`, `register`, `build`, `__version__`. The
generator module (`ix_cosmic_ray.generator`) is not contract: reach the source through ix.
