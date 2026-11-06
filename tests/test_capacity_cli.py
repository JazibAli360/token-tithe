"""Phase 5: capacity sizing (A4), donation window (D9), probes, CLI wiring."""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from tests.test_triage import cand, ok
from token_tithe import capacity, paths
from token_tithe import policy as P
from token_tithe.budget import BudgetGuard
from token_tithe.cli import app
from token_tithe.ledger import Ledger
from token_tithe.triage import triage


@pytest.fixture(autouse=True)
def state(tmp_path):
    with paths.use_state(tmp_path / "state"):
        yield


def ranked(n):
    cs = [cand(number=i) for i in range(1, n + 1)]
    return triage(cs, {c.key: ok(c.key) for c in cs}, P.load_policy(), {"o/r": True})


def test_select_tasks_takes_prefix_then_downgrades():
    est = {"issue_fix": 100, "triage_report": 30, "docs": 10, "tests": 10, "ci_repro": 100}
    g = BudgetGuard(max_tokens_per_run=250, min_viable_tokens=10)
    chosen, decisions = capacity.select_tasks(ranked(4), g, est, max_tasks=10)
    assert [d.decision for d in decisions] == ["take", "take", "downgrade", "skip"]
    assert [t.donation_type for t in chosen] == ["issue_fix", "issue_fix", "triage_report"]


def test_select_tasks_respects_max_tasks():
    g = BudgetGuard(max_tokens_per_run=10**9, min_viable_tokens=10)
    chosen, decisions = capacity.select_tasks(ranked(3), g, {"issue_fix": 1}, max_tasks=1)
    assert len(chosen) == 1 and decisions[-1].reason == "max_tasks_per_run"


def test_estimates_prefer_ledger_averages():
    Ledger().append("queued_for_review", donation_type="docs", tokens=1234, accounted=True)
    est = capacity.estimates(P.load_policy(), Ledger())
    assert est["docs"] == 1234 and est["issue_fix"] == 150000


def test_donation_window():
    p = P.load_policy()
    assert capacity.donation_window(p)["expiring"] is False
    end = date(2026, 10, 31)
    p2 = replace(p, donation_window=replace(p.donation_window, cycle_end=end))
    assert capacity.donation_window(p2, today=date(2026, 10, 1))["expiring"] is False
    w = capacity.donation_window(p2, today=end - timedelta(days=3))
    assert w["expiring"] and not w["force_review"]
    assert capacity.donation_window(p2, today=end)["force_review"]


def test_probe_missing_binary_is_blind():
    cfg = P.load_agent("generic")
    cap = capacity.probe_agent(cfg)
    assert not cap.installed and not cap.capacity_aware


def test_cli_run_review_handover_halt():
    r = CliRunner()
    assert r.invoke(app, ["run", "--fixtures", "--max-tasks", "1"]).exit_code == 0
    out = r.invoke(app, ["review"]).output
    assert "tithe-fixtures/" in out and "queued:" in out
    assert "--live" in r.invoke(app, ["review", "--open", "1", "--fixtures"]).output
    assert "HANDOVER" in r.invoke(app, ["handover"]).output
    assert r.invoke(app, ["halt"]).exit_code == 0 and paths.HALT_FLAG.exists()
    assert r.invoke(app, ["run", "--fixtures"]).exit_code == 1
    r.invoke(app, ["resume", "--fixtures"])
    assert not paths.HALT_FLAG.exists()
    assert "--live and --dry-run" in r.invoke(app, ["run", "--live", "--dry-run"]).output
