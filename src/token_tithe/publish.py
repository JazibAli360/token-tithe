"""The only code that writes to GitHub: race recheck (D2), fork, push, PR, comment.

Order for every write: stop-flag check → race recheck → write. A stale write is a wasted
donation; a wrong write is an incident.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from token_tithe import paths
from token_tithe.etiquette import Etiquette
from token_tithe.github_api import GitHubAPI
from token_tithe.ledger import Ledger
from token_tithe.policy import Policy, RateCounts, rate_limit_violation


def race_recheck(
    api: GitHubAPI, ledger: Ledger, policy: Policy, etiquette: Etiquette,
    repo: str, number: int, action: str, me: str | None = None, follow_up: bool = False,
) -> list[str]:  # fmt: skip
    """D2: re-verify everything that could have changed since triage. Empty list = go.
    PrivateRepoError propagates on purpose: R17 aborts the whole run."""
    problems: list[str] = []
    meta = api.get_repo(repo)
    if meta.get("archived"):
        problems.append("repo was archived")
    issue = api.get_issue(repo, number)
    if issue.get("state") != "open" and not follow_up:  # a follow-up's source may be closed
        problems.append("issue is no longer open")
    others = [a["login"] for a in issue.get("assignees", []) if a.get("login") != me]
    if others:
        problems.append(f"issue was assigned to {', '.join(others)}")
    if action == "pr" and (linked := api.linked_open_prs(repo, number)):
        problems.append(f"another PR now references the issue: {linked}")
    v = etiquette.assess_repo(repo, fresh=True)
    if v.rejected:
        problems.append("etiquette verdict changed: " + "; ".join(v.reasons))
    if action == "pr" and (why := assignment_gap(v, issue, me)):
        problems.append(why)
    counts = RateCounts(ledger.prs_to_repo(repo), ledger.prs_today(),
                        ledger.comments_on_issue(repo, number))  # fmt: skip
    if why := rate_limit_violation(policy, counts, "pr" if action == "pr" else "comment"):
        problems.append(f"rate limit (R4): {why}")
    return problems


def assignment_gap(verdict, issue: dict, me: str | None = None) -> str | None:
    """R9: a repo whose CONTRIBUTING says to get assigned first gets no PR until we are."""
    fname = verdict.signals.get("assignment_required")
    assignees = [a.get("login") for a in issue.get("assignees", [])]
    if not fname or (me and me in assignees):
        return None
    return f"repo requires issue assignment before a PR ({fname}); the issue is not assigned to us (R9)"


class Pusher(Protocol):
    def push(self, worktree: Path, fork: str, branch: str) -> None: ...


class GitPusher:
    """`git push` to OUR fork over HTTPS. The token is supplied by an askpass helper that
    reads $GITHUB_TOKEN at call time — it never appears on argv, in a URL, or on disk (R6)."""

    def _askpass(self) -> Path:
        p = paths.STATE / "tmp" / "askpass.sh"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('#!/bin/sh\nexec printf "%s" "$GITHUB_TOKEN"\n', encoding="utf-8")
        p.chmod(0o700)
        return p

    def push(self, worktree: Path, fork: str, branch: str) -> None:
        if not os.environ.get("GITHUB_TOKEN"):
            raise RuntimeError("GITHUB_TOKEN is not set; cannot push")
        env = {**os.environ, "GIT_ASKPASS": str(self._askpass()), "GIT_TERMINAL_PROMPT": "0"}
        url = f"https://x-access-token@github.com/{fork}.git"
        # Never --force (R1): the branch is new; if it exists, the push fails and we abandon.
        r = subprocess.run(["git", "push", url, f"HEAD:refs/heads/{branch}"], cwd=worktree,
                           capture_output=True, text=True, env=env, timeout=180)  # fmt: skip
        if r.returncode != 0:
            raise RuntimeError(f"git push failed: {r.stderr.strip()[-300:]}")


class RecordingPusher:
    """Fixture mode: record the push instead of performing it."""

    def __init__(self):
        self.pushes: list[tuple[str, str]] = []

    def push(self, worktree: Path, fork: str, branch: str) -> None:
        self.pushes.append((fork, branch))


@dataclass
class Published:
    kind: str  # "pr" | "comment"
    url: str
    number: int | None = None


def wait_for_fork(api: GitHubAPI, fork: str, attempts: int = 10, delay: float = 3.0) -> None:
    for _ in range(attempts):
        try:
            api.get_repo(fork)
            return
        except Exception:  # noqa: BLE001 - fork creation is asynchronous
            time.sleep(delay)
    raise RuntimeError(f"fork {fork} did not appear")


def open_pr(
    api: GitHubAPI, pusher: Pusher, worktree: Path, repo: str, base: str, branch: str,
    title: str, body: str, fixture_mode: bool,
) -> Published:  # fmt: skip
    fork = api.create_fork(repo)["full_name"]
    if not fixture_mode:
        wait_for_fork(api, fork)
    pusher.push(worktree, fork, branch)
    pr = api.create_pull(
        repo, title=title, head=f"{fork.split('/')[0]}:{branch}", base=base, body=body
    )
    return Published("pr", pr.get("html_url", ""), pr.get("number"))


def post_comment(api: GitHubAPI, repo: str, number: int, body: str) -> Published:
    c = api.create_comment(repo, number, body)
    return Published("comment", c.get("html_url", f"https://github.com/{repo}/issues/{number}"))
