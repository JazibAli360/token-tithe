"""The adapter contract every agent implements (§6, AGENTS.md §4)."""

from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from token_tithe.budget import Usage
from token_tithe.sandbox import Runner

SCRATCH = ".tithe"  # per-worktree scratch dir: task brief, summary, prompts. Never committed.
SUMMARY_FILE = f"{SCRATCH}/summary.json"
BRIEF_FILE = f"{SCRATCH}/task_brief.md"


class SummaryError(ValueError):
    pass


@dataclass
class AgentResult:
    exit_code: int
    timed_out: bool = False
    output_tail: str = ""
    final_text: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None


@dataclass
class Summary:
    refused: bool = False
    refusal_reason: str = ""
    what_changed: str = ""
    root_cause: str = ""
    files_touched: list[str] = field(default_factory=list)
    tests_written: list[str] = field(default_factory=list)
    test_commands: list[list[str]] = field(default_factory=list)
    confidence: int = 0
    confidence_reasoning: str = ""
    error: str = ""  # set when the agent's summary was missing or malformed


def read_summary(worktree: Path) -> Summary:
    """Parse and validate .tithe/summary.json. A missing/malformed summary is not fatal:
    it yields confidence 0 and an `error`, which the confidence gate then blocks."""
    path = worktree / SUMMARY_FILE
    if not path.is_file():
        return Summary(error="agent wrote no .tithe/summary.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SummaryError("summary is not a JSON object")
        conf = data.get("confidence", 0)
        if isinstance(conf, bool) or not isinstance(conf, int | float) or not 0 <= conf <= 100:
            raise SummaryError("confidence must be a number 0..100")
        cmds = data.get("test_commands", [])
        if not isinstance(cmds, list) or not all(
            isinstance(c, list) and c and all(isinstance(x, str) for x in c) for c in cmds
        ):
            raise SummaryError("test_commands must be a list of argv lists")
        return Summary(
            refused=bool(data.get("refused", False)),
            refusal_reason=str(data.get("refusal_reason", "")),
            what_changed=str(data.get("what_changed", "")),
            root_cause=str(data.get("root_cause", "")),
            files_touched=[str(x) for x in data.get("files_touched", [])],
            tests_written=[str(x) for x in data.get("tests_written", [])],
            test_commands=cmds,
            confidence=int(conf),
            confidence_reasoning=str(data.get("confidence_reasoning", "")),
        )
    except (ValueError, SummaryError) as e:
        return Summary(error=f"malformed summary: {e}")


class Adapter(ABC):
    """One headless agent. Exactly one agent process per task (R13)."""

    name: str
    display_name: str
    model: str | None

    @abstractmethod
    def invoke(
        self, worktree: Path, task_brief_path: str, runner: Runner, timeout: float
    ) -> AgentResult:
        """Run the agent headless in `worktree`; `task_brief_path` is relative to it."""

    @abstractmethod
    def report_usage(self) -> Usage:
        """ACTUAL usage of the last invoke, from the agent's own accounting (R10)."""

    def write_summary(self, worktree: Path) -> Summary:
        """Structured account of what the agent did (read from .tithe/summary.json)."""
        return read_summary(worktree)

    def cleanup(self, worktree: Path) -> None:
        """Remove scratch files so the worktree holds only the real change."""
        shutil.rmtree(worktree / SCRATCH, ignore_errors=True)

    def ask(self, prompt: str, worktree: Path, runner: Runner, timeout: float) -> AgentResult:
        """Review-only mode (D5): send a prompt, return the final text. No edits expected."""
        raise NotImplementedError(f"{self.name} has no review mode")

    @property
    def accounted(self) -> bool:
        return True

    @property
    def capacity_aware(self) -> bool:
        return False
