"""Control adapters for simulate and eval: deterministic, zero tokens, no network.

- `reference` applies the fixture's known-good solution and refuses reject fixtures.
- `null` changes nothing.
They implement the real adapter contract, so simulate exercises the real executor path.
"""

from __future__ import annotations

import json
from pathlib import Path

from token_tithe.agents.base import SCRATCH, SUMMARY_FILE, Adapter, AgentResult
from token_tithe.budget import Usage
from token_tithe.fixtures import Fixture, overlay
from token_tithe.sandbox import Runner


def _write_summary(worktree: Path, **data) -> None:
    (worktree / SCRATCH).mkdir(exist_ok=True)
    (worktree / SUMMARY_FILE).write_text(json.dumps(data), encoding="utf-8")


class _Control(Adapter):
    """Zero tokens, so usage is real (0) and capacity is known (unlimited)."""

    @property
    def capacity_aware(self) -> bool:
        return True


class ReferenceAdapter(_Control):
    name = "reference"
    display_name = "reference (control)"
    model = "control-reference"

    def __init__(self, fixtures: dict[str, Fixture] | None = None, verdict_score: int = 85):
        self.fixtures = {k.lower(): v for k, v in (fixtures or {}).items()}  # by full_name
        self.verdict_score = verdict_score
        self.repo: str | None = None  # set by the executor before invoke

    def _fixture(self) -> Fixture:
        if not self.repo or self.repo.lower() not in self.fixtures:
            raise RuntimeError("reference adapter only works on fixture repos")
        return self.fixtures[self.repo.lower()]

    def invoke(self, worktree, task_brief_path, runner: Runner, timeout) -> AgentResult:
        f = self._fixture()
        if f.rubric.expect == "reject":
            _write_summary(worktree, refused=True, refusal_reason=f.rubric.reject_reason or "")
            return AgentResult(0)
        files = overlay(f.reference_dir, worktree)
        tests = [x for x in files if "test" in x]
        _write_summary(
            worktree, refused=False, what_changed=f"reference solution for #{f.rubric.issue}",
            root_cause="see reference", files_touched=files, tests_written=tests,
            test_commands=[], confidence=90, confidence_reasoning="known-good reference",
        )  # fmt: skip
        return AgentResult(0)

    def report_usage(self) -> Usage:
        return Usage(model=self.model, input_tokens=0, output_tokens=0, cost_estimate=0.0)

    def ask(self, prompt, worktree, runner, timeout) -> AgentResult:
        verdict = {"fixes_issue": True, "minimal": True, "unrequested_changes": [],
                   "tests_real": True, "risks": [], "score": self.verdict_score,
                   "verdict": "approve", "notes": "control reviewer"}  # fmt: skip
        return AgentResult(0, final_text=json.dumps(verdict))


class NullAdapter(_Control):
    name = "null"
    display_name = "null (control)"
    model = "control-null"

    def __init__(self, **_):
        self.repo = None

    def invoke(self, worktree, task_brief_path, runner, timeout) -> AgentResult:
        return AgentResult(0)

    def report_usage(self) -> Usage:
        return Usage(model=self.model, input_tokens=0, output_tokens=0, cost_estimate=0.0)


CONTROL_ADAPTERS = {"reference": ReferenceAdapter, "null": NullAdapter}
