# Changelog — ix-cosmic-ray

## 0.1.0 — 2026-11-07

The first release: cosmic-ray's mutation operators as a mutant source at ix 0.9's `ix.mutant-source`
point (owner rulings Q-MUT-G and HO1 (c′), 2026-11-06). ix depends on no mutant source; this package
is the one to install for `ix mutants sample`.

### Added

- **`ix-cosmic-ray.v1.mutant-source`**: every argument-free cosmic-ray operator over the whole file,
  each mutant keyed `<operator>:<occurrence>`, its span the mutated node's (widened where an operator
  rewrote more), and every occurrence an operator raised on counted with its reason.
- **cosmic-ray 8.7 as a generator library only**: `operator_names`, `get_operator`,
  `Operator.mutation_positions`, `ast_nodes`, `get_ast` and `mutate_code`. Its `init`, work
  database, runner and distributors are not used.
- **The registration imports no cosmic-ray**, so a session that asks only which sources are installed
  (`ix plan`) loads none of its dependencies.
