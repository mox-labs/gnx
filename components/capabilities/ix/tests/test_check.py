"""``check``: a predicate observed on every trial, and the ``command`` probe that checks a
subject (ruling Q-CHECK; review §5).

The genre's analysis decides on a boolean primary alone, with no interval: it holds when the
primary read true on every trial, fails when it read false on one or the subject failed one,
and is inconclusive only when a trial could not be read (a harness failure, a reading that
could not be taken), so a missing observation is never a failure of the subject (RUBRIC H1).
Its warrant says it is an observation, not an estimate, and for a subject whose result can
vary gives the Wilson interval of what it saw: 1 of 1 leaves about [0.21, 1].

The ``command`` probe runs a program once with the subject up; ``exit-status`` reads its
code. Each runs here against an ``http`` subject that nothing contacts, with no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import ix
from ix.application.experiments import examine
from ix.domain.analysis import Analysis, analyze
from ix.domain.run import Failure, Interaction, Reading, Trial, schedule

if TYPE_CHECKING:
    from ix.application.experiments import Vocabulary
    from ix.domain.design import Design

Write = Any  # the write_experiment fixture


def _check(probe: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A check of a command against an endpoint the command never has to reach."""
    return {
        "name": "a-check",
        "question": "Does the command succeed?",
        "genre": "check",
        "subjects": {"api": {"kind": "http", "url": "http://127.0.0.1:9"}},
        "probes": [{"id": "run", "kind": "command", **probe}],
        "primary": {"metric": "exited_ok"},
        **changes,
    }


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class _Run:
    """A check's design and schedule, and the tables a run would record, written by hand."""

    def __init__(self, vocabulary: Vocabulary, repeats: int = 1) -> None:
        examined = examine(_check({"command": _python("pass")}, repeats=repeats), vocabulary)
        assert examined.problems == (), examined.problems
        assert examined.experiment and examined.design and examined.probes
        self.design: Design = examined.design
        self.trials: tuple[Trial, ...] = schedule(
            examined.experiment, examined.design, examined.probes
        )
        self.interactions: list[Interaction] = []
        self.readings: list[Reading] = []

    def trial(self, seq: int, read: bool | None = None, failed: str = "", unread: str = "") -> _Run:
        """Trial ``seq``: carried out by ``failed`` (an owner) or read as ``read``; ``unread``
        is a reading's failure."""
        trial = self.trials[seq]
        failure = Failure(owner=failed, reason="test") if failed else None  # type: ignore[arg-type]
        self.interactions.append(
            Interaction(
                seq=seq,
                subject=trial.subject,
                role=trial.role,
                started_ns=0,
                duration_ns=1,
                failure=failure,
            )
        )
        if failed:
            return self
        reading = Reading(
            seq=seq,
            subject=trial.subject,
            role=trial.role,
            sensor="ix.v1.sensor.exit-status",
            metric="exited_ok",
            shape="boolean",
            value=read,
        )
        if unread:
            reading = reading.model_copy(
                update={"value": None, "failure": Failure(owner="sensor", reason=unread)}
            )
        self.readings.append(reading)
        return self

    def analyze(self) -> Analysis:
        return analyze(self.design, self.trials, self.interactions, self.readings)


class TestTheVerdict:
    def test_one_trial_that_held_holds_and_says_what_one_cannot_show(
        self, vocabulary: Vocabulary
    ) -> None:
        found = _Run(vocabulary).trial(0, read=True).analyze()
        assert found.outcome == "holds"
        (finding,) = found.findings
        (decision,) = finding.decisions
        assert (decision.form, decision.claim, decision.n) == (
            "predicate",
            "true on every trial",
            1,
        )
        assert decision.interval is None and found.family == 0
        method = " ".join(found.method)
        assert "Check, 1 trial in each cell" in method
        assert "an observation, not an estimate" in method
        assert "a pass shows the result is possible" in method
        (note,) = found.notes
        assert "true on 1 of 1" in note and "[0.21, 1.00]" in note and "Wilson" in note

    def test_one_false_reading_fails(self, vocabulary: Vocabulary) -> None:
        found = _Run(vocabulary, repeats=3).trial(0, read=True).trial(1, read=False)
        analysis = found.trial(2, read=True).analyze()
        assert analysis.outcome == "fails"
        assert analysis.findings[0].reason == "false on 1 of 3 read"

    def test_a_subject_that_failed_a_trial_fails(self, vocabulary: Vocabulary) -> None:
        analysis = _Run(vocabulary).trial(0, failed="subject").analyze()
        assert analysis.outcome == "fails"
        assert analysis.findings[0].reason == "the subject failed 1 of 1"

    @pytest.mark.parametrize(
        ("failed", "unread"),
        [("harness", ""), ("", "error")],
        ids=["the harness failed it", "its reading failed"],
    )
    def test_a_trial_that_could_not_be_read_is_inconclusive_never_a_failure(
        self, vocabulary: Vocabulary, failed: str, unread: str
    ) -> None:
        analysis = _Run(vocabulary).trial(0, failed=failed, unread=unread).analyze()
        assert analysis.outcome == "inconclusive"
        assert analysis.findings[0].decisions[0].n == 0

    def test_a_false_reading_is_shown_whatever_else_could_not_be_read(
        self, vocabulary: Vocabulary
    ) -> None:
        run = _Run(vocabulary, repeats=2).trial(0, read=False).trial(1, failed="harness")
        assert run.analyze().outcome == "fails"

    def test_nothing_carried_out_is_inconclusive(self, vocabulary: Vocabulary) -> None:
        analysis = _Run(vocabulary).analyze()
        assert analysis.outcome == "inconclusive"
        assert analysis.findings[0].reason == "a trial was not carried out"


class TestValidation:
    def test_one_trial_is_enough_to_read(self, vocabulary: Vocabulary) -> None:
        examined = examine(_check({"command": _python("pass")}), vocabulary)
        assert examined.problems == () and examined.design is not None
        assert examined.design.repeats == 1

    @pytest.mark.parametrize(
        ("change", "path"),
        [
            ({"threshold": {"op": ">=", "value": 0.9}}, "threshold"),
            ({"margin": "5%"}, "margin"),
            ({"guardrails": [{"metric": "exited_ok", "threshold": {"op": ">", "value": 0.5}}]},
             "guardrails.0"),
        ],
    )  # fmt: skip
    def test_a_parameter_it_never_reads_is_refused(
        self, vocabulary: Vocabulary, change: dict[str, Any], path: str
    ) -> None:
        examined = examine(_check({"command": _python("pass")}, **change), vocabulary)
        assert (path, "decision_inapplicable") in {(p.path, p.kind) for p in examined.problems}

    def test_a_primary_that_is_not_a_boolean_is_refused(self, vocabulary: Vocabulary) -> None:
        data = _check({"command": _python("pass")}, primary={"metric": "tests_pass_share"})
        data["probes"] = [{"id": "ask", "kind": "prompt", "prompt": "Write f."}]
        data["subjects"] = {"agent": {"kind": "agent-session", "runtime": "mock"}}
        found = {(p.path, p.kind) for p in examine(data, vocabulary).problems}
        assert ("primary.metric", "metric_shape") in found


class TestThePlan:
    def test_it_says_a_check_detects_no_effect(self, write_experiment: Write) -> None:
        made = ix.plan(write_experiment(_check({"command": _python("pass")})))
        assert made.trials.total == 1 and made.verdict_form == "three-way"
        assert made.sensitivity.text.startswith("not applicable: a check observes")
        assert made.decides == ("exited_ok is true on every trial of api",)


class TestTheCommand:
    def _run(self, write_experiment: Write, probe: dict[str, Any], **changes: Any) -> ix.Report:
        file = write_experiment(_check(probe, **changes))
        return ix.run(file, exploratory=True)

    def test_a_command_that_succeeds_holds(self, write_experiment: Write) -> None:
        report = self._run(write_experiment, {"command": _python("print('ok')")})
        assert report.status == "completed", report.errors
        assert report.indication == "holds"
        # A predicate that always holds is what a check is for, not a probe that cannot
        # discriminate: there is no screen to give.
        assert report.screen == ()
        interaction = json.loads(
            Path(report.tables["ix.v1.interaction"]).read_text().splitlines()[0]
        )
        output = interaction["observations"]["output"]
        assert (output["exit_code"], output["stdout"], output["truncated"]) == (0, "ok\n", False)

    def test_a_command_that_fails_fails_and_the_code_it_means_is_the_sensors(
        self, write_experiment: Write
    ) -> None:
        failing = {"command": _python("import sys; sys.exit(3)")}
        assert self._run(write_experiment, failing).indication == "fails"
        report = self._run(write_experiment, failing, sensors=[{"kind": "exit-status", "code": 3}])
        assert report.indication == "holds"

    def test_one_that_overruns_is_the_harnesss_and_inconclusive(
        self, write_experiment: Write
    ) -> None:
        report = self._run(
            write_experiment,
            {"command": _python("import time; time.sleep(30)"), "timeout": "200ms"},
        )
        assert report.indication == "inconclusive"
        (failed,) = report.analysis.failures
        assert (failed.owner, failed.reason) == ("harness", "timeout")

    def test_it_runs_where_cwd_says_with_its_env_and_the_subjects_url(
        self, write_experiment: Write
    ) -> None:
        code = (
            "import os, sys; "
            "sys.exit(0 if os.path.basename(os.getcwd()) == 'scripts' "
            "and os.environ['GREETING'] == 'hi' and sys.argv[1] == 'http://127.0.0.1:9' else 1)"
        )
        file = write_experiment(
            _check(
                {"command": [*_python(code), "{url}"], "cwd": "scripts", "env": {"GREETING": "hi"}}
            )
        )
        (file.parent / "scripts").mkdir()
        assert ix.run(file, exploratory=True).indication == "holds"

    def test_what_it_keeps_of_a_stream_is_bounded(self, write_experiment: Write) -> None:
        from ix.adapters._out.commands import OUTPUT_LIMIT

        report = self._run(write_experiment, {"command": _python("print('x' * 200_000)")})
        interaction = json.loads(
            Path(report.tables["ix.v1.interaction"]).read_text().splitlines()[0]
        )
        output = interaction["observations"]["output"]
        assert output["truncated"] is True and len(output["stdout"]) == OUTPUT_LIMIT

    def test_the_script_it_runs_is_what_was_tested(self, write_experiment: Write) -> None:
        file = write_experiment(_check({"command": [sys.executable, "probe.py"]}))
        (file.parent / "probe.py").write_text("print('checked')\n")
        ix.register(file)
        (file.parent / "probe.py").write_text("print('checked, differently')\n")
        found = {deviation.field for deviation in ix.deviations(file).deviations}
        assert "probes.run.command[probe.py]" in found
