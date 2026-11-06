"""Phase 6: feedback, incidents, reports/journal, adoption, address (D6), optimizer (D10)."""

import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_executor import MATH, TRUSTED, Reviewer, make, policy, triaged
from token_tithe import optimizer, paths, reports
from token_tithe import policy as P
from token_tithe.address import AddressRefused, address
from token_tithe.adoption import adopt, conventions_for
from token_tithe.agents.base import SUMMARY_FILE, AgentResult
from token_tithe.agents.control import ReferenceAdapter
from token_tithe.etiquette import Etiquette
from token_tithe.feedback import check_feedback, incident
from token_tithe.fixtures import load_fixtures
from token_tithe.github_api import GitHubAPI
from token_tithe.ledger import Ledger
from token_tithe.publish import RecordingPusher
from token_tithe.sandbox import LocalRunner

FX = {f.full_name.lower(): f for f in load_fixtures()}


@pytest.fixture(autouse=True)
def state(tmp_path):
    with paths.use_state(tmp_path / "state") as d:
        yield d


@pytest.fixture
def opened():
    """A PR genuinely opened by the executor in trusted live fixture mode."""
    api = GitHubAPI.fixture(live=True)
    ex, _ = make(api, policy(**TRUSTED), live=True, reviewer=Reviewer(FX))
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "pr_opened"
    return api, out.url, int(out.url.rsplit("/", 1)[1])


# ---------------------------------------------------------------- feedback


def test_feedback_classifies_once_and_feeds_history(opened):
    api, url, n = opened
    led, pol = Ledger(), P.load_policy()
    assert check_feedback(api, led, pol)[0].status == "open"
    api.transport.set_pull_state(MATH, n, "closed", merged=True)
    r = check_feedback(api, led, pol)[0]
    assert r.status == "merged" and r.changed
    assert not check_feedback(api, led, pol)[0].changed, "idempotent"
    assert led.history()[MATH] == (1, 0)
    cache = json.loads((paths.FEEDBACK_CACHE / "repos.json").read_text())
    assert cache[MATH]["merged"] == 1 and cache[MATH]["opened"] == 1


def test_feedback_closed_and_stale(opened):
    api, url, n = opened
    api.transport.age_pull(MATH, n, 45)
    assert check_feedback(api, Ledger(), P.load_policy())[0].status == "stale"
    api.transport.set_pull_state(MATH, n, "closed")
    assert check_feedback(api, Ledger(), P.load_policy())[0].status == "closed"
    assert Ledger().history()[MATH] == (0, 1)


def test_incident_procedure(opened, tmp_path):
    api, url, n = opened
    repos = tmp_path / "repos.yaml"
    shutil.copy(paths.REPOS_FILE, repos)
    with pytest.raises(ValueError, match="ledger-verified"):
        incident(
            api,
            Ledger(),
            P.load_policy(),
            "https://github.com/x/y/pull/1",
            "r",
            "a",
            repos_file=repos,
        )
    res = incident(api, Ledger(), P.load_policy(), url, "The PR ignored your style guide.",
                   agent="op", live=True, repos_file=repos)  # fmt: skip
    assert "Sorry" in res["body"] and "reverted" in res["body"]
    assert api.issue_comments(MATH, n)[-1]["body"] == res["body"]
    loaded = P.load_repos(repos)
    entry = P.blocked(loaded, MATH)
    assert entry and entry.until == (datetime.now(UTC) + timedelta(days=90)).date()
    assert "# Per-repo overrides" in repos.read_text(), "comments preserved"
    assert [e for e in Ledger().entries() if e["event"] == "incident"]


def test_incident_dry_run_posts_nothing(opened, tmp_path):
    api, url, n = opened
    repos = tmp_path / "repos.yaml"
    shutil.copy(paths.REPOS_FILE, repos)
    before = len(api.writes)
    incident(api, Ledger(), P.load_policy(), url, "x", agent="op", live=False, repos_file=repos)
    assert len(api.writes) == before


# ---------------------------------------------------------------- reports


def test_report_and_journal(opened):
    api, url, n = opened
    api.transport.set_pull_state(MATH, n, "closed", merged=True)
    check_feedback(api, Ledger(), P.load_policy())
    Ledger().append("rejected", repo="a/b", reason="anti-AI statement")
    rep = reports.report(Ledger())
    assert "| pr_opened | 1 |" in rep and "| merged | 1 |" in rep and MATH in rep
    j = reports.journal(Ledger())
    assert (
        "opened **1 pull request(s)**" in j and "**1** were merged" in j and "anti-AI policy" in j
    )
    assert "Nothing was donated" in reports.journal(Ledger(), "2001-01")


# ---------------------------------------------------------------- adoption


def test_adopt_profiles_and_registers(tmp_path, monkeypatch):
    repos = tmp_path / "repos.yaml"
    shutil.copy(paths.REPOS_FILE, repos)
    monkeypatch.setattr(paths, "REPOS_FILE", repos)
    api = GitHubAPI.fixture()
    prof = adopt(api, MATH, Etiquette(api, cache_dir=None), repos_file=repos)
    assert prof["language"] == "Python" and prof["has_contributing"]
    assert MATH in P.load_repos(repos).adopted
    assert "commit/PR titles" in conventions_for(MATH)
    adopt(api, MATH, Etiquette(api, cache_dir=None), repos_file=repos)
    assert P.load_repos(repos).adopted.count(MATH) == 1
    with pytest.raises(ValueError, match="cannot adopt"):
        adopt(api, "tithe-fixtures/anti-ai", Etiquette(api, cache_dir=None), repos_file=repos)


# ---------------------------------------------------------------- address (D6)


class Fixer(ReferenceAdapter):
    """Applies a small requested change."""

    def invoke(self, worktree, brief, runner, timeout):
        assert "UNTRUSTED" in (worktree / brief).read_text()
        p = worktree / "mathkit" / "stats.py"
        p.write_text(
            p.read_text().replace(
                '"""Small statistics helpers."""', '"""Small statistics helpers (reviewed)."""'
            )
        )
        (worktree / SUMMARY_FILE).write_text(
            json.dumps({"confidence": 80, "what_changed": "docstring tweak"})
        )
        return AgentResult(0)


def _addr(api, url, live=True, **kw):
    pol = kw.pop("pol", policy(signing={"required": False}))
    return address(url, api=api, ledger=Ledger(), policy=pol, adapter=Fixer(FX), runner=LocalRunner(),
                   pusher=kw.pop("pusher", RecordingPusher()), live=live, me="tithe-bot", **kw)  # fmt: skip


def test_address_refuses_prs_we_did_not_open():
    with pytest.raises(AddressRefused, match="ledger-verified"):
        _addr(GitHubAPI.fixture(live=True), "https://github.com/tithe-fixtures/py-math-bug/pull/10")


def test_address_nothing_to_do_without_reviews(opened):
    api, url, n = opened
    assert _addr(api, url).status == "nothing_to_do"


def test_address_round_pushes_and_comments(opened):
    api, url, n = opened
    api.transport.add_review(MATH, n, "Please tweak the module docstring.",
                             inline=[{"path": "mathkit/stats.py", "line": 1, "body": "say reviewed"}])  # fmt: skip
    pusher = RecordingPusher()
    res = _addr(api, url, pusher=pusher)
    assert res.status == "addressed" and len(pusher.pushes) == 1
    assert "Addressing your feedback (round 1" in api.issue_comments(MATH, n)[-1]["body"]
    assert [e["round"] for e in Ledger().entries() if e["event"] == "addressed"] == [1]
    assert _addr(api, url).status == "nothing_to_do", "old reviews are not re-addressed"


def test_address_dry_run_and_limits(opened):
    api, url, n = opened
    api.transport.add_review(MATH, n, "Please tweak the docstring.")
    before = len(api.writes)
    res = _addr(api, url, live=False)
    assert res.status == "dry_run" and "would commit" in res.detail and len(api.writes) == before
    with pytest.raises(AddressRefused, match="window"):
        _addr(api, url, now=datetime.now(UTC) + timedelta(days=30))
    led = Ledger()
    for i in range(3):
        led.append("addressed", url=url, round=i + 1, status="pushed")
    with pytest.raises(AddressRefused, match="max is 3"):
        _addr(api, url)


# ---------------------------------------------------------------- optimizer (D10)


def _seed_economy():
    led = Ledger()
    for agent, tokens, merges in (("claude", 100_000, 2), ("codex", 40_000, 2)):
        for i in range(merges):
            url = f"https://github.com/o/r{agent}/pull/{i}"
            led.append(
                "pr_opened",
                agent=agent,
                model="m",
                language="Rust",
                tokens=tokens,
                url=url,
                accounted=True,
            )
            led.append("merged", url=url)


def test_optimizer_proposes_and_applies_only_on_request(tmp_path):
    _seed_economy()
    pol_file = tmp_path / "policy.yaml"
    shutil.copy(paths.POLICY_FILE, pol_file)
    prop = optimizer.propose(P.load_policy(), Ledger())
    assert prop["changes"] == {} and any("has not passed eval" in n for n in prop["notes"])
    (paths.EVAL_RUNS / "passes.json").write_text(
        json.dumps({"codex": {"issue_fix": {"passed": True}}})
    )
    prop = optimizer.propose(P.load_policy(), Ledger())
    assert prop["changes"] == {"rust": "codex"}
    optimizer.write_proposal(prop)
    assert P.load_policy(pol_file).routing.by_language == {}, "nothing changes without --apply"
    optimizer.apply(prop, pol_file)
    assert P.load_policy(pol_file).routing.by_language == {"rust": "codex"}
    assert "# token-tithe policy" in pol_file.read_text(), "comments preserved"


def test_optimizer_ignores_simulate_runs():
    Ledger().append("pr_opened", agent="x", language="Go", tokens=1, url="u", mode="simulate")
    assert optimizer.stats(Ledger()) == []
