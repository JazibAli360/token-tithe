"""Score candidate issues 0–100 and emit a ranked queue with rationales (§4).

Components (max points):
    clarity          25  specific title, substantive body, expected/actual, maintainer confirmed
    reproducibility  15  code blocks, error output, explicit repro steps
    size             20  small and bounded scores high; refactors/RFCs/debates score low
    test coverage    10  the repo has a test suite we can run (docs donations: full marks)
    responsiveness   20  from etiquette (external merges, push recency, maintainer engagement)
    history         ±10  our past donation outcomes in this repo (feedback.py, Phase 6)
    favorite          5  repos.yaml favorites
    impact           20  repo topic in policy discovery.impact_topics (10 if only its
                         description mentions one): projects that help the world, creative OSS
    staleness       -15  issue age: -5 past 6 months, -10 past a year, -15 past two
    feature_request -14  new-feature asks (-8), more if long-debated (-6); fixes are preferred.
                         Labels, title, or a wish in a body with no error output
    agent_fit       -30  needs things an agent cannot do honestly: hosted services or
                         accounts, real devices/hardware, screenshots/video, visual design,
                         gated datasets (MIMIC, PhysioNet), work only core maintainers can do
Etiquette rejects never reach the queue; deprioritized repos keep their (low) score.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from token_tithe.discover import Candidate
from token_tithe.etiquette import Verdict
from token_tithe.policy import Policy, donation_type_allowed

TEST_DIRS = ("tests", "test", "spec", "__tests__", "testing", "src/test")

_LARGE = re.compile(
    r"\b(?:refactor|rewrite|redesign|overhaul|architecture|RFC|proposal|breaking change|"
    r"epic|roadmap|tracking issue|meta issue|migrate|migration|discussion|design doc)\b",
    re.I,
)
_SMALL = re.compile(
    r"\b(?:typo|off[- ]by[- ]one|docstring|readme|missing test|add tests?|broken link|"
    r"error message|one[- ]line|small|simple|minor)\b",
    re.I,
)
_ERROR = re.compile(
    r"Traceback|Error:|Exception|AssertionError|FAILED|panicked at|error\[E\d+\]|\bstack trace\b",
    re.I,
)
_REPRO = re.compile(r"steps to reproduce|to reproduce|repro(?:duction)?:|reproduces", re.I)
_EXPECTED = re.compile(
    r"\bexpected\b|\bactual(?:ly)?\b|\bshould (?:return|be)\b|\binstead of\b", re.I
)
_FEATURE_LABELS = {"enhancement", "feature", "feature request", "new feature", "feature-request"}
_FEATURE_TITLE = re.compile(
    r"^\W*(?:feat(?:ure)?\b|\[feat(?:ure)?)|\bfeature request\b|\badd support for\b|"
    r"\b(?:implement|introduce) (?:a |an )?new\b|\bnew (?:command|option|feature)\b",
    re.I,
)
_NOT_FOR_AGENTS = re.compile(
    r"\b(?:codespaces|gitpod|devpod|replit|stackblitz|figma|sign[- ]?up|create an account|"
    r"real device|physical device|on (?:a |an )?(?:iphone|ipad|android (?:phone|device))|"
    r"(?:on|requires?|needs?) (?:real |specific |physical )?hardware|"
    # not bare "screenshots"/"hardware": bug templates ask for those in every report
    r"(?:take|update|replace|retake) (?:the |new )?screenshots|screen recording|"
    r"record(?:ing)? a video|video tutorial|survey|user research|"
    r"(?:design|create) (?:a |the )?(?:logo|mockup|banner|illustration)|manual(?:ly)? test(?:ing)?|"
    # gated data and maintainer-only work: no agent holds the access or the authority
    r"mimic-?(?:iii|iv|3|4)|eicu|physionet|credentialed access|data use agreement|"
    r"(?:requires?|needs?) (?:one of )?the (?:core )?(?:devs?|developers|maintainers|team)|"
    r"only (?:they|the maintainers|core devs?) can|openssf|ossf|bestpractices\.dev)\b",
    re.I,
)
# Asks phrased in the body ("any plans to include X?", "would be great to cover this feature");
# only counted when the report has no error output, so bugs that end with a wish stay fixes.
_FEATURE_BODY = re.compile(
    r"\bany plans? (?:to|for|of)\b|\bplans? to (?:add|include|support|implement)\b|"
    r"\b(?:would be|it'?d be) (?:great|nice|useful|cool) (?:to|if)\b|\bthis feature\b|"
    r"\bfeature request\b",
    re.I,
)
_VAGUE = re.compile(r"^(?:bug|issue|problem|help|question|improvement|enhancement)s?\W*$", re.I)


@dataclass
class Triaged:
    candidate: Candidate
    score: int
    donation_type: str
    components: dict[str, int]
    rationale: str
    etiquette: str  # ok | deprioritize

    def to_dict(self) -> dict:
        return {**asdict(self), "key": self.candidate.key}


def infer_donation_type(c: Candidate) -> str:
    labels = {lb.lower() for lb in c.labels}
    title = c.title.lower()
    if labels & {"documentation", "docs"} or re.search(r"\b(?:readme|docs?|document)\b", title):
        return "docs"
    if re.search(r"\badd (?:unit )?tests?\b|\btest coverage\b|\buntested\b", title) or (
        labels & {"tests", "testing"} and "test" in title
    ):
        return "tests"
    if labels & {"ci"} or re.search(r"\bci\b|failing (?:test|build)|flaky", title):
        return "ci_repro"
    return "issue_fix"


def _clarity(c: Candidate, maintainer_engaged: bool | None) -> tuple[int, list[str]]:
    pts, why = 0, []
    if not _VAGUE.match(c.title.strip()) and len(c.title) >= 15:
        pts += 6
        why.append("specific title")
    n = len(c.body)
    if 80 <= n <= 4000:
        pts += 7
        why.append("substantive body")
    elif n > 4000:
        pts += 3
        why.append("very long body")
    if _EXPECTED.search(c.body):
        pts += 6
        why.append("states expected vs actual")
    if maintainer_engaged:
        pts += 6
        why.append("maintainer confirmed")
    return min(pts, 25), why


def _repro(c: Candidate) -> tuple[int, list[str]]:
    pts, why = 0, []
    if "```" in c.body or re.search(r"`[^`]+\(", c.body):
        pts += 6
        why.append("code in report")
    if _ERROR.search(c.body):
        pts += 5
        why.append("error output")
    if _REPRO.search(c.body):
        pts += 4
        why.append("repro steps")
    return min(pts, 15), why


def _size(c: Candidate) -> tuple[int, str]:
    text = f"{c.title}\n{c.body}"
    labels = {lb.lower() for lb in c.labels}
    pts = 12
    if "good first issue" in labels:
        pts += 4
    if _SMALL.search(text):
        pts += 4
    if _LARGE.search(text):
        pts -= 12
    if c.comments > 8:
        pts -= 6
    pts = max(0, min(pts, 20))
    est = "small" if pts >= 15 else "medium" if pts >= 8 else "large"
    return pts, est


def triage(
    candidates: list[Candidate],
    verdicts: Mapping[str, Verdict],
    policy: Policy,
    test_dirs: Mapping[str, bool] | None = None,
    history: Mapping[str, tuple[int, int]] | None = None,
    now: datetime | None = None,
) -> list[Triaged]:
    """`verdicts` keyed by candidate key (owner/repo#N); rejected ones are dropped.
    `test_dirs[repo]` says whether the repo has a test suite. `history[repo]` =
    (merged, closed_unmerged) counts of our past PRs there."""
    test_dirs = test_dirs or {}
    history = history or {}
    now = now or datetime.now(UTC)
    out = []
    for c in candidates:
        v = verdicts.get(c.key)
        if v is None or v.rejected:
            continue
        dtype = infer_donation_type(c)
        if not donation_type_allowed(policy, dtype, c.language):
            continue
        engaged = v.signals.get("maintainer_engaged")
        clarity, cwhy = _clarity(c, engaged)
        repro, rwhy = _repro(c)
        if dtype in ("docs", "tests"):
            repro = max(repro, 10)  # nothing to reproduce; don't punish
        size, est = _size(c)
        has_tests = test_dirs.get(c.repo)
        coverage = 10 if dtype == "docs" or has_tests else 4 if has_tests is None else 0
        resp = round(v.responsiveness * 20)
        merged, closed = history.get(c.repo, (0, 0))
        hist = max(-10, min(10, 4 * merged - 5 * closed))
        fav = 5 if c.favorite else 0
        impact, impact_why = _impact(c, policy.discovery.impact_topics)
        stale, stale_why = _staleness(c, now)
        feature, feature_why = (0, "") if dtype in ("docs", "tests") else _feature_request(c)
        fit, fit_why = _agent_fit(c)
        comps = {"clarity": clarity, "reproducibility": repro, "size": size,
                 "test_coverage": coverage, "responsiveness": resp, "history": hist,
                 "favorite": fav, "impact": impact, "staleness": stale,
                 "feature_request": feature, "agent_fit": fit}  # fmt: skip
        score = max(0, min(100, sum(comps.values())))
        # the size guess is keyword-based ("proposal" in a detailed bug report reads as large);
        # an operator pick was sized by hand, and R8's 400-line limit still applies at verify
        if dtype == "issue_fix" and est == "large" and not c.picked:
            dtype = "triage_report"  # investigate and report rather than a big PR (§5)

        parts = [f"{dtype.replace('_', ' ')} in {c.repo} ({c.language or '?'}, ★{c.stars})"]
        parts.append(f"estimated {est}")
        if cwhy or rwhy:
            parts.append("; ".join(cwhy + rwhy))
        parts.append(
            f"maintainer responsiveness {v.responsiveness:.2f}"
            + (" — deprioritized" if v.status == "deprioritize" else "")
        )
        parts.append(
            "test suite present" if has_tests else "no test suite found" if has_tests is False
            else "test suite unknown"
        )  # fmt: skip
        if merged or closed:
            parts.append(f"our history here: {merged} merged, {closed} closed")
        if fav:
            parts.append("favorite repo")
        parts += [w for w in (impact_why, stale_why, feature_why, fit_why) if w]
        rationale = f"Score {score}/100: " + ". ".join(parts) + "."
        out.append(Triaged(c, score, dtype, comps, rationale, v.status))

    return sorted(out, key=lambda t: (t.etiquette == "deprioritize", -t.score, t.candidate.key))


def _staleness(c: Candidate, now: datetime) -> tuple[int, str]:
    try:
        created = datetime.strptime(c.created_at[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return 0, ""
    days = (now - created).days
    for limit, pts in ((730, -15), (365, -10), (182, -5)):
        if days > limit:
            return pts, f"stale: opened {days // 30} months ago"
    return 0, ""


def _feature_request(c: Candidate) -> tuple[int, str]:
    labels = {lb.lower() for lb in c.labels}
    body_ask = _FEATURE_BODY.search(c.body) and not _ERROR.search(c.body)
    if not (labels & _FEATURE_LABELS or _FEATURE_TITLE.search(c.title) or body_ask):
        return 0, ""
    if c.comments >= 6:
        return -14, f"feature request with a long discussion ({c.comments} comments)"
    return -8, "feature request"


def _agent_fit(c: Candidate) -> tuple[int, str]:
    if m := _NOT_FOR_AGENTS.search(f"{c.title}\n{c.body}"):
        return -30, f"needs something an agent cannot do ({m.group(0).lower()})"
    return 0, ""


def _impact(c: Candidate, topics: list[str]) -> tuple[int, str]:
    wanted = {t.lower() for t in topics}
    if hit := sorted(wanted & {t.lower() for t in c.topics}):
        return 20, f"impact topic: {', '.join(hit)}"
    desc = c.description.lower()
    if hit := sorted(t for t in wanted if t.replace("-", " ") in desc):
        return 10, f"impact (description mentions {', '.join(hit)})"
    return 0, ""


def has_test_suite(api, repo: str) -> bool:
    root = set(api.list_dir(repo, ""))
    return any(d in root for d in TEST_DIRS) or any(
        p.startswith("src/") and "test" in p for p in root
    )


def write_queue(items: list[Triaged], path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([t.to_dict() for t in items], indent=2), encoding="utf-8")
