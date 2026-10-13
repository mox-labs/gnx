"""The ``directory`` probe source: one markdown file per probe.

``probes: {source: directory, path: probes}`` reads every ``*.md`` file of a directory, in
name order. A file's body is the probe's ``prompt`` option and its frontmatter is the
probe's truth, except three keys that are the probe's own: ``id`` (default: the file's
stem), ``kind`` (default: ``prompt``) and ``options`` (further options of that kind).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import frontmatter
import yaml

from ix.domain.errors import ProbeSourceError
from ix.domain.experiment import Probe

if TYPE_CHECKING:
    from collections.abc import Mapping

_OPTIONS = frozenset({"path"})


class DirectoryProbes:
    """Reads a directory of markdown probes."""

    def probes(self, options: Mapping[str, Any], base: str) -> tuple[Probe, ...]:
        unknown = sorted(options.keys() - _OPTIONS)
        if unknown:
            raise ProbeSourceError(
                f"the directory source takes no option {unknown[0]!r}; it takes 'path'",
                fix="probes: {source: directory, path: probes}",
                path=f"probes.{unknown[0]}",
            )
        directory = Path(base) / str(options.get("path", "probes"))
        if not directory.is_dir():
            raise ProbeSourceError(
                f"the probe directory {directory} does not exist",
                fix=f"create {directory} with one .md file per probe, or correct 'path'",
                path="probes.path",
            )
        return tuple(self._probe(file) for file in sorted(directory.glob("*.md")))

    def _probe(self, file: Path) -> Probe:
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
        extra = truth.pop("options", {})
        if not isinstance(extra, dict):
            raise ProbeSourceError(
                f"{file}: 'options' in the frontmatter is a mapping of the probe kind's options",
                fix="options: {<option>: <value>}",
                path="probes.path",
            )
        options = dict(extra)
        if post.content.strip() or kind == "prompt":
            options["prompt"] = post.content
        return Probe(id=probe_id, kind=kind, truth=truth, options=options)
