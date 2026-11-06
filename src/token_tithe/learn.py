"""`donate learn`: turn what happened into candidate lessons (Addendum E3).

Deterministic and token-free: rules over ledger events (and the latest eval report) produce
provenance-tagged candidates, deduped against the Brain, appended to PENDING LESSONS.
`donate learn --apply` promotes them (human approval). Lessons from simulate / eval / dry-run
are labelled as such — cheap experiments still teach, but they are marked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from token_tithe import paths
from token_tithe.brain import Brain
from token_tithe.ledger import Ledger


@dataclass
class Candidate:
    section: str
    text: str
    tags: list[str]
    prov: str
    origin: str


def _t(*xs) -> list[str]:
    return [str(x).lower() for x in xs if x]


def from_entry(e: dict) -> Candidate | None:
    ev, reason = e["event"], str(e.get("reason", ""))
    repo, agent, model = e.get("repo"), e.get("agent"), e.get("model")
    lang, dtype = e.get("language"), e.get("donation_type")
    origin = e.get("mode") or ("feedback" if ev in ("merged", "closed", "stale") else "live")
    prov = e.get("url") or f"run {e.get('run_id', '?')} {repo or ''}#{e.get('issue', '')}".strip()
    low = reason.lower()

    if ev == "rejected":
        if "anti-ai" in low:
            quote = re.search(r'"([^"]{10,140})', reason)
            return Candidate("REJECTIONS & INCIDENTS",
                             f"{repo} rejects AI-generated contributions"
                             + (f' ("{quote.group(1)}")' if quote else "") + " — never send work there.",
                             _t(repo, "etiquette"), prov, origin)  # fmt: skip
        if "opt-out" in low:
            return Candidate("REJECTIONS & INCIDENTS", f"{repo} opted out via .github/token-tithe-ignore.",
                             _t(repo, "etiquette"), prov, origin)  # fmt: skip
        if "leaked credentials" in low:
            return Candidate("REJECTIONS & INCIDENTS",
                             f"{repo} has leaked credentials in its tree; it is skipped entirely (D4).",
                             _t(repo, "secrets"), prov, origin)  # fmt: skip
        if "cla" in low:
            return Candidate("REJECTIONS & INCIDENTS", f"{repo} requires a CLA we have not signed.",
                             _t(repo, "etiquette", "cla"), prov, origin)  # fmt: skip
        if "agent refused" in low:
            return Candidate("AGENT QUIRKS", f"{agent} refused a {dtype} task in {lang}: "
                             f"{reason.split(':', 1)[-1].strip()[:160]}",
                             _t(agent, lang, dtype), prov, origin)  # fmt: skip
    if ev == "abandoned":
        if "tests fail after the change" in low or "linter fails" in low:
            cmd = re.search(r"`([^`]+)`", reason)
            return Candidate("PROMPT PATTERNS",
                             f"{agent} on {lang} {dtype}: its change broke `{cmd.group(1) if cmd else 'the tests'}`"
                             " — the brief must insist on running the full suite and linter before finishing.",
                             _t(agent, lang, dtype), prov, origin)  # fmt: skip
        if "(r8)" in low:
            return Candidate("PROMPT PATTERNS",
                             f"{agent} produced an oversized diff on a {dtype} task ({reason}); "
                             "stress the smallest correct change.", _t(agent, dtype), prov, origin)  # fmt: skip
        if "no runnable test suite" in low:
            return Candidate("ECOSYSTEM PLAYBOOKS",
                             f"{lang} repo {repo} had no detectable test command; such repos can't be "
                             "verified (R7) — prefer repos with a test suite.", _t(lang, "toolchain"),
                             prov, origin)  # fmt: skip
        if "dependency install failed" in low:
            return Candidate("ECOSYSTEM PLAYBOOKS", f"{lang}: dependency install failed in {repo} "
                             f"({reason.split(':', 1)[-1].strip()[:120]}).", _t(lang, "toolchain"),
                             prov, origin)  # fmt: skip
        if "race recheck" in low:
            return Candidate("REJECTIONS & INCIDENTS",
                             f"Issue in {repo} changed before publish ({reason.split(':', 1)[-1].strip()[:120]}); "
                             "issues there move fast.", _t(repo), prov, origin)  # fmt: skip
        if "no changes" in low:
            return Candidate("AGENT QUIRKS", f"{agent} made no changes on a {dtype} task in {lang}.",
                             _t(agent, lang, dtype), prov, origin)  # fmt: skip
    if ev == "timeout":
        return Candidate("AGENT QUIRKS", f"{agent}/{model or 'default'} timed out on a {dtype} task in {lang}.",
                         _t(agent, lang, dtype), prov, origin)  # fmt: skip
    if ev == "unaccounted":
        return Candidate("AGENT QUIRKS", f"{agent}/{model or 'default'} reported no real usage — "
                         "unaccounted, review mode only (R10).", _t(agent), prov, origin)  # fmt: skip
    if ev == "model_switch":
        return Candidate("AGENT QUIRKS", f"{agent}: {e.get('from_model')} was unavailable; fell back to "
                         f"{e.get('to_model')}.", _t(agent), prov, origin)  # fmt: skip
    if ev == "merged":
        return Candidate("WHAT GETS MERGED", f"A {dtype} PR in {lang} was merged in {e.get('days_open', '?')} "
                         f"days in {repo}.", _t(dtype, lang, repo), prov, "feedback")  # fmt: skip
    if ev == "closed":
        return Candidate("REJECTIONS & INCIDENTS", f"A {dtype} PR to {repo} was closed without merging; "
                         "read the thread before trying that repo again.", _t(repo, dtype), prov, "feedback")  # fmt: skip
    if ev == "incident":
        return Candidate("REJECTIONS & INCIDENTS", f"Incident in {repo}: {reason[:160]} "
                         f"(blocklisted until {e.get('cooldown_until')}).", _t(repo, "incident"), prov, "live")  # fmt: skip
    return None


def from_eval_report(report: Path) -> list[Candidate]:
    """Failures of REAL agents in the latest eval report (controls teach nothing about agents)."""
    if not report.is_file():
        return []
    out = []
    current = None
    run = re.search(r"# Eval report — (\S+)", report.read_text(encoding="utf-8"))
    for line in report.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"### (\S+) · (\S+) — (\w+)", line):
            current = m.groups()
        elif current and line.startswith("- FAIL ") and current[0] not in ("reference", "null"):
            agent, fixture, _ = current
            crit = line[7:].split(" — ")[0]
            out.append(Candidate("AGENT QUIRKS", f"{agent} failed eval fixture {fixture}: {crit}.",
                                 _t(agent.split(":")[0], fixture), f"eval {run.group(1) if run else '?'}",
                                 "eval"))  # fmt: skip
    return out


def learn(ledger: Ledger, brain: Brain, eval_report: Path | None = None) -> dict:
    """Append candidates from everything since the last `learned through` mark."""
    marks = [w for _, w in brain.log() if w.startswith("learned through ")]
    since = marks[-1].split()[2] if marks else ""
    candidates, last_ts = [], since
    for e in ledger.entries():
        if str(e.get("ts", "")) <= since:
            continue
        last_ts = max(last_ts, str(e.get("ts", "")))
        if c := from_entry(e):
            candidates.append(c)
    candidates += from_eval_report(eval_report or paths.EVAL_RUNS / "report.md")
    added = []
    for c in candidates:
        pid = brain.add_pending(c.section, c.text, c.tags, c.prov, origin=c.origin)
        if pid:
            added.append(pid)
    if last_ts:
        brain.append_log(
            f"learned through {last_ts} · {len(candidates)} candidates, {len(added)} new"
        )
    return {"candidates": len(candidates), "added": added}


def apply(brain: Brain, ids: list[str] | None = None) -> list[tuple[str, str]]:
    """Human approval: promote the given pending lessons (all open ones if `ids` is empty)."""
    targets = ids or [p.id for p in brain.open_pending()]
    return [(pid, brain.promote(pid)) for pid in targets]
