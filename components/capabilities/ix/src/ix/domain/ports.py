"""The one port the experiment model has: where probes come from.

A **ProbeSource** hides a decision that changes: probes are a directory of files today, and
a dataset or an archive later. There is one adapter per source, keyed by the name an
experiment writes in ``probes: {source: <name>, ...}``.

Experiment files, genre and kind files and the registration file are not ports. Their
layout is published (it is what an author writes and commits), so they are concrete classes
used by the composition root, and the use cases take what was read as data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ix.domain.experiment import Probe


class ProbeSource(Protocol):
    """Resolves one kind of probe source into probes, in a fixed order."""

    def probes(self, options: Mapping[str, Any], base: str) -> tuple[Probe, ...]:
        """The probes ``options`` describe, with relative paths resolved against the
        directory ``base``.

        Raises :class:`ix.domain.errors.ProbeSourceError` when the source cannot be read or
        does not take an option it was given.
        """
        ...
