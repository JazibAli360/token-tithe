"""`donate address <pr>`: respond to maintainer review on OUR pull request (D6).

Strict scope: only PRs the ledger shows we opened; only explicit review comments (review
bodies and inline comments) newer than our last round; at most `address.max_rounds` rounds
within `address.window_days` of opening; each round's diff < `address.max_diff_lines`; the
repo's tests/linter re-run (R7); pushed to the SAME branch without force; every round ledgered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from token_tithe import paths
from token_tithe.agents import Adapter
from token_tithe.agents.base import BRIEF_FILE, SCRATCH
from token_tithe.budget import BudgetGuard
from token_tithe.executor import diff_text, git
from token_tithe.feedback import our_prs, parse_pr_url
from token_tithe.fixtures import diff_stat
from token_tithe.github_api import GitHubAPI, WriteNotAllowed
from token_tithe.ledger import Ledger
from token_tithe.pipeline import check_halt
from token_tithe.policy import Policy
from token_tithe.publish import Pusher
from token_tithe.sandbox import Runner
from token_tithe.secrets_scan import secret_kind
from token_tithe.templates import render
from token_tithe.toolchain import detect, merge_agent_commands


class AddressRefused(RuntimeError):
    pass


@dataclass
class AddressResult:
    pr_url: str
    round: int
    status: str  # addressed | nothing_to_do | dry_run | failed
    detail: str = ""
    comment_url: str = ""


def _ts(iso: str | None) -> datetime:
    if not iso:
        return datetime.min.replace(tzinfo=UTC)
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def rounds_done(ledger: Ledger, pr_url: str) -> list[dict]:
    return [e for e in ledger.entries() if e["event"] == "addressed" and e.get("url") == pr_url]


def pending_feedback(api: GitHubAPI, repo: str, number: int, since: datetime,
                     me: str | None, inclusive: bool = False) -> list[str]:  # fmt: skip
    """Explicit review requests newer than `since` (or at it, for the first round), from anyone
    but us. Timestamps have one-second resolution, so the boundary rule matters."""

    def old(ts: str | None) -> bool:
        t = _ts(ts)
        return t < since if inclusive else t <= since

    out = []
    for r in api.pull_reviews(repo, number):
        if (r.get("user") or {}).get("login") == me or old(r.get("submitted_at")):
            continue
        if r.get("state") in ("CHANGES_REQUESTED", "COMMENTED") and (r.get("body") or "").strip():
            out.append(f"Review by @{r['user']['login']} ({r['state']}):\n{r['body'].strip()}")
    for c in api.pull_review_comments(repo, number):
        if (c.get("user") or {}).get("login") == me or old(c.get("created_at")):
            continue
        where = f"{c.get('path', '?')}:{c.get('line', '?')}"
        out.append(
            f"Inline comment by @{c['user']['login']} on {where}:\n{(c.get('body') or '').strip()}"
        )
    return out


def address(
    pr_url: str, *, api: GitHubAPI, ledger: Ledger, policy: Policy, adapter: Adapter,
    runner: Runner, pusher: Pusher, live: bool, me: str | None = None,
    lessons: str = "None recorded yet.", now: datetime | None = None,
) -> AddressResult:  # fmt: skip
    check_halt()
    now = now or datetime.now(UTC)
    a = policy.address
    opened = our_prs(ledger).get(pr_url)
    if opened is None:
        raise AddressRefused(f"{pr_url} was not opened by this engine (ledger-verified only)")
    parsed = parse_pr_url(pr_url)
    if not parsed:
        raise AddressRefused(f"not a pull request URL: {pr_url}")
    repo, number = parsed
    opened_at = _ts(opened["ts"])
    if (now - opened_at).days > a.window_days:
        raise AddressRefused(f"outside the {a.window_days}-day addressing window")
    done = rounds_done(ledger, pr_url)
    if len(done) >= a.max_rounds:
        raise AddressRefused(f"already addressed {len(done)} round(s); max is {a.max_rounds}")
    pr = api.get_pull(repo, number)
    if pr.get("state") != "open":
        raise AddressRefused("the pull request is no longer open")
    since = max([opened_at] + [_ts(e["ts"]) for e in done])
    feedback = pending_feedback(api, repo, number, since, me or (pr.get("user") or {}).get("login"),
                                inclusive=not done)  # fmt: skip
    rnd = len(done) + 1
    if not feedback:
        return AddressResult(pr_url, rnd, "nothing_to_do", "no new explicit review comments")

    worktree = _worktree_for(opened, pr)
    if worktree is None:
        raise AddressRefused(
            "the PR's worktree is gone; re-clone of fork branches is not supported"
        )
    brief = render("address_brief.md", round=rnd, max_rounds=a.max_rounds, repo=repo,
                   pr_url=pr_url, issue_url=f"https://github.com/{repo}/issues/{opened.get('issue')}",
                   max_diff_lines=a.max_diff_lines, lessons=lessons,
                   review_content="\n\n".join(feedback)[:20_000])  # fmt: skip
    (worktree / SCRATCH).mkdir(exist_ok=True)
    (worktree / BRIEF_FILE).write_text(brief, encoding="utf-8")
    budget = BudgetGuard.from_policy(policy)
    timeout = policy.limits.task_timeout_minutes * 60
    result = adapter.invoke(worktree, BRIEF_FILE, runner, timeout)
    usage = adapter.report_usage()
    budget.record(usage)
    summary = adapter.write_summary(worktree)
    adapter.cleanup(worktree)

    def fail(why: str) -> AddressResult:
        git(["checkout", "--", "."], worktree)
        git(["clean", "-fdq", "-e", ".tithe-*"], worktree)
        ledger.append("addressed", url=pr_url, repo=repo, round=rnd, status="failed", reason=why,
                      tokens=usage.total_tokens, accounted=usage.accounted)  # fmt: skip
        return AddressResult(pr_url, rnd, "failed", why)

    if not result.ok or summary.error:
        return fail(f"agent failed: {result.error or summary.error}")
    if summary.refused:
        return fail(f"agent declined: {summary.refusal_reason}")
    d = diff_stat(worktree)
    if not d.files:
        return fail("agent made no changes")
    if d.lines >= a.max_diff_lines:
        return fail(f"round diff {d.lines} lines ≥ {a.max_diff_lines}")
    if kind := secret_kind(diff_text(worktree)):
        return fail(f"diff contains a {kind} (R6)")
    profile = detect(worktree)
    merge_agent_commands(profile, summary.test_commands)
    for cmd in profile.test + profile.lint:
        r = runner.run(cmd, worktree, "test", timeout)
        if not r.ok:
            return fail(f"`{' '.join(cmd)}` fails after addressing (R7)")
    verification = "\n".join(f"- `{' '.join(c)}` passes" for c in profile.test + profile.lint)
    body = render("review_response.md", round=rnd, max_rounds=a.max_rounds,
                  changes=summary.what_changed or "See the new commit.",
                  verification=verification or "", agent=adapter.display_name)  # fmt: skip
    if not live:
        git(["checkout", "--", "."], worktree)
        return AddressResult(pr_url, rnd, "dry_run",
                             "would commit, push to the PR branch and comment:\n" + body)  # fmt: skip

    s = policy.signing
    import os

    key = os.environ.get(s.signing_key_env, "")
    signed = bool(key and s.committer_name and s.committer_email)
    if s.required and not signed:
        raise AddressRefused("signing identity not configured (R18)")
    cfg = ["-c", f"user.name={s.committer_name or 'token-tithe'}",
           "-c", f"user.email={s.committer_email or 'token-tithe@localhost.invalid'}"]  # fmt: skip
    cfg += ["-c", f"gpg.format={s.format}", "-c", f"user.signingkey={key}"] if signed else \
        ["-c", "commit.gpgsign=false"]  # fmt: skip
    git(["add", "-A"], worktree)
    git([*cfg, "commit", "-q", *(["-S"] if signed else []),
         "-m", f"Address review feedback (round {rnd})"], worktree)  # fmt: skip
    head = (pr.get("head") or {}).get("label", "")
    fork_owner, branch = head.split(":", 1) if ":" in head else ("", "")
    pusher.push(worktree, f"{fork_owner}/{repo.split('/')[1]}", branch)
    try:
        comment = api.create_comment(repo, number, body)
    except WriteNotAllowed as e:
        raise AddressRefused(str(e)) from None
    ledger.append("addressed", url=pr_url, repo=repo, round=rnd, status="pushed",
                  tokens=usage.total_tokens, accounted=usage.accounted, diff_lines=d.lines)  # fmt: skip
    return AddressResult(pr_url, rnd, "addressed", f"{d.lines} lines", comment.get("html_url", ""))


def _worktree_for(opened: dict, pr: dict) -> Path | None:
    """Find the worktree the PR was built in, via its proposal record."""
    for f in paths.REVIEW.glob("*/proposal.json"):
        p = json.loads(f.read_text(encoding="utf-8"))
        if p.get("url") == opened.get("url"):
            wt = Path(p["worktree"])
            wt = wt if wt.is_absolute() else paths.ROOT / wt
            return wt if wt.is_dir() else None
    return None
