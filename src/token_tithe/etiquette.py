"""Maintainer-intent detection — runs BEFORE any repo enters the queue (§4, R3, R9, D4, D8).

Hard rejects (cached in state/feedback_cache/etiquette/):
- opt-out file `.github/token-tithe-ignore` (D8) — re-checked fresh before every run
- anti-AI / no-automation / no-bots statements in repo docs or the issue thread (R3)
- a CLA we have not signed (R3; signed CLAs are listed in config/repos.yaml)
- archived or private repo (R3, R17)
- suspected prompt injection in the issue thread (CLAUDE.md "content is data")
- leaked credentials in the issue thread (D4); the clone itself is scanned by
  `scan_clone()` before any agent runs

Soft signal: maintainer responsiveness 0..1. Unresponsive repos are deprioritized, not rejected.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from token_tithe import paths
from token_tithe.github_api import GitHubAPI, PrivateRepoError
from token_tithe.secrets_scan import scan_tree, secret_kind

OPT_OUT_FILE = ".github/token-tithe-ignore"
CACHE_TTL = timedelta(days=7)
CACHE_DIR = paths.FEEDBACK_CACHE / "etiquette"

POLICY_FILES = (
    "CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md", "CONTRIBUTING.rst",
    "CONTRIBUTING", "README.md", "README.rst", "README", "CODE_OF_CONDUCT.md",
    ".github/CODE_OF_CONDUCT.md", "AI_POLICY.md", ".github/AI_POLICY.md", "AI.md",
    ".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md",
    "PULL_REQUEST_TEMPLATE.md", "docs/pull_request_template.md", "AGENTS.md", "CLAUDE.md",
    ".cursorrules", ".github/copilot-instructions.md", "CLA.md", ".clabot", ".github/cla.yml",
)  # fmt: skip

EXTERNAL_ASSOCIATIONS = {"CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"}
MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

# --------------------------------------------------------------------------- statement detection

_AI_TERMS = re.compile(
    r"\b(?:AI|A\.I\.|LLMs?|large language models?|generative|gen-?AI|ChatGPT|GPT-?\d*|Copilot|"
    r"Claude|Gemini|Cursor|Codex|coding (?:agents?|assistants?)|AI[- ](?:generated|assisted|"
    r"written|tools?|agents?)|machine[- ]generated|auto-?generated (?:code|pull requests?|PRs?)|"
    r"bots?|automated (?:pull requests?|PRs?|contributions?|submissions?|tools?))\b",
    re.I,
)
_PROHIBIT = re.compile(
    r"\b(?:do not|don't|does not|doesn't|will not|won't|cannot|can't|never|no longer) "
    r"(?:accept|allow|permit|merge|review|welcome|take)|"
    r"\b(?:not|never) (?:be )?(?:accepted|allowed|permitted|welcome|merged|reviewed)\b|"
    r"\b(?:banned|prohibited|forbidden|disallowed|not allowed|not permitted|unwelcome)\b|"
    r"\b(?:must|may|should) not (?:use|submit|be (?:generated|written|created))\b|"
    # "You cannot use an AI tool", "best interest to not submit ... AI-generated" (spotDL)
    r"\b(?:not|never|cannot|can't|do not|don't) (?:use|submit)\b|\bwill be blocked\b|"
    r"\bclosed without (?:review|comment|discussion)\b|\bwill be (?:closed|rejected)\b|"
    r"\b(?:reject|refuse)s? (?:all |any )?(?:AI|LLM|bot|automated)|"
    r"\bno (?:AI|LLM|machine[- ]generated|automated|bot)[- \w]{0,20}?"
    r"(?:contributions?|PRs?|pull requests?|patches|submissions?|code)\b|"
    r"\bno bots\b|\bAI[- ]free\b|"
    # forbidding AI attribution forbids our mandatory disclosure (R2), so it is a ban for us
    r"\bno (?:AI|LLM|bot|Claude)[- ](?:attribution|disclosure|co-?author(?:s|ship)?)\b",
    re.I,
)
# Statements that allow AI with conditions we satisfy (we always disclose — R2).
_ALLOW = re.compile(
    r"\b(?:welcome|encouraged|are (?:fine|ok|okay|allowed|permitted|accepted)|is (?:fine|ok|"
    r"okay|allowed|permitted|accepted)|may be used|you may use|feel free)\b|"
    r"\b(?:as long as|provided (?:that)?|if you|so long as)\b.*\b(?:disclose|review|understand|"
    r"test|mark|label)|\b(?:please |must )?disclose\b",
    re.I,
)
# Bots that are routine infrastructure, not contributor policy.
_INFRA_BOTS = re.compile(
    r"\b(?:dependabot|renovate|stale ?bot|github-actions|codecov|pre-commit\.ci|cla ?bot|"
    r"mergify|allcontributors|semantic-release)\b",
    re.I,
)
_CLA = re.compile(
    r"\bcontributor license agreement\b|\b(?:sign|signed|signing) (?:the|our|a) CLA\b|"
    r"\bCLA (?:is )?(?:required|must be signed|assistant|bot)\b|cla-assistant\.io|\bEasyCLA\b|"
    r"\bcla\.(?:developers\.google|opensource\.microsoft)\.com\b",
    re.I,
)
_INJECTION = re.compile(
    r"\bignore (?:all |any )?(?:the )?(?:previous|prior|above) (?:instructions|prompts?)\b|"
    r"\bdisregard (?:all |any )?(?:the )?(?:previous|prior|above|your) (?:instructions|rules)\b|"
    r"\b(?:new|updated) system prompt\b|\bsystem prompt:|<\s*/?\s*system\s*>|"
    r"\byou are (?:now )?(?:an? )?(?:AI|assistant|agent|language model)\b"
    r".*\b(?:must|should|will)\b|"
    r"\bAI agents? (?:reading|processing) this\b|\bnote to (?:any )?(?:AI|LLM|agents?)\b",
    re.I,
)

# A contributor saying they are taking the issue (R9: never race a human for it).
_CLAIM = re.compile(
    r"\bI(?:'d| would) (?:like|love|be happy|be glad) to (?:work on|take|tackle|pick up|fix|"
    r"handle|solve|implement|be assigned)\b|"
    r"\bI(?:'ll| will|'m going to| am going to) (?:work on|take|tackle|pick up|fix|handle)\b|"
    r"\b(?:can|could|may) I (?:please )?(?:work on|take|tackle|pick up|fix|handle|be assigned|"
    r"get assigned)\b|"
    r"\b(?:please )?assign (?:this(?: issue)? |it )?to me\b|\bassign me\b|"
    r"\bI(?:'m| am) (?:currently |already )?working on (?:this|it|a fix)\b",
    re.I,
)
# CONTRIBUTING processes that require issue assignment before work or a PR (R9).
_ASSIGNMENT_FIRST = re.compile(
    r"\bonly (?:start working|work on|begin working|open a (?:pull request|PR))\b[^.]{0,120}?"
    r"\bassigned\b|"
    r"\b(?:wait|must|need|have) (?:to )?(?:be|get) assigned\b[^.]{0,60}?\b(?:before|prior to)\b|"
    r"\bcomment asking for the issue to be assigned\b|"
    r"\b(?:get|be) assigned (?:to (?:the|an|this) issue )?(?:first|before (?:starting|working|"
    r"opening))\b",
    re.I,
)
CLAIM_WINDOW_DAYS = 14

# "not welcome" / "isn't allowed" must not count as permission.
_NEGATED_ALLOW = re.compile(
    r"\b(?:not|never|no longer|isn't|aren't|is not|are not)\s+(?:be\s+)?(?:welcome|encouraged|"
    r"accepted|allowed|permitted|fine|ok|okay)\b",
    re.I,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n\s*\n|\n(?=\s*[-*#>|]|\s*\d+\.)")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def rules_fingerprint() -> str:
    """Changes whenever a detection pattern changes, so cached verdicts made by older rules
    are not trusted."""
    import hashlib

    pats = [v.pattern for k, v in sorted(globals().items())
            if k.startswith("_") and isinstance(v, re.Pattern)]  # fmt: skip
    return hashlib.sha256("\n".join(pats).encode()).hexdigest()[:12]


def anti_ai_statements(text: str) -> list[str]:
    """Sentences that prohibit AI-generated, bot or automated contributions."""
    hits = []
    for s in _sentences(text):
        flat = " ".join(s.split())
        # markdown emphasis splits phrases: "to **not** submit" must read as "to not submit"
        scrubbed = _INFRA_BOTS.sub("", re.sub(r"[*`]", "", flat))
        permissive = _ALLOW.search(_NEGATED_ALLOW.sub(" ", scrubbed))
        if _AI_TERMS.search(scrubbed) and _PROHIBIT.search(scrubbed) and not permissive:
            hits.append(flat[:200])
    return hits


def claim_comments(comments: list[dict], now: datetime, window_days: int) -> list[str]:
    """`@login (date)` for each recent human comment that claims the issue."""
    since = now - timedelta(days=window_days)
    hits = []
    for c in comments:
        login = (c.get("user") or {}).get("login", "?")
        when = c.get("created_at", "")
        if login.endswith("[bot]") or not _CLAIM.search(c.get("body") or ""):
            continue
        try:
            created = datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        if created >= since:
            hits.append(f"@{login} ({when[:10]})")
    return hits


def assignment_required(text: str) -> bool:
    """True when the text tells contributors to get assigned before working or opening a PR."""
    flat = " ".join(re.sub(r"[*_`]", "", text).split())  # "**only** start" reads as "only start"
    return bool(_ASSIGNMENT_FIRST.search(flat))


def cla_required(text: str) -> bool:
    return bool(_CLA.search(text))


def injection_attempts(text: str) -> list[str]:
    return [" ".join(s.split())[:160] for s in _sentences(text) if _INJECTION.search(s)]


# --------------------------------------------------------------------------- verdict


@dataclass
class Verdict:
    repo: str
    status: str  # "ok" | "reject" | "deprioritize"
    reasons: list[str] = field(default_factory=list)
    responsiveness: float = 0.0
    signals: dict = field(default_factory=dict)
    checked_at: str = ""

    @property
    def rejected(self) -> bool:
        return self.status == "reject"


def responsiveness(repo: dict, merged_pulls: list[dict], issue_comments: list[dict] | None,
                   now: datetime) -> tuple[float, dict]:  # fmt: skip
    """0..1 from external merges (90d), push recency, and maintainer engagement on the issue."""

    def age_days(iso: str | None) -> float:
        if not iso:
            return 10_000.0
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        return (now - dt).total_seconds() / 86400

    external = [
        p for p in merged_pulls
        if p.get("author_association") in EXTERNAL_ASSOCIATIONS
        and age_days(p.get("merged_at")) <= 90
    ]  # fmt: skip
    merges = min(1.0, len(external) / 3)
    pushed = age_days(repo.get("pushed_at"))
    push = 1.0 if pushed <= 14 else 0.5 if pushed <= 60 else 0.0
    parts = [(merges, 0.5), (push, 0.2)]
    engaged = None
    if issue_comments is not None:
        engaged = any(
            c.get("author_association") in MAINTAINER_ASSOCIATIONS for c in issue_comments
        )
        parts.append((1.0 if engaged else 0.0, 0.3))
    total_w = sum(w for _, w in parts)
    score = round(sum(v * w for v, w in parts) / total_w, 3)
    return score, {"external_merges_90d": len(external), "pushed_days_ago": round(pushed, 1),
                   "maintainer_engaged": engaged}  # fmt: skip


class Etiquette:
    """Assess repos (and optionally an issue) for maintainer intent, with a verdict cache."""

    def __init__(
        self,
        api: GitHubAPI,
        cla_signed: list[str] | None = None,
        cache_dir: Path | None | str = "default",
        deprioritize_below: float = 0.3,
        now: datetime | None = None,
        claim_window_days: int = CLAIM_WINDOW_DAYS,
    ):
        self.api = api
        self.cla_signed = {r.lower() for r in (cla_signed or [])}
        self.cache_dir = paths.FEEDBACK_CACHE / "etiquette" if cache_dir == "default" else cache_dir
        self.deprioritize_below = deprioritize_below
        self.now = now or datetime.now(UTC)
        self.claim_window_days = claim_window_days

    # ----------------------------------------------------------------- cache

    def _cache_file(self, repo: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (repo.replace("/", "__").lower() + ".json")

    def _cached(self, repo: str) -> Verdict | None:
        f = self._cache_file(repo)
        if not f or not f.is_file():
            return None
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.pop("rules", None) != rules_fingerprint():
                return None  # verdict made by older detection rules (spotDL stayed ranked)
            v = Verdict(**data)
            checked = datetime.strptime(v.checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except (ValueError, TypeError, KeyError):
            return None
        return v if self.now - checked <= CACHE_TTL else None

    def _store(self, v: Verdict) -> None:
        f = self._cache_file(v.repo)
        if f:
            f.parent.mkdir(parents=True, exist_ok=True)
            data = {**asdict(v), "rules": rules_fingerprint()}
            f.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ----------------------------------------------------------------- checks

    def opted_out(self, repo: str) -> bool:
        """D8: presence of the file = opt-out. Never cached."""
        return self.api.get_file(repo, OPT_OUT_FILE) is not None

    def assess_repo(self, repo: str, fresh: bool = False) -> Verdict:
        """Repo-level verdict. `fresh=True` bypasses the cache (use immediately before a run)."""
        try:
            opted_out = self.opted_out(repo)
        except PrivateRepoError:
            v = self._verdict(repo, "reject", ["private repo (R17)"])
            self._store(v)
            return v
        if opted_out:
            v = self._verdict(repo, "reject", [f"opt-out: {OPT_OUT_FILE} present (D8)"])
            self._store(v)
            return v
        if not fresh and (cached := self._cached(repo)):
            return cached

        reasons: list[str] = []
        try:
            meta = self.api.get_repo(repo)
        except PrivateRepoError:
            v = self._verdict(repo, "reject", ["private repo (R17)"])
            self._store(v)
            return v
        if meta.get("archived"):
            reasons.append("archived repo (R3)")

        present = {p for d in ("", ".github", "docs") for p in self.api.list_dir(repo, d)}
        docs = {
            f: t
            for f in POLICY_FILES
            if f in present and (t := self.api.get_file(repo, f)) is not None
        }
        assignment_files: list[str] = []
        for fname, text in docs.items():
            for s in anti_ai_statements(text):
                reasons.append(f'anti-AI/automation statement in {fname}: "{s}" (R3)')
            if cla_required(text) and repo.lower() not in self.cla_signed:
                reasons.append(f"requires a CLA we have not signed ({fname}) (R3)")
            if assignment_required(text):
                assignment_files.append(fname)
        reasons = list(dict.fromkeys(reasons))

        score, signals = responsiveness(meta, self.api.recent_merged_pulls(repo), None, self.now)
        signals["policy_files"] = sorted(docs)
        if assignment_files:
            signals["assignment_required"] = assignment_files[0]
        if reasons:
            status = "reject"
        elif score < self.deprioritize_below:
            status = "deprioritize"
        else:
            status = "ok"
        v = self._verdict(repo, status, reasons, score, signals)
        self._store(v)
        return v

    def assess_issue(self, repo: str, number: int, fresh: bool = False) -> Verdict:
        """Repo verdict plus the issue thread: anti-AI asks, injection, leaked secrets."""
        base = self.assess_repo(repo, fresh=fresh)
        if base.rejected:
            return base
        issue = self.api.get_issue(repo, number)
        comments = self.api.issue_comments(repo, number)
        texts = [issue.get("title", ""), issue.get("body") or ""] + [
            c.get("body", "") for c in comments
        ]
        reasons: list[str] = []
        for t in texts:
            reasons += [
                f'anti-AI request in issue thread: "{s}" (R3)' for s in anti_ai_statements(t)
            ]
            reasons += [
                f'suspected prompt injection in issue thread: "{s}"' for s in injection_attempts(t)
            ]
            if kind := secret_kind(t):
                reasons.append(f"leaked credential in issue thread ({kind}) — not quoted (D4)")
        if claims := claim_comments(comments, self.now, self.claim_window_days):
            reasons.append(f"claimed by a contributor in a comment: {', '.join(claims)} (R9)")
        reasons = list(dict.fromkeys(reasons))

        meta = self.api.get_repo(repo)
        score, signals = responsiveness(
            meta, self.api.recent_merged_pulls(repo), comments, self.now
        )
        signals = {**base.signals, **signals}
        if reasons:
            return self._verdict(repo, "reject", reasons, score, signals)
        status = "deprioritize" if score < self.deprioritize_below else "ok"
        return self._verdict(repo, status, [], score, signals)

    def _verdict(self, repo, status, reasons, score=0.0, signals=None) -> Verdict:
        return Verdict(repo, status, reasons, score, signals or {},
                       self.now.strftime("%Y-%m-%dT%H:%M:%SZ"))  # fmt: skip


def scan_clone(worktree: Path) -> list[str]:
    """D4: scan a local clone for leaked credentials. Returns `file:line (kind)` only."""
    return scan_tree(worktree)
