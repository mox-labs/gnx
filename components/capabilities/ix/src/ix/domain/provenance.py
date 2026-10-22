"""What a verdict rests on beyond the experiment's own document.

The experiment's digest covers the file, which names a subject's skills, plugins, fixtures
and agent definition, and a sensor's graders or rubric, by **path**. What is at those paths
is what was tested, so its digest is frozen at registration and recorded with each run
(:class:`Content`): a skill edited after registration is a deviation, as an edited probe
file is. An option holds files when its kind declares it a ``path`` or a list of
``paths``; :func:`named_files` lists them, and the composition root reads and digests them.

A ``command`` (a program and its arguments) names files too, though not every item is one:
``[python3, server.py, --port, "80"]`` runs the file ``server.py`` with the program
``python3`` from PATH. Each item is relative to the directory the option's ``within`` names
(a process's ``cwd``), and is digested when it names a file there: the program, when it is
the experiment's (``./proxy``), and each file an argument names. A program found on PATH
and an argument that names no file are not the experiment's.

**The digest of a file** is the sha256 of its bytes. **The digest of a folder** is the
sha256 of the sorted lines ``<path relative to the folder>\\0<digest of the file>\\n``,
one for each file under it, links followed. Its contents and their relative paths count;
modification times, permissions and empty folders do not, and neither do ``.git``,
``__pycache__`` and ``.DS_Store``, which change without what the folder says changing.
Nothing at the path digests as empty.

Where a run ran from is :class:`Provenance`: the commit and whether the work tree differed
from it, and the versions of what ran it. It is recorded, never compared: a new commit is
not a new experiment.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ix.domain.experiment import cells
from ix.domain.kind import fill, placeholders
from ix.domain.schema import Stored

if TYPE_CHECKING:
    from ix.domain.experiment import Experiment
    from ix.domain.kind import Kind, Kinds, Option

#: The option types whose values name files: one path, or a list of them.
FILE_TYPES = frozenset({"path", "paths"})

#: Names left out of a folder's digest.
UNDIGESTED = frozenset({".git", "__pycache__", ".DS_Store"})


@dataclass(frozen=True)
class Named:
    """A file or folder an option names: where the option is written (``subjects.agent``),
    the option (``skills``), the path as written (each ``{placeholder}`` filled for a cell),
    and what one of the option's items is called (``skill``). An item of a ``command`` is
    an ``argument``: digested only when it names a file, and never expanded."""

    entry: str
    option: str
    path: str
    noun: str
    argument: bool = False


class Content(Stored):
    """What one file or folder an option names held: where the option is written, the
    option, the path (relative to the experiment as written; a ``*`` expanded to what it
    matched), what a reader calls it (``skill review``) and its digest, empty when nothing
    is there. Never the content itself."""

    entry: str
    option: str
    path: str
    label: str = ""
    digest: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.entry, self.option, self.path)

    @property
    def field(self) -> str:
        """Where it is, as a deviation names it: ``subjects.agent.skills[skills/review]``."""
        return f"{self.entry}.{self.option}[{self.path}]"


def label(noun: str, path: str) -> str:
    """``skill review``: the noun, and the last segment of the path."""
    return f"{noun} {path.rstrip('/').rsplit('/', 1)[-1]}"


def _noun(name: str, many: bool) -> str:
    """What one item of an option is called: ``skills`` gives ``skill``, ``rubric_file``
    gives ``rubric file``."""
    words = name.replace("_", " ")
    return words[:-1] if many and words.endswith("s") else words


def _paths(value: Any) -> list[str]:
    """The paths an option's value names: a path, or each item of a list that is a path
    or a mapping with a ``path`` (a plugin written ``{type: local, path}``)."""
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list | tuple):
        return []
    found = []
    for item in value:
        path = item.get("path") if isinstance(item, Mapping) else item
        if isinstance(path, str) and path:
            found.append(path)
    return found


def _joined(directory: Any, item: str) -> str:
    """``item`` relative to ``directory``, itself relative to the experiment; an absolute
    item, or one in the experiment's own directory, as written."""
    if item.startswith("/") or not isinstance(directory, str) or directory in ("", "."):
        return item
    return f"{directory.rstrip('/')}/{item.removeprefix('./')}"


def _arguments(
    entry: str, name: str, option: Option, written: list[Mapping[str, Any]]
) -> list[Named]:
    """Each item of a ``command`` option, as a path relative to the experiment."""
    found: list[Named] = []
    for options in written:
        command = options.get(name)
        if not isinstance(command, list | tuple):
            continue
        for index, item in enumerate(command):
            if isinstance(item, str) and item and not placeholders(item):
                path = _joined(options.get(option.within), item)
                found.append(Named(entry, name, path, "program" if index == 0 else "file", True))
    return found


def _named(entry: str, kind: Kind | None, written: list[Mapping[str, Any]]) -> list[Named]:
    if kind is None:
        return []
    found: list[Named] = []
    for name, option in kind.options.items():
        if option.type == "command":
            found += _arguments(entry, name, option, written)
            continue
        if option.type not in FILE_TYPES:
            continue
        noun = _noun(name, option.type == "paths")
        for options in written:
            found += [Named(entry, name, path, noun) for path in _paths(options.get(name))]
    return found


def named_files(experiment: Experiment, kinds: Kinds) -> tuple[Named, ...]:
    """Every file or folder the experiment's subjects and named sensors give an option of
    their kind's ``path`` or ``paths`` type, each once, in the order written. A subject's
    options are filled for each cell, as a trial fills them, so a factor that picks a skill
    names each skill it can pick."""
    found: list[Named] = []
    for name, subject in experiment.subjects.items():
        filled = [fill(subject.options, cell) for cell in cells(experiment.factors)]
        found += _named(f"subjects.{name}", kinds.get("subject", subject.kind), filled)
    for index, sensor in enumerate(experiment.sensors):
        found += _named(f"sensors.{index}", kinds.get("sensor", sensor.kind), [sensor.options])
    return tuple(dict.fromkeys(found))


class RuntimeOrigin(Stored):
    """A runtime installed at one of matrix's two runtime points: its type URL, the
    distribution that registered it with that distribution's version, and each distribution
    it drives (``claude-agent-sdk`` for ``claude-sdk``) with its installed version, ``not
    installed`` for one that is not."""

    type_url: str
    distribution: str = ""
    version: str = ""
    drives: dict[str, str] = {}


class Provenance(Stored):
    """Where a run ran from. Recorded with the run and shown in its report; never compared
    with a registration."""

    #: The commit checked out in the git work tree the experiment sits in.
    commit: str = ""
    #: True when a tracked file differs from that commit (as ``git describe --dirty`` says);
    #: an untracked file does not count.
    dirty: bool = False
    #: Why there is no commit: ``not a repository``, ``no commit yet``, ``git is not
    #: installed``, or what git said.
    no_commit: str = ""
    #: The installed versions of matrix, ix and hardline, by distribution; ``not installed``
    #: for one that is not.
    versions: dict[str, str] = {}
    #: Every runtime installed at matrix's agent-runtime and model-runtime points.
    runtimes: tuple[RuntimeOrigin, ...] = ()
    #: Each setting the run read (``ix.runs``, ``matrix.observers``, ``matrix.models``) and
    #: the rung that set it: the config file (several, comma-separated, when tiers merged a
    #: mapping), ``default`` when no tier set it, or ``given in code`` when the caller passed
    #: the settings. Never the value.
    settings: dict[str, str] = {}

    def checkout(self) -> str:
        """The commit in words: ``commit 1a2b3c4d5e6f (dirty)``, or why there is none."""
        if not self.commit:
            return self.no_commit or "unknown"
        return f"commit {self.commit[:12]}" + (" (dirty)" if self.dirty else "")
