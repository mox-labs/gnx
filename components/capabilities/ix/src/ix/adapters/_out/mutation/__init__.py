"""Mutation testing: whether a test suite fails when the code is wrong, as an estimate over
sampled mutants.

A trial applies one mutant to a fresh copy of a tree and runs the suite's tests that ran its
line; a kill counts only when the failing tests pass without the mutant. The kit is the
``test-suite`` subject, the ``mutant`` probe and the ``mutant-kill`` sensor (:mod:`.suite`),
the population and its probe files (:mod:`.population`), the selection map
(:mod:`.selection`), sampling (:mod:`.sample`) and the survivor and pruning report
(:mod:`.reading`). The mutants come from a mutant source at ix's ``ix.mutant-source`` point
(``ix-cosmic-ray`` is one), or from probe files any tool wrote.

It imports only :mod:`ix.extension`, the standard library and itself (the layer map holds it
to that), so it could be its own package with no edit but the package's name. The suite's
side, ix's pytest plugin (``plugin/``) and the coverage reader (``scripts/``), runs in the
suite's interpreter and imports nothing of ix.
"""

from ix.adapters._out.mutation.suite import Mutant, MutantKill, TestSuite

__all__ = ["Mutant", "MutantKill", "TestSuite"]
