"""Phase 2: budget guard (R10, R15, A4)."""

import pytest

from token_tithe import policy as P
from token_tithe.budget import (
    BudgetExhausted,
    BudgetGuard,
    InsufficientCapacity,
    UnaccountedAdapter,
    UnaccountedUsageWarning,
    Usage,
    require_budgetable,
)


def u(tokens: int) -> Usage:
    return Usage(model="m", input_tokens=tokens, output_tokens=0, cost_estimate=None)


def test_effective_budget_is_min_of_cap_and_real_quota():
    g = BudgetGuard(max_tokens_per_run=1000, min_viable_tokens=100)
    assert g.effective_budget == 1000
    g.real_quota = 400
    assert g.effective_budget == 400
    g.real_quota = 5000
    assert g.effective_budget == 1000


def test_hard_stop_when_exhausted_even_mid_task():
    g = BudgetGuard(max_tokens_per_run=1000, min_viable_tokens=100)
    g.record(u(600))
    with pytest.raises(BudgetExhausted):
        g.record(u(500))
    with pytest.raises(BudgetExhausted):
        g.checkpoint()


def test_unaccounted_usage_is_excluded_with_warning():
    g = BudgetGuard(max_tokens_per_run=1000, min_viable_tokens=100)
    with pytest.warns(UnaccountedUsageWarning):
        g.record(Usage.unaccounted("x"))
    assert g.spent == 0
    assert g.unaccounted_invocations == 1


def test_below_min_viable_attempts_nothing():
    g = BudgetGuard(max_tokens_per_run=1000, min_viable_tokens=100, real_quota=50)
    with pytest.raises(InsufficientCapacity, match="nothing attempted"):
        g.ensure_viable()


def test_affordable_count():
    g = BudgetGuard(max_tokens_per_run=1000, min_viable_tokens=100)
    g.record(u(100))
    assert g.affordable_count(300) == 3
    assert g.can_afford(900) and not g.can_afford(901)


def test_from_policy():
    pol = P.load_policy()
    g = BudgetGuard.from_policy(pol)
    assert g.effective_budget == pol.budget.max_tokens_per_run


def test_unaccounted_or_blind_adapter_refused_in_live_runs():
    agents = P.load_agents()
    require_budgetable(agents["aider"], live=False)  # review mode: fine
    with pytest.raises(UnaccountedAdapter, match="R10"):
        require_budgetable(agents["aider"], live=True)
    with pytest.raises(UnaccountedAdapter, match="capacity-blind"):
        require_budgetable(agents["claude"], live=True)
