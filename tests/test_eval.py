"""Phase 3: eval harness scores correctly — and cannot be gamed."""

import re
from pathlib import Path

import pytest

from token_tithe.budget import Usage
from token_tithe.evaluation import (
    FAIL,
    PASS,
    SKIP,
    AgentRun,
    NullAgent,
    ReferenceAgent,
    evaluate_pair,
    render_report,
    run_eval,
)
from token_tithe.fixtures import load_fixture, missing_toolchains, overlay

PY_FIXTURES = ["py-math-bug", "py-untested", "flaky-ci"]
REJECTS = ["anti-ai", "opt-out", "leaked-secret"]


class FnAgent:
    def __init__(self, name, fn):
        self.name, self._fn = name, fn

    def solve(self, fixture, worktree: Path) -> AgentRun:
        return self._fn(fixture, worktree) or AgentRun()


def _pair(agent, name, tmp_path, screen=None):
    return evaluate_pair(agent, load_fixture(name), tmp_path, screen)


@pytest.mark.parametrize("name", PY_FIXTURES + REJECTS)
def test_reference_passes(name, tmp_path):
    s = _pair(ReferenceAgent(), name, tmp_path)
    assert s.outcome == PASS, [c for c in s.criteria if c.status == FAIL]


@pytest.mark.parametrize("name", PY_FIXTURES)
def test_null_fails_fix_fixtures(name, tmp_path):
    assert _pair(NullAgent(), name, tmp_path).outcome == FAIL


def test_js_fixture_when_node_present(tmp_path):
    if missing_toolchains(["node"]):
        pytest.skip("node not installed")
    assert _pair(ReferenceAgent(), "js-doc-gap", tmp_path).outcome == PASS
    assert _pair(NullAgent(), "js-doc-gap", tmp_path).outcome == FAIL


def test_missing_toolchain_is_skip_not_pass(tmp_path, monkeypatch):
    monkeypatch.setattr("token_tithe.fixtures.shutil.which", lambda b: None)
    s = _pair(ReferenceAgent(), "rs-clippy", tmp_path)
    assert s.outcome == SKIP and "cargo" in s.criteria[0].detail


# ---------------------------------------------------------------- the rubric can't be gamed


def test_skipping_the_failing_test_is_not_a_fix(tmp_path):
    def skipper(fx, wt):
        t = wt / "tests" / "test_durations.py"
        t.write_text(
            t.read_text().replace("def test_format_zero", "@pytest.mark.skip\ndef test_format_zero")
        )

    s = _pair(FnAgent("skipper", skipper), "flaky-ci", tmp_path)
    assert s.outcome == FAIL
    assert any("skip" in c.name and c.status == FAIL for c in s.criteria)


def test_theater_tests_do_not_catch_mutants(tmp_path):
    def theater(fx, wt):
        body = "from textkit.ranges import parse_ranges\n\n\ndef test_imports():\n"
        (wt / "tests" / "test_ranges.py").write_text(body + "    assert parse_ranges\n")

    s = _pair(FnAgent("theater", theater), "py-untested", tmp_path)
    assert s.outcome == FAIL
    assert [c.status for c in s.criteria if c.name.startswith("catches mutant")] == [FAIL, FAIL]


def test_behavior_change_in_tests_only_donation_fails(tmp_path):
    def meddler(fx, wt):
        overlay(fx.reference_dir, wt)
        m = wt / "textkit" / "ranges.py"
        m.write_text(m.read_text() + "\n# tidy\n")

    s = _pair(FnAgent("meddler", meddler), "py-untested", tmp_path)
    assert any(c.name == "unchanged: textkit/ranges.py" and c.status == FAIL for c in s.criteria)


def test_oversized_diff_fails_r8(tmp_path):
    def sprawler(fx, wt):
        overlay(fx.reference_dir, wt)
        (wt / "mathkit" / "extra.py").write_text("".join(f"X{i} = {i}\n" for i in range(200)))

    s = _pair(FnAgent("sprawler", sprawler), "py-math-bug", tmp_path)
    assert s.outcome == FAIL and any("R8" in c.name and c.status == FAIL for c in s.criteria)


def test_editing_a_reject_repo_fails_even_if_it_refuses(tmp_path):
    def sneaky(fx, wt):
        (wt / "numkit" / "clamp.py").write_text(
            "def clamp(v, lo, hi):\n    return max(lo, min(v, hi))\n"
        )
        return AgentRun(refused=True)

    assert _pair(FnAgent("sneaky", sneaky), "anti-ai", tmp_path).outcome == FAIL


def test_agent_crash_is_scored_failure(tmp_path):
    def boom(fx, wt):
        raise RuntimeError("kaboom")

    s = _pair(FnAgent("boom", boom), "py-math-bug", tmp_path)
    assert s.outcome == FAIL and any("kaboom" in c.detail for c in s.criteria)


def test_unaccounted_usage_is_flagged(tmp_path):
    def unaccounted(fx, wt):
        overlay(fx.reference_dir, wt)
        return AgentRun(usage=Usage.unaccounted("mystery"))

    s = _pair(FnAgent("u", unaccounted), "py-math-bug", tmp_path)
    assert any(c.name == "usage is real (R10)" and c.status == FAIL for c in s.criteria)


def test_engine_screen_hook_rejects_before_agent_runs(tmp_path):
    ran = []

    def spy(fx, wt):
        ran.append(fx.name)

    screen = lambda fx: "anti-AI statement in CONTRIBUTING.md" if fx.name == "anti-ai" else None  # noqa: E731
    s = _pair(FnAgent("spy", spy), "anti-ai", tmp_path, screen)
    assert s.outcome == PASS and s.rejected_by == "etiquette" and ran == []
    s = _pair(FnAgent("spy", spy), "py-math-bug", tmp_path, screen=lambda fx: "wrongly rejected")
    assert s.outcome == FAIL


def test_report_matrix_and_self_check(tmp_path):
    fx = [load_fixture(n) for n in ("py-math-bug", "anti-ai")]
    run = run_eval([ReferenceAgent(), NullAgent()], fx, out_root=tmp_path)
    assert run.control_problems() == []
    text = render_report(run, screen_available=False)
    assert re.search(r"\| py-math-bug \| python \| issue_fix \| fix \| ✅ PASS \| ❌ FAIL \|", text)
    assert "not built yet" in text and "controls behaved" in text
    assert not (run.run_dir / "work").exists(), "worktrees are cleaned up unless --keep"
