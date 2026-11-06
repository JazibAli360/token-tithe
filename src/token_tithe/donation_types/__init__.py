"""Donation types (§5): each has its own brief instructions, output kind and scope rules."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from token_tithe.policy import DONATION_TYPES

DOC_GLOBS = ("*.md", "*.rst", "*.txt", "*.adoc", "docs/*", "doc/*", "examples/*", "*.ipynb")
TEST_GLOBS = (
    "tests/*", "test/*", "*/tests/*", "*/test/*", "spec/*", "__tests__/*", "*_test.*",
    "*.test.*", "*.spec.*", "test_*.py", "*/test_*.py", "*_test.go", "testing/*",
)  # fmt: skip


def matches(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


@dataclass(frozen=True)
class DonationType:
    name: str
    output: str  # "pr" | "comment"
    title_prefix: str
    instructions: str
    max_diff_lines: int = 400
    needs_tests: bool = False
    allowed_globs: tuple[str, ...] | None = None  # None = any file
    off_scope_penalty: int = 0  # confidence penalty when files fall outside allowed_globs
    hard_scope: bool = False  # off-scope files abandon the task instead of penalizing
    notes: list[str] = field(default_factory=list)

    def scope_problems(self, files: list[str]) -> list[str]:
        if self.allowed_globs is None:
            return []
        return [f for f in files if not matches(f, self.allowed_globs)]


def get_type(name: str) -> DonationType:
    from token_tithe.donation_types import ci_repro, docs, issue_fix, tests, triage_report

    registry = {
        "issue_fix": issue_fix.TYPE, "tests": tests.TYPE, "docs": docs.TYPE,
        "ci_repro": ci_repro.TYPE, "triage_report": triage_report.TYPE,
    }  # fmt: skip
    assert set(registry) == set(DONATION_TYPES)
    return registry[name]
