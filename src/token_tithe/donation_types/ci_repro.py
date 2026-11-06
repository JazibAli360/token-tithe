"""ci_repro — reproduce a failing/flaky CI run, diagnose, fix (§5.4).
If the diagnosis is uncertain the executor downgrades it to a triage_report."""

from token_tithe.donation_types import DonationType

TYPE = DonationType(
    name="ci_repro",
    output="pr",
    title_prefix="fix(ci)",
    instructions="""### Instructions: failing CI

1. Reproduce the failure locally with the repository's own test command.
2. Diagnose the root cause. Fix the code or the test so the intended behavior holds.
3. Never "fix" CI by skipping, xfail-ing, deleting or loosening a test, or by pinning around
   the problem, unless a maintainer explicitly asked for exactly that in the issue.
4. If you cannot reproduce it or are unsure of the cause, set a low `confidence` and explain
   your findings in `confidence_reasoning` — the engine will post an investigation report
   instead of a PR.""",
)
