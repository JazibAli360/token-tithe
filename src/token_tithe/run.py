"""`donate run` / `donate resume`: one donation run from capacity probe to handover (§4, A, B)."""

from __future__ import annotations

import json
import os
import uuid
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from token_tithe import capacity, paths
from token_tithe.agents import Adapter, get_adapter
from token_tithe.budget import BudgetExhausted, BudgetGuard, InsufficientCapacity
from token_tithe.discover import Candidate
from token_tithe.etiquette import Etiquette
from token_tithe.executor import Executor, TaskOutcome, require_budgetable_adapter
from token_tithe.fixtures import load_fixtures
from token_tithe.github_api import GitHubAPI, GitHubError, PrivateRepoError, RateLimited
from token_tithe.handover import HandoverSession, StopRequested
from token_tithe.handover import read as read_handover
from token_tithe.ledger import Ledger
from token_tithe.pipeline import Halted, build_queue, check_halt
from token_tithe.policy import for_fixtures, load_agents, load_policy, load_repos, route_agent
from token_tithe.publish import GitPusher, Pusher, RecordingPusher
from token_tithe.sandbox import LocalRunner, UnsandboxedWarning, select_runner
from token_tithe.second_review import get_reviewer
from token_tithe.triage import Triaged


@dataclass
class RunResult:
    run_id: str
    status: str
    reason: str = ""
    outcomes: list[TaskOutcome] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def donate_run(
    *, live: bool = False, fixtures: bool = False, plan_only: bool = False,
    agent: str | None = None, max_tasks: int | None = None,
    api: GitHubAPI | None = None, pusher: Pusher | None = None,
    reviewer: Adapter | None = None, echo: Callable[[str], None] = print,
    install_signals: bool = True, ledger: Ledger | None = None, only: list[str] | None = None,
    follow_ups: list[str] | None = None,
) -> RunResult:  # fmt: skip
    run_id = _new_run_id()
    try:
        check_halt()  # R12: before anything at all
    except Halted as e:
        with HandoverSession(run_id, live, install_signals=False) as s:
            s.finalize("halted", str(e))
        echo(str(e))
        return RunResult(run_id, "halted", str(e))

    policy, repos_cfg = load_policy(), load_repos()
    policy = for_fixtures(policy) if fixtures else policy
    ledger = ledger or Ledger()
    fx = {f.full_name.lower(): f for f in load_fixtures()} if fixtures else None
    api = api or (GitHubAPI.fixture(live=live) if fixtures else GitHubAPI.rest(live=live))
    pusher = pusher or (RecordingPusher() if fixtures else GitPusher())
    res = RunResult(run_id, "running")
    if not fixtures and not os.environ.get("GITHUB_TOKEN"):
        echo(f"WARNING: {_UNAUTHENTICATED_HINT}")

    with HandoverSession(run_id, live, install_signals=install_signals) as session:
        try:
            # ---- capacity (Addendum A)
            report = capacity.probe(policy, ledger, load_agents(), None if fixtures else api)
            echo(report.render())
            budget = BudgetGuard.from_policy(
                policy, real_quota=None if fixtures else _quota(report, policy)
            )
            try:
                budget.ensure_viable()
            except InsufficientCapacity as e:
                ledger.append("sizing", run_id=run_id, reason=str(e))
                session.finalize("insufficient_capacity", str(e))
                echo(str(e))
                res.status, res.reason = "insufficient_capacity", str(e)
                return res

            # ---- queue
            etiquette = Etiquette(api, cla_signed=repos_cfg.cla_signed,
                                  cache_dir=None if fixtures else paths.FEEDBACK_CACHE / "etiquette",
                                  claim_window_days=policy.discovery.claim_window_days)  # fmt: skip
            wanted = {k.lower() for k in only or ()}
            # an operator pick may retry an attempted issue; every other gate still applies
            attempted = {k for k in ledger.attempted() if k.lower() not in wanted}
            queue = build_queue(
                api, policy, repos_cfg, etiquette,
                recent_repos=ledger.recent_repos(policy.discovery.exclude_recent_repo_days),
                attempted=attempted, history=ledger.history(), picked=list(only or ()),
                follow_ups=list(follow_ups or ()),
            )  # fmt: skip
            # a follow-up names our PR; the task is its source issue
            wanted |= {c.key.lower() for c in queue.discovery.candidates if c.follow_up_pr}
            for s in queue.discovery.skipped:
                if s.reason.startswith("follow-up:"):
                    echo(f"--follow-up: {s.target} skipped: {s.reason}")
            if only or follow_ups:  # the operator picked these; every gate still applies to them
                queue.ranked = [t for t in queue.ranked if t.candidate.key.lower() in wanted]
                for k in sorted(wanted - {t.candidate.key.lower() for t in queue.ranked}):
                    echo(f"--only: {k} is not in the queue (rejected, skipped or not discovered)")
            tasks, sizing = capacity.select_tasks(
                queue.ranked, budget, report.estimates, max_tasks or policy.limits.max_tasks_per_run
            )
            for d in sizing:
                ledger.append("sizing", run_id=run_id, key=d.key, decision=d.decision,
                              donation_type=d.donation_type, estimate=d.estimate, reason=d.reason)  # fmt: skip

            res.plan = [f"{t.candidate.key} [{t.score}] {t.donation_type} → "
                        f"{agent or _default_agent(policy, t, fixtures)}" for t in tasks]  # fmt: skip
            if plan_only:
                echo("plan (nothing executed):" + "".join(f"\n  - {line}" for line in res.plan))
                session.finalize("completed", "plan only")
                res.status = "planned"
                return res

            # ---- sandbox (R16)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", UnsandboxedWarning)
                runner = LocalRunner() if fixtures else select_runner(policy, live)
            for w in caught:
                ledger.append("warning", run_id=run_id, reason=str(w.message))
                echo(f"WARNING: {w.message}")

            window_review = report.window.get("force_review", False)
            lessons = lessons_provider(policy)
            for t in tasks:
                adapter = _adapter(agent or _default_agent(policy, t, fixtures), fx)
                rev = reviewer if reviewer is not None else get_reviewer(policy, adapter)
                force = window_review
                try:
                    require_budgetable_adapter(adapter, live)
                except Exception:  # noqa: BLE001 - capacity-blind/unaccounted → review only
                    force = True
                ex = Executor(api=api, policy=policy, ledger=ledger, adapter=adapter, runner=runner,
                              etiquette=etiquette, pusher=pusher, session=session, budget=budget,
                              run_id=run_id, live=live, reviewer=rev, fixtures=fx,
                              force_review=force, lessons=lessons)  # fmt: skip
                o = ex.run_task(t)
                res.outcomes.append(o)
                echo(_line(o))
                if o.outcome == "timeout":  # stop here: its worktree and snapshot wait for resume
                    break
            if res.outcomes and res.outcomes[-1].outcome == "timeout":
                session.finalize("timeout", f"{res.outcomes[-1].key} timed out; `donate resume`")
                res.status = "timeout"
            else:
                session.finalize("completed")
                res.status = "completed"
        except BudgetExhausted as e:
            session.finalize("budget_exhausted", str(e))
            res.status, res.reason = "budget_exhausted", str(e)
        except StopRequested as e:
            session.finalize("stopped", str(e))
            res.status, res.reason = "stopped", str(e)
        except Halted as e:
            session.finalize("halted", str(e))
            res.status, res.reason = "halted", str(e)
        except PrivateRepoError as e:
            session.finalize("aborted", f"R17: {e}")
            res.status, res.reason = "aborted", str(e)
        except RateLimited as e:
            reason = (
                str(e)
                if fixtures or os.environ.get("GITHUB_TOKEN")
                else f"{e} — {_UNAUTHENTICATED_HINT}"
            )
            session.finalize("rate_limited", reason)
            res.status, res.reason = "rate_limited", reason
        except GitHubError as e:
            session.finalize("github_error", str(e))
            res.status, res.reason = "github_error", str(e)
        echo(f"run {run_id}: {res.status}" + (f" — {res.reason}" if res.reason else ""))
    return res


_UNAUTHENTICATED_HINT = (
    "GITHUB_TOKEN is unset: GitHub allows 60 unauthenticated requests/hour, too few for a "
    "run. export GITHUB_TOKEN=$(gh auth token)"
)


def lessons_provider(policy):
    """Addendum E3: inject relevant approved lessons into every brief and review prompt."""
    from token_tithe.brain import Brain

    if not policy.brain.auto_inject:
        return lambda t: "None recorded yet."
    brain = Brain.load(max_lines=policy.brain.max_lines)

    def provide(t: Triaged) -> str:
        c = t.candidate
        tags = {c.language or "", t.donation_type, c.repo, "general"}
        return brain.relevant_text(tags, policy.brain.max_injected)

    return provide


def _quota(report: capacity.CapacityReport, policy) -> int | None:
    for a in report.agents:
        if a.name == policy.routing.default:
            return a.quota_tokens
    return None


def _default_agent(policy, t: Triaged, fixtures: bool) -> str:
    return "reference" if fixtures else route_agent(policy, t.candidate.language, t.donation_type)


def _adapter(name: str, fx) -> Adapter:
    if name in ("reference", "null"):
        return get_adapter(name, fixtures=fx or {})
    return get_adapter(name)


def _line(o: TaskOutcome) -> str:
    conf = f" conf {o.confidence}" if o.confidence is not None else ""
    where = f" → {o.url or o.proposal_id}" if (o.url or o.proposal_id) else ""
    why = f" ({o.reason})" if o.reason else ""
    return f"  {o.outcome:<18} {o.key} [{o.donation_type}]{conf}{where}{why}"


def resume_run(*, live: bool = False, fixtures: bool = False, api: GitHubAPI | None = None,
               pusher: Pusher | None = None, echo: Callable[[str], None] = print) -> RunResult:  # fmt: skip
    """Clear HALT (R12) and continue the handover's task from its recorded next step (B3)."""
    paths.HALT_FLAG.unlink(missing_ok=True)
    h = read_handover()
    if h is None:
        echo("no handover found (state/ was reset?) — starting a fresh run instead of guessing")
        return donate_run(live=live, fixtures=fixtures, api=api, pusher=pusher, echo=echo)
    if not h.resumable:
        echo(f"last run {h.run_id} ended '{h.status}' with nothing in flight — nothing to resume")
        return RunResult(h.run_id, "nothing_to_resume")

    t = h.task
    echo(f"resuming {t.key} at step '{t.next_step}' (run {h.run_id})")
    policy, repos_cfg, ledger = load_policy(), load_repos(), Ledger()
    policy = for_fixtures(policy) if fixtures else policy
    api = api or (GitHubAPI.fixture(live=live) if fixtures else GitHubAPI.rest(live=live))
    pusher = pusher or (RecordingPusher() if fixtures else GitPusher())
    etiquette = Etiquette(api, cla_signed=repos_cfg.cla_signed, cache_dir=None)

    if t.next_step == "publish" and t.proposal_id:
        from token_tithe.review import open_proposal

        pub = open_proposal(t.proposal_id, live=live, api=api, policy=policy, ledger=ledger,
                            etiquette=etiquette, pusher=pusher, fixture_mode=fixtures)  # fmt: skip
        echo(f"published: {pub.url}")
        with HandoverSession(h.run_id, live, install_signals=False) as s:
            s.finalize("completed", f"resumed publish of {t.key}")
        return RunResult(h.run_id, "completed")

    worktree = paths.ROOT / t.worktree
    snap_file = Executor.snapshot_path(worktree)
    if not worktree.is_dir() or not snap_file.is_file():
        echo(f"worktree {t.worktree} is missing or incomplete — starting a fresh run")
        return donate_run(live=live, fixtures=fixtures, api=api, pusher=pusher, echo=echo)
    snap = json.loads(snap_file.read_text(encoding="utf-8"))
    tri = snap["triaged"]
    cand = Candidate(**tri["candidate"])
    triaged = Triaged(cand, tri["score"], tri["donation_type"], tri["components"],
                      tri["rationale"], tri["etiquette"])  # fmt: skip
    fx = {f.full_name.lower(): f for f in load_fixtures()} if fixtures else None
    adapter = _adapter(
        "reference" if fixtures else route_agent(policy, cand.language, triaged.donation_type), fx
    )
    resume_snap = (
        snap if "summary" in snap and t.next_step in ("verify", "review", "decide") else None
    )
    with HandoverSession(h.run_id, live) as session:
        ex = Executor(api=api, policy=policy, ledger=ledger, adapter=adapter,
                      runner=LocalRunner() if fixtures else select_runner(policy, live),
                      etiquette=etiquette, pusher=pusher, session=session,
                      budget=BudgetGuard.from_policy(policy), run_id=h.run_id, live=live,
                      reviewer=get_reviewer(policy, adapter), fixtures=fx,
                      work_root=Path(worktree).parent.parent, force_review=True)  # fmt: skip
        o = ex.run_task(triaged, resume=resume_snap)
        echo(_line(o))
        session.finalize("completed", f"resumed {t.key}")
    return RunResult(h.run_id, "completed", outcomes=[o])
