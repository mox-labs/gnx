"""What a verdict rests on beyond the experiment's document (W3.6): the digest rule for a file
or a folder, and which options name files. The run path that freezes, records and compares
the digests is held in ``test_agent_runs.py::TestAVerdictIsTiedToWhatItJudged``."""

from __future__ import annotations

import hashlib
import os
import subprocess
from typing import TYPE_CHECKING

from ix.adapters._out.provenance import checkout, digest
from ix.domain.experiment import Experiment
from ix.domain.kind import check_options
from ix.domain.provenance import Content, Named, named_files
from ix.domain.registration import Registration, deviations

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ix.domain.kind import Kinds


def _folder(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


class TestTheDigestRule:
    def test_a_folder_is_its_files_and_their_relative_paths(self, tmp_path: Path) -> None:
        files = {"SKILL.md": "Review the diff.\n", "notes/tone.md": "Be brief.\n"}
        one, two = _folder(tmp_path / "one", files), _folder(tmp_path / "two", files)
        os.utime(two / "SKILL.md", (0, 0))
        (two / "empty").mkdir()
        for skipped in (".git/index", "__pycache__/x.pyc", ".DS_Store"):
            _folder(two, {skipped: "changes on its own"})
        assert digest(one) == digest(two) != ""
        (two / "notes" / "tone.md").rename(two / "tone.md")
        assert digest(one) != digest(two)

    def test_a_file_is_its_bytes_and_nothing_there_is_empty(self, tmp_path: Path) -> None:
        file = _folder(tmp_path, {"rubric.md": "Helpful.\n"}) / "rubric.md"
        assert digest(file) == hashlib.sha256(b"Helpful.\n").hexdigest()
        assert digest(tmp_path / "absent") == ""

    def test_a_link_back_up_the_tree_is_followed_once(self, tmp_path: Path) -> None:
        folder = _folder(tmp_path / "skill", {"SKILL.md": "Review.\n"})
        (folder / "loop").symlink_to(folder, target_is_directory=True)
        assert len(digest(folder)) == 64


class TestTheFilesAnExperimentNames:
    def test_each_option_of_a_path_type_names_its_files_in_every_cell(
        self, builtin_kinds: Kinds
    ) -> None:
        experiment = Experiment.model_validate(
            {
                "name": "variants",
                "question": "Which skill fires?",
                "genre": "evaluate",
                "factors": {"variant": ["terse", "verbose"]},
                "subjects": {
                    "agent": {
                        "kind": "agent-session",
                        "runtime": "mock",
                        "skills": ["skills/{variant}"],
                        "plugins": [{"type": "local", "path": "plugins/lint"}],
                        "model": "not-a-path",
                    }
                },
                "sensors": [{"kind": "judge", "rubric_file": "rubric.md", "model_runtime": "mock"}],
            }
        )
        assert named_files(experiment, builtin_kinds) == (
            Named("subjects.agent", "plugins", "plugins/lint", "plugin"),
            Named("subjects.agent", "skills", "skills/terse", "skill"),
            Named("subjects.agent", "skills", "skills/verbose", "skill"),
            Named("sensors.0", "rubric_file", "rubric.md", "rubric file"),
        )

    def test_a_paths_option_holds_paths_or_mappings_that_hold_one(
        self, builtin_kinds: Kinds
    ) -> None:
        session = builtin_kinds.get("subject", "agent-session")
        assert session is not None
        plugin = {"type": "local", "path": "plugins/lint"}
        assert check_options(session, {"runtime": "mock", "plugins": [plugin]}, "s") == []
        (problem,) = check_options(session, {"runtime": "mock", "skills": [{}]}, "s")
        assert (problem.path, problem.kind) == ("s.skills", "option_invalid")
        assert "a list of paths" in problem.message


def test_a_registration_that_froze_no_contents_is_not_compared() -> None:
    """A registration an earlier ix wrote holds no contents: nothing is compared, as a
    design value only one of two records holds is not."""
    skill = Content(entry="subjects.a", option="skills", path="s", label="skill s", digest="1")
    assert (
        deviations(Registration(experiment="e"), Registration(experiment="e", contents=(skill,)))
        == ()
    )


class TestTheCommit:
    def test_a_repository_with_no_commit_says_so(self, tmp_path: Path) -> None:
        subprocess.run(["git", "-C", str(tmp_path), "init", "--quiet"], check=True)
        assert checkout(str(tmp_path)).no_commit == "no commit yet"

    def test_a_machine_without_git_says_so(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
        found = checkout(str(tmp_path))
        assert (found.commit, found.no_commit) == ("", "git is not installed")
