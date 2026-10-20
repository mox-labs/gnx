"""What a run's agent sessions spend, held against the run's ceiling.

``matrix.limits.spend_usd`` is the most a run may spend on agent sessions, in USD, as their
runtimes report it (``AgentResponse.cost_usd``). The executor keeps one :class:`Spend` for
each run, and an agent a component calls inside the run asks it before a session starts:

* a runtime that bounds one session (``budget_usd``: the ``claude-sdk`` runtime's
  ``max_budget_usd``) gets its session only when what the run has spent, what the sessions
  in flight may still spend, and that budget together stay within the ceiling;
* a runtime that does not gets its session only while what is spent and held is below the
  ceiling, so the sessions in flight when it is reached may pass it.

A session refused raises :class:`~matrix.domain.errors.SpendError` and never starts. When a
session ends, its reported cost is counted; one that reports none, or that failed, counts
its budget, or nothing when its runtime has none.
"""

from __future__ import annotations

from contextvars import ContextVar

from matrix.domain.errors import SpendError


class Spend:
    """One run's spend: counted as sessions end, held for those in flight."""

    def __init__(self, ceiling_usd: float | None = None) -> None:
        self.ceiling_usd = ceiling_usd
        #: What ended sessions cost, as reported (or as their budgets, when they reported none).
        self.spent_usd = 0.0
        #: Whether anything was counted: a run with no agent session spent nothing knowable.
        self.counted = False
        self._held = 0.0

    def hold(self, agent: str, budget_usd: float | None) -> float:
        """Hold ``budget_usd`` for a session of ``agent`` about to start, and return what was
        held. Raises SpendError when the session could take the run past its ceiling."""
        ceiling = self.ceiling_usd
        committed = self.spent_usd + self._held
        if ceiling is not None and (
            committed >= ceiling or (budget_usd is not None and committed + budget_usd > ceiling)
        ):
            per_session = (
                "" if budget_usd is None else f", and one session may spend ${budget_usd:.2f}"
            )
            raise SpendError(
                f"agent {agent!r} was not started: the run has spent ${self.spent_usd:.4f} and "
                f"holds ${self._held:.4f} for sessions in flight, of its ${ceiling:.2f} ceiling "
                f"(matrix.limits.spend_usd){per_session}",
                fix="raise matrix.limits.spend_usd, or lower the runtime's max_budget_usd; "
                "`matrix plan` states the most a run may spend",
                agent=agent,
                spent_usd=self.spent_usd,
                held_usd=self._held,
                ceiling_usd=ceiling,
                budget_usd=budget_usd,
            )
        held = budget_usd or 0.0
        self._held += held
        return held

    def settle(self, held: float, cost_usd: float | None) -> None:
        """A session that held ``held`` ended, costing ``cost_usd`` (None: not reported)."""
        self._held = max(0.0, self._held - held)
        self.spent_usd += held if cost_usd is None else cost_usd
        self.counted = self.counted or cost_usd is not None or held > 0


#: The spend of the run whose component is running in this task, set by the executor so an
#: agent a component calls is held to the run's ceiling without the component passing it on.
CURRENT_SPEND: ContextVar[Spend | None] = ContextVar("matrix_spend", default=None)
