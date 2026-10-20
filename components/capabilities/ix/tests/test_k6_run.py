"""The real thing, through the command line: a local HTTP server as the subject, k6 as the
load tool, matrix underneath.

Skipped where ``k6`` is not installed. The runs are small (a second of load per trial), so
what is asserted is that the path works and cleans up, never which way a timing fell.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

import ix
from ix.adapters._out.k6_load import K6Load
from ix.adapters._out.matrix_process import MatrixLauncher
from ix.adapters._out.processes import answers
from ix.domain.run import Target

pytestmark = [
    pytest.mark.skipif(shutil.which("k6") is None, reason="k6 is not on PATH"),
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals"),
]

SERVER = Path(ix.__file__).parents[2] / "examples" / "delay-server" / "server.py"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _experiment(tmp_path: Path, *, duration: str = "1s", repeats: int = 5) -> tuple[Path, int]:
    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    def server(delay: int) -> dict[str, Any]:
        return {
            "kind": "process",
            "command": [sys.executable, str(SERVER), "--port", str(port), "--delay-ms", str(delay)],
            "url": url,
            "ready": f"{url}/healthz",
        }

    data = {
        "name": "tiny",
        "question": "Is the candidate no slower than the baseline?",
        "genre": "compare",
        "subjects": {"baseline": server(5), "candidate": server(5)},
        "probes": [
            {
                "kind": "http-load",
                "target": "{url}/work",
                "load_model": "closed",
                "concurrency": 2,
                "duration": duration,
            }
        ],
        "primary": {"metric": "latency", "statistic": "p50"},
        "comparison": "superiority",
        "guardrails": [{"metric": "memory_peak", "margin": "50%"}],
        "metrics": ["throughput", "error_rate"],
        "repeats": repeats,
    }
    file = tmp_path / "experiment.yaml"
    file.write_text(yaml.safe_dump(data, sort_keys=False))
    return file, port


def _ix(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ix.adapters._in.cli", *args],
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_register_run_and_report_through_the_command_line(tmp_path: Path) -> None:
    file, port = _experiment(tmp_path)
    assert _ix("register", str(file)).returncode == 0
    ran = _ix("run", str(file), "--json")
    assert ran.returncode in (0, 10, 11), ran.stderr
    assert ran.stderr == ""  # under --json, stderr is for an error envelope only
    report = json.loads(ran.stdout)
    assert report["schema"] == "ix.v1.report" and report["status"] == "completed"
    assert report["gated"] and report["verdict"] in ("holds", "fails", "inconclusive")
    assert report["trials"] == {
        "planned": 15, "carried_out": 15, "subject_failures": 0, "harness_failures": 0,
    }  # fmt: skip
    assert report["analysis"]["failures"] == [] and report["readings"] == "recorded"

    # Every metric named was read for every trial, from the two sensors that provide them.
    readings = [
        json.loads(line)
        for line in Path(report["location"], "ix.v1.reading.jsonl").read_text().splitlines()
    ]
    assert len(readings) == 15 * 4 and all(row["failure"] is None for row in readings)
    by_metric = {row["metric"]: row for row in readings}
    assert 4.0 < by_metric["latency"]["statistics"]["p50"] < 500.0
    assert by_metric["throughput"]["value"] > 1
    errors = by_metric["error_rate"]["statistics"]
    assert errors["events"] == 0 and errors["units"] >= 1  # a count: failed of sent
    assert by_metric["memory_peak"]["value"] > 1_000_000
    assert {row["sensor"] for row in readings} == {"ix.v1.sensor.timing", "ix.v1.sensor.memory"}

    # The subject was stopped after its last trial, and the report can be made again.
    assert not asyncio.run(answers(f"http://127.0.0.1:{port}/healthz"))
    again = _ix("report", report["location"], "--json")
    assert again.returncode == ran.returncode and json.loads(again.stdout) == report
    text = _ix("report", str(file))
    assert text.returncode == ran.returncode
    assert (
        f"Verdict:      {report['verdict']}" in text.stdout and "Intervals are 95%" in text.stdout
    )
    listed = json.loads(_ix("runs", str(file), "--json").stdout)
    assert [run["run"] for run in listed["runs"]] == [report["run"]]


def test_ctrl_c_stops_the_run_cleanly_and_leaves_a_reportable_record(tmp_path: Path) -> None:
    file, port = _experiment(tmp_path, duration="2s", repeats=5)
    process = subprocess.Popen(
        [sys.executable, "-m", "ix.adapters._in.cli", "run", str(file), "--exploratory", "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ},
    )
    try:
        table = None
        until = time.monotonic() + 120
        while table is None and time.monotonic() < until:
            found = list((tmp_path / ".ix" / "runs").glob("*/ix.v1.interaction.jsonl"))
            if found and len(found[0].read_text().splitlines()) >= 2:
                table = found[0]
            else:
                assert process.poll() is None, process.stderr.read() if process.stderr else ""
                time.sleep(0.05)
        assert table is not None, "the run recorded nothing"
        time.sleep(0.5)  # into the next trial: a subject is up and under load
        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=120)
    finally:
        process.kill()
    assert process.returncode == 12, err
    report = json.loads(out)
    assert report["status"] == "interrupted" and report["verdict"] is None
    assert 2 <= report["trials"]["carried_out"] < 15
    assert report["readings"] == "derived"
    # Nothing the run started is left: the subject's port is free again.
    assert not asyncio.run(answers(f"http://127.0.0.1:{port}/healthz"))
    again = _ix("report", report["location"], "--json")
    assert again.returncode == 12 and json.loads(again.stdout)["status"] == "interrupted"


def _slow_server(delay_ms: int) -> tuple[subprocess.Popen[bytes], str]:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(port), "--delay-ms", str(delay_ms)]
    )
    url = f"http://127.0.0.1:{port}"
    until = time.monotonic() + 20
    while not asyncio.run(answers(f"{url}/healthz")):
        assert time.monotonic() < until and server.poll() is None, "the server did not come up"
        time.sleep(0.05)
    return server, url


def _load(options: dict[str, Any]) -> dict[str, Any]:
    probe = K6Load(MatrixLauncher())
    applied = asyncio.run(probe.apply(options, Target(subject="slow"), seed=1))
    return dict(applied["load"])


def test_a_subject_too_slow_for_an_open_models_rate_is_measured() -> None:
    """20 arrivals a second at a subject that answers in 1.5s keeps about 30 clients
    waiting. That is the subject's doing, so the trial is an observation, not missing data."""
    server, url = _slow_server(1500)
    try:
        seen = _load(
            {"target": f"{url}/work", "load_model": "open", "rate": "20/s", "duration": "3s"}
        )
    finally:
        server.kill()
        server.wait()
    assert seen["latency_ms"]["p50"] >= 1500 and seen["requests"] > 0


def test_throughput_counts_only_requests_answered_within_the_measured_time() -> None:
    """Each of 3 clients can finish at most floor(2s / 0.7s) = 2 requests of 0.7s in a 2s
    window; a third, still in flight when the window closes, is not counted."""
    server, url = _slow_server(700)
    try:
        seen = _load(
            {"target": f"{url}/work", "load_model": "closed", "concurrency": 3, "duration": "2s"}
        )
    finally:
        server.kill()
        server.wait()
    assert seen["requests"] <= 3 * 2
    assert seen["throughput"] == seen["requests"] / 2.0
