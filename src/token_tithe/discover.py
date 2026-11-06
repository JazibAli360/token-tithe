"""GitHub search → candidate issues (§4).

Filters, in order: favorites searched first; blocklist and repos touched in the last
`exclude_recent_repo_days` excluded; then per repo: public (R17), not archived, not a fork,
permissive license (R3), star window, pushed recently; per issue: open, unassigned, no open
linked PR, not already attempted. Every exclusion is returned with its reason.

Follow-ups (`--follow-up owner/repo#PR`): a maintainer closed one of our PRs but asked for a
different version of it in that PR's thread. The candidate is the PR's source issue (which may
be closed) with the maintainer's request added to its body; the repo gates still apply, the
open-issue, recently-touched and already-attempted checks do not.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from token_tithe.github_api import GitHubAPI, NotFound
from token_tithe.policy import Policy, Repos, blocked, license_allowed


@dataclass
class Candidate:
    repo: str
    number: int
    title: str
    url: str
    body: str
    labels: list[str]
    language: str | None
    stars: int
    license: str | None
    comments: int
    created_at: str
    updated_at: str
    favorite: bool = False
    topics: list[str] = field(default_factory=list)
    description: str = ""
    follow_up_pr: int | None = None  # our closed PR whose thread asked for this follow-up
    follow_up_url: str = ""  # the maintainer's comment that asked for it

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"


@dataclass
class Skip:
    target: str
    reason: str


@dataclass
class DiscoveryResult:
    candidates: list[Candidate] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)


def _fetch_picked(api: GitHubAPI, key: str) -> dict | None:
    repo, _, num = key.partition("#")
    if not (num.isdigit() and repo.count("/") == 1):
        return None
    try:
        return api.get_issue(repo, int(num))
    except NotFound:
        return None


MAINTAINER = {"OWNER", "MEMBER", "COLLABORATOR"}
_SOURCE_REF = re.compile(r"(?i)\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)")


def _fetch_follow_up(api: GitHubAPI, key: str) -> tuple[dict, dict] | str:
    """(source issue, maintainer comment) for a follow-up key, or the reason it is not one."""
    repo, _, num = key.partition("#")
    if not (num.isdigit() and repo.count("/") == 1):
        return "not an owner/repo#PR key"
    try:
        pr = api.get_pull(repo, int(num))
    except NotFound:
        return "PR not found"
    if pr.get("state") != "closed":
        return "PR is still open (use `donate address`)"
    asks = [c for c in api.issue_comments(repo, int(num))
            if c.get("author_association") in MAINTAINER]  # fmt: skip
    if not asks:
        return "no maintainer comment on the PR asks for a follow-up"
    if not (m := _SOURCE_REF.search(pr.get("body") or "")):
        return "PR body names no source issue (Fixes #N)"
    try:
        return api.get_issue(repo, int(m.group(1))), asks[-1]
    except NotFound:
        return "source issue not found"


def _repo_of(issue: dict) -> str:
    return "/".join(issue["repository_url"].rstrip("/").split("/")[-2:])


def build_queries(policy: Policy, today: datetime) -> list[str]:
    d = policy.discovery
    # Only qualifiers GitHub *issue* search supports. Stars, push date and license are
    # repository properties, checked per repo in _repo_reason().
    since = (today - timedelta(days=d.pushed_within_days)).date().isoformat()
    base = f"is:open no:assignee archived:false updated:>={since}"
    return [f'{base} label:"{label}" language:{lang}' for lang in d.languages for label in d.labels]


def impact_queries(api: GitHubAPI, policy: Policy, repos_cfg: Repos, today: datetime) -> list[str]:
    """Issue queries for repos carrying an impact topic. Issue search cannot filter by repo
    topic (L-0006), so find the repos first, then search their issues (repo: qualifiers OR)."""
    d = policy.discovery
    if not d.impact_topics:
        return []
    since = (today - timedelta(days=d.pushed_within_days)).date().isoformat()
    names: list[str] = []
    for topic in d.impact_topics:
        q = f"topic:{topic} stars:{d.min_stars}..{d.max_stars} pushed:>={since} archived:false"
        for r in api.search_repos(q, limit=d.impact_repos_per_topic):
            name = r["full_name"]
            if name not in names and not blocked(repos_cfg, name, today.date()):
                names.append(name)
    out = []
    for i in range(0, len(names), 10):  # keep each query well under GitHub's length limit
        repos = " ".join(f"repo:{n}" for n in names[i : i + 10])
        out += [f'is:open no:assignee {repos} label:"{label}"' for label in d.labels]
        if d.impact_labels:  # GitHub ORs comma-separated label values: one query, not one each
            any_of = ",".join(f'"{label}"' for label in d.impact_labels)
            out.append(f"is:open no:assignee {repos} label:{any_of}")
    return out


def discover(
    api: GitHubAPI,
    policy: Policy,
    repos_cfg: Repos,
    recent_repos: Iterable[str] = (),
    attempted: Iterable[str] = (),
    now: datetime | None = None,
    picked: Iterable[str] = (),
    follow_ups: Iterable[str] = (),
) -> DiscoveryResult:
    """`recent_repos`: repos we touched inside the exclusion window (from the ledger).
    `attempted`: `owner/repo#N` keys already in the ledger. `picked`: operator-chosen
    `owner/repo#N` keys (`--only`), fetched directly instead of searched; every repo and
    issue filter still applies to them."""
    now = now or datetime.now(UTC)
    d = policy.discovery
    res = DiscoveryResult()
    recent = {r.lower() for r in recent_repos}
    seen_keys = {k.lower() for k in attempted}
    favorites = {r.lower() for r in [*repos_cfg.favorites, *repos_cfg.adopted]}
    repo_meta: dict[str, dict | None] = {}
    found: dict[str, Candidate] = {}

    picked, follow_ups = list(picked), list(follow_ups)
    queries = (
        []
        if picked or follow_ups
        else (
            [
                f'is:open no:assignee repo:{fav} label:"{label}"'
                for fav in dict.fromkeys([*repos_cfg.favorites, *repos_cfg.adopted])
                for label in d.labels
            ]
            + build_queries(policy, now)
            + impact_queries(api, policy, repos_cfg, now)
        )
    )
    res.queries = queries

    batches = [api.search_issues(q, limit=d.per_query_limit) for q in queries] if queries else []
    asks: dict[str, tuple[int, dict]] = {}  # issue key -> (our PR, maintainer comment)
    if picked:
        batches = [[_fetch_picked(api, key) for key in picked]]
    if follow_ups:
        batch = []
        for key in follow_ups:
            got = _fetch_follow_up(api, key)
            if isinstance(got, str):
                res.skipped.append(Skip(key, f"follow-up: {got}"))
                continue
            issue, ask = got
            asks[f"{_repo_of(issue)}#{issue['number']}".lower()] = (int(key.partition("#")[2]), ask)
            batch.append(issue)
        batches.append(batch)
    for batch in batches:
        for issue in batch:
            if issue is None or issue.get("pull_request"):
                continue
            repo = _repo_of(issue)
            key = f"{repo}#{issue['number']}"
            if key.lower() in found or any(s.target == key for s in res.skipped):
                continue
            follow = asks.get(key.lower())

            reason = _repo_reason(api, policy, repos_cfg, repo, repo_meta,
                                  set() if follow else recent, now, bool(picked or follow))  # fmt: skip
            if reason is None:
                reason = (_follow_up_reason(api, repo, issue) if follow
                          else _issue_reason(api, repo, issue, seen_keys))  # fmt: skip
            if reason:
                res.skipped.append(Skip(key, reason))
                continue

            meta = repo_meta[repo.lower()] or {}
            found[key.lower()] = Candidate(
                repo=meta.get("full_name", repo),
                number=issue["number"],
                title=issue["title"],
                url=issue["html_url"],
                body=issue.get("body") or "",
                labels=[lb["name"] for lb in issue.get("labels", [])],
                language=meta.get("language"),
                stars=meta.get("stargazers_count", 0),
                license=(meta.get("license") or {}).get("spdx_id"),
                comments=issue.get("comments", 0),
                created_at=issue.get("created_at", ""),
                updated_at=issue.get("updated_at", ""),
                favorite=repo.lower() in favorites,
                topics=list(meta.get("topics") or []),
                description=meta.get("description") or "",
            )
            if follow:
                pr, ask = follow
                c = found[key.lower()]
                c.follow_up_pr, c.follow_up_url = pr, ask.get("html_url", "")
                c.body = (f"{c.body.strip()}\n\n--- Follow-up requested by @"
                          f"{ask.get('user', {}).get('login', '?')} ({ask.get('author_association')})"
                          f" on our closed PR #{pr}. Implement THIS request, not the original fix:"
                          f"\n\n{(ask.get('body') or '').strip()}")  # fmt: skip

    res.candidates = sorted(found.values(), key=lambda c: (not c.favorite, c.repo, c.number))
    return res


def _repo_reason(api, policy, repos_cfg, repo, cache, recent, now, picked=False) -> str | None:
    if entry := blocked(repos_cfg, repo, now.date()):
        return f"blocklisted: {entry.reason}"
    if repo.lower() in recent:
        return f"touched within {policy.discovery.exclude_recent_repo_days} days"
    if repo.lower() not in cache:
        try:
            cache[repo.lower()] = api.get_repo(repo)  # raises PrivateRepoError (R17)
        except NotFound:
            cache[repo.lower()] = None
    meta = cache[repo.lower()]
    if meta is None:
        return "repo not found"
    d = policy.discovery
    spdx = (meta.get("license") or {}).get("spdx_id")
    pushed = datetime.strptime(meta["pushed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    if meta.get("archived"):
        return "archived"
    if meta.get("fork"):
        return "is a fork"
    if not license_allowed(policy, spdx):
        return f"license {spdx or 'none'} not allowed (R3)"
    # the star range steers search toward projects that need help; an operator pick skips it
    if not picked and not d.min_stars <= meta.get("stargazers_count", 0) <= d.max_stars:
        return f"stars {meta.get('stargazers_count')} outside {d.min_stars}..{d.max_stars}"
    if now - pushed > timedelta(days=d.pushed_within_days):
        return f"no push in {d.pushed_within_days} days"
    lang = (meta.get("language") or "").lower()
    if lang not in {x.lower() for x in d.languages}:
        return f"language {meta.get('language')} not in policy"
    return None


def _issue_reason(api, repo, issue, seen_keys) -> str | None:
    if issue.get("state") != "open":
        return "issue not open"
    if issue.get("assignees"):
        return "assigned"
    if f"{repo}#{issue['number']}".lower() in seen_keys:
        return "already attempted (ledger)"
    if prs := api.linked_open_prs(repo, issue["number"]):
        return f"open linked PR(s) {prs}"
    return None


def _follow_up_reason(api, repo, issue) -> str | None:
    if prs := api.linked_open_prs(repo, issue["number"]):
        return f"open linked PR(s) {prs}"
    return None
