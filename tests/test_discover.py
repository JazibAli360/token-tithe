"""Phase 4: discovery filters (§4, R3, R17)."""

import pytest
import yaml

from token_tithe import policy as P
from token_tithe.discover import build_queries, discover
from token_tithe.github_api import GitHubAPI, PrivateRepoError


@pytest.fixture
def api():
    return GitHubAPI.fixture()


def keys(res):
    return {c.key for c in res.candidates}


def skipped(res):
    return {s.target: s.reason for s in res.skipped}


def test_discovers_all_fixture_issues(api):
    res = discover(api, P.load_policy(), P.load_repos())
    assert len(res.candidates) == 8
    assert all(c.license and c.stars for c in res.candidates)


def test_queries_use_only_issue_search_qualifiers():
    from datetime import UTC, datetime

    for q in build_queries(P.load_policy(), datetime.now(UTC)):
        assert "stars:" not in q and "pushed:" not in q and "license:" not in q
        assert "is:open" in q and "no:assignee" in q


def test_blocklist_recent_and_attempted(api, config_copy):
    f = config_copy / "repos.yaml"
    f.write_text(
        "schema_version: 1\nblocklist:\n  - {repo: tithe-fixtures/anti-ai, reason: 'bans AI'}\n"
    )
    res = discover(
        api, P.load_policy(), P.load_repos(f),
        recent_repos=["tithe-fixtures/flaky-ci"], attempted=["tithe-fixtures/js-doc-gap#31"],
    )  # fmt: skip
    s = skipped(res)
    assert s["tithe-fixtures/anti-ai#3"].startswith("blocklisted")
    assert "touched within" in s["tithe-fixtures/flaky-ci#58"]
    assert "already attempted" in s["tithe-fixtures/js-doc-gap#31"]


def test_repo_with_broken_tests_is_skipped_unless_picked(api):
    broken = {"tithe-fixtures/py-math-bug": "2026-11-01"}
    s = skipped(discover(api, P.load_policy(), P.load_repos(), broken_repos=broken))
    assert (
        "tests already failed before our change on 2026-11-01" in s["tithe-fixtures/py-math-bug#12"]
    )
    res = discover(api, P.load_policy(), P.load_repos(), broken_repos=broken,
                   picked=["tithe-fixtures/py-math-bug#12"])  # fmt: skip
    assert keys(res) == {"tithe-fixtures/py-math-bug#12"}


def test_fork_is_skipped_unless_picked(api, monkeypatch):
    real = api.get_repo
    monkeypatch.setattr(api, "get_repo", lambda repo: {**real(repo), "fork": True})
    key = "tithe-fixtures/py-math-bug#12"
    assert skipped(discover(api, P.load_policy(), P.load_repos()))[key] == "is a fork"
    res = discover(api, P.load_policy(), P.load_repos(), picked=[key])
    assert keys(res) == {key}


def test_license_and_stars_filters(api, config_copy):
    f = config_copy / "policy.yaml"
    data = yaml.safe_load(f.read_text())
    data["licenses"]["allow"] = ["MIT"]
    data["discovery"]["min_stars"] = 200
    f.write_text(yaml.safe_dump(data))
    res = discover(api, P.load_policy(f), P.load_repos())
    s = skipped(res)
    assert "license BSD-3-Clause not allowed" in s["tithe-fixtures/py-untested#7"]
    assert "Apache-2.0" in s["tithe-fixtures/flaky-ci#58"]
    assert "stars 95 outside" in s["tithe-fixtures/rs-clippy#4"]


def test_language_filter(api, config_copy):
    f = config_copy / "policy.yaml"
    data = yaml.safe_load(f.read_text())
    data["discovery"]["languages"] = ["python"]
    f.write_text(yaml.safe_dump(data))
    res = discover(api, P.load_policy(f), P.load_repos())
    assert all(c.language == "Python" for c in res.candidates)


def test_linked_pr_assigned_closed_skipped(api):
    t = api.transport
    t.link_pr("tithe-fixtures/py-math-bug", 12, 99)
    t.close_issue("tithe-fixtures/flaky-ci", 58)
    t.assign("tithe-fixtures/js-doc-gap", 31, "someone")
    res = discover(api, P.load_policy(), P.load_repos())
    assert "open linked PR" in skipped(res)["tithe-fixtures/py-math-bug#12"]
    assert "tithe-fixtures/flaky-ci#58" not in keys(res)
    assert "tithe-fixtures/js-doc-gap#31" not in keys(res)


def test_archived_repo_skipped(api):
    api.transport.set_archived("tithe-fixtures/py-untested")
    assert "tithe-fixtures/py-untested#7" not in keys(
        discover(api, P.load_policy(), P.load_repos())
    )


def test_favorites_first(api, config_copy):
    f = config_copy / "repos.yaml"
    f.write_text("schema_version: 1\nfavorites: [tithe-fixtures/rs-clippy]\n")
    res = discover(api, P.load_policy(), P.load_repos(f))
    assert res.candidates[0].key == "tithe-fixtures/rs-clippy#4" and res.candidates[0].favorite


def test_private_repo_aborts(api):
    api.transport.set_private("tithe-fixtures/py-math-bug")
    # is:public keeps it out of search entirely...
    assert "tithe-fixtures/py-math-bug#12" not in keys(
        discover(api, P.load_policy(), P.load_repos())
    )
    # ...and any direct touch raises (R17)
    with pytest.raises(PrivateRepoError):
        api.get_issue("tithe-fixtures/py-math-bug", 12)


def test_impact_topics_are_searched_and_recorded(api):
    api.transport.repos["tithe-fixtures/py-math-bug"].meta["topics"] = ["education", "math"]
    res = discover(api, P.load_policy(), P.load_repos())
    assert any("repo:tithe-fixtures/py-math-bug" in q for q in res.queries)
    c = next(c for c in res.candidates if c.repo == "tithe-fixtures/py-math-bug")
    assert c.topics == ["education", "math"] and c.description


def test_impact_search_skips_blocked_owners(api, config_copy):
    api.transport.repos["tithe-fixtures/py-math-bug"].meta["topics"] = ["education"]
    f = config_copy / "repos.yaml"
    f.write_text(
        "schema_version: 1\nblocklist:\n  - {repo: tithe-fixtures/*, reason: x, until: null}\n"
    )
    res = discover(api, P.load_policy(), P.load_repos(f))
    assert not any("repo:" in q for q in res.queries) and res.candidates == []


def test_abandon_without_tokens_stays_retryable(tmp_path):
    from token_tithe.ledger import Ledger

    led = Ledger(tmp_path / "l.jsonl")
    led.append("abandoned", repo="o/r", issue=1, reason="agent failed: Not logged in")
    led.append("abandoned", repo="o/r", issue=2, tokens=5000, reason="tests fail (R7)")
    led.append("rejected", repo="o/r", issue=3, reason="leaked credentials (D4)")
    assert led.attempted() == {"o/r#2", "o/r#3"}


def test_picked_issues_are_fetched_directly_and_still_filtered(api, config_copy):
    f = config_copy / "repos.yaml"
    f.write_text(
        "schema_version: 1\nblocklist:\n  - {repo: tithe-fixtures/js-doc-gap, reason: 'test'}\n"
    )
    picked = ["tithe-fixtures/py-math-bug#12", "tithe-fixtures/js-doc-gap#31", "bad-key"]
    res = discover(api, P.load_policy(), P.load_repos(f), picked=picked)
    assert res.queries == []  # no search: --only issues need not carry the search labels
    assert [c.key for c in res.candidates] == ["tithe-fixtures/py-math-bug#12"]
    assert skipped(res)["tithe-fixtures/js-doc-gap#31"].startswith("blocklisted")

    tight = P.load_policy()
    from dataclasses import replace

    tight = replace(tight, discovery=replace(tight.discovery, max_stars=10))
    res = discover(api, tight, P.load_repos(f), picked=picked[:1])
    assert [c.key for c in res.candidates] == ["tithe-fixtures/py-math-bug#12"]  # stars ignored
    assert "tithe-fixtures/py-math-bug#12" not in [
        c.key for c in discover(api, tight, P.load_repos(f)).candidates
    ]


def _closed_pr(api, repo, number, body, comments):
    r = api.transport._get(repo)
    r.pulls[number] = {"number": number, "state": "closed", "merged_at": None, "body": body,
                       "comments": comments, "user": {"login": "us"}}  # fmt: skip


def test_follow_up_turns_maintainer_ask_on_closed_pr_into_a_candidate(api):
    repo = "tithe-fixtures/py-math-bug"
    api.transport.close_issue(repo, 12)  # the original issue is done; the follow-up still runs
    ask = {"user": "owner", "association": "OWNER", "days_ago": 1, "body": "Please add pow(x, n)."}
    drive_by = {"user": "fan", "association": "NONE", "days_ago": 0, "body": "+1"}
    _closed_pr(api, repo, 90, "Fixes #12", [ask, drive_by])
    _closed_pr(api, repo, 91, "Fixes #12", [drive_by])  # nobody with write access asked
    _closed_pr(api, repo, 92, "no reference", [ask])
    res = discover(api, P.load_policy(), P.load_repos(), recent_repos=[repo],
                   attempted=[f"{repo}#12"], follow_ups=[f"{repo}#90", f"{repo}#91", f"{repo}#92"])  # fmt: skip
    assert res.queries == []
    [c] = res.candidates
    assert (c.key, c.follow_up_pr) == (f"{repo}#12", 90)
    assert "Please add pow(x, n)." in c.body and "Implement THIS request" in c.body
    assert "no maintainer comment" in skipped(res)[f"{repo}#91"]
    assert "names no source issue" in skipped(res)[f"{repo}#92"]


def test_follow_up_still_applies_repo_gates_and_linked_prs(api, config_copy):
    repo = "tithe-fixtures/py-math-bug"
    ask = {"user": "owner", "association": "MEMBER", "days_ago": 1, "body": "do it differently"}
    _closed_pr(api, repo, 90, "Closes #12", [ask])
    api.transport.link_pr(repo, 12, 99)
    res = discover(api, P.load_policy(), P.load_repos(), follow_ups=[f"{repo}#90"])
    assert "open linked PR" in skipped(res)[f"{repo}#12"]

    f = config_copy / "repos.yaml"
    f.write_text(f"schema_version: 1\nblocklist:\n  - {{repo: {repo}, reason: 'test'}}\n")
    res = discover(api, P.load_policy(), P.load_repos(f), follow_ups=[f"{repo}#90"])
    assert skipped(res)[f"{repo}#12"].startswith("blocklisted")


def test_impact_labels_are_one_ored_query_in_impact_repos_only(api, config_copy):
    from dataclasses import replace

    t = api.transport
    t.repos["tithe-fixtures/py-math-bug"].meta["topics"] = ["education"]
    pol = P.load_policy()
    pol = replace(pol, discovery=replace(pol.discovery, impact_labels=["bug", "type: bug"]))
    res = discover(api, pol, P.load_repos())
    bug_qs = [q for q in res.queries if '"type: bug"' in q]
    assert len(bug_qs) == 1 and "repo:tithe-fixtures/py-math-bug" in bug_qs[0]
    assert not any('"type: bug"' in q and "language:" in q for q in res.queries)
    from token_tithe.github_api import _parse_query

    q = _parse_query(bug_qs[0])
    repo = {"private": False, "full_name": "tithe-fixtures/py-math-bug"}
    issue = {"state": "open", "labels": [{"name": "type: bug"}], "assignees": [],
             "title": "", "body": ""}  # fmt: skip
    assert q.matches({**repo, "language": None, "archived": False, "stargazers_count": 1,
                      "pushed_at": "2026-01-01", "license": None}, issue, {})  # fmt: skip
