"""`donate simulate`: the pipeline against local fixture repos — no network, no GitHub (§7).

Every stage talks to GitHub through `GitHubAPI.fixture()`, the same client the live engine
uses, so simulate exercises the real guard code (R5 write gate, R17 public-only). Stages
that are not built yet are listed as such, never faked. Any GitHub write during a simulate
run is a failure.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from token_tithe import paths
from token_tithe.etiquette import Etiquette
from token_tithe.fixtures import Fixture, load_fixtures, materialize, missing_toolchains, run_cmd
from token_tithe.github_api import GitHubAPI, PrivateRepoError
from token_tithe.pipeline import build_queue, gate
from token_tithe.policy import for_fixtures, load_policy, load_repos

# Stages the full pipeline will have, with the phase that delivers each one.
PIPELINE = [
    ("world", 3),
    ("github (fixture mode)", 3),
    ("baseline", 3),
    ("discover → etiquette → triage", 4),
    ("clone gate (fresh etiquette + secrets scan)", 4),
    ("capacity → execute → review queue", 5),
    ("ledger + feedback + reports", 6),
    ("learn → apply → inject", 6),
]


@dataclass
class StageResult:
    name: str
    status: str  # PASS | FAIL | PENDING
    lines: list[str] = field(default_factory=list)


@dataclass
class SimReport:
    run_id: str
    stages: list[StageResult]
    run_dir: Path

    @property
    def ok(self) -> bool:
        return all(s.status != "FAIL" for s in self.stages)

    def render(self) -> str:
        out = [f"simulate run {self.run_id}", ""]
        for s in self.stages:
            mark = {"PASS": "✓", "FAIL": "✗", "PENDING": "·"}[s.status]
            out.append(f" {mark} {s.status:<7} {s.name}")
            out += [f"           {line}" for line in s.lines]
        out += ["", f" simulate: {'PASS' if self.ok else 'FAIL'}"]
        return "\n".join(out)


def _stage_world(fixtures: list[Fixture], run_dir: Path) -> tuple[StageResult, dict[str, Path]]:
    st = StageResult("world", "PASS")
    repos = {}
    for f in fixtures:
        repos[f.name] = materialize(f, run_dir / "repos" / f.name)
    st.lines.append(f"{len(repos)} fixture repos materialized as git repos")
    return st, repos


def _stage_github(api: GitHubAPI, fixtures: list[Fixture]) -> StageResult:
    st = StageResult("github (fixture mode)", "PASS")
    found = api.search_issues('is:open no:assignee archived:false label:"good first issue"')
    st.lines.append(f"search: {len(found)} open unassigned 'good first issue' issues")
    for f in fixtures:
        try:
            repo = api.get_repo(f.full_name)
            lic = (repo.get("license") or {}).get("spdx_id")
            st.lines.append(f"{f.full_name}: ★{repo['stargazers_count']} {lic} public")
        except PrivateRepoError as e:
            st.status = "FAIL"
            st.lines.append(f"R17: {e}")
    return st


def _stage_baseline(fixtures: list[Fixture], repos: dict[str, Path]) -> StageResult:
    """Each fixture behaves as its rubric claims before anyone touches it."""
    st = StageResult("baseline", "PASS")
    for f in fixtures:
        rb = f.rubric
        missing = missing_toolchains(rb.requires)
        if missing:
            st.lines.append(f"{f.name}: skipped (not installed: {', '.join(missing)})")
            continue
        if not rb.checks.repo_tests:
            st.lines.append(f"{f.name}: expect={rb.expect} ({rb.reject_reason or 'no tests'})")
            continue
        r = run_cmd(rb.checks.repo_tests, repos[f.name])
        actual = "pass" if r.ok else "fail"
        ok = actual == rb.baseline.repo_tests
        if not ok:
            st.status = "FAIL"
        st.lines.append(
            f"{f.name}: repo tests {actual} (expected {rb.baseline.repo_tests})"
            + ("" if ok else "  ← fixture is broken")
        )
    return st


def _stage_queue(api: GitHubAPI, fixtures: list[Fixture], repos: dict[str, Path]):
    st = StageResult("discover → etiquette → triage", "PASS")
    et = Etiquette(api, cache_dir=None)
    res = build_queue(api, for_fixtures(load_policy()), load_repos(), etiquette=et)
    st.lines.append(
        f"{len(res.discovery.candidates)} discovered, {len(res.rejected)} rejected by etiquette, "
        f"{len(res.ranked)} ranked"
    )
    for sk in res.rejected:
        st.lines.append(f"rejected {sk.target}: {sk.reason[:110]}")
    for i, t in enumerate(res.ranked, 1):
        st.lines.append(f"#{i} [{t.score}] {t.candidate.key} ({t.donation_type})")

    gate_st = StageResult("clone gate (fresh etiquette + secrets scan)", "PASS")
    by_repo = {f.full_name.lower(): f for f in fixtures}
    outcome: dict[str, str] = {}
    for sk in res.rejected:
        outcome[sk.target.split("#")[0].lower()] = "rejected (etiquette)"
    for t in res.ranked:
        f = by_repo[t.candidate.repo.lower()]
        reason = gate(et, t.candidate.repo, t.candidate.number, repos[f.name])
        outcome[f.full_name.lower()] = f"rejected (gate: {reason})" if reason else "cleared"
    for f in fixtures:
        got = outcome.get(f.full_name.lower(), "not discovered")
        want_reject = f.rubric.expect == "reject"
        ok = got.startswith("rejected") if want_reject else got == "cleared"
        if not ok:
            gate_st.status = "FAIL"
        gate_st.lines.append(f"{f.name}: {got[:100]} (expect {f.rubric.expect})"
                             + ("" if ok else "  ← WRONG"))  # fmt: skip
    return st, gate_st


def _stage_execute(fixtures: list[Fixture], run_dir: Path, api: GitHubAPI) -> StageResult:
    """The real `donate run` path with the reference control agent, in an isolated state/."""
    from token_tithe import handover
    from token_tithe.ledger import Ledger
    from token_tithe.publish import RecordingPusher
    from token_tithe.review import load as load_queue
    from token_tithe.run import donate_run

    st = StageResult("capacity → execute → review queue", "PASS")
    lines: list[str] = []
    pusher = RecordingPusher()
    with paths.use_state(run_dir / "state"):
        res = donate_run(fixtures=True, api=api, pusher=pusher, max_tasks=len(fixtures),
                         echo=lambda _m: None, install_signals=False)  # fmt: skip
        queued = {p.repo.lower() for p in load_queue()}
        h = handover.read()
        ledger_lines = sum(1 for _ in Ledger().entries())
    expect = {
        f.full_name.lower() for f in fixtures
        if f.rubric.expect == "fix" and not missing_toolchains(f.rubric.requires)
    }  # fmt: skip
    for o in res.outcomes:
        lines.append(f"{o.outcome:<18} {o.key} ({o.donation_type}) {o.reason[:80]}")
    missing = sorted(expect - queued)
    if res.status != "completed" or missing or pusher.pushes:
        st.status = "FAIL"
    lines.append(f"run status: {res.status}; {len(queued)} proposals in the review queue; "
                 f"{ledger_lines} ledger lines; handover: {h.status if h else 'MISSING'}")  # fmt: skip
    if missing:
        lines.append(f"expected in the queue but missing: {missing}  ← WRONG")
    if h is None or h.status != "completed":
        st.status = "FAIL"
    st.lines = lines
    return st


def _stage_feedback_learn(run_dir: Path, api: GitHubAPI) -> list[StageResult]:
    """Feedback, report and journal over the simulated ledger; then the learning loop on a
    COPY of the Brain (simulate never touches the committed brain/BRAIN.md)."""
    from token_tithe import reports
    from token_tithe.brain import Brain
    from token_tithe.feedback import check_feedback
    from token_tithe.learn import apply, learn
    from token_tithe.ledger import Ledger

    fb = StageResult("ledger + feedback + reports", "PASS")
    with paths.use_state(run_dir / "state"):
        led = Ledger()
        results = check_feedback(api, led, for_fixtures(load_policy()))
        rep = reports.report(led)
        jour = reports.journal(led)
        entries = list(led.entries())
        fb.lines.append(f"{len(entries)} ledger lines, all schema_version 1: "
                        f"{all(e.get('schema_version') == 1 for e in entries)}")  # fmt: skip
        fb.lines.append(f"feedback: {len(results)} PR(s) tracked (review mode opened none)")
        fb.lines.append(
            f"report: {len(rep.splitlines())} lines; journal: {len(jour.splitlines())} lines"
        )
        if led.problems or not all(e.get("schema_version") == 1 for e in entries):
            fb.status = "FAIL"

        ln = StageResult("learn → apply → inject", "PASS")
        before = paths.BRAIN.read_text(encoding="utf-8")
        copy = run_dir / "brain" / "BRAIN.md"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(before, encoding="utf-8")
        brain = Brain.load(copy)
        res = learn(led, brain)
        promoted = apply(brain)
        brain.save()
        tags = {"tithe-fixtures/leaked-secret", "secrets", "general"}
        injected = brain.relevant_text(tags, 6)
        ln.lines.append(f"{res['candidates']} candidates → {len(res['added'])} pending → "
                        f"{len(promoted)} promoted in the simulate copy")  # fmt: skip
        hit = any(lid in injected for _, lid in promoted)
        ln.lines.append(
            "a promoted lesson is injected into the next brief: " + ("yes" if hit else "NO")
        )
        ln.lines.append("committed brain/BRAIN.md untouched: "
                        + ("yes" if paths.BRAIN.read_text(encoding="utf-8") == before else "NO"))  # fmt: skip
        if not promoted or not hit or paths.BRAIN.read_text(encoding="utf-8") != before:
            ln.status = "FAIL"
    return [fb, ln]


def simulate(keep: bool = False, out_root: Path | None = None) -> SimReport:
    out_root = out_root or paths.STATE / "sim"
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    fixtures = load_fixtures()
    api = GitHubAPI.fixture(live=False)
    stages: list[StageResult] = []

    world, repos = _stage_world(fixtures, run_dir)
    stages += [world, _stage_github(api, fixtures), _stage_baseline(fixtures, repos)]
    stages += list(_stage_queue(api, fixtures, repos))
    stages.append(_stage_execute(fixtures, run_dir, api))
    stages += _stage_feedback_learn(run_dir, api)

    built = {s.name for s in stages}
    for name, phase in PIPELINE:
        if name not in built:
            stages.append(StageResult(name, "PENDING", [f"not built yet (Phase {phase})"]))

    if api.writes:
        stages.append(StageResult("no GitHub writes", "FAIL", [str(w) for w in api.writes]))
    else:
        stages.append(StageResult("no GitHub writes", "PASS", ["0 writes"]))

    report = SimReport(run_id, stages, run_dir)
    (run_dir / "report.txt").write_text(report.render() + "\n", encoding="utf-8")
    if not keep:
        shutil.rmtree(run_dir / "repos", ignore_errors=True)
    return report
