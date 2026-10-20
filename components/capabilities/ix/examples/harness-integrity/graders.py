"""Graders the experiment owns: each takes the response text and returns a score from 0 to 1.

The outcome sensor runs this module in a Python process of its own and calls GRADERS[probe id]
with the text of the agent's answer.
"""


def _has_def(text: str) -> float:
    return 1.0 if "def merge" in text else 0.0


GRADERS = {
    "correct-solution": _has_def,
    "partially-correct": _has_def,
    "raising-solution": _has_def,
    # no-code has no grader: its truth's expected_facts grade it instead.
}
