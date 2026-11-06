"""Phase 4: triage scoring, ranking and rationales; pipeline + CLI."""

from typer.testing import CliRunner

from token_tithe import paths
from token_tithe import policy as P
from token_tithe.cli import app
from token_tithe.discover import Candidate
from token_tithe.etiquette import Etiquette, Verdict
from token_tithe.github_api import GitHubAPI
from token_tithe.pipeline import Halted, build_queue, gate
from token_tithe.triage import infer_donation_type, triage

BODY = "Steps to reproduce:\n```\nparse('')\n```\nTraceback ... Error: boom. Expected []."


def cand(**kw):
    base = dict(repo="o/r", number=1, title="Parser crashes on empty input", url="u",
                body=BODY,
                labels=["good first issue", "bug"], language="Python", stars=300, license="MIT",
                comments=1, created_at="", updated_at="")  # fmt: skip
    base.update(kw)
    return Candidate(**base)


def ok(key, resp=0.8, engaged=True, status="ok"):
    return Verdict(key.split("#")[0], status, [], resp, {"maintainer_engaged": engaged})


def test_donation_type_inference():
    assert infer_donation_type(cand(title="README: document options", labels=[])) == "docs"
    assert infer_donation_type(cand(title="Add tests for ranges", labels=["tests"])) == "tests"
    assert infer_donation_type(cand(title="CI failing on main", labels=["ci"])) == "ci_repro"
    assert infer_donation_type(cand()) == "issue_fix"


def test_clear_small_issue_outranks_vague_large_one():
    good = cand(number=1)
    vague = cand(
        number=2, title="Bug", body="it is broken, needs a refactor", labels=["help wanted"]
    )
    v = {c.key: ok(c.key) for c in (good, vague)}
    ranked = triage([vague, good], v, P.load_policy(), {"o/r": True})
    assert [t.candidate.number for t in ranked] == [1, 2]
    assert ranked[0].score > ranked[1].score + 20
    assert ranked[1].donation_type == "triage_report"  # large → investigate + report (§5)
    assert ranked[0].rationale.startswith(f"Score {ranked[0].score}/100")


def test_rejected_dropped_and_deprioritized_sink():
    a, b, c = cand(number=1), cand(number=2), cand(number=3)
    v = {
        a.key: ok(a.key, status="deprioritize"),
        b.key: Verdict("o/r", "reject", ["anti-AI"]),
        c.key: ok(c.key, resp=0.4),
    }
    ranked = triage([a, b, c], v, P.load_policy(), {"o/r": True})
    assert [t.candidate.number for t in ranked] == [3, 1]
    assert "deprioritized" in ranked[1].rationale


def test_history_moves_score():
    c = cand()
    v = {c.key: ok(c.key)}
    base = triage([c], v, P.load_policy(), {"o/r": True})[0].score
    up = triage([c], v, P.load_policy(), {"o/r": True}, history={"o/r": (2, 0)})[0].score
    down = triage([c], v, P.load_policy(), {"o/r": True}, history={"o/r": (0, 2)})[0].score
    assert down < base < up


def test_disabled_donation_type_filtered(config_copy):
    import yaml

    f = config_copy / "policy.yaml"
    d = yaml.safe_load(f.read_text())
    d["donation_types"]["docs"]["enabled"] = False
    f.write_text(yaml.safe_dump(d))
    c = cand(title="README: document options", labels=["documentation"])
    assert triage([c], {c.key: ok(c.key)}, P.load_policy(f)) == []


def test_pipeline_on_fixtures():
    api = GitHubAPI.fixture()
    res = build_queue(api, P.load_policy(), P.load_repos(), Etiquette(api, cache_dir=None))
    ranked = [t.candidate.repo.split("/")[1] for t in res.ranked]
    assert ranked[0] == "py-math-bug"
    assert "anti-ai" not in ranked and "opt-out" not in ranked
    assert {s.target for s in res.rejected} == {
        "tithe-fixtures/anti-ai#3",
        "tithe-fixtures/opt-out#3",
    }
    assert api.writes == []


def test_gate_catches_secret_in_clone():
    from token_tithe.fixtures import load_fixture

    api = GitHubAPI.fixture()
    e = Etiquette(api, cache_dir=None)
    f = load_fixture("leaked-secret")
    reason = gate(e, f.full_name, 3, f.repo_dir)
    assert "D4" in reason and "AKIA" not in reason
    assert gate(e, "tithe-fixtures/py-math-bug", 12, load_fixture("py-math-bug").repo_dir) is None


def test_halt_flag_blocks_pipeline(tmp_path, monkeypatch):
    flag = tmp_path / "HALT"
    flag.touch()
    monkeypatch.setattr(paths, "HALT_FLAG", flag)
    api = GitHubAPI.fixture()
    try:
        build_queue(api, P.load_policy(), P.load_repos())
        raise AssertionError("should have halted")
    except Halted as e:
        assert "R12" in str(e)


def test_cli_discover_and_triage_offline():
    r = CliRunner().invoke(app, ["discover", "--dry-run", "--fixtures"])
    assert r.exit_code == 0 and "[dry-run] 8 candidates" in r.output
    r = CliRunner().invoke(app, ["triage", "--fixtures"])
    assert r.exit_code == 0, r.output
    assert "rejected by etiquette (2)" in r.output and paths.QUEUE.is_file()


def test_impact_projects_rank_up():
    plain = cand(repo="o/plain", number=1)
    world = cand(repo="o/world", number=2, topics=["accessibility", "react"])
    art = cand(repo="o/art", number=3, description="A creative coding toolkit for artists")
    v = {c.key: ok(c.key) for c in (plain, world, art)}
    ranked = triage([plain, world, art], v, P.load_policy(), {})
    by = {t.candidate.repo: t for t in ranked}
    assert by["o/world"].components["impact"] == 20 and "accessibility" in by["o/world"].rationale
    assert by["o/art"].components["impact"] == 10  # description only
    assert by["o/plain"].components["impact"] == 0
    assert [t.candidate.repo for t in ranked][0] == "o/world"


def test_stale_feature_and_agent_unfit_issues_sink():
    from datetime import UTC, datetime

    now = datetime(2026, 10, 15, tzinfo=UTC)
    fresh = cand(repo="o/fresh", number=1, created_at="2026-09-20T00:00:00Z")
    stale = cand(repo="o/stale", number=2, created_at="2020-08-24T00:00:00Z")
    feature = cand(repo="o/feat", number=3, title="Feature: fill command for closed shapes",
                   labels=["enhancement"], comments=9, created_at="2026-09-20T00:00:00Z")  # fmt: skip
    unfit = cand(repo="o/unfit", number=4, created_at="2026-09-20T00:00:00Z",
                 body=BODY + "\nPlease test GitHub Codespaces and Gitpod and compare them.")  # fmt: skip
    template = cand(repo="o/tmpl", number=5, created_at="2026-09-20T00:00:00Z",
                    body=BODY + "\n**Screenshots**\nIf applicable, add screenshots.\nHardware: M1")  # fmt: skip
    cs = [fresh, stale, feature, unfit, template]
    ranked = triage(cs, {c.key: ok(c.key) for c in cs}, P.load_policy(), {}, now=now)
    by = {t.candidate.repo: t.components for t in ranked}
    assert by["o/stale"]["staleness"] == -15 and by["o/fresh"]["staleness"] == 0
    assert by["o/feat"]["feature_request"] == -14
    assert by["o/unfit"]["agent_fit"] == -30
    assert by["o/tmpl"]["agent_fit"] == 0  # bug-template boilerplate is not a task
    order = [t.candidate.repo for t in ranked]
    assert order[:2] == ["o/fresh", "o/tmpl"] or order[:2] == ["o/tmpl", "o/fresh"]
    assert set(order[2:]) == {"o/stale", "o/feat", "o/unfit"}


def test_body_feature_asks_gated_data_and_maintainer_only_work_sink():
    # Shapes of PyPSA/PyPSA#1218, sunlabuiuc/PyHealth#843 and PyPSA/PyPSA#1713 (2026-10-15 plan)
    wish = cand(repo="o/wish", number=1, title="To include more than one branch ratings",
                body="I wonder if the project has any plan to include an emergency rating "
                     "in the future. It would be great to cover this feature.")  # fmt: skip
    gated = cand(repo="o/gated", number=2, title="Investigate whether short circuit ifs are faster",
                 body=BODY + "\nRun the test on the full MIMIC3 and/or MIMIC4 datasets.")  # fmt: skip
    admin = cand(repo="o/admin", number=3, title="Gain an OSSF best practices badge",
                 body="It's manual and ideally requires one of the core devs to complete it.")  # fmt: skip
    bug = cand(repo="o/bug", number=4, title="Crash when loading empty network",
               body="Traceback (most recent call last):\nValueError: empty\n"
                    "It would be nice if this raised a clearer message.")  # fmt: skip
    cs = [wish, gated, admin, bug]
    by = {t.candidate.repo: t.components
          for t in triage(cs, {c.key: ok(c.key) for c in cs}, P.load_policy(), {})}  # fmt: skip
    assert by["o/wish"]["feature_request"] == -8
    assert by["o/gated"]["agent_fit"] == -30
    assert by["o/admin"]["agent_fit"] == -30
    assert by["o/bug"]["feature_request"] == 0 and by["o/bug"]["agent_fit"] == 0
