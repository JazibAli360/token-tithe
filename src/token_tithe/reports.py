"""Monthly ledger report (`donate report`) and donation journal (`donate journal`, D11)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from token_tithe import paths
from token_tithe.ledger import Ledger

TASK_EVENTS = ("pr_opened", "comment_posted", "queued_for_review", "abandoned", "rejected",
               "timeout", "budget_exhausted")  # fmt: skip


def _month(month: str | None) -> str:
    return month or datetime.now(UTC).strftime("%Y-%m")


def month_entries(ledger: Ledger, month: str) -> list[dict]:
    return [e for e in ledger.entries() if str(e.get("ts", "")).startswith(month)]


def report(ledger: Ledger, month: str | None = None) -> str:
    m = _month(month)
    es = month_entries(ledger, m)
    events = Counter(e["event"] for e in es)
    tokens_by: dict[str, int] = defaultdict(int)
    cost_by: dict[str, float] = defaultdict(float)
    unaccounted = 0
    for e in es:
        if e["event"] in TASK_EVENTS and e.get("tokens") is not None:
            key = f"{e.get('agent', '?')}/{e.get('model') or 'default'}"
            if e.get("accounted", True):
                tokens_by[key] += int(e.get("tokens") or 0)
            else:
                unaccounted += 1
        if e.get("cost") is not None:
            cost_by[str(e.get("agent"))] += float(e["cost"])
    repos = Counter(str(e["repo"]) for e in es if e["event"] in ("pr_opened", "comment_posted"))
    lines = [f"# token-tithe report — {m}", "", "## Outcomes", "", "| event | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(events.items())]
    lines += ["", "## Tokens (real usage only, R10)", "", "| agent/model | tokens |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in sorted(tokens_by.items())] or ["| — | 0 |"]
    if unaccounted:
        lines.append(f"\n{unaccounted} invocation(s) were `unaccounted` and excluded from totals.")
    lines += ["", "## Repos helped", ""]
    lines += [f"- {r} ({n})" for r, n in repos.most_common()] or ["- none yet"]
    merged = events.get("merged", 0)
    opened = events.get("pr_opened", 0)
    total = sum(tokens_by.values())
    lines += ["", "## Efficiency", "",
              f"- PRs opened: {opened} · merged: {merged} · closed: {events.get('closed', 0)}",
              f"- tokens per merged PR: {total // merged:,}" if merged else "- tokens per merged PR: n/a"]  # fmt: skip
    if ledger.problems:
        lines += ["", "## Ledger problems", ""] + [f"- {p}" for p in ledger.problems]
    return "\n".join(lines) + "\n"


def journal(ledger: Ledger, month: str | None = None) -> str:
    """A readable monthly retrospective: blog-ready and a personal retro tool."""
    m = _month(month)
    es = month_entries(ledger, m)
    title = datetime.strptime(m, "%Y-%m").strftime("%B %Y")
    opened = [e for e in es if e["event"] == "pr_opened"]
    merged = [e for e in es if e["event"] == "merged"]
    closed = [e for e in es if e["event"] == "closed"]
    comments = [e for e in es if e["event"] == "comment_posted"]
    queued = [e for e in es if e["event"] == "queued_for_review"]
    skipped = [e for e in es if e["event"] in ("rejected", "abandoned")]
    incidents = [e for e in es if e["event"] == "incident"]
    tokens = sum(int(e.get("tokens") or 0) for e in es
                 if e["event"] in TASK_EVENTS and e.get("accounted", True))  # fmt: skip
    repos = sorted({str(e["repo"]) for e in opened + comments})
    p = [f"# Donation journal — {title}", ""]
    if not es:
        return "\n".join(p + ["Nothing was donated this month."]) + "\n"
    p.append(
        f"This month the engine spent **{tokens:,} tokens** of leftover capacity on open source. "
        f"It opened **{len(opened)} pull request(s)** and posted **{len(comments)} investigation "
        f"note(s)** across {len(repos)} repositor{'y' if len(repos) == 1 else 'ies'}; "
        f"**{len(merged)}** were merged and {len(closed)} closed without merging."
    )
    if queued:
        p.append(f"\n{len(queued)} proposal(s) went to the human review queue instead of being "
                 "opened directly.")  # fmt: skip
    if skipped:
        reasons = Counter(_reason_kind(e.get("reason", "")) for e in skipped)
        top = ", ".join(f"{k} ({v})" for k, v in reasons.most_common(4))
        p.append(f"\nIt walked away from {len(skipped)} task(s) — most often: {top}. Walking away is "
                 "part of the job: a donation that costs a maintainer time is not a gift.")  # fmt: skip
    if merged:
        p += ["", "## Merged", ""] + [f"- {e.get('url')} ({e.get('donation_type')}, "
                                      f"{e.get('days_open', '?')} days to merge)" for e in merged]  # fmt: skip
    if repos:
        p += ["", "## Repositories helped", ""] + [f"- {r}" for r in repos]
    if incidents:
        p += ["", "## Incidents", ""] + [f"- {e.get('repo')}: {e.get('reason')}" for e in incidents]
    p += ["", "## Next month", "",
          "Run `donate learn` to turn this month's outcomes into lessons, and `donate optimize` "
          "to see which agent gets work merged most cheaply."]  # fmt: skip
    return "\n".join(p) + "\n"


def _reason_kind(reason: str) -> str:
    r = reason.lower()
    for key, label in (("anti-ai", "anti-AI policy"), ("opt-out", "maintainer opt-out"),
                       ("cla", "CLA"), ("tests fail", "tests failed"), ("r8", "diff too large"),
                       ("race recheck", "issue changed"), ("credential", "leaked secret"),
                       ("refused", "agent refused"), ("timed out", "timeout")):  # fmt: skip
        if key in r:
            return label
    return "other"


def write(text: str, name: str) -> Path:
    paths.REPORTS.mkdir(parents=True, exist_ok=True)
    f = paths.REPORTS / name
    f.write_text(text, encoding="utf-8")
    return f
