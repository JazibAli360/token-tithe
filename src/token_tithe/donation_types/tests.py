"""tests — add tests for an untested module. No behavior change (§5.2)."""

from token_tithe.donation_types import TEST_GLOBS, DonationType

TYPE = DonationType(
    name="tests",
    output="pr",
    title_prefix="test",
    needs_tests=True,
    allowed_globs=TEST_GLOBS,
    hard_scope=True,  # touching non-test files means behavior change: abandon
    instructions="""### Instructions: add tests

1. Add tests for the module named in the issue, following the existing test layout and style.
2. Cover normal input, edge cases and error paths. Every test must be able to fail: it should
   assert real behavior, not just that functions exist.
3. Change ONLY test files. Do not modify the code under test, even if you find a bug — note
   the bug in your summary's `confidence_reasoning` instead.""",
)
