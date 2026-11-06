"""`donate optimize [--apply]`: routing as an economy (D10).

Cost per merged PR = all real tokens an agent spent on a language (every attempt, merged or
not) ÷ PRs of that agent+language that got merged. The cheapest agent with enough evidence
wins the language's routing slot. The proposal is always written to state/reports/ first;
config/policy.yaml changes only with --apply. An agent must have passed eval to be routed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from token_tithe import local_config, paths
from token_tithe.evaluation import eval_passed
from token_tithe.ledger import Ledger
from token_tithe.policy import Policy, load_policy

TASK_EVENTS = {
    "pr_opened",
    "comment_posted",
    "queued_for_review",
    "abandoned",
    "rejected",
    "timeout",
}
MIN_MERGES = 2  # evidence threshold before a routing change is proposed


@dataclass
class Stat:
    agent: str
    model: str
    language: str
    tokens: int = 0
    attempts: int = 0
    merged: int = 0

    @property
    def cost_per_merge(self) -> float | None:
        return self.tokens / self.merged if self.merged else None


def stats(ledger: Ledger) -> list[Stat]:
    table: dict[tuple[str, str, str], Stat] = {}
    url_key: dict[str, tuple[str, str, str]] = {}
    for e in ledger.entries():
        if e.get("mode") == "simulate":
            continue
        if e["event"] in TASK_EVENTS and e.get("agent"):
            key = (str(e["agent"]), str(e.get("model") or "default"),
                   str(e.get("language") or "?").lower())  # fmt: skip
            s = table.setdefault(key, Stat(*key))
            s.attempts += 1
            if e.get("accounted", True):
                s.tokens += int(e.get("tokens") or 0)
            if e["event"] == "pr_opened" and e.get("url"):
                url_key[e["url"]] = key
        elif e["event"] == "merged" and e.get("url") in url_key:
            table[url_key[e["url"]]].merged += 1
    return sorted(table.values(), key=lambda s: (s.language, s.cost_per_merge or float("inf")))


def propose(policy: Policy, ledger: Ledger) -> dict:
    by_lang: dict[str, list[Stat]] = defaultdict(list)
    for s in stats(ledger):
        by_lang[s.language].append(s)
    current = {k.lower(): v for k, v in policy.routing.by_language.items()}
    changes, notes = {}, []
    for lang, rows in sorted(by_lang.items()):
        eligible = [r for r in rows if r.merged >= MIN_MERGES]
        if not eligible:
            notes.append(f"{lang}: not enough merged PRs yet (need {MIN_MERGES} per agent)")
            continue
        best = min(eligible, key=lambda r: r.cost_per_merge or float("inf"))
        incumbent = current.get(lang, policy.routing.default)
        if best.agent == incumbent:
            notes.append(
                f"{lang}: {incumbent} already routed ({best.cost_per_merge:,.0f} tok/merge)"
            )
            continue
        if not any(eval_passed(best.agent, m, t) for m in (None, best.model)
                   for t in ("issue_fix", "tests", "docs", "ci_repro")):  # fmt: skip
            notes.append(f"{lang}: {best.agent} is cheapest but has not passed eval — not routed")
            continue
        changes[lang] = best.agent
        notes.append(f"{lang}: {incumbent} → {best.agent} ({best.cost_per_merge:,.0f} tok/merge)")
    return {"by_language": {**current, **changes}, "changes": changes, "notes": notes,
            "stats": [asdict(s) | {"cost_per_merge": s.cost_per_merge} for s in stats(ledger)]}  # fmt: skip


def write_proposal(proposal: dict) -> Path:
    paths.REPORTS.mkdir(parents=True, exist_ok=True)
    f = paths.REPORTS / "optimizer-proposal.json"
    f.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    return f


def apply(proposal: dict, policy_file: Path = paths.POLICY_FILE) -> None:
    """Write routing.by_language to your policy.local.yaml overlay; shared policy.yaml untouched."""
    local_config.set_value(
        policy_file, "routing.by_language", dict(sorted(proposal["by_language"].items()))
    )
    load_policy(policy_file)  # must still validate


def render(proposal: dict) -> str:
    lines = ["agent        model                language    attempts  merged  tokens/merge"]
    for s in proposal["stats"]:
        cpm = f"{s['cost_per_merge']:,.0f}" if s["cost_per_merge"] else "—"
        lines.append(f"{s['agent']:<12} {s['model']:<20} {s['language']:<11} {s['attempts']:>8} "
                     f"{s['merged']:>7}  {cpm:>12}")  # fmt: skip
    lines += ["", *proposal["notes"]] if proposal["notes"] else ["", "no data yet"]
    if proposal["changes"]:
        lines += ["", "proposed routing.by_language: " + json.dumps(proposal["by_language"]),
                  "apply with: donate optimize --apply"]  # fmt: skip
    return "\n".join(lines)
