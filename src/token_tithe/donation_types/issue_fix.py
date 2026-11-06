"""issue_fix — the core donation: fix one scoped issue, with tests (§5.1)."""

from token_tithe.donation_types import DonationType

TYPE = DonationType(
    name="issue_fix",
    output="pr",
    title_prefix="fix",
    needs_tests=True,
    instructions="""### Instructions: fix the issue

1. Reproduce the problem first: write a test that fails because of the bug.
2. Make the smallest correct change that makes it pass. Fix the root cause, not the symptom.
3. Keep every existing test passing. Do not delete, skip or weaken existing tests.
4. If the issue is ambiguous, too large, or needs a design decision, refuse instead of guessing.""",
)
