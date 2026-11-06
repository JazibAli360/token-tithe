"""Phase 3: GitHub client — fixture mode, R5 write gate, R17 public-only, backoff, R6."""

import pytest

from token_tithe.github_api import (
    ConnectionFailed,
    GitHubAPI,
    GitHubError,
    NotFound,
    PrivateRepoError,
    RateLimited,
    Response,
    WriteNotAllowed,
    _parse_query,
)

REPO = "tithe-fixtures/py-math-bug"


@pytest.fixture
def api():
    return GitHubAPI.fixture()


def test_search_filters(api):
    gfi = api.search_issues('is:open label:"good first issue" no:assignee archived:false')
    names = {
        i["repository_url"].rsplit("/", 2)[-2] + "/" + i["repository_url"].rsplit("/", 1)[-1]
        for i in gfi
    }
    assert REPO in names
    assert "tithe-fixtures/flaky-ci" not in names  # labeled help wanted, not gfi
    py = api.search_issues("is:open language:python stars:200..500")
    assert {i["html_url"].split("/")[4] for i in py} == {
        "py-math-bug",
        "flaky-ci",
        "opt-out",
    }  # anti-ai has 610 stars


def test_unknown_qualifier_raises():
    with pytest.raises(ValueError, match="unsupported qualifier"):
        _parse_query("is:open sort:stars")


def test_repo_issue_comments_contents(api):
    repo = api.get_repo(REPO)
    assert repo["license"]["spdx_id"] == "MIT" and repo["default_branch"] == "main"
    issue = api.get_issue(REPO, 12)
    assert issue["title"].startswith("moving_average")
    assert api.issue_comments(REPO, 12)[0]["author_association"] == "OWNER"
    assert "moving_average" in api.get_file(REPO, "mathkit/stats.py")
    assert api.get_file(REPO, "nope.txt") is None
    assert "tests/test_stats.py" in api.list_dir(REPO, "tests")
    assert api.get_file("tithe-fixtures/opt-out", ".github/token-tithe-ignore")


def test_contents_cannot_escape_repo(api):
    assert api.get_file(REPO, "../github.yaml") is None


def test_recent_merged_pulls(api):
    pulls = api.recent_merged_pulls(REPO)
    assert {p["author_association"] for p in pulls} == {"CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR"}


def test_linked_prs_and_race_mutators(api):
    t = api.transport
    assert api.linked_open_prs(REPO, 12) == []
    t.link_pr(REPO, 12, 77)
    assert api.linked_open_prs(REPO, 12) == [77]
    t.close_issue(REPO, 12)
    assert api.get_issue(REPO, 12)["state"] == "closed"


def test_r17_private_repo_aborts(api):
    api.transport.set_private(REPO)
    with pytest.raises(PrivateRepoError, match="R17"):
        api.get_repo(REPO)
    with pytest.raises(PrivateRepoError):
        api.get_issue(REPO, 12)
    assert all(REPO.split("/")[1] not in i["html_url"] for i in api.search_issues("is:open"))


def test_r5_writes_refused_without_live(api):
    with pytest.raises(WriteNotAllowed, match="dry-run: would open PR"):
        api.create_pull(REPO, "t", "tithe-bot:b", "main", "body")
    with pytest.raises(WriteNotAllowed):
        api.create_comment(REPO, 12, "hi")
    with pytest.raises(WriteNotAllowed):
        api.create_fork(REPO)
    assert api.writes == []


def test_live_fixture_writes_are_recorded():
    api = GitHubAPI.fixture(live=True)
    pr = api.create_pull(REPO, "Fix", "tithe-bot:donate/12-fix", "main", "Fixes #12")
    assert pr["state"] == "open"
    assert api.get_pull(REPO, pr["number"])["title"] == "Fix"
    api.transport.set_pull_state(REPO, pr["number"], "closed", merged=True)
    assert api.get_pull(REPO, pr["number"])["merged_at"]
    assert [w["describe"] for w in api.writes] == [f"open PR on {REPO}"]


class FlakyTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def request(self, method, path, params=None, json=None):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_backoff_retries_then_succeeds():
    sleeps = []
    t = FlakyTransport([
        Response(429, {"message": "slow down"}, {"retry-after": "2"}),
        Response(502, {}),
        Response(200, {"resources": {}}),
    ])  # fmt: skip
    api = GitHubAPI(t, sleep=sleeps.append)
    assert api.rate_limit() == {"resources": {}}
    assert sleeps == [2.0, 2.0] and t.calls == 3


def test_dropped_connection_retries_reads_but_never_writes():
    sleeps = []
    t = FlakyTransport([ConnectionFailed("GET /rate_limit: dropped"), Response(200, {"ok": 1})])
    api = GitHubAPI(t, sleep=sleeps.append)
    assert api.rate_limit() == {"ok": 1} and sleeps == [1.0] and t.calls == 2
    t = FlakyTransport([ConnectionFailed("POST /x: dropped")])
    with pytest.raises(ConnectionFailed):
        GitHubAPI(t, sleep=lambda s: pytest.fail("must not retry a write"))._call("POST", "/x")
    t = FlakyTransport([ConnectionFailed("dropped")] * 3)
    with pytest.raises(ConnectionFailed):
        GitHubAPI(t, sleep=lambda s: None, max_retries=2).rate_limit()


def test_rate_limit_reset_too_far_raises_instead_of_hanging():
    t = FlakyTransport(
        [
            Response(
                403,
                {"message": "limit"},
                {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "10000"},
            )
        ]
    )
    api = GitHubAPI(t, sleep=lambda s: pytest.fail("must not sleep"), clock=lambda: 0.0)
    with pytest.raises(RateLimited):
        api.rate_limit()


def test_errors_are_typed():
    api = GitHubAPI(FlakyTransport([Response(404, {"message": "Not Found"})]))
    with pytest.raises(NotFound):
        api.get_repo("x/y")
    api = GitHubAPI(FlakyTransport([Response(422, {"message": "Validation Failed"})]))
    with pytest.raises(GitHubError, match="422"):
        api.rate_limit()


def test_token_never_stored_on_client(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "sekrit-value-123")
    api = GitHubAPI.rest()
    assert "sekrit" not in repr(api)
    assert "sekrit" not in repr(vars(api)) and "sekrit" not in repr(vars(api.transport))
