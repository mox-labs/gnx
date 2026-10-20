"""The ``http-load`` probe kind, driven by k6.

One application is one k6 run: an optional warm-up, then the measured load for a fixed time.
The script takes its settings from the environment, so nothing an experiment writes is
interpolated into code and a credential is never written to disk.

It leaves the facet ``load``: ``{tool, load_model, duration_s, requests, throughput, sent,
failed, error_rate, dropped, latency_ms: {...}, ttfb_ms: {...}}``, the two distributions keyed by
``min, mean, p50, p90, p95, p99, p999, max``. ``latency_ms`` runs from request sent to
response complete, ``ttfb_ms`` to the first byte. ``requests``, ``throughput`` (per second
of measured load) and both distributions count only requests the subject answered
successfully within the measured window, so a fast refusal never reads as a fast answer and
a request still in flight when the window closes is not counted; ``sent`` counts every
request of the measured window, ``failed`` those that failed, and ``error_rate`` is their
share. Any load tool that offers ``load`` leaves this shape, so a sensor
reads it without knowing the tool.

Whose failure: k6 missing or failing, and no request sent, are the harness's. No request
answered successfully, and under ``verify_body`` a first response with no body or a body
that differs from it, are the subject's. Under an open model every client is started before
the load, so k6 drops an arrival only when every client is waiting on the subject: the
subject was too slow for the rate, and the observation stands with its ``dropped`` count and
the latencies that caused it.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ix.domain.kind import arrivals, seconds
from ix.domain.run import HarnessError, SubjectError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ix.adapters._out.processes import Launcher
    from ix.domain.run import Target

#: Time allowed beyond the load itself for k6 to start, stop and write its summary.
OVERHEAD_S = 60.0

#: k6's names for the statistics ix keeps of one trial's distribution.
_STATISTICS = {
    "min": "min",
    "mean": "avg",
    "p50": "med",
    "p90": "p(90)",
    "p95": "p(95)",
    "p99": "p(99)",
    "p999": "p(99.9)",
    "max": "max",
}

SCRIPT = """\
import http from 'k6/http';
import crypto from 'k6/crypto';
import { Rate } from 'k6/metrics';

const cfg = JSON.parse(__ENV.IX_LOAD);
const mismatch = new Rate('ix_body_mismatch');
const params = { headers: cfg.headers, responseType: cfg.verify ? 'binary' : 'none' };

export const options = {
  scenarios: cfg.scenarios,
  thresholds: {
    'http_req_duration{scenario:measured,expected_response:true}': [],
    'http_req_waiting{scenario:measured,expected_response:true}': [],
    'http_reqs{scenario:measured,expected_response:true}': [],
    'http_reqs{scenario:measured}': [],
    'http_req_failed{scenario:measured}': [],
    'dropped_iterations{scenario:measured}': [],
  },
  summaryTrendStats: ['min', 'avg', 'med', 'max', 'p(90)', 'p(95)', 'p(99)', 'p(99.9)', 'count'],
};

function digest(response) {
  return response.body === null ? null : crypto.sha256(response.body, 'hex');
}

export function setup() {
  if (!cfg.verify) return {};
  const first = http.request(cfg.method, cfg.url, null, params);
  if (first.error_code || first.body === null) {
    return { digest: null, failed: first.error || `status ${first.status}` };
  }
  return { digest: digest(first) };
}

export default function (data) {
  const response = http.request(cfg.method, cfg.url, null, params);
  if (!cfg.verify) return;
  const found = digest(response);
  mismatch.add(found === null || found !== data.digest);
}

export function handleSummary(data) {
  return { [__ENV.IX_SUMMARY]: JSON.stringify(data) };
}
"""


def scenarios(options: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
    """The k6 scenarios for a probe's options, by name, with the measured seconds.

    Validation holds the options as written; this raises :class:`HarnessError` for a value
    bound per cell that k6 cannot carry out."""
    duration = seconds(options.get("duration"))
    if duration is None or duration <= 0:
        raise HarnessError("option", f"duration {options.get('duration')!r} is not a duration")
    warmup = seconds(options.get("warmup")) or 0.0
    model = options.get("load_model")
    clients = int(options.get("concurrency") or 0)
    if model == "open":
        rate = arrivals(options.get("rate"))
        if rate is None:
            raise HarnessError("option", f"rate {options.get('rate')!r} is not an arrival rate")
        count, per = rate
        per_second = max(1, math.ceil(count / (60 if per == "m" else 1)))
        # Every client is started before the load: k6 drops an arrival while it starts a
        # client of its own, so a drop then would not mean the subject kept them all waiting.
        clients = clients or max(100, per_second * 10)
        executor: dict[str, Any] = {
            "executor": "constant-arrival-rate",
            "rate": count,
            "timeUnit": f"1{per}",
            "preAllocatedVUs": clients,
            "maxVUs": clients,
        }
    elif model == "closed":
        executor = {"executor": "constant-vus", "vus": clients or 1}
    else:
        raise HarnessError("option", f"load_model {model!r} is not open or closed")
    found: dict[str, Any] = {}
    if warmup > 0:
        found["warmup"] = {**executor, "duration": f"{warmup:g}s", "gracefulStop": "0s"}
    found["measured"] = {
        **executor,
        "duration": f"{duration:g}s",
        "startTime": f"{warmup:g}s",
        # A request still in flight when the window closes is cut off, not counted.
        "gracefulStop": "0s",
    }
    return found, duration


def observation(summary: Mapping[str, Any], *, model: str, duration_s: float) -> dict[str, Any]:
    """The ``load`` observation from a k6 summary.

    Raises :class:`HarnessError` when no request was sent, and :class:`SubjectError` when
    none was answered successfully."""
    metrics = summary.get("metrics", {})

    def values(name: str, *, answered: bool = False) -> dict[str, Any]:
        tags = "scenario:measured,expected_response:true" if answered else "scenario:measured"
        found: dict[str, Any] = metrics.get(f"{name}{{{tags}}}", {}).get("values", {})
        return found

    sent = int(values("http_reqs").get("count", 0))
    if sent == 0:
        raise HarnessError("no_requests", "no request was sent during the measured load")
    answered = int(values("http_reqs", answered=True).get("count", 0))
    if answered == 0:
        raise SubjectError("no_answer", f"none of {sent} request(s) was answered successfully")

    def distribution(name: str) -> dict[str, float]:
        found = values(name, answered=True)
        return {ours: float(found[theirs]) for ours, theirs in _STATISTICS.items()}

    return {
        "tool": "k6",
        "load_model": model,
        "duration_s": duration_s,
        "requests": answered,
        "throughput": answered / duration_s,
        "sent": sent,
        # k6 counts a request for which http_req_failed is true as a "pass" of that rate.
        "failed": int(values("http_req_failed").get("passes", 0)),
        "error_rate": float(values("http_req_failed").get("rate", 0.0)),
        "dropped": int(values("dropped_iterations").get("count", 0)),
        "latency_ms": distribution("http_req_duration"),
        "ttfb_ms": distribution("http_req_waiting"),
    }


def load_observation(
    summary: Mapping[str, Any],
    *,
    verify: bool,
    duration_s: float,
    model: str,
) -> dict[str, Any]:
    """The ``load`` observation of one k6 ``summary``.

    Raises :class:`SubjectError` when nothing was answered, or under ``verify`` when the
    first response had no body or a later body differed from it, and :class:`HarnessError`
    when nothing was sent."""
    first = summary.get("setup_data") or {}
    if verify and first.get("digest") is None:
        raise SubjectError(
            "no_answer",
            f"the first response, which every body is checked against, failed: "
            f"{first.get('failed', 'no body')}",
        )
    seen = observation(summary, model=model, duration_s=duration_s)
    metrics = summary.get("metrics", {})
    wrong = metrics.get("ix_body_mismatch", {}).get("values", {}).get("rate", 0)
    if verify and wrong and not math.isclose(wrong, 0.0):
        raise SubjectError(
            "body_mismatch", f"{wrong:.1%} of response bodies differed from the first one"
        )
    return seen


class K6Load:
    """HTTP load against the target's URL for a fixed time.

    Options: ``target`` (the URL), ``load_model`` (``open`` holds an arrival ``rate``;
    ``closed`` holds ``concurrency`` clients), ``duration``, ``warmup``, ``method``,
    ``headers`` and ``verify_body`` (``sha256``: every body must match the first one
    fetched).
    """

    def __init__(self, launcher: Launcher, binary: str = "k6") -> None:
        self._launcher = launcher
        self._binary = binary

    def budget_s(self, options: Mapping[str, Any]) -> float | None:
        total = (seconds(options.get("warmup")) or 0.0) + (seconds(options.get("duration")) or 0.0)
        return total + OVERHEAD_S

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        binary = shutil.which(self._binary)
        if binary is None:
            raise HarnessError(
                "tool_missing", f"{self._binary} is not on PATH; http-load needs it installed"
            )
        scenarios_by_name, duration = scenarios(options)
        config = {
            "url": str(options["target"]),
            "method": str(options.get("method") or "GET").upper(),
            "headers": {**(target.facets.get("headers") or {}), **(options.get("headers") or {})},
            "verify": options.get("verify_body") == "sha256",
            "scenarios": scenarios_by_name,
        }
        with tempfile.TemporaryDirectory(prefix="ix-k6-") as scratch:
            script, summary = Path(scratch, "load.js"), Path(scratch, "summary.json")
            script.write_text(SCRIPT, encoding="utf-8")
            process = await self._launcher.start(
                [binary, "run", "--quiet", "--no-color", "--no-usage-report", str(script)],
                cwd=scratch,
                env={**os.environ, "IX_LOAD": json.dumps(config), "IX_SUMMARY": str(summary)},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, errors = await process.communicate()
            finally:
                await self._launcher.stop(process)
            if process.returncode != 0 or not summary.is_file():
                tail = errors.decode("utf-8", errors="replace").strip()[-400:]
                raise HarnessError(
                    "tool_failed", f"k6 exited with code {process.returncode}: {tail}"
                )
            data = json.loads(summary.read_text(encoding="utf-8"))

        seen = load_observation(
            data,
            verify=bool(config["verify"]),
            duration_s=duration,
            model=str(options["load_model"]),
        )
        return {"load": seen}
