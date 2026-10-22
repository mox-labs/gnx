"""ix's settings: the ``ix:`` section of matrix's config tiers.

Read with ``matrix.load_config(client_type=IxSettings, client_key="ix")``, so they come from
the same files as every tool's, later tiers winning: ``~/.matrix/config.yaml``,
``~/.ix/config.yaml``, ``./matrix.yaml``, ``./ix.yaml``, ``$MATRIX_CONFIG``, ``$IX_CONFIG``
(``matrix --skill -r config``). The same read gives the ``matrix:`` section ix also uses:
the observers of a run, and the ``models`` a ``model`` runtime is handed.

They hold what varies by where ix runs and not by experiment: where runs are kept. An
unknown key is refused, by its path and file.
"""

from __future__ import annotations

from typing import Annotated

from matrix import BesideFile
from pydantic import BaseModel, ConfigDict


class IxSettings(BaseModel):
    """The ``ix:`` section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Where every run is kept, one directory a run: a path relative to the file that sets
    #: it. Absent: ``.ix/runs`` beside each experiment.
    runs: Annotated[str | None, BesideFile()] = None
