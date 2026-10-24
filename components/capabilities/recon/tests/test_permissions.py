"""Ruling A2: what recon writes for a mission is its owner's alone. Every directory it makes is
0700 and every file 0600, whatever the umask lets through: a mission of mail is as private as
the mailbox, and a scheduled one is written while nobody watches."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from recon.composition import root
from tests.recon_fakes import VirtualClock, plugins_with
from tests.test_stream_use import Feed

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def usual_umask() -> Iterator[None]:
    """The umask most shells set, which lets group and others read what is created: the modes
    below are recon's choice, not this one's."""
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _modes(top: Path) -> dict[str, int]:
    """Each path under ``top``, itself included, by its path relative to ``top``'s parent, with
    its permission bits."""
    found = {top.name: stat.S_IMODE(top.stat().st_mode)}
    for path in top.rglob("*"):
        found[str(path.relative_to(top.parent))] = stat.S_IMODE(path.lstat().st_mode)
    return found


def test_every_directory_is_0700_and_every_file_0600(tmp_path: Path, usual_umask: None) -> None:
    config = {
        "preserve_raw": True,
        "collectors": [
            {"name": "now", "type": "cli", "run": "echo '{\"a\": 1}'"},
            {"name": "broken", "type": "cli", "run": "echo 'no newline' >&2; exit 3"},
            {"name": "events", "type": "feed", "window": {"seconds": 10},
             "retain": {"windows": 1}},
        ],
    }  # fmt: skip
    given = tmp_path / "mission.yaml"
    given.write_text(yaml.safe_dump(config, sort_keys=False))
    clock = VirtualClock()
    recon = root({}, project=tmp_path, clock=clock, plugins=plugins_with(feed=Feed(clock, 1.0, 40)))

    recon.init("m", config=given)
    survey = recon.survey("m")
    recon.stream("m", "events", windows=2)
    recon.capture("m", {"tool_name": "mcp__x__y", "tool_input": {}, "tool_response": "ok"})

    assert survey.status == "incomplete", "a failed table leaves its archive's marker in place"
    modes = _modes(tmp_path / ".recon")
    made = {"dirs": 0, "files": 0}
    for path, mode in modes.items():
        if (tmp_path / path).is_dir():
            made["dirs"] += 1
            assert mode == 0o700, f"{path}: {mode:o}"
        else:
            made["files"] += 1
            assert mode == 0o600, f"{path}: {mode:o}"
    # The checker saw what it is about: a config, an archive with its meta, event log, marker,
    # tables and captures, a stream with its windows and tombstones, the mission's captures.
    written = {Path(path).name for path in modes}
    expected = (
        "config.yaml",
        "meta.yaml",
        "telemetry.jsonl",
        ".incomplete",
        "now.jsonl",
        "captures.jsonl",
        "raw",
        "events.jsonl",
        "retired.jsonl",
        "writer.lock",
    )
    for name in expected:
        assert name in written, name
    assert made["dirs"] >= 8 and made["files"] >= 12, made


def test_the_check_above_fails_on_a_file_any_other_user_can_read(tmp_path: Path) -> None:
    """Check the checker: a file left 0644 is caught by the same comparison."""
    leaked = tmp_path / "m" / "config.yaml"
    leaked.parent.mkdir(mode=0o700)
    leaked.write_text("collectors: []\n")
    leaked.chmod(0o644)

    modes = _modes(tmp_path / "m")

    assert modes["m/config.yaml"] != 0o600
