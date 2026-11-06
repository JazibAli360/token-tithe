"""triage_report — investigate and post ONE structured comment; not a PR (§5.5, R4)."""

from token_tithe.donation_types import DonationType

REPORT_FILE = ".tithe/report.md"

TYPE = DonationType(
    name="triage_report",
    output="comment",
    title_prefix="",
    max_diff_lines=0,
    instructions=f"""### Instructions: investigation report (no code changes)

1. Investigate the issue: reproduce it, find the root cause in the code, and identify the
   smallest plausible fix direction. You may add temporary files, but delete them afterwards:
   the repository must be left unchanged.
2. Write the report to `{REPORT_FILE}` in Markdown with these sections:
   **Reproduction** (exact steps/commands and observed output), **Root cause** (file and line
   references), **Suggested fix** (direction, not a full patch), **Open questions**.
3. Be concise: a maintainer should get value from it in two minutes.""",
)
