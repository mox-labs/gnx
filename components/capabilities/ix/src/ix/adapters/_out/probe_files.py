"""Probe sources on the filesystem.

``{source: directory, path: probes}`` reads every ``*.md`` file of a directory, in name
order. A file's frontmatter is the probe's truth and its body is the prompt; ``id`` (default:
the file's stem) and ``kind`` (default: ``prompt``) in the frontmatter are the probe's own.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import frontmatter
import yaml

from ix.domain.errors import ProbeSourceError
from ix.domain.experiment import ProbeBinding

if TYPE_CHECKING:
    from ix.domain.experiment import ProbeSource

SOURCES = ("directory",)
_DIRECTORY_OPTIONS = frozenset({"path"})


class ProbeFiles:
    """Resolves the probe sources ix ships: ``directory``."""

    def read(self, source: ProbeSource, base: str) -> tuple[ProbeBinding, ...]:
        if source.source != "directory":
            raise ProbeSourceError(
                f"no probe source named {source.source!r}. Sources: {', '.join(SOURCES)}",
                fix="probes: {source: directory, path: probes}",
                path="probes.source",
            )
        unknown = sorted(source.options.keys() - _DIRECTORY_OPTIONS)
        if unknown:
            raise ProbeSourceError(
                f"the directory source does not take {unknown}; it takes 'path'",
                fix="probes: {source: directory, path: probes}",
                path=f"probes.{unknown[0]}",
            )
        relative = source.options.get("path", "probes")
        directory = Path(base).parent / str(relative)
        if not directory.is_dir():
            raise ProbeSourceError(
                f"the probe directory {directory} does not exist",
                fix=f"create {directory} with one .md file per probe, or correct 'path'",
                path="probes.path",
            )
        return tuple(self._probe(file) for file in sorted(directory.glob("*.md")))

    def _probe(self, file: Path) -> ProbeBinding:
        try:
            post = frontmatter.loads(file.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as error:
            raise ProbeSourceError(
                f"{file} is not a probe file: {' '.join(str(error).split())}",
                fix="a probe file is YAML frontmatter between --- lines, then the prompt",
                path="probes.path",
            ) from error
        truth: dict[str, Any] = dict(post.metadata)
        probe_id = str(truth.pop("id", file.stem))
        kind = str(truth.pop("kind", "prompt"))
        options: dict[str, Any] = {"prompt": post.content}
        if truth:
            options["truth"] = truth
        return ProbeBinding(id=probe_id, kind=kind, options=options)
