"""Real-time token budget guard (R10, R15, A4).

Only REAL usage counts. An adapter that cannot report actual usage produces
`Usage(accounted=False)`: the work is recorded as `unaccounted`, excluded from budget math
with a warning, and such adapters are refused in budgeted live runs.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

from token_tithe.policy import AgentConfig, Policy


class BudgetExhausted(RuntimeError):
    """Raised the moment spending reaches the effective budget — even mid-task."""


class InsufficientCapacity(RuntimeError):
    """R15: the run cannot afford one minimum-viable task; nothing is attempted."""


class UnaccountedAdapter(RuntimeError):
    """R10/A3: an adapter without real usage reporting asked for a budgeted live run."""


class UnaccountedUsageWarning(UserWarning):
    """Emitted when usage without real accounting is recorded."""


@dataclass(frozen=True)
class Usage:
    """One agent invocation's usage, as reported by the agent itself."""

    model: str | None
    input_tokens: int
    output_tokens: int
    cost_estimate: float | None
    accounted: bool = True

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def unaccounted(cls, model: str | None = None) -> Usage:
        return cls(
            model=model, input_tokens=0, output_tokens=0, cost_estimate=None, accounted=False
        )


@dataclass
class BudgetGuard:
    """Tracks spending for one run against min(configured cap, real remaining quota)."""

    max_tokens_per_run: int
    min_viable_tokens: int
    real_quota: int | None = None
    spent: int = 0
    unaccounted_invocations: int = 0
    history: list[Usage] = field(default_factory=list)

    @classmethod
    def from_policy(cls, policy: Policy, real_quota: int | None = None) -> BudgetGuard:
        return cls(
            max_tokens_per_run=policy.budget.max_tokens_per_run,
            min_viable_tokens=policy.budget.min_viable_tokens,
            real_quota=real_quota,
        )

    @property
    def effective_budget(self) -> int:
        """A4: never trust the configured cap over what the provider says is left."""
        if self.real_quota is None:
            return self.max_tokens_per_run
        return max(0, min(self.max_tokens_per_run, self.real_quota))

    @property
    def remaining(self) -> int:
        return max(0, self.effective_budget - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def ensure_viable(self) -> None:
        """R15: call before attempting anything."""
        if self.remaining < self.min_viable_tokens:
            raise InsufficientCapacity(
                f"insufficient capacity, nothing attempted: {self.remaining} tokens available, "
                f"{self.min_viable_tokens} needed for one minimum-viable task"
            )

    def can_afford(self, estimated_tokens: int) -> bool:
        return estimated_tokens <= self.remaining

    def affordable_count(self, estimated_tokens: int) -> int:
        """How many tasks of this estimated size fit in what's left."""
        if estimated_tokens <= 0:
            raise ValueError("estimated_tokens must be positive")
        return self.remaining // estimated_tokens

    def record(self, usage: Usage) -> None:
        """Add real usage; raise BudgetExhausted the moment the budget is gone (R10)."""
        self.history.append(usage)
        if not usage.accounted:
            self.unaccounted_invocations += 1
            warnings.warn(
                "agent reported no real usage: invocation marked `unaccounted` and excluded "
                "from budget math (R10)",
                UnaccountedUsageWarning,
                stacklevel=2,
            )
            return
        self.spent += usage.total_tokens
        if self.exhausted:
            raise BudgetExhausted(
                f"budget exhausted: spent {self.spent} of {self.effective_budget} tokens"
            )

    def checkpoint(self) -> None:
        """Call between executor steps; stops a task mid-flight once the budget is gone."""
        if self.exhausted:
            raise BudgetExhausted(
                f"budget exhausted: spent {self.spent} of {self.effective_budget} tokens"
            )


def require_budgetable(agent: AgentConfig, live: bool) -> None:
    """R10 + A3: live budgeted runs need real usage AND a real capacity probe."""
    if not live:
        return
    if not agent.accounted:
        raise UnaccountedAdapter(
            f"agent {agent.name!r} cannot report real usage; it may run only in review mode, "
            "never in budgeted live runs (R10)"
        )
    if not agent.capacity_aware:
        raise UnaccountedAdapter(
            f"agent {agent.name!r} has no real quota probe (capacity-blind); it may run only in "
            "review mode (Addendum A3)"
        )
