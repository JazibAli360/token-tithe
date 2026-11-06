"""PR outcome tracking (§7) and the incident procedure (SAFETY.md).

`donate feedback` checks every PR we ever opened and classifies it merged / closed / open /
stale. Only *changes* are appended to the ledger, so running it daily is cheap and idempotent.
Merged/closed counts flow back into triage through `Ledger.history()` and are cached per repo
in state/feedback_cache/repos.json.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from token_tithe import paths
from token_tithe.github_api import GitHubAPI, GitHubError
from token_tithe.ledger import Ledger
from token_tithe.policy import Policy
from token_tithe.templates import render

PR_URL = re.compile(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)")
TERMINAL = {"merged", "closed"}


@dataclass
class PrStatus:
    url: str
    repo: str
    number: int
    status: str  # merged | closed | open | stale | unknown
    changed: bool
    days_open: float = 0.0


def parse_pr_url(url: str) -> tuple[str, int] | None:
    m = PR_URL.match(url or "")
    return (m.group(1), int(m.group(2))) if m else None


def our_prs(ledger: Ledger) -> dict[str, dict]:
    """url -> the pr_opened ledger entry, for every PR the engine opened."""
    return {e["url"]: e for e in ledger.entries() if e["event"] == "pr_opened" and e.get("url")}


def last_status(ledger: Ledger) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in ledger.entries():
        if e["event"] in ("merged", "closed", "stale") and e.get("url"):
            out[e["url"]] = e["event"]
    return out


def _age_days(iso: str | None, now: datetime) -> float:
    if not iso:
        return 0.0
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return (now - dt).total_seconds() / 86400


def classify(pr: dict, stale_after_days: int, now: datetime) -> str:
    if pr.get("merged_at"):
        return "merged"
    if pr.get("state") == "closed":
        return "closed"
    if _age_days(pr.get("updated_at") or pr.get("created_at"), now) > stale_after_days:
        return "stale"
    return "open"


def check_feedback(api: GitHubAPI, ledger: Ledger, policy: Policy,
                   now: datetime | None = None) -> list[PrStatus]:  # fmt: skip
    now = now or datetime.now(UTC)
    known = last_status(ledger)
    results: list[PrStatus] = []
    for url, opened in our_prs(ledger).items():
        parsed = parse_pr_url(url)
        if not parsed:
            continue
        repo, number = parsed
        if known.get(url) in TERMINAL:
            results.append(PrStatus(url, repo, number, known[url], changed=False))
            continue
        try:
            pr = api.get_pull(repo, number)
        except GitHubError as e:
            results.append(PrStatus(url, repo, number, f"unknown ({e})", changed=False))
            continue
        status = classify(pr, policy.feedback.stale_after_days, now)
        changed = status != "open" and known.get(url) != status
        if changed:
            ledger.append(status, url=url, repo=repo, issue=opened.get("issue"),
                          agent=opened.get("agent"), model=opened.get("model"),
                          donation_type=opened.get("donation_type"),
                          language=opened.get("language"),
                          days_open=round(_age_days(pr.get("created_at"), now), 1))  # fmt: skip
        results.append(PrStatus(url, repo, number, status, changed,
                                round(_age_days(pr.get("created_at"), now), 1)))  # fmt: skip
    _write_cache(ledger)
    return results


def _write_cache(ledger: Ledger) -> Path:
    scores: dict[str, dict[str, int]] = {}
    for e in ledger.entries():
        if e["event"] in ("pr_opened", "merged", "closed", "stale", "incident") and e.get("repo"):
            r = scores.setdefault(str(e["repo"]), {"opened": 0, "merged": 0, "closed": 0,
                                                    "stale": 0, "incident": 0})  # fmt: skip
            r[{"pr_opened": "opened"}.get(e["event"], e["event"])] += 1
    f = paths.FEEDBACK_CACHE / "repos.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(scores, indent=2, sort_keys=True), encoding="utf-8")
    return f


def render_feedback(results: list[PrStatus]) -> str:
    if not results:
        return "no PRs opened yet"
    counts: dict[str, int] = {}
    lines = []
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        mark = " (new)" if r.changed else ""
        lines.append(f"  {r.status:<8} {r.repo}#{r.number}{mark}")
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    return f"{len(results)} PRs — {summary}\n" + "\n".join(lines)


# --------------------------------------------------------------------------- incidents


def block_repo(repo: str, reason: str, days: int, repos_file: Path = paths.REPOS_FILE) -> str:
    """Append a cooldown entry to config/repos.yaml without disturbing its comments."""
    until = (datetime.now(UTC) + timedelta(days=days)).date().isoformat()
    text = repos_file.read_text(encoding="utf-8")
    entry = f"  - repo: {repo}\n    reason: {json.dumps(reason)}\n    until: {until}\n"
    if re.search(r"^blocklist:\s*\[\]\s*$", text, re.M):
        text = re.sub(r"^blocklist:\s*\[\]\s*$", "blocklist:\n" + entry.rstrip("\n"), text,
                      count=1, flags=re.M)  # fmt: skip
    elif re.search(r"^blocklist:\s*$", text, re.M):
        text = re.sub(r"^(blocklist:\s*\n)", r"\g<1>" + entry.replace("\\", "\\\\"), text,
                      count=1, flags=re.M)  # fmt: skip
    else:
        text = text.rstrip("\n") + "\n\nblocklist:\n" + entry
    repos_file.write_text(text, encoding="utf-8")
    return until


def incident(
    api: GitHubAPI, ledger: Ledger, policy: Policy, pr_url: str, reason: str,
    agent: str, offer_revert: bool = True, live: bool = False,
    repos_file: Path | None = None,
) -> dict:  # fmt: skip
    """Apology comment (no excuses, offer revert), blocklist with cooldown, ledger `incident`.
    Never argues and never closes anything itself."""
    parsed = parse_pr_url(pr_url)
    if not parsed or pr_url not in our_prs(ledger):
        raise ValueError(f"{pr_url} is not a PR this engine opened (ledger-verified)")
    repo, number = parsed
    offer = ("If any of this was merged and you'd like it reverted, say so and it will be done "
             "promptly." if offer_revert else "")  # fmt: skip
    body = render("apology_comment.md", acknowledgement=reason.strip(), offer=offer, agent=agent)
    comment_url = ""
    if live:
        comment_url = api.create_comment(repo, number, body).get("html_url", "")
    until = block_repo(repo, f"incident: {reason}", policy.feedback.incident_cooldown_days,
                       repos_file or paths.REPOS_FILE)  # fmt: skip
    ledger.append("incident", url=pr_url, repo=repo, reason=reason, cooldown_until=until,
                  apology=comment_url or "(dry-run: not posted)")  # fmt: skip
    return {"repo": repo, "until": until, "comment": comment_url, "body": body}
