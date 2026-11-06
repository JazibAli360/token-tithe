"""Cross-model second review (D5): a DIFFERENT model judges the diff before anything opens.

The reviewer runs after the task agent has finished — sequentially, never concurrently, so
R13's one-agent-process-at-a-time holds. Its verdict feeds the confidence score (R11). No
reviewer available → the work is held in the review queue.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from token_tithe.agents import Adapter, get_adapter
from token_tithe.budget import Usage
from token_tithe.policy import Policy
from token_tithe.sandbox import Runner
from token_tithe.templates import render_prompt

MAX_DIFF_CHARS = 60_000


@dataclass
class ReviewVerdict:
    reviewer: str
    model: str | None
    score: int
    verdict: str  # approve | reject
    fixes_issue: bool = False
    minimal: bool = False
    tests_real: bool = False
    unrequested_changes: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    notes: str = ""
    usage: Usage | None = None

    @property
    def approved(self) -> bool:
        return self.verdict == "approve"

    def label(self) -> str:
        return f"{self.reviewer}/{self.model or 'default'}: {self.verdict}, score {self.score}"


def get_reviewer(policy: Policy, author: Adapter) -> Adapter | None:
    """The configured reviewer, or None. A reviewer identical to the author is not a review."""
    cfg = policy.second_review
    if not cfg.agent:
        return None
    reviewer = get_adapter(cfg.agent, model=cfg.model or None)
    if reviewer.name == author.name and reviewer.model == author.model:
        return None
    return reviewer


def parse_verdict(text: str, reviewer: Adapter) -> ReviewVerdict:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return ReviewVerdict(reviewer.name, reviewer.model, 0, "reject",
                             notes="reviewer answer was not parseable JSON")  # fmt: skip
    score = data.get("score", 0)
    score = int(score) if isinstance(score, int | float) and 0 <= score <= 100 else 0
    verdict = "approve" if data.get("verdict") == "approve" else "reject"
    return ReviewVerdict(
        reviewer.name, reviewer.model, score, verdict,
        fixes_issue=bool(data.get("fixes_issue")), minimal=bool(data.get("minimal")),
        tests_real=bool(data.get("tests_real")),
        unrequested_changes=[str(x) for x in data.get("unrequested_changes", [])][:10],
        risks=[str(x) for x in data.get("risks", [])][:10],
        notes=str(data.get("notes", ""))[:500],
    )  # fmt: skip


def review(
    reviewer: Adapter, worktree: Path, runner: Runner, timeout: float, *,
    issue_content: str, donation_type: str, diff: str, test_results: str, lessons: str,
) -> ReviewVerdict:  # fmt: skip
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n… (diff truncated)"
    prompt = render_prompt(
        "second_review.md", issue_content=issue_content, donation_type=donation_type,
        diff=diff, test_results=test_results, lessons=lessons,
    )  # fmt: skip
    result = reviewer.ask(prompt, worktree, runner, timeout)
    verdict = parse_verdict(result.final_text, reviewer)
    verdict.usage = reviewer.report_usage()
    if not result.ok and not result.final_text:
        verdict.verdict, verdict.score = "reject", 0
        verdict.notes = f"reviewer failed: {result.error}"
    return verdict


def combine_confidence(agent_conf: int, verdict: ReviewVerdict | None, weight: float) -> int:
    """R11 input. A reject verdict caps confidence at 40 regardless of the agent's own view."""
    if verdict is None:
        return agent_conf
    score = round((1 - weight) * agent_conf + weight * verdict.score)
    return min(score, 40) if not verdict.approved else score
