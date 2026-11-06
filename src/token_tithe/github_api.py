"""Thin GitHub REST wrapper: auth from env, backoff, R5 write gate, R17 public-only — and a
fixture mode that serves eval/fixtures/*/github.yaml through the *same* client code.

Every caller goes through `GitHubAPI`. The transport is either `HttpTransport` (real API) or
`FixtureTransport` (an in-memory fake GitHub). Guards live in `GitHubAPI`, so they hold in
both modes:

- R5  writes raise `WriteNotAllowed` unless the client was built with `live=True`.
- R17 every repo-scoped call first verifies the repo is public; a private repo raises
      `PrivateRepoError`, which callers treat as "abort the run with a handover note".
- R6  the token is read from the environment per request and never stored on the client.
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import yaml

from token_tithe import paths

API_URL = "https://api.github.com"
USER_AGENT = "token-tithe"
MAX_RATE_LIMIT_SLEEP = 60  # seconds; longer waits abort instead of hanging a run


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotFound(GitHubError):
    pass


class RateLimited(GitHubError):
    pass


class ConnectionFailed(GitHubError):
    """The connection dropped or timed out before GitHub answered."""


class PrivateRepoError(GitHubError):
    """R17: the engine only ever touches public repos."""


class WriteNotAllowed(GitHubError):
    """R5: mutations require --live."""


@dataclass
class Response:
    status: int
    data: Any
    headers: dict[str, str] = field(default_factory=dict)


class Transport(Protocol):
    def request(
        self, method: str, path: str, params: dict | None = None, json: dict | None = None
    ) -> Response: ...


# --------------------------------------------------------------------------- real transport


class HttpTransport:
    """api.github.com over httpx. Reads GITHUB_TOKEN at request time; never stores it."""

    def __init__(self, base_url: str = API_URL, timeout: float = 20.0):
        import httpx

        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def request(self, method, path, params=None, json=None) -> Response:
        import httpx

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            r = self._client.request(method, path, params=params, json=json, headers=headers)
        except httpx.TransportError as e:
            raise ConnectionFailed(f"{method} {path}: {type(e).__name__}: {e}") from None
        try:
            data = r.json() if r.content else None
        except ValueError:
            data = r.text
        return Response(r.status_code, data, {k.lower(): v for k, v in r.headers.items()})


# --------------------------------------------------------------------------- client


class GitHubAPI:
    def __init__(
        self,
        transport: Transport,
        live: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        max_retries: int = 4,
    ):
        self.transport = transport
        self.live = live
        self._sleep = sleep
        self._clock = clock
        self.max_retries = max_retries
        self._public: dict[str, bool] = {}
        self.writes: list[dict] = []  # every write performed (or attempted in fixture mode)

    @classmethod
    def rest(cls, live: bool = False) -> GitHubAPI:
        return cls(HttpTransport(), live=live)

    @classmethod
    def fixture(
        cls, fixtures_dir: Path = paths.FIXTURES, live: bool = False, now: datetime | None = None
    ) -> GitHubAPI:
        return cls(FixtureTransport.load(fixtures_dir, now=now), live=live, sleep=lambda _s: None)

    def __repr__(self) -> str:
        return f"GitHubAPI(transport={type(self.transport).__name__}, live={self.live})"

    # ----------------------------------------------------------------- plumbing

    def _call(self, method: str, path: str, params: dict | None = None, json: dict | None = None):
        for attempt in range(self.max_retries + 1):
            try:
                r = self.transport.request(method, path, params=params, json=json)
            except ConnectionFailed:
                # a read is safe to repeat; a write may already have landed, so never resend it
                if method != "GET" or attempt == self.max_retries:
                    raise
                self._sleep(float(min(2**attempt, MAX_RATE_LIMIT_SLEEP)))
                continue
            if r.status < 400:
                return r.data
            wait = self._retry_after(r, attempt)
            if wait is None or attempt == self.max_retries:
                break
            self._sleep(wait)
        message = r.data.get("message", "") if isinstance(r.data, dict) else str(r.data)
        if r.status == 404:
            raise NotFound(f"{method} {path}: not found", 404)
        if r.status in (403, 429) and self._is_rate_limited(r):
            raise RateLimited(f"{method} {path}: rate limited ({message})", r.status)
        raise GitHubError(f"{method} {path}: HTTP {r.status} {message}".strip(), r.status)

    @staticmethod
    def _is_rate_limited(r: Response) -> bool:
        return (
            r.status == 429
            or r.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in r.headers
        )

    def _retry_after(self, r: Response, attempt: int) -> float | None:
        """Seconds to wait before retrying, or None if the error is not retryable."""
        if r.status in (403, 429) and self._is_rate_limited(r):
            if "retry-after" in r.headers:
                wait = float(r.headers["retry-after"])
            elif "x-ratelimit-reset" in r.headers:
                wait = max(0.0, float(r.headers["x-ratelimit-reset"]) - self._clock())
            else:
                wait = float(2**attempt)
            return wait if wait <= MAX_RATE_LIMIT_SLEEP else None
        if r.status in (500, 502, 503, 504):
            return float(min(2**attempt, MAX_RATE_LIMIT_SLEEP))
        return None

    def _write(self, method: str, path: str, json: dict, describe: str):
        if not self.live:
            raise WriteNotAllowed(f"dry-run: would {describe}")
        data = self._call(method, path, json=json)
        self.writes.append({"method": method, "path": path, "describe": describe})
        return data

    # ----------------------------------------------------------------- R17

    def ensure_public(self, full_name: str) -> None:
        key = full_name.lower()
        if key not in self._public:
            repo = self._call("GET", f"/repos/{full_name}")
            self._public[key] = not repo.get("private", True)
        if not self._public[key]:
            raise PrivateRepoError(f"{full_name} is not public — R17 forbids touching it")

    # ----------------------------------------------------------------- reads

    def get_repo(self, full_name: str) -> dict:
        repo = self._call("GET", f"/repos/{full_name}")
        self._public[full_name.lower()] = not repo.get("private", True)
        if repo.get("private", True):
            raise PrivateRepoError(f"{full_name} is not public — R17 forbids touching it")
        return repo

    def search_repos(self, query: str, limit: int = 10) -> list[dict]:
        """Repository search (one page). `is:public` is always enforced."""
        q = query if "is:public" in query else query + " is:public"
        data = self._call("GET", "/search/repositories",
                          params={"q": q, "per_page": min(100, limit), "sort": "stars"})  # fmt: skip
        return data.get("items", [])[:limit]

    def search_issues(self, query: str, limit: int = 30) -> list[dict]:
        """Issue search. `is:public` and `is:issue` are always enforced."""
        q = query
        if "is:public" not in q:
            q += " is:public"
        if "is:issue" not in q:
            q += " is:issue"
        items: list[dict] = []
        page = 1
        while len(items) < limit:
            per_page = min(100, limit - len(items))
            data = self._call(
                "GET", "/search/issues", params={"q": q, "per_page": per_page, "page": page}
            )
            batch = data.get("items", [])
            items.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return items[:limit]

    def get_issue(self, full_name: str, number: int) -> dict:
        self.ensure_public(full_name)
        return self._call("GET", f"/repos/{full_name}/issues/{number}")

    def issue_comments(self, full_name: str, number: int) -> list[dict]:
        self.ensure_public(full_name)
        return self._call("GET", f"/repos/{full_name}/issues/{number}/comments")

    def linked_open_prs(self, full_name: str, number: int) -> list[int]:
        """Open PRs that reference the issue (timeline cross-references)."""
        self.ensure_public(full_name)
        events = self._call(
            "GET", f"/repos/{full_name}/issues/{number}/timeline", params={"per_page": 100}
        )
        prs = []
        for ev in events or []:
            src = (ev.get("source") or {}).get("issue") or {}
            if ev.get("event") == "cross-referenced" and src.get("pull_request"):
                if src.get("state") == "open":
                    prs.append(src["number"])
        return prs

    def get_file(self, full_name: str, path: str, ref: str | None = None) -> str | None:
        """Text of a file, or None if absent."""
        self.ensure_public(full_name)
        params = {"ref": ref} if ref else None
        try:
            data = self._call("GET", f"/repos/{full_name}/contents/{quote(path)}", params=params)
        except NotFound:
            return None
        if not isinstance(data, dict) or data.get("type") != "file":
            return None
        return base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")

    def list_dir(self, full_name: str, path: str = "") -> list[str]:
        self.ensure_public(full_name)
        try:
            data = self._call("GET", f"/repos/{full_name}/contents/{quote(path)}")
        except NotFound:
            return []
        return [e["path"] for e in data] if isinstance(data, list) else []

    def recent_merged_pulls(self, full_name: str, limit: int = 30) -> list[dict]:
        self.ensure_public(full_name)
        data = self._call(
            "GET",
            f"/repos/{full_name}/pulls",
            params={"state": "closed", "sort": "updated", "direction": "desc", "per_page": limit},
        )
        return [p for p in data if p.get("merged_at")]

    def get_pull(self, full_name: str, number: int) -> dict:
        self.ensure_public(full_name)
        return self._call("GET", f"/repos/{full_name}/pulls/{number}")

    def pull_reviews(self, full_name: str, number: int) -> list[dict]:
        self.ensure_public(full_name)
        return self._call("GET", f"/repos/{full_name}/pulls/{number}/reviews")

    def pull_review_comments(self, full_name: str, number: int) -> list[dict]:
        self.ensure_public(full_name)
        return self._call("GET", f"/repos/{full_name}/pulls/{number}/comments")

    def rate_limit(self) -> dict:
        return self._call("GET", "/rate_limit")

    # ----------------------------------------------------------------- writes (R5)

    def create_fork(self, full_name: str) -> dict:
        self.ensure_public(full_name)
        return self._write("POST", f"/repos/{full_name}/forks", {}, f"fork {full_name}")

    def create_pull(
        self, full_name: str, title: str, head: str, base: str, body: str, draft: bool = False
    ) -> dict:
        self.ensure_public(full_name)
        payload = {"title": title, "head": head, "base": base, "body": body, "draft": draft}
        return self._write("POST", f"/repos/{full_name}/pulls", payload, f"open PR on {full_name}")

    def create_comment(self, full_name: str, number: int, body: str) -> dict:
        self.ensure_public(full_name)
        return self._write(
            "POST",
            f"/repos/{full_name}/issues/{number}/comments",
            {"body": body},
            f"comment on {full_name}#{number}",
        )


# --------------------------------------------------------------------------- fixture transport


def _after_now(frozen: datetime) -> datetime:
    """Reviews happen after the PR opened: never earlier than the real clock."""
    return max(frozen, datetime.now(UTC).replace(microsecond=0))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class _FixtureRepo:
    meta: dict
    repo_dir: Path
    issues: dict[int, dict]
    pulls: dict[int, dict]


class FixtureTransport:
    """An in-memory GitHub built from eval/fixtures/*/github.yaml + repo/ trees.

    Dates in fixtures are relative (`*_days_ago`) so fixtures never age. Tests can mutate
    state through the helper methods (close_issue, assign, link_pr, set_private, ...).
    """

    OWNER_FORK = "tithe-bot"

    def __init__(self, repos: dict[str, _FixtureRepo], now: datetime):
        self.repos = repos
        self.now = now
        self._next_pr = 1000
        self.calls: list[tuple[str, str]] = []

    @classmethod
    def load(cls, fixtures_dir: Path = paths.FIXTURES, now: datetime | None = None):
        now = now or datetime.now(UTC).replace(microsecond=0)
        repos = {}
        for meta_file in sorted(fixtures_dir.glob("*/github.yaml")):
            meta = yaml.safe_load(meta_file.read_text(encoding="utf-8"))
            full = meta["repo"]["full_name"]
            issues = {i["number"]: dict(i) for i in meta.get("issues", [])}
            pulls = {
                p["number"]: {
                    "number": p["number"],
                    "state": "closed",
                    "merged_at": _iso(now - timedelta(days=p["merged_days_ago"])),
                    "author_association": p.get("author_association", "CONTRIBUTOR"),
                    "created_at": _iso(now - timedelta(days=p["merged_days_ago"] + 2)),
                    "updated_at": _iso(now - timedelta(days=p["merged_days_ago"])),
                    "user": {"login": p.get("user", "someone")},
                    "title": p.get("title", f"PR #{p['number']}"),
                }
                for p in meta.get("pulls_recent", [])
            }
            repos[full.lower()] = _FixtureRepo(
                meta["repo"], meta_file.parent / "repo", issues, pulls
            )
        return cls(repos, now)

    # ----------------------------------------------------------------- shapes

    def _ago(self, days: float) -> str:
        return _iso(self.now - timedelta(days=days))

    def _repo_json(self, r: _FixtureRepo) -> dict:
        m = r.meta
        return {
            "full_name": m["full_name"],
            "name": m["full_name"].split("/")[1],
            "owner": {"login": m["full_name"].split("/")[0]},
            "description": m.get("description", ""),
            "topics": list(m.get("topics", [])),
            "private": bool(m.get("private", False)),
            "visibility": "private" if m.get("private") else "public",
            "archived": bool(m.get("archived", False)),
            "fork": bool(m.get("fork", False)),
            "stargazers_count": m.get("stargazers_count", 0),
            "language": m.get("language"),
            "license": {"spdx_id": m["license"]} if m.get("license") else None,
            "default_branch": m.get("default_branch", "main"),
            "pushed_at": self._ago(m.get("pushed_days_ago", 0)),
            "html_url": f"https://github.com/{m['full_name']}",
        }

    def _issue_json(self, r: _FixtureRepo, i: dict) -> dict:
        full = r.meta["full_name"]
        return {
            "number": i["number"],
            "title": i["title"],
            "body": i.get("body", ""),
            "state": i.get("state", "open"),
            "labels": [{"name": n} for n in i.get("labels", [])],
            "assignees": [{"login": a} for a in i.get("assignees", [])],
            "comments": len(i.get("comments", [])),
            "created_at": self._ago(i.get("created_days_ago", 0)),
            "updated_at": self._ago(i.get("updated_days_ago", i.get("created_days_ago", 0))),
            "user": {"login": i.get("author", "reporter")},
            "html_url": f"https://github.com/{full}/issues/{i['number']}",
            "repository_url": f"{API_URL}/repos/{full}",
        }

    # ----------------------------------------------------------------- routing

    def _repo(self, owner: str, name: str) -> _FixtureRepo | None:
        return self.repos.get(f"{owner}/{name}".lower())

    def request(self, method, path, params=None, json=None) -> Response:
        self.calls.append((method, path))
        params = params or {}
        if method == "GET" and path == "/rate_limit":
            core = {"limit": 5000, "remaining": 5000, "reset": int(self.now.timestamp()) + 3600}
            return Response(200, {"resources": {"core": core, "search": core}})
        if method == "GET" and path == "/search/issues":
            return self._search(params)
        if method == "GET" and path == "/search/repositories":
            return self._search_repos(params)
        m = re.fullmatch(r"/repos/([^/]+)/([^/]+)(/.*)?", path)
        if not m:
            return Response(404, {"message": "Not Found"})
        r = self._repo(m.group(1), m.group(2))
        if r is None:
            return Response(404, {"message": "Not Found"})
        rest = m.group(3) or ""
        return self._repo_route(method, r, rest, params, json or {})

    def _repo_route(self, method, r: _FixtureRepo, rest: str, params, json) -> Response:
        if method == "GET" and rest == "":
            return Response(200, self._repo_json(r))
        if m := re.fullmatch(r"/issues/(\d+)", rest):
            i = r.issues.get(int(m.group(1)))
            return Response(200, self._issue_json(r, i)) if i else Response(404, {})
        if m := re.fullmatch(r"/issues/(\d+)/comments", rest):
            # GitHub shares numbering and comment threads between issues and PRs.
            i = r.issues.get(int(m.group(1))) or r.pulls.get(int(m.group(1)))
            if not i:
                return Response(404, {})
            if method == "POST":
                c = {"user": self.OWNER_FORK, "association": "NONE", "days_ago": 0}
                i.setdefault("comments", []).append({**c, "body": json.get("body", "")})
                return Response(201, {"id": len(i["comments"]), "body": json.get("body", "")})
            return Response(200, [self._comment_json(c) for c in i.get("comments", [])])
        if m := re.fullmatch(r"/issues/(\d+)/timeline", rest):
            i = r.issues.get(int(m.group(1)))
            if not i:
                return Response(404, {})
            events = [
                {
                    "event": "cross-referenced",
                    "source": {
                        "issue": {"number": n, "state": "open", "pull_request": {"url": "x"}}
                    },
                }
                for n in i.get("linked_prs", [])
            ]
            return Response(200, events)
        if m := re.fullmatch(r"/contents/?(.*)", rest):
            return self._contents(r, m.group(1))
        if rest == "/pulls" and method == "GET":
            state = params.get("state", "open")
            pulls = [p for p in r.pulls.values() if state == "all" or p["state"] == state]
            return Response(200, sorted(pulls, key=lambda p: -p["number"]))
        if rest == "/pulls" and method == "POST":
            self._next_pr += 1
            pr = {
                "number": self._next_pr,
                "state": "open",
                "merged_at": None,
                "title": json.get("title"),
                "body": json.get("body"),
                "head": {"label": json.get("head")},
                "base": {"ref": json.get("base")},
                "draft": json.get("draft", False),
                "author_association": "NONE",
                "created_at": _iso(self.now),
                "updated_at": _iso(self.now),
                "user": {"login": self.OWNER_FORK},
                "html_url": f"https://github.com/{r.meta['full_name']}/pull/{self._next_pr}",
            }
            r.pulls[pr["number"]] = pr
            return Response(201, pr)
        if m := re.fullmatch(r"/pulls/(\d+)", rest):
            pr = r.pulls.get(int(m.group(1)))
            return Response(200, pr) if pr else Response(404, {})
        if m := re.fullmatch(r"/pulls/(\d+)/(reviews|comments)", rest):
            pr = r.pulls.get(int(m.group(1)))
            if not pr:
                return Response(404, {})
            key = "reviews" if m.group(2) == "reviews" else "review_comments"
            return Response(200, list(pr.get(key, [])))
        if rest == "/forks" and method == "POST":
            name = r.meta["full_name"].split("/")[1]
            return Response(202, {"full_name": f"{self.OWNER_FORK}/{name}"})
        return Response(404, {"message": f"fixture route not implemented: {method} {rest}"})

    def _comment_json(self, c: dict) -> dict:
        return {
            "user": {"login": c.get("user", "someone")},
            "author_association": c.get("association", "NONE"),
            "body": c.get("body", ""),
            "created_at": self._ago(c.get("days_ago", 0)),
        }

    def _contents(self, r: _FixtureRepo, rel_path: str) -> Response:
        rel_path = rel_path.strip("/")
        target = (r.repo_dir / rel_path).resolve()
        if not target.is_relative_to(r.repo_dir.resolve()) or not target.exists():
            return Response(404, {"message": "Not Found"})
        if target.is_dir():
            entries = [
                {
                    "name": p.name,
                    "path": str(p.relative_to(r.repo_dir)),
                    "type": "dir" if p.is_dir() else "file",
                }
                for p in sorted(target.iterdir())
            ]
            return Response(200, entries)
        content = base64.b64encode(target.read_bytes()).decode()
        return Response(
            200, {"type": "file", "path": rel_path, "encoding": "base64", "content": content}
        )

    # ----------------------------------------------------------------- search

    def _search(self, params: dict) -> Response:
        q = params.get("q", "")
        per_page = int(params.get("per_page", 30))
        page = int(params.get("page", 1))
        filt = _parse_query(q)
        items = []
        for r in self.repos.values():
            repo = self._repo_json(r)
            for i in r.issues.values():
                if filt.matches(repo, self._issue_json(r, i), i):
                    items.append(self._issue_json(r, i))
        items.sort(key=lambda it: it["updated_at"], reverse=True)
        chunk = items[(page - 1) * per_page : page * per_page]
        return Response(
            200, {"total_count": len(items), "incomplete_results": False, "items": chunk}
        )

    def _search_repos(self, params: dict) -> Response:
        filt = _parse_query(params.get("q", ""))
        per_page = int(params.get("per_page", 30))
        items = [j for r in self.repos.values() if _repo_matches(filt, j := self._repo_json(r))]
        items.sort(key=lambda it: it["stargazers_count"], reverse=True)
        return Response(200, {"total_count": len(items), "incomplete_results": False,
                              "items": items[:per_page]})  # fmt: skip

    # ----------------------------------------------------------------- test mutators

    def _get(self, full_name: str) -> _FixtureRepo:
        return self.repos[full_name.lower()]

    def close_issue(self, full_name: str, number: int) -> None:
        self._get(full_name).issues[number]["state"] = "closed"

    def assign(self, full_name: str, number: int, login: str) -> None:
        self._get(full_name).issues[number].setdefault("assignees", []).append(login)

    def link_pr(self, full_name: str, number: int, pr_number: int) -> None:
        self._get(full_name).issues[number].setdefault("linked_prs", []).append(pr_number)

    def set_private(self, full_name: str, private: bool = True) -> None:
        self._get(full_name).meta["private"] = private

    def set_archived(self, full_name: str, archived: bool = True) -> None:
        self._get(full_name).meta["archived"] = archived

    def add_review(self, full_name: str, number: int, body: str, state: str = "CHANGES_REQUESTED",
                   user: str = "maintainer", inline: list[dict] | None = None) -> None:  # fmt: skip
        pr = self._get(full_name).pulls[number]
        pr.setdefault("reviews", []).append({
            "id": len(pr.get("reviews", [])) + 1, "state": state, "body": body,
            "user": {"login": user}, "author_association": "OWNER",
            "submitted_at": _iso(_after_now(self.now)),
        })  # fmt: skip
        for c in inline or []:
            pr.setdefault("review_comments", []).append(
                {
                    "id": len(pr.get("review_comments", [])) + 1,
                    "user": {"login": user},
                    "author_association": "OWNER",
                    "created_at": _iso(_after_now(self.now)),
                    **c,
                }  # fmt: skip
            )

    def age_pull(self, full_name: str, number: int, days: int) -> None:
        pr = self._get(full_name).pulls[number]
        pr["created_at"] = pr["updated_at"] = _iso(self.now - timedelta(days=days))

    def set_pull_state(self, full_name: str, number: int, state: str, merged: bool = False) -> None:
        pr = self._get(full_name).pulls[number]
        pr["state"] = state
        pr["merged_at"] = _iso(self.now) if merged else None


# --------------------------------------------------------------------------- query parsing


@dataclass
class _Query:
    labels: list[str] = field(default_factory=list)
    language: str | None = None
    repos: list[str] = field(default_factory=list)  # several repo: qualifiers are ORed
    topics: list[str] = field(default_factory=list)  # repository search only
    state: str | None = None
    no_assignee: bool = False
    archived: bool | None = None
    stars: tuple[int | None, int | None] = (None, None)
    pushed_after: date | None = None
    updated_after: date | None = None
    licenses: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)

    def matches(self, repo: dict, issue: dict, raw: dict) -> bool:
        if repo["private"]:
            return False  # is:public is always enforced
        if self.repos and repo["full_name"].lower() not in self.repos:
            return False
        if self.state and issue["state"] != self.state:
            return False
        names = {lb["name"].lower() for lb in issue["labels"]}
        # each label: qualifier is ANDed; comma-separated values inside one are ORed
        if any(not names & set(lb.split(",")) for lb in self.labels):
            return False
        if self.language and (repo["language"] or "").lower() != self.language:
            return False
        if self.no_assignee and issue["assignees"]:
            return False
        if self.archived is not None and repo["archived"] != self.archived:
            return False
        lo, hi = self.stars
        if lo is not None and repo["stargazers_count"] < lo:
            return False
        if hi is not None and repo["stargazers_count"] > hi:
            return False
        if self.pushed_after and repo["pushed_at"][:10] < self.pushed_after.isoformat():
            return False
        if self.updated_after and issue["updated_at"][:10] < self.updated_after.isoformat():
            return False
        spdx = ((repo.get("license") or {}).get("spdx_id") or "").lower()
        if self.licenses and spdx not in self.licenses:
            return False
        hay = (issue["title"] + " " + (issue["body"] or "")).lower()
        return all(t in hay for t in self.text)


def _repo_matches(f: _Query, repo: dict) -> bool:
    """Repository search: only the repository qualifiers apply."""
    if repo["private"]:
        return False
    if any(t not in repo.get("topics", []) for t in f.topics):
        return False
    if f.language and (repo["language"] or "").lower() != f.language:
        return False
    if f.archived is not None and repo["archived"] != f.archived:
        return False
    lo, hi = f.stars
    if (lo is not None and repo["stargazers_count"] < lo) or (
        hi is not None and repo["stargazers_count"] > hi
    ):
        return False
    if f.pushed_after and repo["pushed_at"][:10] < f.pushed_after.isoformat():
        return False
    return not f.text or all(t in (repo.get("description") or "").lower() for t in f.text)


def _parse_query(q: str) -> _Query:
    """Subset of GitHub search syntax. Unknown qualifiers raise, so bugs surface in tests."""
    out = _Query()
    for tok in shlex.split(q):
        if ":" not in tok:
            out.text.append(tok.lower())
            continue
        key, val = tok.split(":", 1)
        val = val.strip('"').lower()
        if key == "is" and val in ("issue", "public"):
            continue
        if (key == "is" and val in ("open", "closed")) or key == "state":
            out.state = val
        elif key == "label":
            out.labels.append(val)
        elif key == "language":
            out.language = val
        elif key == "repo":
            out.repos.append(val)
        elif key == "topic":
            out.topics.append(val)
        elif key == "no" and val == "assignee":
            out.no_assignee = True
        elif key == "archived":
            out.archived = val == "true"
        elif key == "stars":
            out.stars = _parse_range(val)
        elif key == "pushed":
            out.pushed_after = date.fromisoformat(val.lstrip(">="))
        elif key == "updated":
            out.updated_after = date.fromisoformat(val.lstrip(">="))
        elif key == "license":
            out.licenses.append(val)
        else:
            raise ValueError(f"fixture search: unsupported qualifier {tok!r}")
    return out


def _parse_range(val: str) -> tuple[int | None, int | None]:
    if ".." in val:
        lo, hi = val.split("..", 1)
        return (None if lo == "*" else int(lo), None if hi == "*" else int(hi))
    if val.startswith(">="):
        return (int(val[2:]), None)
    if val.startswith("<="):
        return (None, int(val[2:]))
    return (int(val), int(val))
