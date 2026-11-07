"""``ix.mutants``: mutation testing, as the library offers it. Provisional (ruling R2): its
names and the documents it returns may change in a minor release, said in the changelog.

* :func:`sample` draws the mutants of an experiment's ``test-suite`` scope and pins them as
  probe files, with each subject's selection map: ``ix mutants sample``.
* :func:`map` makes those maps alone, for mutant probe files another tool wrote:
  ``ix mutants map``.
* :func:`read` reads a run of ``mutation-score`` or ``mutation-regress``: its survivors, by
  class, and the tests it found no use for: ``ix mutants read``.

Each returns one JSON-ready document (``ix.v1.mutation-sample``, ``ix.v1.mutation-maps``,
``ix.v1.mutants-reading``)
and raises :class:`ix.ConfigError` (with a ``fix``) when it cannot do what was asked. The
root is built on first use, so importing ``ix`` wires nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ix.api import PathLike


def sample(
    experiment: PathLike,
    *,
    n: int | None = None,
    seed: int | None = None,
    exclude: PathLike | None = None,
    source: str | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Draw ``n`` mutants (100 when None) from ``seed`` (1) of the scope of the experiment's
    ``test-suite`` subjects, none that the experiment at ``exclude`` sampled, with the mutant
    source named ``source`` (the one installed when None), and run each suite's baseline
    under coverage.py's per-test contexts, past ``timeout_s`` (3600) refused. Writes
    ``probes/``, each subject's ``map`` and ``mutation/population.json`` beside the
    experiment, and nothing when it raises."""
    from ix.composition import root

    return root().mutants_sample(
        experiment, n=n, seed=seed, exclude=exclude, mutant_source=source, timeout_s=timeout_s
    )


def map(experiment: PathLike, *, timeout_s: float | None = None) -> dict[str, Any]:  # noqa: A001
    """Make each ``test-suite`` subject's selection map and nothing else, running its
    baseline under coverage.py's per-test contexts: for mutant probe files another tool
    wrote, which run with no mutant source installed. ``ix.v1.mutation-maps``."""
    from ix.composition import root

    return root().mutants_map(experiment, timeout_s=timeout_s)


def read(run: PathLike, *, every: bool = False) -> dict[str, Any]:
    """The survivors of a mutation run, each an assertion gap or a coverage gap, and its
    pruning candidates; ``run`` is a run's directory, or an experiment for its latest run.
    Lists 50 survivors and 50 candidates unless ``every``, and says when it cut."""
    from ix.composition import root

    return root().mutants_read(run, every=every)


__all__ = ["map", "read", "sample"]
