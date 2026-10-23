"""What the two runtimes built on hardline share: building hardline's runtime from a registry
section, and naming who served a call.

Both the ``hardline`` model runtime and the ``pydantic-ai`` agent runtime hold a hardline
runtime; this module is the one place each is built, so a registry hardline refuses is a
matrix ``ConfigError`` with its problems, whichever runtime reads it.

``hardline`` is imported inside the functions, so importing matrix does not load it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from matrix.domain.errors import invalid, missing_dependency
from matrix.domain.problems import Problem, problem

if TYPE_CHECKING:
    from collections.abc import Mapping

    from hardline import Completion
    from hardline import ModelRuntime as Hardline
    from hardline import Problem as HardlineProblem


def build(models: Mapping[str, Any] | None, *, needed_by: str) -> Hardline:
    """hardline's runtime on the registry section ``models`` (a runtime config's ``models``
    key), or on the tiers hardline discovers when it is None, for ``needed_by``.

    The section is labelled ``matrix.models``, the tier each registry row's fields are
    recorded as set by. Raises ConfigError when hardline refuses the registry. A problem in
    ``models`` is at ``models.<path>``, relative to the runtime's config, with no source, so
    whoever built the runtime places it in its document; a problem in a tier file keeps its
    path and the file."""
    try:
        from hardline import HardlineError, build_runtime
    except ImportError as e:
        raise missing_dependency(needed_by=needed_by, module="hardline") from e
    try:
        if models is None:
            return build_runtime()
        return build_runtime(config=models, label="matrix.models")
    except HardlineError as e:
        given = models is not None
        found = [_problem(p, given=given) for p in e.problems] or [
            problem("models" if given else "", "invalid_value", str(e), fix=e.fix or "")
        ]
        raise invalid(
            "the hardline model registry cannot be loaded",
            found,
            fix=e.fix or "fix the registry as each problem says; `hardline models` lists it",
        ) from None


def _problem(found: HardlineProblem, *, given: bool) -> Problem:
    return Problem(
        path=f"models.{found.path}" if given else found.path,
        kind=found.kind,
        message=found.message,
        fix=found.fix or "",
        allowed=found.allowed,
        got=found.got,
        source="" if given else found.source or "",
    )


def served_by(models: Hardline, completion: Completion) -> str:
    """Who served ``completion``, as the GenAI conventions name providers: its registry row's
    ``provider``, or else its backend's name, as hardline's own ``gen_ai.provider.name`` is. An
    OpenAI-compatible backend cannot tell which server answered; the row's author can."""
    return models.registry.get(completion.name).provider or completion.backend
