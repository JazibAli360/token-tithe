"""The human review queue: `donate review` lists proposals, `--open N --live` approves one.

A human approval stands in for the confidence gate (R11), the second review (D5) and the
capacity-blind restriction (A3) — that is the point of review mode. It does NOT bypass R5
(--live), R12 (halt), R17, R18 (signing) or the race recheck (D2).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from token_tithe import paths
from token_tithe.etiquette import Etiquette
from token_tithe.executor import Proposal, git
from token_tithe.github_api import GitHubAPI, WriteNotAllowed
from token_tithe.ledger import Ledger
from token_tithe.pipeline import check_halt
from token_tithe.policy import Policy
from token_tithe.publish import Published, Pusher, open_pr, post_comment, race_recheck

OPENABLE = ("pending", "pr_pending")


class ReviewError(RuntimeError):
    pass


def load(root: Path | None = None, statuses: tuple[str, ...] = OPENABLE) -> list[Proposal]:
    root = root or paths.REVIEW
    out = []
    for f in sorted(root.glob("*/proposal.json")):
        try:
            p = Proposal(**json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            continue
        if p.status in statuses:
            out.append(p)
    return sorted(out, key=lambda p: p.created)


def save(p: Proposal, root: Path | None = None) -> None:
    root = root or paths.REVIEW
    (root / p.id / "proposal.json").write_text(json.dumps(asdict(p), indent=2), encoding="utf-8")


def pick(ref: str, root: Path | None = None) -> Proposal:
    items = load(root)
    if ref.isdigit() and 1 <= int(ref) <= len(items):
        return items[int(ref) - 1]
    for p in items:
        if p.id == ref:
            return p
    raise ReviewError(f"no open proposal {ref!r} ({len(items)} in queue)")


def render_list(items: list[Proposal]) -> str:
    if not items:
        return "review queue is empty"
    lines = []
    for i, p in enumerate(items, 1):
        what = "PR" if p.kind == "pr" else "comment"
        lines.append(f"{i:>2}. [{p.confidence:>3}] {what:<7} {p.repo}#{p.issue} — {p.title}")
        lines.append(f"     {p.donation_type} · {p.agent} ({p.model}) · {p.tokens:,} tokens · "
                     f"review: {p.review or 'none'} · state/review/{p.id}/")  # fmt: skip
        for why in p.queued_because:
            lines.append(f"     queued: {why}")
    return "\n".join(lines)


def _resign(p: Proposal, policy: Policy) -> bool:
    s = policy.signing
    key = os.environ.get(s.signing_key_env, "")
    if not (key and s.committer_name and s.committer_email):
        return False
    wt = paths.ROOT / p.worktree
    cfg = ["-c", f"user.name={s.committer_name}", "-c", f"user.email={s.committer_email}",
           "-c", f"gpg.format={s.format}", "-c", f"user.signingkey={key}"]  # fmt: skip
    r = git([*cfg, "commit", "-q", "--amend", "--no-edit", "--reset-author", "-S"], wt)
    return r.returncode == 0


def open_proposal(
    ref: str, *, live: bool, api: GitHubAPI, policy: Policy, ledger: Ledger,
    etiquette: Etiquette, pusher: Pusher, fixture_mode: bool = False,
    root: Path | None = None,
) -> Published:  # fmt: skip
    check_halt()
    p = pick(ref, root)
    if not live:
        raise WriteNotAllowed(
            f"dry-run: would open a {p.kind} on {p.repo}#{p.issue}: {p.title!r} "
            f"(confidence {p.confidence}). Re-run with --live to publish."
        )
    wt = paths.ROOT / p.worktree if not Path(p.worktree).is_absolute() else Path(p.worktree)
    if p.kind == "pr" and not wt.is_dir():
        raise ReviewError(f"worktree {p.worktree} is gone; the proposal cannot be opened")
    if p.kind == "pr" and policy.signing.required and not p.signed:
        if not _resign(p, policy):
            raise ReviewError("commit is unsigned and no signing identity is configured (R18): "
                              "set signing.committer_name/email and $"
                              f"{policy.signing.signing_key_env}")  # fmt: skip
        p.signed = True
    problems = race_recheck(
        api, ledger, policy, etiquette, p.repo, p.issue, p.kind, follow_up=bool(p.follow_up)
    )
    if problems:
        p.status = "discarded"
        save(p, root)
        ledger.append("abandoned", repo=p.repo, issue=p.issue, donation_type=p.donation_type,
                      reason="race recheck at approval (D2): " + "; ".join(problems))  # fmt: skip
        raise ReviewError("cannot open — " + "; ".join(problems))
    if p.kind == "pr":
        pub = open_pr(api, pusher, wt, p.repo, p.base, p.branch, p.title, p.body, fixture_mode)
        event = "pr_opened"
    else:
        pub = post_comment(api, p.repo, p.issue, p.body)
        event = "comment_posted"
    p.status, p.url = "opened", pub.url
    save(p, root)
    ledger.append(event, repo=p.repo, issue=p.issue, donation_type=p.donation_type, url=pub.url,
                  confidence=p.confidence, tokens=p.tokens, model=p.model, agent=p.agent,
                  approved_by="human review", proposal_id=p.id)  # fmt: skip
    return pub


def discard(ref: str, ledger: Ledger, reason: str = "discarded in human review",
            root: Path | None = None) -> Proposal:  # fmt: skip
    p = pick(ref, root)
    p.status = "discarded"
    save(p, root)
    ledger.append("abandoned", repo=p.repo, issue=p.issue, donation_type=p.donation_type,
                  reason=reason, proposal_id=p.id)  # fmt: skip
    wt = paths.ROOT / p.worktree
    if wt.is_dir() and wt.is_relative_to(paths.WORKTREES):
        shutil.rmtree(wt, ignore_errors=True)
    return p
