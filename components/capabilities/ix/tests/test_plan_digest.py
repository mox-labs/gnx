"""The plan digest is a contract: computed over a declared, versioned form, and pinned here.

A registration persists for the life of an experiment, and its ``plan_digest`` gates every
run of it (review D4, F4). The digest is over the design in the form a registration freezes
(``ix.v1.registered-design``, :func:`~ix.domain.registration.design_form`), built key by key,
so a rename inside the design's records moves nothing. The literals below are the digests of
every checked-in example and of the built-in genres' examples in ``--skill``.

**When one of these fails,** something changed what a plan digest is over. If that was
meant, update the literal and add a line to the changelog saying which registrations must be
amended (``ix register <path> --amend --reason "..."``): every registered run of a moved
digest is otherwise ungated. If it was not meant, the change leaked into the digest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import ix
from ix.application.experiments import Vocabulary, examine, register
from ix.application.skill import EXAMPLES
from ix.domain import schema
from ix.domain.plan import plan_digest
from ix.domain.registration import design_form

PACKAGE = Path(ix.__file__).resolve().parents[2]

#: The plan digest of each checked-in example, version 1 of the form.
EXAMPLE_DIGESTS = {
    "examples/delay-server/identical.yaml": (
        "9c53bf6a719dfb9d928d4aa26210fad0ca2127d17a6def6bd7df2c227ad834e1"
    ),
    "examples/delay-server/slower.yaml": (
        "7b6ed29d74c6e6f87f72cf1f0340eb851b100e7e4439fdb6127d7913920b03e0"
    ),
    "examples/harness-integrity": (
        "6dd748c91fb1865ba4c81d0483560b0780d57cca07ce2e4e9534aedaefd98064"
    ),
    "examples/proxy-streaming": (
        "f4f3aa74f06de8cc4f28658b7e61e16d13e45f4987e8d27c7fdc72f1c1ec185f"
    ),
    "examples/trials-in-flight/experiment.yaml": (
        "f9cb319c3902815507c49831529a89e3c918874f225f08706638fae6b870f9e0"
    ),
    "examples/trials-in-flight/k6-control.yaml": (
        "4957344da6a2fc5c8cc9d2441a14a8c0f31889402f5d7576bac254ae486a823a"
    ),
}

#: The plan digest of the ``--skill`` example of each built-in genre.
GENRE_EXAMPLE_DIGESTS = {
    "evaluate": "e88d7a384c2c746e5c336a35062f1c662104e03eba293c2c252cc426b1e76602",
    "compare": "2bbbb148545a0c1eef86e29a2d175f493cf514a4815c5e7184d9a138dadebfa7",
    "characterize": "f72a898992fa4b0edc8adbebc1b4dd7ff45e8e6e12dd94d9b3ca99b2a1053c56",
    "regress": "548632584889711fad4e39bf15d99b98de1023534ee7b29731505b2aa801c5dd",
    "check": "0df23bff15d3b5093d74a563d3acaaf402ade9fa12786cec7df6f5bff30069e6",
}

#: The registered form of examples/delay-server/slower.yaml, whole: every key the form
#: declares that this design uses, under its declared name.
SLOWER_FORM: dict[str, Any] = {
    "schema": "ix.v1.registered-design",
    "experiment": "delay-slower",
    "genre": "ix.v1.genre.compare",
    "roles": [
        {"subject": "baseline", "role": "control", "kind": "process"},
        {"subject": "candidate", "role": "variant", "kind": "process"},
    ],
    "control": "baseline",
    "primary": {"metric": "latency", "statistic": "p50", "estimator": "mean"},
    "comparison": "superiority",
    "guardrails": [],
    "metrics": [
        {
            "name": "latency",
            "unit": "ms",
            "direction": "lower",
            "shape": "histogram",
            "judged": False,
            "flag": "",
        }
    ],
    "sensors": [
        {"kind": "ix.v1.sensor.timing", "metrics": ["latency"], "options": {}, "origin": "ix"}
    ],
    "defaults": {
        "subjects.baseline": {"cwd": ".", "startup_timeout": "30s"},
        "subjects.candidate": {"cwd": ".", "startup_timeout": "30s"},
        "probes.get": {"timeout": "60s", "method": "GET"},
    },
    "repeats": 10,
    "seed": 4237755122,
    "trials_in_flight": 1,
    "order": "interleaved",
    "calibration": "aa",
    "analysis": {"id": "ix.v1.analysis.compare", "params": {}},
    "verdict_form": "three-way",
    "report": ["question", "design", "estimates", "calibration", "failures", "warrant", "raw"],
}


@pytest.mark.parametrize("example", sorted(EXAMPLE_DIGESTS))
def test_each_example_plans_to_its_pinned_digest(example: str) -> None:
    assert ix.plan(PACKAGE / example).digest == EXAMPLE_DIGESTS[example]


@pytest.mark.parametrize("genre", sorted(GENRE_EXAMPLE_DIGESTS))
def test_each_genres_skill_example_plans_to_its_pinned_digest(genre: str, tmp_path: Path) -> None:
    (tmp_path / "experiment.yaml").write_text(EXAMPLES[genre])
    assert ix.plan(tmp_path).digest == GENRE_EXAMPLE_DIGESTS[genre]


def test_the_form_is_declared_key_by_key() -> None:
    assert design_form(ix.resolve(PACKAGE / "examples/delay-server/slower.yaml")) == SLOWER_FORM


def test_the_digest_is_over_the_form_a_registration_freezes(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    """One form, for both: the registration's design is the form, and the plan digest is the
    digest of that form with the experiment's, the probes' and the schedule's."""
    examined = examine(compare_data, vocabulary)
    assert examined.experiment and examined.design and examined.probes
    registration = register(
        examined, vocabulary, None, location="r", now="", version=""
    ).registration
    assert registration.design == design_form(examined.design)
    assert registration.design["schema"] == schema.REGISTERED_DESIGN
    made = ix.plan(ix.Experiment.model_validate(compare_data))
    assert registration.plan_digest == made.digest
    rebuilt = schema.digest(
        {
            "experiment": examined.experiment.digest(operative=True),
            "design": registration.design,
            "probes": examined.probes.digest,
            "trials": made.trials.total,
            "schedule": made.schedule_digest,
        }
    )
    assert rebuilt == made.digest


def test_what_the_form_leaves_out_moves_no_digest(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    """The hypothesis sentences and the provenance are derived and outside the form; a value
    in it moves the digest."""
    examined = examine(compare_data, vocabulary)
    experiment, design, probes = examined.experiment, examined.design, examined.probes
    assert experiment and design and probes

    def digest(changed: ix.Design) -> str:
        return plan_digest(experiment, changed, probes, 30, "s")

    reworded = design.model_copy(update={"hypotheses": ("another sentence",), "provenance": ()})
    assert digest(reworded) == digest(design)
    assert digest(design.model_copy(update={"repeats": 11})) != digest(design)
    defaults = {**design.defaults, "probes.download": {"timeout": "61s"}}
    assert digest(design.model_copy(update={"defaults": defaults})) != digest(design)
