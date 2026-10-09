"""Filesystem storage adapter — YAML+MD in, JSONL and JSON out.

Directory convention (a lab is a workspace of experiments)::

  <lab>/<experiment>/
    experiment.yaml                         experiment config
    tasks/*.md                              probes (frontmatter + body); cases/ if no tasks/
    subjects/*.md                           subjects (frontmatter + body), optional
    results/<subject>/
      <run_id>/trials.jsonl                 one TrialRecord per trial, every repeat
      summary-<run_id>.json                 that run's ExperimentResults
      summary-latest.json                   the most recent run's, for `ix results`

Results are keyed by subject so running one subject never overwrites another's. The CLI
saves a ``--simulate`` run of a non-simulated subject as ``<subject>@simulated`` (directory
``<subject>-simulated``), so a harness check never replaces a measurement's latest summary.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import frontmatter
import yaml
from pydantic import ValidationError

from ix.domain.errors import ConfigError, ResultsNotFoundError
from ix.domain.types import Probe, Subject
from ix.eval.models import ExperimentConfig, ExperimentResults, TrialRecord

if TYPE_CHECKING:
    from pathlib import Path

_FILE_KEYS = frozenset(set(ExperimentConfig.model_fields) - {"probes"} | {"sensor"})
_LATEST = "summary-latest.json"

#: Keys earlier ix versions accepted, and where each now lives — so an old experiment.yaml fails
#: with the move to make rather than only a list of what is legal.
_MOVED = {
    "agent": "agent settings belong to a subject: subjects: [{name, config: {runtime, ...}}]",
    "skill": "the expected skill is per probe: `expected_skill:` in each tasks/*.md frontmatter",
}


def subject_dir_name(subject: str) -> str:
    """A subject name as a directory name: anything outside ``[A-Za-z0-9._-]`` becomes ``-``."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", subject).strip("-.") or "subject"


class FilesystemStore:
    """Loads experiments from YAML+MD; persists trial records and per-subject summaries."""

    def __init__(self, workspace: Path):
        self._workspace = workspace

    # --- loading -------------------------------------------------------------------

    def load_experiment(self, path: Path) -> ExperimentConfig:
        """Load an experiment directory. Errors name the file and the key."""
        config_path = path / "experiment.yaml"
        if not config_path.exists():
            raise ConfigError(f"no experiment.yaml in {path}")
        try:
            config = yaml.safe_load(config_path.read_text()) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"{config_path}: not valid YAML: {e}") from e
        if not isinstance(config, dict):
            raise ConfigError(f"{config_path}: top level must be a mapping")
        unknown = set(config) - _FILE_KEYS
        if unknown:
            moved = "".join(f"; {k}: {_MOVED[k]}" for k in sorted(unknown) if k in _MOVED)
            raise ConfigError(
                f"{config_path}: unknown key(s) {sorted(unknown)}. "
                f"Legal: {sorted(_FILE_KEYS)}{moved}"
            )
        data: dict[str, Any] = {
            **config,
            "name": config.get("name", path.name),
            "subjects": tuple(self._load_subjects(path, config)),
            "probes": tuple(self._load_probes(path)),
        }
        try:
            return ExperimentConfig.model_validate(data)
        except ValidationError as e:
            issues = "; ".join(
                f"{'.'.join(map(str, i['loc'])) or '(root)'}: {i['msg']}" for i in e.errors()
            )
            raise ConfigError(f"{config_path}: {issues}") from None

    def _load_probes(self, exp_path: Path) -> list[Probe]:
        """Probes from tasks/ (or cases/). Frontmatter is metadata; the body is the prompt."""
        tasks_dir = exp_path / "tasks"
        probe_dir = tasks_dir if tasks_dir.exists() else exp_path / "cases"
        if not probe_dir.exists():
            return []
        probes = []
        for md_path in sorted(probe_dir.glob("*.md")):
            post = frontmatter.load(str(md_path))
            metadata = dict(post.metadata)
            # Frontmatter is untyped YAML: `id: 1` parses to an int, and Probe.id is the join
            # key the whole ledger indexes on. Coerce rather than mismatch on lookup.
            probe_id = str(metadata.pop("id", md_path.stem))
            probes.append(Probe(id=probe_id, prompt=post.content.strip(), metadata=metadata))
        return probes

    def _load_subjects(self, exp_path: Path, config: dict[str, Any]) -> list[Subject]:
        """Subjects from subjects/*.md when there are any, else the YAML ``subjects:`` list."""
        subjects_dir = exp_path / "subjects"
        subject_files = sorted(subjects_dir.glob("*.md")) if subjects_dir.is_dir() else []
        # Only a directory that holds subject files takes precedence: an empty subjects/
        # must not silently discard the YAML subjects.
        if subject_files:
            subjects = []
            for md_path in subject_files:
                post = frontmatter.load(str(md_path))
                meta = dict(post.metadata)
                subjects.append(
                    Subject(
                        name=str(meta.pop("name", md_path.stem)),
                        description=str(meta.pop("description", "")),
                        config={**meta, "system_prompt": post.content.strip()},
                    )
                )
            return subjects

        subjects = []
        for raw in config.get("subjects") or []:
            if isinstance(raw, str):
                subjects.append(Subject(name=raw))
            elif isinstance(raw, dict) and "name" in raw:
                subjects.append(
                    Subject(
                        name=str(raw["name"]),
                        description=str(raw.get("description", "")),
                        config=raw.get("config") or {},
                    )
                )
            else:
                raise ConfigError(
                    f"{exp_path / 'experiment.yaml'}: each subjects entry needs a name"
                )
        return subjects

    def list_experiments(self, base: Path) -> list[Path]:
        """Every directory under ``base`` that holds an experiment.yaml."""
        if not base.exists():
            return []
        return [
            c for c in sorted(base.iterdir()) if c.is_dir() and (c / "experiment.yaml").exists()
        ]

    # --- results -------------------------------------------------------------------

    def _results(self, experiment: str, subject: str | None = None) -> Path:
        root = self._workspace / experiment / "results"
        return root / subject_dir_name(subject) if subject is not None else root

    def append_trials(
        self, experiment: str, subject: str, run_id: str, records: list[TrialRecord]
    ) -> Path:
        """Append records to the run's trials.jsonl; return its path relative to the experiment."""
        run_dir = self._results(experiment, subject) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        log = run_dir / "trials.jsonl"
        with log.open("a") as f:
            for record in records:
                f.write(record.model_dump_json() + "\n")
        return log.relative_to(self._workspace / experiment)

    def save_summary(self, experiment: str, results: ExperimentResults) -> Path:
        directory = self._results(experiment, results.subject)
        directory.mkdir(parents=True, exist_ok=True)
        text = results.model_dump_json(indent=2)
        path = directory / f"summary-{results.run_id or 'run'}.json"
        path.write_text(text)
        (directory / _LATEST).write_text(text)
        return path

    def load_summary(self, experiment: str, subject: str) -> ExperimentResults:
        path = self._results(experiment, subject) / _LATEST
        if not path.exists():
            have = ", ".join(self.subjects_with_results(experiment)) or "none yet"
            raise ResultsNotFoundError(
                f"no results for subject {subject!r} in {experiment}. Subjects with results: {have}"
            )
        return ExperimentResults.model_validate_json(path.read_text())

    def subjects_with_results(self, experiment: str) -> list[str]:
        root = self._results(experiment)
        if not root.is_dir():
            return []
        names = []
        for d in sorted(root.iterdir()):
            latest = d / _LATEST
            if d.is_dir() and latest.exists():
                names.append(ExperimentResults.model_validate_json(latest.read_text()).subject)
        return names
