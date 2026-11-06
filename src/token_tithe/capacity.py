"""Capacity-aware operation (Addendum A): probe what this run actually has, size work to it.

Never assumes resources. Per agent: installed? authenticated? models? real remaining quota?
Plus GitHub rate limit, ledger cost averages, and the expiry-aware donation window (D9).
An agent without a real quota probe is capacity-blind: review mode only (A3).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from token_tithe.budget import BudgetGuard
from token_tithe.github_api import GitHubAPI, GitHubError
from token_tithe.ledger import Ledger
from token_tithe.policy import AgentConfig, Policy
from token_tithe.triage import Triaged

CHEAPEST_FIRST = ("triage_report", "docs", "tests", "issue_fix", "ci_repro")


@dataclass
class AgentCapacity:
    name: str
    installed: bool
    authenticated: bool | None  # None = no auth probe configured
    models: list[str]
    quota_tokens: int | None  # None = unknown (capacity-blind)
    accounted: bool
    notes: list[str] = field(default_factory=list)

    @property
    def capacity_aware(self) -> bool:
        return self.quota_tokens is not None


@dataclass
class Sizing:
    key: str
    decision: str  # "take" | "downgrade" | "skip"
    donation_type: str
    estimate: int
    reason: str


@dataclass
class CapacityReport:
    agents: list[AgentCapacity]
    github: dict
    estimates: dict[str, int]
    effective_budget: int
    window: dict

    def afford(self) -> dict[str, int]:
        return {t: self.effective_budget // max(1, n) for t, n in self.estimates.items()}

    def render(self) -> str:
        rows = [" agent        installed  auth   accounted  quota            models"]
        for a in self.agents:
            auth = {True: "yes", False: "NO", None: "?"}[a.authenticated]
            quota = f"{a.quota_tokens:,}" if a.quota_tokens is not None else "unknown (blind)"
            rows.append(f" {a.name:<12} {'yes' if a.installed else 'no':<10} {auth:<6} "
                        f"{'yes' if a.accounted else 'NO':<10} {quota:<16} "
                        f"{', '.join(a.models)}")  # fmt: skip
            rows += [f"   - {n}" for n in a.notes]
        gh = self.github
        rows.append("")
        rows.append(
            f" github: core {gh.get('core', '?')} remaining, search {gh.get('search', '?')}"
        )
        rows.append(f" effective budget this run: {self.effective_budget:,} tokens")
        afford = ", ".join(f"{t} ~{n}" for t, n in self.afford().items())
        rows.append(f" can afford (by type, avg cost from ledger or policy): {afford}")
        w = self.window
        if w.get("cycle_end"):
            state = "EXPIRING" if w["expiring"] else "ok"
            rows.append(f" donation window: ends {w['cycle_end']} ({w['days_left']} days) — {state}"
                        + (" — review mode forced" if w["force_review"] else ""))  # fmt: skip
        return "\n".join(rows)


def _probe(argv: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def probe_agent(cfg: AgentConfig) -> AgentCapacity:
    installed = shutil.which(cfg.binary) is not None
    cap = AgentCapacity(cfg.name, installed, None, list(cfg.models), None, cfg.accounted)
    if not installed:
        cap.notes.append(f"`{cfg.binary}` not on PATH")
        return cap
    probe = cfg.capacity_probe
    if probe.auth:
        code, out = _probe([cfg.binary, *probe.auth])
        cap.authenticated = code == 0
        try:
            data = json.loads(out)
            if isinstance(data, dict) and "loggedIn" in data:
                cap.authenticated = bool(data["loggedIn"])
        except ValueError:
            pass
    if probe.models:
        code, out = _probe([cfg.binary, *probe.models])
        if code == 0 and out:
            cap.models = [m.strip() for m in out.splitlines() if m.strip()]
    if probe.quota:
        code, out = _probe([cfg.binary, *probe.quota])
        m = re.search(r"\d[\d,_]*", out) if code == 0 else None
        if m:
            cap.quota_tokens = int(re.sub(r"[,_]", "", m.group(0)))
    if cap.quota_tokens is None:
        cap.notes.append("no real quota probe: capacity-blind → review mode only (A3)")
    if not cfg.accounted:
        cap.notes.append("no real usage reporting: unaccounted → review mode only (R10)")
    return cap


def donation_window(policy: Policy, today: date | None = None) -> dict:
    w = policy.donation_window
    if w.cycle_end is None:
        return {"cycle_end": None, "expiring": False, "force_review": False, "days_left": None}
    now = datetime.now(UTC) if today is None else datetime.combine(today, datetime.min.time(), UTC)
    end = datetime.combine(w.cycle_end, datetime.max.time(), UTC)
    hours_left = (end - now).total_seconds() / 3600
    return {
        "cycle_end": w.cycle_end.isoformat(),
        "days_left": max(0, int(hours_left // 24)),
        "expiring": hours_left <= w.expiring_days * 24,
        "force_review": 0 <= hours_left <= w.review_mode_hours_before,
    }


def estimates(policy: Policy, ledger: Ledger) -> dict[str, int]:
    est = dict(policy.budget.default_task_tokens)
    est.update(ledger.average_tokens())
    return est


def probe(
    policy: Policy, ledger: Ledger, agents: dict[str, AgentConfig], api: GitHubAPI | None
) -> CapacityReport:
    caps = [probe_agent(a) for n, a in agents.items() if n != "generic"]
    gh: dict = {}
    if api is not None:
        try:
            res = api.rate_limit().get("resources", {})
            gh = {k: res.get(k, {}).get("remaining") for k in ("core", "search")}
        except GitHubError as e:
            gh = {"error": str(e)}
    routed = caps[0] if caps else None
    for c in caps:
        if c.name == policy.routing.default:
            routed = c
    quota = routed.quota_tokens if routed else None
    budget = BudgetGuard.from_policy(policy, real_quota=quota)
    return CapacityReport(caps, gh, estimates(policy, ledger), budget.effective_budget,
                          donation_window(policy))  # fmt: skip


def select_tasks(
    ranked: list[Triaged], budget: BudgetGuard, est: dict[str, int], max_tasks: int
) -> tuple[list[Triaged], list[Sizing]]:
    """A4: largest affordable prefix of the ranked queue. When an issue_fix/ci_repro no longer
    fits but a triage_report would, downgrade it rather than skip. Every decision is returned
    so it can be ledgered."""
    remaining = budget.remaining
    chosen: list[Triaged] = []
    decisions: list[Sizing] = []
    for t in ranked:
        if len(chosen) >= max_tasks:
            decisions.append(
                Sizing(t.candidate.key, "skip", t.donation_type, 0, "max_tasks_per_run")
            )
            continue
        cost = est.get(t.donation_type, 0)
        if cost <= remaining:
            chosen.append(t)
            remaining -= cost
            decisions.append(Sizing(t.candidate.key, "take", t.donation_type, cost, "fits budget"))
            continue
        cheap = est.get("triage_report", 0)
        if t.donation_type in ("issue_fix", "ci_repro") and cheap <= remaining:
            t.donation_type = "triage_report"
            chosen.append(t)
            remaining -= cheap
            decisions.append(Sizing(t.candidate.key, "downgrade", "triage_report", cheap,
                                    f"{cost} needed for a fix; only {remaining + cheap} left"))  # fmt: skip
            continue
        decisions.append(Sizing(t.candidate.key, "skip", t.donation_type, cost,
                                f"needs ~{cost}, {remaining} left"))  # fmt: skip
    return chosen, decisions
