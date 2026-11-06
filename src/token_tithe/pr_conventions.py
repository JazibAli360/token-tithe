"""Follow the target repo's own PR conventions (R9): its PR template and title markers.

The engine's own body (templates/pr_body.md) is the fallback. When the clone has a PR
template, our description goes under the template's first description heading, its
"Fixes #" line gets the issue number, its checklist stays for the maintainer, and our
verification notes and the AI disclosure (R2) always follow at the end.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE_PATHS = (
    ".github/PULL_REQUEST_TEMPLATE.md", ".github/pull_request_template.md",
    "PULL_REQUEST_TEMPLATE.md", "pull_request_template.md",
    "docs/PULL_REQUEST_TEMPLATE.md", "docs/pull_request_template.md",
)  # fmt: skip
TEMPLATE_DIRS = (
    ".github/PULL_REQUEST_TEMPLATE",
    "PULL_REQUEST_TEMPLATE",
    "docs/PULL_REQUEST_TEMPLATE",
)

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEADING = re.compile(r"^(?:#{1,6}\s+.+|\*\*[^*\n]+\*\*:?)\s*$", re.M)
_DESCRIPTIVE = re.compile(r"what|descri|summary|why|change|purpose|motivation|context", re.I)
_FIXES = re.compile(
    r"^(\s*[-*]?\s*(?:fix(?:es)?|close[sd]?|resolve[sd]?)\s*:?\s*)#\s*(?:<[^>\n]*>|\d+)?\s*$",
    re.I | re.M,
)
# cluster-api style: the template lists an emoji per change kind
_EMOJI_STYLE = re.compile(r":seedling:|🌱", re.I)
EMOJI = {"issue_fix": "🐛", "docs": "📖", "tests": "🌱", "ci_repro": "🌱", "config": "🌱"}


def find_template(worktree: Path) -> str | None:
    for rel in TEMPLATE_PATHS:
        if (f := worktree / rel).is_file():
            return f.read_text(encoding="utf-8", errors="replace")
    for rel in TEMPLATE_DIRS:
        if (d := worktree / rel).is_dir() and (files := sorted(d.glob("*.md"))):
            return files[0].read_text(encoding="utf-8", errors="replace")
    return None


def title_marker(template: str | None, donation_type: str, config_only: bool) -> str | None:
    """The emoji the repo's template asks PR titles to start with, or None."""
    if not template or not _EMOJI_STYLE.search(template):
        return None
    return EMOJI["config" if config_only else donation_type] if donation_type in EMOJI else None


def issue_ref(c) -> str:
    """The line that ties a PR to its issue (R2). A follow-up says what it follows up and does
    not claim to fix the (already closed) source issue."""
    if c.follow_up_pr is None:
        return f"Fixes #{c.number} ({c.url})"
    asked = f" as requested in {c.follow_up_url}" if c.follow_up_url else ""
    return f"Follow-up to #{c.follow_up_pr}{asked}. Original issue: #{c.number}."


def fill_template(
    template: str, description: str, issue_number: int, follow_up_ref: str | None = None
) -> str:
    """Our description in the repo's template: comments stripped, Fixes line filled. A
    follow-up drops the template's Fixes line and states its own reference instead."""
    text = _COMMENT.sub("", template).replace("\r\n", "\n")
    if follow_up_ref:
        text, n = _FIXES.sub("", text), 0
    else:
        text, n = _FIXES.subn(lambda m: m.group(1) + f"#{issue_number}", text)
    ref = follow_up_ref or f"Fixes #{issue_number}"
    heads = list(_HEADING.finditer(text))
    target = next(
        (h for h in heads if _DESCRIPTIVE.search(h.group(0))), heads[0] if heads else None
    )
    block = description.strip() + ("" if n else f"\n\n{ref}")
    if target is None:
        # no heading to fill: the template is instructions for the author (nolimits4web/swiper),
        # so only its checklist, if any, carries over
        checklist = [ln for ln in text.splitlines() if re.match(r"\s*[-*] \[[ xX]\]", ln)]
        text = block + ("\n\n" + "\n".join(checklist) if checklist else "")
    else:
        text = text[: target.end()] + "\n\n" + block + "\n" + text[target.end() :]
    text = re.sub(
        r"(?m)^(?=(?:#{1,6}\s+.+|\*\*[^*\n]+\*\*:?)\s*$)", "\n", text
    )  # blank before heads
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
