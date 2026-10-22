"""The report: what a run found, and how far that can be relied on.

A report is a pure function of a run's record. It states the question and the decision the
experiment declared, the verdict, the estimates behind it, the calibration check, the failures by
owner, and the warrant: what the verdict rests on and what weakens it (deviations from the
registration, trials left out, the method's own standing), and what it was about: the
digest of each file the subjects and sensors name, the commit, and the versions that ran it.

**A verdict is a gate result, and only some runs give one.** ``verdict`` is set when the
genre decides, the run was held to a registration it had not moved away from, it finished,
and the subjects themselves answered. Otherwise it is None and ``no_verdict`` says why;
what the analysis found is then in ``indication``, which is information and never a gate
result. A dry run (``ix run --dry-run``), or any run in which a stand-in answered a trial
(a mock runtime: ``simulated``), measured the harness and not the subject: it gives
neither a verdict nor an indication, and says so in words that cannot be read as
evidence (:data:`DRY_RUN`).

Beside the verdict a report carries what :mod:`ix.domain.summaries` computes: confusion
tables, flags, and the probe screen (left out when a stand-in answered: canned replies
screen nothing). ``shows`` says what the evidence shows in plain words, in
the direction it points, whatever the verdict: ``build30's latency p50 is higher than
build20's by 29.6% [26.2%, 33.1%]``. ``best_case`` says when the run was too small for even
every trial passing to show its threshold.

``exit_code`` is what a command that printed the report exits with: 0 when the verdict
holds or there is none to give, and a code of its own for ``fails``, ``inconclusive``, an
interrupted run, a failed one, one that is still running, one whose experiment had changed
since its registration (``deviations``), and a registered run a stand-in answered
(``simulated``): neither of the last two is ever 0, so no gate passes on it.
``exit`` is the same with what it means and what to do next, as :data:`EXIT_NEXT` says.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, ValidationError, computed_field

from ix.domain import schema
from ix.domain.analysis import Analysis, Decision, Finding, Outcome, analyze
from ix.domain.design import Design, metric_text
from ix.domain.errors import EXIT_CODES, EXIT_MEANINGS, EXIT_NEXT, ConfigError
from ix.domain.genre import VerdictForm
from ix.domain.plan import best_case
from ix.domain.problem import Problem
from ix.domain.provenance import Content, Provenance
from ix.domain.registration import Deviation
from ix.domain.run import RunStatus, cell_key
from ix.domain.schema import Record
from ix.domain.summaries import ConfusionTable, ProbeScreen, confusion, flags, screen

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ix.domain.run import Reading, RunRecord

#: What a dry run's report says in place of a verdict.
DRY_RUN = "Dry run on mock replies: checks the harness, not the agent"


class TrialCounts(Record):
    """How much of the plan a run carried out."""

    planned: int
    carried_out: int
    subject_failures: int = 0
    harness_failures: int = 0

    def text(self) -> str:
        parts = f"{self.carried_out} of {self.planned} carried out"
        if self.subject_failures:
            parts += f", {self.subject_failures} failed by a subject"
        if self.harness_failures:
            parts += f", {self.harness_failures} failed by the harness"
        return parts


class Exit(Record):
    """How a command that printed a document exits: the code, its kind, what that means and
    what to do next (:data:`~ix.domain.errors.EXIT_NEXT`)."""

    code: int
    kind: str
    meaning: str
    next: str


class Report(Record):
    """What one run found."""

    schema_: Literal["ix.v1.report"] = Field(default=schema.REPORT, alias="schema")
    run: str
    location: str
    status: RunStatus
    experiment: str
    question: str = ""
    #: The genre as the author writes it, and its type URL.
    genre: str
    genre_url: str
    #: The hypothesis the verdict is about: one sentence per variant.
    decides: tuple[str, ...] = ()
    verdict_form: VerdictForm
    #: The gate result. None when this run gives none; ``no_verdict`` says why.
    verdict: Outcome | None = None
    no_verdict: str = ""
    #: What the analysis found when that is not a gate result.
    indication: Outcome | None = None
    #: What the evidence shows, one sentence per primary decision with an interval, in the
    #: direction it points: what the verdict is about, in the words of the question. Empty
    #: when a stand-in answered (canned replies show nothing about the subject).
    shows: tuple[str, ...] = ()
    #: When the run was too small for even every trial going the right way to show the
    #: threshold of a pass rate: why it could not hold, and how many trials it would take.
    best_case: str = ""
    #: False for an exploratory run, for one of an experiment that had changed since its
    #: registration, and for one a stand-in answered (a dry run, or a mock runtime).
    gated: bool
    #: True when the run was asked to be exploratory.
    exploratory: bool = False
    #: True for a dry run (``--dry-run``), and what was set aside to put every subject on
    #: a stand-in.
    dry_run: bool = False
    stood_in: tuple[str, ...] = ()
    #: What the run changed from the experiment as written: a pilot's ``--repeats``.
    overridden: tuple[str, ...] = ()
    registered_at: str = ""
    #: How the experiment differed from its registration when the run started.
    deviations: tuple[Deviation, ...] = ()
    #: What the verdict is about: the digest of each file and folder the subjects and
    #: sensors name, as the run found them.
    contents: tuple[Content, ...] = ()
    #: Where the run ran from: the commit and the versions that ran it. None for a run an
    #: earlier ix recorded.
    provenance: Provenance | None = None
    trials: TrialCounts
    #: ``recorded``: by the run. ``derived``: the run ended before its sensors read the
    #: interactions, so the report read them. ``none``: no readings, because none were
    #: recorded and none were derived here (:attr:`unread` says why).
    readings: Literal["recorded", "derived", "none"] = "recorded"
    #: Why the interactions of a run that ended before its sensors read them were not read
    #: when this report was made, and how to get readings: a sensor the run used has no
    #: adapter here, or may call a model or starts processes, which a report never does.
    #: Empty otherwise.
    unread: str = ""
    analysis: Analysis
    #: ``all`` when a stand-in (a mock runtime) answered every trial, ``some`` when it
    #: answered some: such a run measured the harness and gives no verdict; ``none`` else.
    simulated: Literal["none", "some", "all"] = "none"
    #: The sentences of the flagged metrics any trial raised.
    flags: tuple[str, ...] = ()
    confusion: tuple[ConfusionTable, ...] = ()
    #: Per probe, whether it can discriminate on the primary metric.
    screen: tuple[ProbeScreen, ...] = ()
    #: What whatever ran it says went wrong.
    errors: tuple[str, ...] = ()
    #: What the run warned of before it started (a runtime that lets the agent use tools
    #: without asking), once per run.
    warnings: tuple[Problem, ...] = ()
    #: The report sections the genre asks for, in order.
    sections: tuple[str, ...] = ()
    #: Each table's kind and the file that holds it.
    tables: dict[str, str] = {}
    plan_digest: str = ""
    started_at: str = ""
    ended_at: str = ""
    ix_version: str = ""

    @property
    def exit_kind(self) -> str:
        """The kind in :data:`EXIT_CODES` a command that printed this report exits as."""
        match self.status:
            case "failed":
                return "run_failed"
            case "interrupted":
                return "run_interrupted"
            case "running":
                return "running"
        if not self.gated and self.deviations and not self.exploratory:
            # Not a gate result, and not one anybody asked to be ungated: never exit 0.
            return "deviations"
        if self.simulated != "none" and not self.exploratory:
            # Run as a gate, but a stand-in answered: it measured the harness. Never exit 0.
            return "simulated"
        return "ok" if self.verdict in (None, "holds") else self.verdict

    @property
    def exit_code(self) -> int:
        """What a command that printed this report exits with."""
        return EXIT_CODES[self.exit_kind]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def exit(self) -> Exit:
        """The exit code, what it means and what to do next."""
        kind = self.exit_kind
        return Exit(
            code=EXIT_CODES[kind], kind=kind, meaning=EXIT_MEANINGS[kind], next=EXIT_NEXT[kind]
        )


def design_of(record: RunRecord) -> Design:
    """The design a run was carried out under, from its manifest."""
    try:
        return Design.model_validate(record.manifest.design)
    except ValidationError as error:
        raise ConfigError(
            f"the design in run {record.run} cannot be read by this ix "
            f"({error.error_count()} problem(s)); it was written by ix "
            f"{record.manifest.ix_version or '(unknown)'}",
            fix="read the run with the ix that wrote it",
        ) from error


def report(
    record: RunRecord,
    *,
    readings: Sequence[Reading] | None = None,
    unread: str = "",
) -> Report:
    """The report of ``record``.

    ``readings`` replaces the record's own when the run ended before its sensors read the
    interactions and the caller read them afterwards; the report then says ``derived``.
    ``unread`` is why the caller did not read them, when it did not.
    """
    design = design_of(record)
    manifest = record.manifest
    used = record.readings if readings is None else tuple(readings)
    source: Literal["recorded", "derived", "none"] = (
        "none" if not used else "recorded" if readings is None else "derived"
    )
    analysis = analyze(design, record.trials, record.interactions, used)

    owners = [i.failure.owner for i in record.interactions if i.failure is not None]
    stand_ins = sum(1 for i in record.interactions if i.simulated)
    simulated: Literal["none", "some", "all"] = (
        "none" if not stand_ins else "all" if stand_ins == len(record.interactions) else "some"
    )
    stood_in = record.stood_in
    counts = TrialCounts(
        planned=len(record.trials),
        carried_out=len(record.interactions),
        subject_failures=owners.count("subject"),
        harness_failures=owners.count("harness"),
    )

    verdict: Outcome | None = None
    why = ""
    if design.verdict_form == "none":
        why = "the genre gives estimates only"
    elif record.status == "failed":
        why = "the run failed"
    elif record.status == "running":
        why = f"the run has not finished: {counts.text()} so far"
    elif record.status != "completed":
        why = f"the run was {record.status}: {counts.text()}"
    elif stood_in:
        why = DRY_RUN + (" (a stand-in answered some trials)" if simulated == "some" else "")
    elif not manifest.gated and not manifest.exploratory and manifest.deviations:
        why = (
            f"the experiment changed since its registration ({len(manifest.deviations)} "
            "deviation(s)) and the registration was not amended, so the run was not held to "
            "it and what it found is not a gate result; amend it with a reason, or restore the "
            "design, and run again"
        )
    elif not manifest.gated:
        why = (
            "the run was exploratory: it was not held to a registration, so what it found "
            "is not a gate result"
        )
    else:
        verdict = analysis.outcome

    return Report(
        shows=() if stood_in else shows(design, analysis.findings),
        best_case=best_case(design, design.repeats * len(manifest.probes)),
        overridden=manifest.overridden,
        warnings=manifest.warnings,
        run=record.run,
        location=record.location,
        status=record.status,
        experiment=manifest.experiment,
        question=str(manifest.declared.get("question", "")),
        genre=schema.short(design.genre),
        genre_url=design.genre,
        decides=design.hypotheses,
        verdict_form=design.verdict_form,
        verdict=verdict,
        no_verdict=why,
        indication=None if verdict is not None or stood_in else analysis.outcome,
        gated=record.gated,
        exploratory=manifest.exploratory,
        dry_run=manifest.dry_run,
        stood_in=manifest.stood_in,
        registered_at=str((manifest.registration or {}).get("registered_at", "")),
        deviations=manifest.deviations,
        contents=manifest.contents,
        provenance=manifest.provenance,
        trials=counts,
        readings=source,
        unread=unread if source == "none" else "",
        analysis=analysis,
        simulated=simulated,
        flags=flags(design, used),
        confusion=confusion(design, used),
        screen=() if stood_in else screen(design, record.trials, used),
        errors=record.errors,
        sections=design.report,
        tables=dict(record.tables),
        plan_digest=manifest.plan_digest,
        started_at=manifest.started_at,
        ended_at=record.ended_at,
        ix_version=manifest.ix_version,
    )


def _amount(value: float, unit: str) -> str:
    """A number as a report's sentence gives it: ``29.6%``, ``3.1pp``, ``120 ms``."""
    text = f"{value:.3g}"
    return f"{text}{unit}" if unit in ("%", "pp") else f"{text} {unit}" if unit else text


def _span(low: float, high: float, unit: str) -> str:
    return f"[{_amount(low, unit)}, {_amount(high, unit)}]"


def _against_control(subject: str, control: str, decision: Decision) -> str:
    assert decision.interval is not None and decision.difference is not None
    low, high, unit = decision.interval.low, decision.interval.high, decision.unit
    measure = metric_text(decision)
    if low <= 0 <= high:
        return (
            f"{subject}'s {measure} differs from {control}'s by {decision.difference:+.3g}"
            f"{unit if unit in ('%', 'pp') else ''} {_span(low, high, unit)}: the interval "
            "holds 0, so the run does not show which is higher"
        )
    word = "higher" if low > 0 else "lower"
    gap = _span(low, high, unit) if low > 0 else _span(-high, -low, unit)
    sentence = (
        f"{subject}'s {measure} is {word} than {control}'s by "
        f"{_amount(abs(decision.difference), unit)} {gap}"
    )
    if decision.better in ("higher", "lower"):
        verdict = "better" if word == decision.better else "worse"
        sentence += f"; {decision.better} is better, so {subject} is {verdict}"
    return sentence


def _against_threshold(subject: str, design: Design, decision: Decision) -> str:
    assert decision.interval is not None and decision.difference is not None
    threshold = design.threshold
    low, high, unit = decision.interval.low, decision.interval.high, decision.unit
    said = f"{subject}'s {metric_text(decision)} is {_amount(decision.difference, unit)} " + (
        _span(low, high, unit)
    )
    if threshold is None:
        return said
    bound, op, claim = threshold.value, threshold.op, threshold.text()
    if op in (">", ">="):
        meets = low > bound if op == ">" else low >= bound
        misses = high <= bound if op == ">" else high < bound
        side, other = "above", "below"
    else:
        meets = high < bound if op == "<" else high <= bound
        misses = low >= bound if op == "<" else low > bound
        side, other = "below", "above"
    if meets:
        return f"{said}: all of the interval is {side} {bound:g}, so it meets {claim}"
    if misses:
        return f"{said}: all of the interval is {other} {bound:g}, so it does not meet {claim}"
    return (
        f"{said}: the interval holds {bound:g}, so the run does not show whether it meets {claim}"
    )


def shows(design: Design, findings: Sequence[Finding]) -> tuple[str, ...]:
    """What the evidence shows, a sentence per primary decision with an interval, in the
    direction it points; the verdict is not changed by it and does not change it."""
    found = []
    for finding in findings:
        where = f"in {cell_key(finding.cell)}: " if finding.cell else ""
        for decision in finding.decisions:
            if decision.part != "primary" or decision.interval is None:
                continue
            if decision.difference is None:
                continue
            if decision.form == "threshold":
                sentence = _against_threshold(finding.subject, design, decision)
            else:
                sentence = _against_control(finding.subject, design.control or "", decision)
            found.append(where + sentence)
    return tuple(found)


#: How a run stood: ``gated`` (held to its registration), ``exploratory`` (not, and so no
#: verdict) or ``dry`` (a stand-in answered: the harness, not the subject).
RunMode = Literal["gated", "exploratory", "dry"]


class RunSummary(Record):
    """One run in a list of runs: what it was, how it ended and what it said."""

    run: str
    location: str
    experiment: str
    status: RunStatus
    gated: bool
    started_at: str = ""
    ended_at: str = ""
    planned: int = 0
    carried_out: int = 0
    mode: RunMode = "exploratory"
    #: The gate result, or else what the analysis indicated; both None for a dry run, and
    #: for a run whose report could not be computed here.
    verdict: Outcome | None = None
    indication: Outcome | None = None
    #: The commit the run ran from, and whether a tracked file differed from it; empty for
    #: a run outside a git work tree.
    commit: str = ""
    dirty: bool = False


def summary(report: Report) -> RunSummary:
    """The line a list of runs gives ``report``'s run."""
    mode: RunMode = (
        "dry" if report.dry_run or report.simulated != "none"
        else "gated" if report.gated
        else "exploratory"
    )  # fmt: skip
    return RunSummary(
        run=report.run,
        location=report.location,
        experiment=report.experiment,
        status=report.status,
        gated=report.gated,
        started_at=report.started_at,
        ended_at=report.ended_at,
        planned=report.trials.planned,
        carried_out=report.trials.carried_out,
        mode=mode,
        verdict=report.verdict,
        indication=report.indication,
        commit=report.provenance.commit if report.provenance else "",
        dirty=report.provenance.dirty if report.provenance else False,
    )


class Runs(Record):
    """The runs kept beside an experiment, oldest first."""

    schema_: Literal["ix.v1.runs"] = Field(default=schema.RUNS, alias="schema")
    location: str
    runs: tuple[RunSummary, ...] = ()
