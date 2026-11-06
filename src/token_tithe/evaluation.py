"""Deterministic agent evaluation over fixture repos (§7).

An eval run = every agent × every fixture. For each pair the harness materializes the
fixture into a fresh git worktree, lets the agent work on it, then scores the result
against the fixture's rubric. Hidden acceptance files and mutants are only ever applied to
a *scoring copy*, never to the agent's worktree.

Built-in control agents keep the harness honest:
- `reference` applies the known-good solution → must PASS every runnable fixture.
- `null` changes nothing → must FAIL every `fix` fixture (else the rubric is too lax).
Real adapters (Phase 5) plug in through the same `EvalAgent` interface.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from token_tithe import paths
from token_tithe.budget import Usage
from token_tithe.fixtures import (
    Fixture,
    diff_stat,
    load_fixtures,
    materialize,
    missing_toolchains,
    overlay,
    run_cmd,
)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


# --------------------------------------------------------------------------- agents


@dataclass
class AgentRun:
    refused: bool = False
    summary: dict = field(default_factory=dict)
    usage: Usage | None = None
    error: str | None = None


class EvalAgent(Protocol):
    name: str

    def solve(self, fixture: Fixture, worktree: Path) -> AgentRun: ...


class ReferenceAgent:
    """Control: applies eval/expected/<fixture>/reference/, refuses reject fixtures."""

    name = "reference"

    def solve(self, fixture: Fixture, worktree: Path) -> AgentRun:
        if fixture.rubric.expect == "reject":
            return AgentRun(refused=True, summary={"refused": fixture.rubric.reject_reason})
        files = overlay(fixture.reference_dir, worktree)
        return AgentRun(summary={"files_touched": files, "confidence": 100})


class NullAgent:
    """Control: does nothing at all."""

    name = "null"

    def solve(self, fixture: Fixture, worktree: Path) -> AgentRun:
        return AgentRun(summary={"files_touched": []})


CONTROL_AGENTS: dict[str, Callable[[], EvalAgent]] = {
    "reference": ReferenceAgent,
    "null": NullAgent,
}

# Phase 4 sets this to etiquette's verdict function: (fixture) -> reason | None.
# Until then the engine-level gate is reported as not available, never faked.
ScreenFn = Callable[[Fixture], str | None]


# --------------------------------------------------------------------------- scoring


@dataclass
class Criterion:
    name: str
    status: str
    detail: str = ""


@dataclass
class Score:
    agent: str
    fixture: str
    language: str
    donation_type: str
    expect: str
    outcome: str  # PASS | FAIL | SKIP
    criteria: list[Criterion]
    rejected_by: str | None = None
    diff_lines: int = 0
    usage: Usage | None = None

    @property
    def failed(self) -> list[Criterion]:
        return [c for c in self.criteria if c.status == FAIL]


def _scoring_copy(worktree: Path, scratch: Path, name: str) -> Path:
    dest = scratch / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(worktree, dest, ignore=shutil.ignore_patterns(".git", "target"))
    return dest


def score(fixture: Fixture, worktree: Path, run: AgentRun, scratch: Path, agent: str) -> Score:
    """Score an agent's finished worktree against the fixture rubric."""
    rb, ck = fixture.rubric, fixture.rubric.checks
    crit: list[Criterion] = []
    diff = diff_stat(worktree)

    def add(name: str, ok: bool, detail: str = "") -> None:
        crit.append(Criterion(name, PASS if ok else FAIL, detail))

    if rb.expect == "reject":
        add("no changes made", not diff.files, ", ".join(diff.files[:5]))
        add("refused", run.refused, str(run.summary.get("refused", "")))
    else:
        if ck.repo_tests:
            r = run_cmd(ck.repo_tests, worktree)
            add("repo tests pass (R7)", r.ok, _tail(r.output))
        for i, cmd in enumerate(ck.lint):
            r = run_cmd(cmd, worktree)
            add(f"lint[{i}] passes (R7)", r.ok, _tail(r.output))
        if ck.acceptance:
            copy = _scoring_copy(worktree, scratch, "acceptance")
            overlay(fixture.acceptance_dir, copy)
            for i, cmd in enumerate(ck.acceptance):
                r = run_cmd(cmd, copy)
                add(f"acceptance[{i}] (hidden)", r.ok, _tail(r.output))
        if ck.tests_added:
            hits = [f for f in diff.files if any(fnmatch.fnmatch(f, g) for g in ck.tests_added)]
            add("tests added/updated", bool(hits), ", ".join(hits))
        for m in ck.mutants:
            copy = _scoring_copy(worktree, scratch, "mutant")
            shutil.copy2(fixture.expected / m.replacement, copy / m.target)
            r = run_cmd(ck.repo_tests or [], copy)
            add(f"catches mutant {Path(m.replacement).name}", not r.ok, "tests must fail")
        for f in ck.unchanged:
            add(f"unchanged: {f}", f not in diff.files)
        for rule in ck.must_contain:
            text = _read(worktree / rule.file)
            add(f"contains {rule.pattern!r} in {rule.file}", rule.pattern in text)
        for rule in ck.must_not_contain:
            text = _read(worktree / rule.file)
            add(f"no {rule.pattern!r} in {rule.file}", not re.search(re.escape(rule.pattern), text))
        add(
            f"non-empty diff ≤ {ck.max_diff_lines} lines (R8)",
            0 < diff.lines <= ck.max_diff_lines,
            f"{diff.lines} lines",
        )
        add("did not refuse", not run.refused)

    if run.usage is not None:
        add("usage is real (R10)", run.usage.accounted, run.usage.model or "")
    if run.error:
        add("agent ran without error", False, run.error)

    outcome = PASS if all(c.status != FAIL for c in crit) else FAIL
    return Score(
        agent, fixture.name, rb.language, rb.donation_type, rb.expect, outcome, crit,
        rejected_by="agent" if run.refused else None, diff_lines=diff.lines, usage=run.usage,
    )  # fmt: skip


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _tail(text: str, n: int = 300) -> str:
    return text.strip().replace("\n", " ⏎ ")[-n:]


# --------------------------------------------------------------------------- running


@dataclass
class EvalRun:
    run_id: str
    started: str
    scores: list[Score]
    run_dir: Path

    def control_problems(self) -> list[str]:
        """Harness self-check: reference must pass, null must fail every fix fixture."""
        problems = []
        for s in self.scores:
            if s.outcome == SKIP:
                continue
            if s.agent == "reference" and s.outcome != PASS:
                problems.append(f"reference FAILED {s.fixture}: fixture or rubric is broken")
            if s.agent == "null" and s.expect == "fix" and s.outcome == PASS:
                problems.append(f"null PASSED {s.fixture}: rubric is too lax")
        return problems


def evaluate_pair(
    agent: EvalAgent, fixture: Fixture, run_dir: Path, screen: ScreenFn | None = None
) -> Score:
    rb = fixture.rubric
    base = Score(agent.name, fixture.name, rb.language, rb.donation_type, rb.expect, SKIP, [])
    missing = missing_toolchains(rb.requires)
    if missing:
        base.criteria.append(Criterion("toolchain", SKIP, f"not installed: {', '.join(missing)}"))
        return base

    pair_dir = run_dir / "work" / f"{agent.name}__{fixture.name}"
    worktree = materialize(fixture, pair_dir / "worktree")
    scratch = pair_dir / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)

    if screen is not None:
        reason = screen(fixture)
        if reason is not None:
            ok = rb.expect == "reject"
            c = Criterion("rejected by etiquette", PASS if ok else FAIL, reason)
            outcome = PASS if ok else FAIL
            return Score(
                agent.name, fixture.name, rb.language, rb.donation_type, rb.expect, outcome, [c],
                rejected_by="etiquette",
            )  # fmt: skip

    try:
        run = agent.solve(fixture, worktree)
    except Exception as e:  # noqa: BLE001 - an agent crash is a scored failure
        run = AgentRun(error=f"{type(e).__name__}: {e}")
    return score(fixture, worktree, run, scratch, agent.name)


def run_eval(
    agents: list[EvalAgent],
    fixtures: list[Fixture] | None = None,
    screen: ScreenFn | None = None,
    keep_work: bool = False,
    out_root: Path = paths.EVAL_RUNS,
) -> EvalRun:
    fixtures = fixtures if fixtures is not None else load_fixtures()
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = started.replace(":", "").replace("-", "") + "-" + uuid.uuid4().hex[:6]
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    scores = [evaluate_pair(a, f, run_dir, screen) for a in agents for f in fixtures]
    if not keep_work:
        shutil.rmtree(run_dir / "work", ignore_errors=True)
    return EvalRun(run_id, started, scores, run_dir)


# --------------------------------------------------------------------------- report


def render_report(run: EvalRun, screen_available: bool) -> str:
    agents = list(dict.fromkeys(s.agent for s in run.scores))
    fixtures = list(dict.fromkeys(s.fixture for s in run.scores))
    by = {(s.agent, s.fixture): s for s in run.scores}
    icon = {PASS: "✅ PASS", FAIL: "❌ FAIL", SKIP: "⏭ SKIP"}

    lines = [
        f"# Eval report — {run.run_id}",
        "",
        f"Started {run.started}. Agents: {', '.join(agents)}. Fixtures: {len(fixtures)}.",
        "",
        "Engine etiquette gate: "
        + ("active." if screen_available else "**not built yet** (Phase 4) — reject fixtures "
           "are scored on agent refusal only."),
        "",
        "## Matrix (agent × fixture)",
        "",
        "| fixture | language | type | expect | " + " | ".join(agents) + " |",
        "|---|---|---|---|" + "---|" * len(agents),
    ]  # fmt: skip
    for f in fixtures:
        s0 = by[(agents[0], f)]
        cells = [icon[by[(a, f)].outcome] for a in agents]
        lines.append(
            f"| {f} | {s0.language} | {s0.donation_type} | {s0.expect} | "
            + " | ".join(cells)
            + " |"
        )

    lines += ["", "## By agent × language", "", "| agent | language | pass | fail | skip |",
              "|---|---|---|---|---|"]  # fmt: skip
    for a in agents:
        for lang in sorted({s.language for s in run.scores}):
            ss = [s for s in run.scores if s.agent == a and s.language == lang]
            n = {k: sum(s.outcome == k for s in ss) for k in (PASS, FAIL, SKIP)}
            lines.append(f"| {a} | {lang} | {n[PASS]} | {n[FAIL]} | {n[SKIP]} |")

    lines += ["", "## Details", ""]
    for s in run.scores:
        lines.append(f"### {s.agent} · {s.fixture} — {s.outcome}")
        if s.rejected_by:
            lines.append(f"rejected by: {s.rejected_by}")
        if s.usage is not None:
            u = s.usage
            lines.append(f"usage: model={u.model} in={u.input_tokens} out={u.output_tokens} "
                         f"accounted={u.accounted}")  # fmt: skip
        for c in s.criteria:
            detail = f" — {c.detail}" if c.detail and c.status != PASS else ""
            lines.append(f"- {c.status} {c.name}{detail}")
        lines.append("")

    problems = run.control_problems()
    lines += ["## Harness self-check", ""]
    lines += [f"- ❌ {p}" for p in problems] or [
        "- ✅ controls behaved (reference passes, null fails)"
    ]
    return "\n".join(lines) + "\n"


def write_report(run: EvalRun, screen_available: bool, out_root: Path = paths.EVAL_RUNS) -> Path:
    text = render_report(run, screen_available)
    (run.run_dir / "report.md").write_text(text, encoding="utf-8")
    latest = out_root / "report.md"
    latest.write_text(text, encoding="utf-8")
    return latest


# --------------------------------------------------------------------------- real adapters


class AdapterAgent:
    """Runs a real adapter (claude, codex, …) on a fixture with the real task brief."""

    def __init__(self, adapter, timeout: float = 900):
        self.adapter = adapter
        self.name = adapter.name if adapter.model is None else f"{adapter.name}:{adapter.model}"
        self.timeout = timeout

    def solve(self, fixture: Fixture, worktree: Path) -> AgentRun:
        from token_tithe.agents.base import BRIEF_FILE, SCRATCH
        from token_tithe.donation_types import get_type
        from token_tithe.sandbox import LocalRunner
        from token_tithe.templates import render

        rb, issue = fixture.rubric, fixture.issue
        dtype = get_type(rb.donation_type)
        comments = "\n".join(f"--- comment by @{c.get('user')}: {c.get('body', '')}"
                             for c in issue.get("comments", []))  # fmt: skip
        brief = render(
            "task_brief.md", repo=fixture.full_name, issue_number=issue["number"],
            issue_title=issue["title"], issue_url=f"https://github.com/{fixture.full_name}/issues/{issue['number']}",
            donation_type=dtype.name, type_instructions=dtype.instructions,
            max_diff_lines=rb.checks.max_diff_lines, conventions="(eval fixture)",
            lessons="None recorded yet.",
            issue_content=f"Title: {issue['title']}\n\n{issue.get('body', '')}\n{comments}",
        )  # fmt: skip
        (worktree / SCRATCH).mkdir(exist_ok=True)
        (worktree / BRIEF_FILE).write_text(brief, encoding="utf-8")
        result = self.adapter.invoke(worktree, BRIEF_FILE, LocalRunner(), self.timeout)
        summary = self.adapter.write_summary(worktree)
        usage = self.adapter.report_usage()
        self.adapter.cleanup(worktree)
        return AgentRun(
            refused=summary.refused,
            summary={"confidence": summary.confidence, "error": summary.error},
            usage=usage,
            error=None if result.ok else (result.error or "agent failed"),
        )


def record_passes(run: EvalRun, out_root: Path = paths.EVAL_RUNS) -> Path:
    """Persist which agent:model passed which donation types (gates model fallback, A3)."""
    import json

    f = out_root / "passes.json"
    data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    by_pair: dict[tuple[str, str], list[str]] = {}
    for s in run.scores:
        if s.agent in CONTROL_AGENTS or s.outcome == SKIP:
            continue
        by_pair.setdefault((s.agent, s.donation_type), []).append(s.outcome)
    for (agent, dtype), outcomes in by_pair.items():
        passed = all(o == PASS for o in outcomes)
        data.setdefault(agent, {})[dtype] = {"passed": passed, "run": run.run_id}
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return f


def eval_passed(
    agent: str, model: str | None, donation_type: str, out_root: Path | None = None
) -> bool:
    import json

    f = (out_root or paths.EVAL_RUNS) / "passes.json"
    if not f.is_file():
        return False
    key = agent if model is None else f"{agent}:{model}"
    entry = json.loads(f.read_text(encoding="utf-8")).get(key, {}).get(donation_type, {})
    return bool(entry.get("passed"))
