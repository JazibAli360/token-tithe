"""Phase 5: executor, review queue, publishing guards, stop/resume/halt — the acceptance items."""

import json
import os
import signal
from dataclasses import replace
from pathlib import Path

import pytest

from token_tithe import paths
from token_tithe import policy as P
from token_tithe import review as rq
from token_tithe import run as run_mod
from token_tithe.agents.base import SUMMARY_FILE
from token_tithe.agents.control import ReferenceAdapter
from token_tithe.budget import BudgetGuard
from token_tithe.etiquette import Etiquette
from token_tithe.executor import Executor
from token_tithe.fixtures import load_fixtures
from token_tithe.github_api import GitHubAPI, WriteNotAllowed
from token_tithe.handover import HandoverSession, StopRequested, request_stop
from token_tithe.handover import read as read_handover
from token_tithe.ledger import Ledger
from token_tithe.pipeline import build_queue
from token_tithe.publish import RecordingPusher
from token_tithe.sandbox import LocalRunner

FX = {f.full_name.lower(): f for f in load_fixtures()}


@pytest.fixture(autouse=True)
def state(tmp_path):
    with paths.use_state(tmp_path / "state") as d:
        yield d


def policy(**sections):
    p = P.load_policy()
    for name, changes in sections.items():
        p = (
            replace(p, **{name: replace(getattr(p, name), **changes)})
            if isinstance(changes, dict)
            else replace(p, **{name: changes})
        )
    return p


TRUSTED = dict(trust=True, signing={"required": False})


class Reviewer(ReferenceAdapter):
    name, display_name, model = "reviewer", "reviewer (control)", "control-reviewer"


def triaged(api, repo):
    q = build_queue(api, P.load_policy(), P.load_repos(), Etiquette(api, cache_dir=None))
    return next(t for t in q.ranked if t.candidate.repo == repo)


def make(api, pol, *, adapter=None, live=False, reviewer=None, clock=None, force=False):
    session = HandoverSession("t-run", live, install_signals=False)
    session.__enter__()
    ex = Executor(api=api, policy=pol, ledger=Ledger(), adapter=adapter or ReferenceAdapter(FX),
                  runner=LocalRunner(), etiquette=Etiquette(api, cache_dir=None),
                  pusher=RecordingPusher(), session=session, budget=BudgetGuard.from_policy(pol),
                  run_id="t-run", live=live, reviewer=reviewer, fixtures=FX, force_review=force,
                  **({"clock": clock} if clock else {}))  # fmt: skip
    return ex, session


MATH = "tithe-fixtures/py-math-bug"


# ---------------------------------------------------------------- review mode (R5, §4)


def test_default_run_queues_proposals_and_writes_nothing():
    api = GitHubAPI.fixture(live=False)
    ex, _ = make(api, P.load_policy())
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "queued_for_review" and api.writes == []
    d = paths.REVIEW / out.proposal_id
    body = (d / "body.md").read_text()
    assert "Generated with reference (control) by the token-tithe engine" in body
    assert "Fixes #12" in body and "mathkit/stats.py" in (d / "diff.patch").read_text()
    assert [e["event"] for e in Ledger().entries()] == ["queued_for_review"]


def test_trusted_live_run_publishes_exactly_one_pr():
    api = GitHubAPI.fixture(live=True)
    ex, _ = make(api, policy(**TRUSTED), live=True, reviewer=Reviewer(FX))
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "pr_opened", out.reason
    assert [w["describe"] for w in api.writes] == [f"fork {MATH}", f"open PR on {MATH}"]
    pr = api.get_pull(MATH, int(out.url.rsplit("/", 1)[1]))
    assert (
        "token-tithe engine" in pr["body"]
        and "second\nreview: reviewer/control-reviewer" in pr["body"]
    )
    assert ex.pusher.pushes == [("tithe-bot/py-math-bug", pr["head"]["label"].split(":")[1])]


def test_confidence_gate_r11():
    api = GitHubAPI.fixture(live=True)
    harsh = Reviewer(FX, verdict_score=10)
    ex, _ = make(api, policy(**TRUSTED), live=True, reviewer=harsh)
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "queued_for_review" and out.confidence < 70 and "R11" in out.reason
    assert api.writes == []


def test_no_second_reviewer_holds_d5():
    api = GitHubAPI.fixture(live=True)
    ex, _ = make(api, policy(**TRUSTED), live=True, reviewer=None)
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "queued_for_review" and "D5" in out.reason and api.writes == []


def test_unsigned_commit_holds_r18():
    api = GitHubAPI.fixture(live=True)
    ex, _ = make(api, policy(trust=True), live=True, reviewer=Reviewer(FX))
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "queued_for_review" and "R18" in out.reason


def test_forced_review_for_capacity_blind_agents():
    api = GitHubAPI.fixture(live=True)
    ex, _ = make(api, policy(**TRUSTED), live=True, reviewer=Reviewer(FX), force=True)
    assert ex.run_task(triaged(api, MATH)).outcome == "queued_for_review"


# ---------------------------------------------------------------- D2 race recheck


class ClosingAdapter(ReferenceAdapter):
    """Solves the task, but the maintainer closes the issue meanwhile."""

    def __init__(self, api, *a, **kw):
        super().__init__(*a, **kw)
        self.api = api

    def invoke(self, worktree, brief, runner, timeout):
        self.api.transport.close_issue(MATH, 12)
        return super().invoke(worktree, brief, runner, timeout)


def test_assignment_first_repo_never_gets_an_unassigned_pr():
    api = GitHubAPI.fixture(live=True)
    f = api.transport.repos[MATH].repo_dir / "CONTRIBUTING.md"
    original = f.read_text()
    try:
        f.write_text(original + "\nOnly start working on an issue once it is assigned to you.\n")
        ex, _ = make(api, policy(**TRUSTED), live=True, reviewer=Reviewer(FX))
        out = ex.run_task(triaged(api, MATH))
    finally:
        f.write_text(original)
    assert out.outcome == "queued_for_review" and "requires issue assignment" in out.reason
    assert api.writes == []


def test_race_recheck_abandons_stale_work():
    api = GitHubAPI.fixture(live=True)
    ex, _ = make(api, policy(**TRUSTED), adapter=ClosingAdapter(api, FX), live=True,
                 reviewer=Reviewer(FX))  # fmt: skip
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "abandoned" and "no longer open" in out.reason and api.writes == []


def test_existing_pr_stops_the_task_before_the_agent_runs():
    api = GitHubAPI.fixture()
    t = triaged(api, MATH)  # discovered while no PR existed
    api.transport.link_pr(MATH, 12, 99)  # then someone opens one
    calls = []

    class Recording(ReferenceAdapter):
        def invoke(self, *a, **kw):
            calls.append(1)
            return super().invoke(*a, **kw)

    out = make(api, P.load_policy(), adapter=Recording(FX))[0].run_task(t)
    assert out.outcome == "rejected" and "[99]" in out.reason
    assert calls == [] and out.tokens == 0


# ---------------------------------------------------------------- D3 timeout, R6 secrets, scope


def test_task_timeout_preserves_worktree():
    api = GitHubAPI.fixture()
    ticks = iter([0, 0, 0, 0, 10**9] + [10**9] * 50)
    ex, session = make(api, P.load_policy(), clock=lambda: next(ticks))
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "timeout"
    assert "D3" in out.reason or "task_timeout" in out.reason or "deadline" in out.reason
    assert any(Path(paths.WORKTREES).rglob("mathkit")), "timed-out worktree kept for resume"
    h = read_handover()
    assert h.task.key == out.key and h.task.next_step, "timed-out task stays in the handover"
    session.finalize("timeout")
    assert read_handover().resumable


class Writes(ReferenceAdapter):
    def __init__(self, files, *a, **kw):
        super().__init__(*a, **kw)
        self.files = files

    def invoke(self, worktree, brief, runner, timeout):
        r = super().invoke(worktree, brief, runner, timeout)
        for rel, text in self.files.items():
            (worktree / rel).parent.mkdir(parents=True, exist_ok=True)
            (worktree / rel).write_text(text)
        return r


def test_secret_in_diff_is_never_published():
    api = GitHubAPI.fixture()
    leak = Writes({"mathkit/conf.py": "KEY = 'AKIA" + "ABCDEFGHIJKLMNOP'\n"}, FX)
    ex, _ = make(api, P.load_policy(), adapter=leak)
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "abandoned" and "R6" in out.reason
    assert not list(paths.WORKTREES.rglob("conf.py")), "abandoned worktree removed"


def test_tests_donation_may_not_touch_source():
    api = GitHubAPI.fixture()
    meddle = Writes({"textkit/ranges.py": "# changed\n"}, FX)
    ex, _ = make(api, P.load_policy(), adapter=meddle)
    out = ex.run_task(triaged(api, "tithe-fixtures/py-untested"))
    assert out.outcome == "abandoned" and "out-of-scope" in out.reason


# ---------------------------------------------------------------- triage_report / comments (R4)


class Reporter(ReferenceAdapter):
    def invoke(self, worktree, brief, runner, timeout):
        (worktree / ".tithe").mkdir(exist_ok=True)
        (worktree / ".tithe/report.md").write_text(
            "**Reproduction**\n\nrun it\n\n**Root cause**\n\nrange"
        )
        (worktree / SUMMARY_FILE).write_text(json.dumps({"confidence": 80}))
        from token_tithe.agents.base import AgentResult

        return AgentResult(0)


def test_triage_report_posts_one_comment_ever():
    api = GitHubAPI.fixture(live=True)
    t = triaged(api, MATH)
    t.donation_type = "triage_report"
    ex, _ = make(api, P.load_policy(), adapter=Reporter(FX))
    out = ex.run_task(t)
    assert out.outcome == "queued_for_review"
    rq.open_proposal("1", live=True, api=api, policy=P.load_policy(), ledger=Ledger(),
                     etiquette=Etiquette(api, cache_dir=None), pusher=RecordingPusher(), fixture_mode=True)  # fmt: skip
    assert [w["describe"] for w in api.writes] == [f"comment on {MATH}#12"]
    comment = api.issue_comments(MATH, 12)[-1]["body"]
    assert "Investigation notes for #12" in comment and "token-tithe engine" in comment
    out2 = make(api, P.load_policy(), adapter=Reporter(FX))[0].run_task(t)
    with pytest.raises(rq.ReviewError, match="R4"):
        rq.open_proposal(out2.proposal_id, live=True, api=api, policy=P.load_policy(),
                         ledger=Ledger(), etiquette=Etiquette(api, cache_dir=None),
                         pusher=RecordingPusher(), fixture_mode=True)  # fmt: skip
    assert len(api.writes) == 1


# ---------------------------------------------------------------- review queue acceptance


def test_review_open_needs_live_and_opens_exactly_one():
    res = run_mod.donate_run(fixtures=True, echo=lambda m: None, install_signals=False, max_tasks=2)
    assert res.status == "completed" and len(rq.load()) == 2
    api = GitHubAPI.fixture(live=True)
    kw = dict(api=api, policy=P.load_policy(), ledger=Ledger(),
              etiquette=Etiquette(api, cache_dir=None), pusher=RecordingPusher(), fixture_mode=True)  # fmt: skip
    with pytest.raises(WriteNotAllowed, match="would open a pr"):
        rq.open_proposal("1", live=False, **kw)
    with pytest.raises(rq.ReviewError, match="R18"):
        rq.open_proposal("1", live=True, **kw)  # unsigned, no identity configured
    pub = rq.open_proposal("1", live=True, **{**kw, "policy": policy(signing={"required": False})})
    assert pub.kind == "pr" and len([w for w in api.writes if "open PR" in w["describe"]]) == 1
    assert len(rq.load()) == 1
    pr = api.get_pull(
        pub.url.split("/")[4] and "tithe-fixtures/" + pub.url.split("/")[4], pub.number
    )
    assert "Generated with reference (control) by the token-tithe engine" in pr["body"]
    assert "tokens: 0" in pr["body"]
    assert [e["event"] for e in Ledger().entries()][-1] == "pr_opened"


def test_discard():
    run_mod.donate_run(fixtures=True, echo=lambda m: None, install_signals=False, max_tasks=1)
    p = rq.discard("1", Ledger())
    assert rq.load() == [] and not (paths.ROOT / p.worktree).exists()


# ---------------------------------------------------------------- R12 halt, R14/B handover, R15


def test_halt_blocks_and_resume_clears():
    paths.HALT_FLAG.write_text("x")
    res = run_mod.donate_run(fixtures=True, echo=lambda m: None, install_signals=False)
    assert res.status == "halted" and read_handover().status == "halted" and rq.load() == []
    run_mod.resume_run(fixtures=True, echo=lambda m: None)
    assert not paths.HALT_FLAG.exists()


def test_rate_limit_stops_cleanly_with_handover(monkeypatch):
    from token_tithe.github_api import RateLimited

    def limited(*a, **k):
        raise RateLimited("GET /x: rate limited", 403)

    monkeypatch.setattr(run_mod, "build_queue", limited)
    res = run_mod.donate_run(fixtures=True, echo=lambda m: None, install_signals=False)
    h = read_handover()
    assert (
        res.status == "rate_limited" and h.status == "rate_limited" and "rate limited" in h.reason
    )
    assert not h.resumable and rq.load() == []


class Stopper(ReferenceAdapter):
    def invoke(self, worktree, brief, runner, timeout):
        r = super().invoke(worktree, brief, runner, timeout)
        request_stop()
        return r


def test_stop_mid_task_writes_handover_and_publishes_nothing(monkeypatch):
    api = GitHubAPI.fixture(live=True)
    monkeypatch.setattr(run_mod, "_adapter", lambda name, fx: Stopper(fx))
    res = run_mod.donate_run(fixtures=True, live=True, api=api, echo=lambda m: None,
                             install_signals=False)  # fmt: skip
    h = read_handover()
    assert res.status == "stopped" and h.status == "stopped" and api.writes == []
    assert h.task.next_step == "verify" and (paths.ROOT / h.task.worktree).is_dir()
    assert "next step: verify" in paths.HANDOVER_MD.read_text()
    # resume continues at verify with the agent's work intact (no second agent call)
    calls = []
    monkeypatch.setattr(ReferenceAdapter, "invoke", lambda *a, **k: calls.append(1))
    out = run_mod.resume_run(fixtures=True, api=api, echo=lambda m: None)
    assert out.status == "completed" and calls == []
    assert out.outcomes[0].outcome == "queued_for_review"


class Overspender(ReferenceAdapter):
    def report_usage(self):
        from token_tithe.budget import Usage

        return Usage(model=self.model, input_tokens=10**9, output_tokens=0, cost_estimate=0.0)


def test_budget_overrun_keeps_agent_work_resumable(monkeypatch):
    monkeypatch.setattr(run_mod, "_adapter", lambda name, fx: Overspender(fx))
    res = run_mod.donate_run(fixtures=True, echo=lambda m: None, install_signals=False)
    h = read_handover()
    assert res.status == "budget_exhausted" and h.status == "budget_exhausted"
    assert h.task.next_step == "verify" and h.tokens_spent >= 10**9
    calls = []
    monkeypatch.setattr(ReferenceAdapter, "invoke", lambda *a, **k: calls.append(1))
    out = run_mod.resume_run(fixtures=True, echo=lambda m: None)
    assert out.status == "completed" and calls == []  # the paid-for work is reused


def test_stop_right_before_publish_then_resume_publishes(monkeypatch):
    api = GitHubAPI.fixture(live=True)
    orig = Executor._save_proposal

    def save_then_stop(self, p, wt):
        d = orig(self, p, wt)
        if p.status == "pr_pending":
            request_stop()
        return d

    monkeypatch.setattr(Executor, "_save_proposal", save_then_stop)
    ex, session = make(api, policy(**TRUSTED), live=True, reviewer=Reviewer(FX))
    with pytest.raises(StopRequested):
        ex.run_task(triaged(api, MATH))
    session.__exit__(StopRequested, StopRequested("x"), None)
    h = read_handover()
    assert h.status == "stopped" and h.task.next_step == "publish" and api.writes == []
    monkeypatch.setattr(Executor, "_save_proposal", orig)
    monkeypatch.setattr(run_mod, "load_policy", lambda: policy(**TRUSTED))
    run_mod.resume_run(live=True, fixtures=True, api=api, echo=lambda m: None)
    assert [w["describe"] for w in api.writes] == [f"fork {MATH}", f"open PR on {MATH}"]


def test_sigterm_leaves_valid_handover():
    with pytest.raises(StopRequested), HandoverSession("sig-run", False) as s:
        s.step("execute", key="o/r#1", next_step="invoke")
        os.kill(os.getpid(), signal.SIGTERM)
    h = read_handover()
    assert h.status == "stopped" and h.task.next_step == "invoke" and "SIGTERM" in h.reason


@pytest.mark.parametrize("exc, status", [(None, "completed"), (RuntimeError, "crashed"),
                                         (StopRequested, "stopped")])  # fmt: skip
def test_every_exit_writes_a_fresh_handover(exc, status):
    with (
        pytest.raises(exc) if exc else _null(),
        HandoverSession("p-run", False, install_signals=False),
    ):
        if exc:
            raise exc("boom")
    assert read_handover().status == status


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_below_min_viable_capacity_attempts_nothing(monkeypatch):
    real = BudgetGuard.from_policy
    monkeypatch.setattr(BudgetGuard, "from_policy",
                        classmethod(lambda cls, p, real_quota=None: real(p, real_quota=1000)))  # fmt: skip
    res = run_mod.donate_run(fixtures=True, echo=lambda m: None, install_signals=False)
    assert res.status == "insufficient_capacity" and "nothing attempted" in res.reason
    assert rq.load() == [] and read_handover().status == "insufficient_capacity"


def test_plan_only_executes_nothing():
    res = run_mod.donate_run(
        fixtures=True, plan_only=True, echo=lambda m: None, install_signals=False
    )
    assert res.status == "planned" and res.plan and rq.load() == []


# ---------------------------------------------------------------- A3 model fallback


class ModelRunner(LocalRunner):
    """Agent calls: the first model is 'unavailable'; later ones apply the reference fix."""

    def __init__(self):
        self.models = []

    def run(self, argv, cwd, purpose, timeout, env=None, stdin=None):
        from token_tithe.sandbox import RunResult

        if purpose != "agent":
            return super().run(argv, cwd, purpose, timeout, env, stdin)
        model = argv[argv.index("--model") + 1]
        self.models.append(model)
        if model == "claude-opus-5-5":
            return RunResult(1, "", f"API error: model {model} not found")
        _ref(cwd).invoke(cwd, "", self, 1)
        usage = {"usage": {"input_tokens": 10, "output_tokens": 5}, "result": "ok",
                 "modelUsage": {model: {}}}  # fmt: skip
        return RunResult(0, json.dumps(usage), "")


def _ref(cwd):
    a = ReferenceAdapter(FX)
    a.repo = MATH
    return a


def _fallback_executor(api):
    from token_tithe.agents import get_adapter

    ex, _ = make(api, P.load_policy(), adapter=get_adapter("claude"))
    ex.runner = ModelRunner()
    return ex


def test_fallback_refused_without_eval_pass():
    api = GitHubAPI.fixture()
    out = _fallback_executor(api).run_task(triaged(api, MATH))
    assert out.outcome == "abandoned" and "no eval-passed fallback" in out.reason


def test_fallback_to_eval_passed_model_is_ledgered_and_disclosed():
    paths.EVAL_RUNS.mkdir(parents=True, exist_ok=True)
    (paths.EVAL_RUNS / "passes.json").write_text(
        json.dumps({"claude:claude-sonnet-5-5": {"issue_fix": {"passed": True}}})
    )
    api = GitHubAPI.fixture()
    ex = _fallback_executor(api)
    out = ex.run_task(triaged(api, MATH))
    assert ex.runner.models == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert out.outcome == "queued_for_review", out.reason
    events = [e["event"] for e in Ledger().entries()]
    assert "model_switch" in events
    body = (paths.REVIEW / out.proposal_id / "body.md").read_text()
    assert "fallback from claude-opus-5-5" in body


# ---------------------------------------------------------------- R7 missing toolchain


class NoToolchainRunner(LocalRunner):
    """The host has no toolchain for the repo's tests (review mode runs them on the host)."""

    def run(self, argv, cwd, purpose, timeout, env=None, stdin=None):
        from token_tithe.sandbox import RunResult

        if purpose == "test":
            return RunResult(127, "", f"[Errno 2] No such file or directory: '{argv[0]}'")
        return super().run(argv, cwd, purpose, timeout, env, stdin)


def test_missing_toolchain_is_flagged_not_silently_skipped():
    api = GitHubAPI.fixture()
    baseline = make(api, P.load_policy())[0].run_task(triaged(api, MATH)).confidence
    api = GitHubAPI.fixture()
    ex, _ = make(api, P.load_policy())
    ex.runner = NoToolchainRunner()
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "queued_for_review", out.reason
    assert out.confidence < baseline
    body = (paths.REVIEW / out.proposal_id / "body.md").read_text()
    assert "NOT RUN: `python` is not installed" in body and "NOT VERIFIED" in body


def test_missing_toolchain_abandons_live_r7():
    api = GitHubAPI.fixture(live=True)
    ex, _ = make(api, policy(**TRUSTED), live=True, reviewer=Reviewer(FX))
    ex.runner = NoToolchainRunner()
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "abandoned" and "toolchain missing" in out.reason and api.writes == []


def test_change_in_undetected_ecosystem_lowers_confidence():
    """A Go file outside any module has nothing that could build or test it: say so."""
    api = GitHubAPI.fixture()
    baseline = make(api, P.load_policy())[0].run_task(triaged(api, MATH)).confidence
    api = GitHubAPI.fixture()
    gofile = Writes({"mathkit/x.go": "package x\n"}, FX)
    ex, _ = make(api, P.load_policy(), adapter=gofile)
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "queued_for_review", out.reason
    assert out.confidence < baseline
    body = (paths.REVIEW / out.proposal_id / "body.md").read_text()
    assert "touches go files, but no go test/build command" in body


class FakeGoRunner(LocalRunner):
    """`go` succeeds without running; everything else runs on the host."""

    def __init__(self):
        super().__init__()
        self.go_calls = []

    def run(self, argv, cwd, purpose, timeout, env=None, stdin=None):
        from token_tithe.sandbox import RunResult

        if argv[0] == "go":
            self.go_calls.append(argv)
            return RunResult(0, "ok", "")
        return super().run(argv, cwd, purpose, timeout, env, stdin)


def test_change_in_nested_go_module_is_tested_and_vetted():
    """meshery.io-style assets/scripts/go.mod: the touched module runs via `go -C <dir>`."""
    api = GitHubAPI.fixture()
    gomod = Writes({"mathkit/go.mod": "module example.com/x\n\ngo 1.27.2\n",
                    "mathkit/x.go": "package x\n"}, FX)  # fmt: skip
    ex, _ = make(api, P.load_policy(), adapter=gomod)
    ex.runner = FakeGoRunner()
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "queued_for_review", out.reason
    assert ["go", "-C", "mathkit", "test", "./..."] in ex.runner.go_calls
    assert ["go", "-C", "mathkit", "vet", "./..."] in ex.runner.go_calls
    body = (paths.REVIEW / out.proposal_id / "body.md").read_text()
    assert "`go -C mathkit test ./...` passes" in body and "NOT VERIFIED" not in body


def test_only_runs_the_picked_issues():
    msgs = []
    res = run_mod.donate_run(fixtures=True, echo=msgs.append, install_signals=False,
                             only=[MATH + "#12", "nobody/nothing#1"], max_tasks=5)  # fmt: skip
    assert [o.key for o in res.outcomes] == [f"{MATH}#12"]
    assert any("nobody/nothing#1 is not in the queue" in m for m in msgs)


def test_only_may_retry_an_attempted_issue():
    Ledger().append("abandoned", repo=MATH, issue=12, tokens=900, reason="tests fail (R7)")
    other = "tithe-fixtures/js-doc-gap"
    Ledger().append("abandoned", repo=other, issue=31, tokens=900, reason="tests fail (R7)")
    res = run_mod.donate_run(fixtures=True, echo=lambda m: None, install_signals=False,
                             only=[MATH + "#12"], max_tasks=5)  # fmt: skip
    assert [o.key for o in res.outcomes] == [f"{MATH}#12"]
    res = run_mod.donate_run(fixtures=True, echo=lambda m: None, install_signals=False, max_tasks=5)
    assert f"{other}#31" not in [o.key for o in res.outcomes], "unpicked attempts stay skipped"


class FailingTestsRunner(LocalRunner):
    """Every pytest run fails, before and after the change: an environment problem."""

    output = "E   ImportError: libfoo.so not found\n"

    def run(self, argv, cwd, purpose, timeout, env=None, stdin=None):
        from token_tithe.sandbox import RunResult

        if purpose == "test" and "pytest" in argv:
            return RunResult(1, "", self.output)
        return super().run(argv, cwd, purpose, timeout, env, stdin)


def evidence(out, name):
    folder = Path(out.evidence)  # repo-relative in real runs, absolute under a tmp state
    return ((folder if folder.is_absolute() else paths.ROOT / folder) / name).read_text()


def test_r7_abandon_keeps_failing_output_and_diff():
    api = GitHubAPI.fixture()
    broken = Writes({"mathkit/stats.py": "raise SystemError('broken by the change')\n"}, FX)
    ex, _ = make(api, P.load_policy(), adapter=broken)
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "abandoned" and out.reason.startswith("tests fail after the change")
    assert "broken by the change" in evidence(out, "output.txt")
    assert "+raise SystemError" in evidence(out, "diff.patch")
    entry = [e for e in Ledger().entries() if e["event"] == "abandoned"][-1]
    assert entry["evidence"] == out.evidence
    assert not list(paths.WORKTREES.rglob("stats.py")), "abandoned worktree still removed"


def test_tests_failing_before_the_change_are_blamed_on_the_environment():
    api = GitHubAPI.fixture()
    ex, _ = make(api, P.load_policy())
    ex.runner = FailingTestsRunner()
    out = ex.run_task(triaged(api, MATH))
    assert out.outcome == "abandoned" and "already failed before the change" in out.reason
    assert "libfoo.so" in evidence(out, "output.txt")


def test_evidence_withholds_secrets():
    api = GitHubAPI.fixture()
    ex, _ = make(api, P.load_policy())
    ex.runner = FailingTestsRunner()
    ex.runner.output = "token AKIA" + "ABCDEFGHIJKLMNOP leaked\n"
    out = ex.run_task(triaged(api, MATH))
    text = evidence(out, "output.txt")
    assert "AKIA" not in text and "withheld" in text


def test_dco_signoff_only_where_the_repo_requires_it(tmp_path):
    import subprocess

    from token_tithe.executor import requires_dco

    def last_message(out):
        wt = (
            paths.ROOT
            / json.loads((paths.REVIEW / out.proposal_id / "proposal.json").read_text())["worktree"]
        )
        return subprocess.run(
            ["git", "log", "-1", "--format=%B"], cwd=wt, capture_output=True, text=True
        ).stdout

    api = GitHubAPI.fixture()
    plain = make(api, P.load_policy())[0].run_task(triaged(api, MATH))
    assert plain.outcome == "queued_for_review" and "Signed-off-by" not in last_message(plain)

    api = GitHubAPI.fixture()
    dco = Writes({"DCO": "Developer Certificate of Origin\n"}, FX)
    base = P.load_policy()  # identity is blank in a fresh clone: give the test its own
    pol = replace(base, signing=replace(base.signing, committer_name="T Tithe",
                                        committer_email="1+t@users.noreply.github.com"))  # fmt: skip
    out = make(api, pol, adapter=dco)[0].run_task(triaged(api, MATH))
    assert out.outcome == "queued_for_review", out.reason
    s = pol.signing
    assert f"Signed-off-by: {s.committer_name} <{s.committer_email}>" in last_message(out)

    (tmp_path / "CONTRIBUTING.md").write_text("Please add a Signed-off-by line to commits.\n")
    assert requires_dco(tmp_path)
    (tmp_path / "CONTRIBUTING.md").write_text("Run the tests before opening a PR.\n")
    assert not requires_dco(tmp_path)

    def commit(msg):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q",
                        "--allow-empty", "-m", msg], cwd=tmp_path, check=True)  # fmt: skip

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for i in range(6):
        commit(f"change {i}")
    assert not requires_dco(tmp_path)
    for i in range(20):
        commit(f"signed {i}\n\nSigned-off-by: A <a@x>")
    assert requires_dco(tmp_path)  # history shows the repo enforces DCO


def test_pr_title_keeps_one_conventional_prefix_and_cuts_at_a_word():
    from token_tithe.executor import pr_title

    t = "feat: add cookies_file option for NetEase provider (workaround for yt-dlp DPAPI issue)"
    assert pr_title("fix", t, 263) == (
        "feat: add cookies_file option for NetEase provider (workaround for… (#263)"
    )
    assert pr_title("fix", "Parser crashes on empty input", 12) == (
        "fix: Parser crashes on empty input (#12)"
    )
    assert pr_title("fix", "Bug: crash on start", 5) == "fix: crash on start (#5)"
    assert pr_title("docs", "[Docs]: fix typo", 6) == "docs: fix typo (#6)"
    assert pr_title("fix", "[Bug]: Band renderer loses precision", 229) == (
        "fix: Band renderer loses precision (#229)"
    )
    assert pr_title("fix", "[BUG] crash", 1) == "fix: crash (#1)"


def test_agent_command_naming_a_deleted_scratch_file_is_ignored(tmp_path):
    from token_tithe.toolchain import Profile, merge_agent_commands

    (tmp_path / "real_test.go").write_text("package x\n")
    prof = Profile()
    rejected = merge_agent_commands(
        prof,
        [["go", "test", "-run", "TestParse", "./zz_scratch_test.go"],
         ["go", "test", "./real_test.go"], ["go", "test", "./..."], ["rm", "-rf", "/"]],
        tmp_path,
    )  # fmt: skip
    assert prof.test == [["go", "test", "./real_test.go"], ["go", "test", "./..."]]
    assert any("zz_scratch_test.go" in r and "not in the change" in r for r in rejected)
    assert "rm -rf /" in rejected


class ConfigOnly(ReferenceAdapter):
    """Changes only a YAML config file, like a dependabot.yml fix."""

    def invoke(self, worktree, brief, runner, timeout):
        from token_tithe.agents.base import AgentResult
        from token_tithe.agents.control import _write_summary

        (worktree / ".github").mkdir(exist_ok=True)
        (worktree / ".github" / "dependabot.yml").write_text("version: 2\nupdates: []\n")
        _write_summary(worktree, refused=False, what_changed="config", root_cause="config",
                       files_touched=[".github/dependabot.yml"], tests_written=[],
                       test_commands=[], confidence=90, confidence_reasoning="config only")  # fmt: skip
        return AgentResult(0)


def test_config_only_change_is_not_penalized_for_missing_tests():
    api = GitHubAPI.fixture()
    out = make(api, P.load_policy(), adapter=ConfigOnly(FX))[0].run_task(triaged(api, MATH))
    assert out.outcome == "queued_for_review", out.reason
    body = (paths.REVIEW / out.proposal_id / "body.md").read_text()
    assert "Configuration-only change" in body and "No test file was added" not in body


CAPI_TEMPLATE = """<!-- Thank you for contributing! -->

<!-- STEPS TO FOLLOW:
	1.  Add an icon to the title of this PR: ⚠️ (:warning:, breaking), ✨ (:sparkles:, feature),
	🐛 (:bug:, bugfixes), 📖 (:book:, docs), or 🌱 (:seedling:, minor or other)
-->

**What this PR does / why we need it**:

<!-- Which issue(s) this PR fixes, in `fixes #<issue number>` format -->
Fixes #

**Checklist:**

- [ ] Documentation has been updated, if necessary.
- [ ] Unit tests have been added, if necessary.
"""


def test_fill_template_and_title_marker():
    from token_tithe.pr_conventions import fill_template, title_marker

    body = fill_template(CAPI_TEMPLATE, "Ignore kustomize on release branches.", 3834)
    assert "<!--" not in body and "Fixes #3834" in body
    what = body.index("**What this PR does / why we need it**:")
    assert what < body.index("Ignore kustomize") < body.index("**Checklist:**")
    assert "- [ ] Unit tests have been added" in body  # left for the maintainer
    assert "Fixes #3834\n\n**Checklist:**" in body
    assert title_marker(CAPI_TEMPLATE, "issue_fix", config_only=False) == "🐛"
    assert title_marker(CAPI_TEMPLATE, "issue_fix", config_only=True) == "🌱"
    assert title_marker(CAPI_TEMPLATE, "docs", config_only=True) == "🌱"
    assert title_marker("## Description\n", "issue_fix", config_only=False) is None
    plain = fill_template("## Summary\n\n## Testing\n", "Did X.", 7)
    assert plain.index("Did X.") < plain.index("## Testing") and "Fixes #7" in plain


def test_pr_follows_the_repos_template_and_keeps_disclosure():
    api = GitHubAPI.fixture()
    tpl = Writes({".github/PULL_REQUEST_TEMPLATE.md": CAPI_TEMPLATE}, FX)
    out = make(api, P.load_policy(), adapter=tpl)[0].run_task(triaged(api, MATH))
    assert out.outcome == "queued_for_review", out.reason
    p = json.loads((paths.REVIEW / out.proposal_id / "proposal.json").read_text())
    assert p["title"].startswith("🐛 ") and not p["title"].startswith("🐛 fix:")
    body = p["body"]
    assert body.startswith("**What this PR does / why we need it**:")
    assert "Fixes #12" in body and "## How it was verified" in body
    assert "Generated with" in body and "token-tithe engine" in body  # R2 disclosure kept


def test_pr_without_template_keeps_engine_body():
    api = GitHubAPI.fixture()
    out = make(api, P.load_policy())[0].run_task(triaged(api, MATH))
    p = json.loads((paths.REVIEW / out.proposal_id / "proposal.json").read_text())
    assert p["title"].startswith("fix: ") and "## What changed" in p["body"]


def test_handover_points_at_operator_notes_only_when_present():
    from token_tithe.handover import Handover, render_md

    h = Handover(run_id="r")
    assert "OPERATOR_NOTES" not in render_md(h)
    paths.OPERATOR_NOTES.write_text("# plan\n")
    assert "OPERATOR_NOTES.md" in render_md(h)


def test_review_runner_reinstalls_after_the_host_agent(monkeypatch):
    from token_tithe.sandbox import ReviewRunner

    assert ReviewRunner.agent_on_host is True
    calls = []
    ex = Executor.__new__(Executor)
    ex.runner = type("R", (), {"agent_on_host": True, "isolated": True})()
    monkeypatch.setattr(Executor, "_install", lambda self, *a: calls.append("install"))
    # _verify reinstalls right before the test loop; stop there by making the test run raise
    ex.runner.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tests ran"))
    from token_tithe.toolchain import Profile

    prof = Profile(test=[["npm", "test"]])
    monkeypatch.setattr("token_tithe.executor.diff_stat",
                        lambda w: type("D", (), {"files": ["a.ts", "a.test.ts"], "lines": 4})())  # fmt: skip
    monkeypatch.setattr("token_tithe.executor.diff_text", lambda w: "")
    monkeypatch.setattr("token_tithe.executor.merge_agent_commands", lambda *a: [])
    monkeypatch.setattr("token_tithe.executor.nested_go_modules", lambda *a: Profile())
    ex.policy = P.load_policy()
    ex._remaining = lambda d: 60
    dtype = type("T", (), {"max_diff_lines": 400, "scope_problems": lambda s, f: [],
                           "needs_tests": False, "name": "issue_fix"})()  # fmt: skip
    out = type("O", (), {})()
    with pytest.raises(RuntimeError, match="tests ran"):
        ex._verify(dtype, out, Path("."), prof, {}, type("S", (), {"test_commands": []})(), 0)
    assert calls == ["install"]


def test_heading_less_template_prose_is_not_copied_into_the_body():
    from token_tithe.pr_conventions import fill_template

    template = (
        "Describe the big picture of your changes here.\n\n"
        "Always follow the contribution guidelines.\n\n- [ ] Tests added\n"
    )
    body = fill_template(template, "Fix the thumbs direction.", 8238)
    assert "big picture" not in body and "contribution guidelines" not in body
    assert body.startswith("Fix the thumbs direction.") and "Fixes #8238" in body
    assert "- [ ] Tests added" in body  # a checklist still carries over


def test_repo_env_reaches_install_and_test_runs(monkeypatch):
    seen = []
    ex = Executor.__new__(Executor)
    ex.runner = type("R", (), {"isolated": True})()
    ex.runner.run = (
        lambda cmd, w, purpose, t, env=None: seen.append((purpose, env))
        or type("Res", (), {"ok": True, "timed_out": False, "output": ""})()
    )
    ex._remaining = lambda d: 60
    ex._repo_env = {"VELLO_CI_GPU_SUPPORT": "no"}
    from token_tithe.toolchain import Profile

    ex._install(Profile(install=[[["cargo", "fetch"]]]), Path("."), 0)
    ex._run_all([["cargo", "test"]], Path("."), 0)
    assert seen == [("install", {"VELLO_CI_GPU_SUPPORT": "no"}),
                    ("test", {"VELLO_CI_GPU_SUPPORT": "no"})]  # fmt: skip
    assert P.load_policy().sandbox.repo_env == {}

    seen.clear()
    ex._repo_extra_deps = ["numba"]
    ex._install(Profile(ecosystems=["python"], install=[[["python", "-m", "pip", "install", "."]]]),
                Path("."), 0)  # fmt: skip
    assert len(seen) == 3  # venv, the repo's install, then the extra deps last


def test_follow_up_pr_references_the_closed_pr_not_fixes():
    from types import SimpleNamespace

    from token_tithe.pr_conventions import fill_template, issue_ref

    plain = SimpleNamespace(number=12, url="u", follow_up_pr=None, follow_up_url="")
    assert issue_ref(plain) == "Fixes #12 (u)"
    c = SimpleNamespace(number=12, url="u", follow_up_pr=90, follow_up_url="https://x/c1")
    ref = issue_ref(c)
    assert ref == "Follow-up to #90 as requested in https://x/c1. Original issue: #12."
    body = fill_template("## What this PR does\n\nFixes #\n", "Adds pow.", 12, ref)
    assert "Fixes" not in body and ref in body and "Adds pow." in body


def test_env_broken_matches_missing_dependencies_only():
    from token_tithe.executor import ENV_BROKEN

    assert ENV_BROKEN.search("E   ModuleNotFoundError: No module named 'sympy'")
    assert ENV_BROKEN.search("ImportError while loading conftest '/w/tests/conftest.py'.")
    assert ENV_BROKEN.search("Error: Cannot find module 'vitest'")
    assert not ENV_BROKEN.search("FAILED tests/test_x.py::test_pow - AssertionError")
    assert ENV_BROKEN.search("note: collect2: fatal error: ld terminated with signal 9 [Killed]")
    assert ENV_BROKEN.search("process didn't exit successfully (signal: 9, SIGKILL: kill)")


def test_operator_guidance_is_read_per_repo(tmp_path, monkeypatch):
    from token_tithe import executor, paths

    monkeypatch.setattr(paths, "BRIEFS", tmp_path)
    assert executor.operator_guidance("o/r") == ""
    (tmp_path / "o__r.md").write_text("Keep the hot path unchanged.\n")
    assert "Keep the hot path unchanged." in executor.operator_guidance("o/r")
    assert executor.operator_guidance("o/other") == ""
    assert executor.operator_title("o/r") is None
    (tmp_path / "o__r.md").write_text("PR title: feat: add Algebra.pow\n\nGuidance.\n")
    assert executor.operator_title("o/r") == "feat: add Algebra.pow"


def test_env_broken_matches_missing_linter_module():
    from token_tithe.executor import ENV_BROKEN

    assert ENV_BROKEN.search("/work/.tithe-venv/bin/python: No module named ruff")


def test_env_broken_matches_unfetchable_rust_toolchain():
    from token_tithe.executor import ENV_BROKEN

    assert ENV_BROKEN.search(
        "error: could not download file from 'https://static.rust-lang.org/dist/channel-rust-1.95.0.toml.sha256'"
    )
