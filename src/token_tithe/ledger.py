"""Append-only JSONL ledger (state/ledger.jsonl): every token, decision and outcome.

Every line carries `schema_version`. Readers tolerate unknown versions and malformed lines
(they are reported, never crash a run). A line that looks like it contains a secret is refused
before it reaches disk (R6).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from token_tithe import paths
from token_tithe.secrets_scan import secret_kind

# abandon reasons meaning the repo's own suite was broken before our change (executor)
BROKEN_BASELINE = re.compile(r"before any change|already failed before the change")

SCHEMA_VERSION = 1

# Outcomes a task can end with. `pr_opened` / `comment_posted` are the publishing events
# rate limits count; `merged` / `closed` are appended later by feedback.
OUTCOMES = {
    "pr_opened", "comment_posted", "queued_for_review", "abandoned", "rejected", "timeout",
    "stopped", "budget_exhausted", "dry_run", "merged", "closed", "stale", "incident",
    "addressed", "skipped", "unaccounted", "model_switch", "subagent", "sizing",
    "sandbox_relaxed",
}  # fmt: skip


class LedgerError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@dataclass
class Ledger:
    path: Path = field(default_factory=lambda: paths.LEDGER)
    problems: list[str] = field(default_factory=list)

    def append(self, event: str, **data: object) -> dict:
        if event not in OUTCOMES:
            raise LedgerError(f"unknown ledger event {event!r}")
        entry = {"schema_version": SCHEMA_VERSION, "ts": now_iso(), "event": event, **data}
        line = json.dumps(entry, sort_keys=True, default=str)
        if kind := secret_kind(line):
            raise LedgerError(f"refusing to write a ledger line containing a {kind} (R6)")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return entry

    def entries(self) -> Iterator[dict]:
        """Every readable entry. Malformed lines and unknown schema versions are recorded in
        `self.problems` and skipped (unknown versions are still yielded if they parse)."""
        self.problems = []
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                    if not isinstance(entry, dict) or "event" not in entry:
                        raise ValueError("not a ledger object")
                except ValueError as e:
                    self.problems.append(f"line {n}: malformed ({e})")
                    continue
                v = entry.get("schema_version")
                if v != SCHEMA_VERSION:
                    self.problems.append(f"line {n}: unknown schema_version {v!r}")
                yield entry

    # ----------------------------------------------------------------- derived views

    def _since(self, days: float, now: datetime | None = None) -> Iterator[dict]:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
        for e in self.entries():
            try:
                if _parse_ts(e["ts"]) >= cutoff:
                    yield e
            except (KeyError, ValueError):
                continue

    def prs_to_repo(self, repo: str, days: int = 30, now: datetime | None = None) -> int:
        return sum(
            1 for e in self._since(days, now)
            if e["event"] == "pr_opened" and str(e.get("repo", "")).lower() == repo.lower()
        )  # fmt: skip

    def prs_today(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        today = now.date().isoformat()
        return sum(1 for e in self.entries() if e["event"] == "pr_opened" and e["ts"][:10] == today)

    def comments_on_issue(self, repo: str, number: int) -> int:
        return sum(
            1 for e in self.entries()
            if e["event"] == "comment_posted"
            and str(e.get("repo", "")).lower() == repo.lower() and e.get("issue") == number
        )  # fmt: skip

    def recent_repos(self, days: int, now: datetime | None = None) -> set[str]:
        """Repos we published to (PR or comment) within `days`."""
        return {
            str(e["repo"]) for e in self._since(days, now)
            if e["event"] in ("pr_opened", "comment_posted") and e.get("repo")
        }  # fmt: skip

    def broken_env_repos(self, days: float, now: datetime | None = None) -> dict[str, str]:
        """repo -> date of a recent abandon because its tests failed before any change.
        Other issues there would fail the same way until the environment changes."""
        return {
            str(e["repo"]).lower(): str(e["ts"])[:10] for e in self._since(days, now)
            if e["event"] == "abandoned" and e.get("repo")
            and BROKEN_BASELINE.search(str(e.get("reason", "")))
        }  # fmt: skip

    def attempted(self) -> set[str]:
        """`owner/repo#N` keys we already worked on (any terminal task outcome). An abandon
        that spent no tokens (agent not logged in, failed to start) did no work: retryable."""
        terminal = {"pr_opened", "comment_posted", "queued_for_review", "abandoned", "rejected",
                    "timeout", "dry_run"}  # fmt: skip
        return {
            f"{e['repo']}#{e['issue']}" for e in self.entries()
            if e["event"] in terminal and e.get("repo") and e.get("issue") is not None
            and not (e["event"] == "abandoned" and not e.get("tokens"))
        }  # fmt: skip

    def history(self) -> dict[str, tuple[int, int]]:
        """repo -> (merged, closed_unmerged) for triage (outcomes come from feedback)."""
        merged: Counter[str] = Counter()
        closed: Counter[str] = Counter()
        for e in self.entries():
            if e["event"] == "merged":
                merged[str(e.get("repo"))] += 1
            elif e["event"] == "closed":
                closed[str(e.get("repo"))] += 1
        return {r: (merged[r], closed[r]) for r in set(merged) | set(closed)}

    def average_tokens(self) -> dict[str, int]:
        """Mean real tokens per donation type over accounted task outcomes."""
        sums: dict[str, list[int]] = defaultdict(list)
        for e in self.entries():
            if e.get("accounted") and e.get("tokens") and e.get("donation_type"):
                sums[str(e["donation_type"])].append(int(e["tokens"]))
        return {k: sum(v) // len(v) for k, v in sums.items() if v}
