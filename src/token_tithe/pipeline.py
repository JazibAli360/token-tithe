"""discover → etiquette → triage, shared by the CLI, simulate and (Phase 5) the executor."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from token_tithe import paths
from token_tithe.discover import DiscoveryResult, Skip, discover
from token_tithe.etiquette import Etiquette, Verdict
from token_tithe.github_api import GitHubAPI
from token_tithe.policy import Policy, Repos
from token_tithe.triage import Triaged, has_test_suite, triage


class Halted(RuntimeError):
    """R12: state/HALT is set; nothing may run."""


def check_halt() -> None:
    if paths.HALT_FLAG.exists():
        raise Halted("halted: state/HALT is set — `donate resume` clears it (R12)")


@dataclass
class QueueResult:
    discovery: DiscoveryResult
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    rejected: list[Skip] = field(default_factory=list)
    ranked: list[Triaged] = field(default_factory=list)


def build_queue(
    api: GitHubAPI,
    policy: Policy,
    repos_cfg: Repos,
    etiquette: Etiquette | None = None,
    recent_repos: Iterable[str] = (),
    attempted: Iterable[str] = (),
    history: Mapping[str, tuple[int, int]] | None = None,
    now: datetime | None = None,
    picked: Iterable[str] = (),
    follow_ups: Iterable[str] = (),
    broken_repos: Mapping[str, str] | None = None,
) -> QueueResult:
    check_halt()
    now = now or datetime.now(UTC)
    etiquette = etiquette or Etiquette(
        api,
        cla_signed=repos_cfg.cla_signed,
        now=now,
        claim_window_days=policy.discovery.claim_window_days,
    )
    disc = discover(
        api, policy, repos_cfg, recent_repos, attempted, now, picked, follow_ups, broken_repos
    )
    res = QueueResult(disc)

    test_dirs: dict[str, bool] = {}
    for c in disc.candidates:
        v = etiquette.assess_issue(c.repo, c.number)
        res.verdicts[c.key] = v
        if v.rejected:
            res.rejected.append(Skip(c.key, "; ".join(v.reasons)))
        elif c.repo not in test_dirs:
            test_dirs[c.repo] = has_test_suite(api, c.repo)

    res.ranked = triage(disc.candidates, res.verdicts, policy, test_dirs, history, now)
    return res


def render_queue(res: QueueResult, limit: int = 20) -> str:
    lines = [f"discovered {len(res.discovery.candidates)} candidates from "
             f"{len(res.discovery.queries)} queries"]  # fmt: skip
    if res.discovery.skipped:
        lines.append(f"\nskipped at discovery ({len(res.discovery.skipped)}):")
        lines += [f"  - {s.target}: {s.reason}" for s in res.discovery.skipped[:limit]]
    if res.rejected:
        lines.append(f"\nrejected by etiquette ({len(res.rejected)}):")
        lines += [f"  - {s.target}: {s.reason}" for s in res.rejected]
    lines.append(f"\nranked queue ({len(res.ranked)}):")
    for i, t in enumerate(res.ranked[:limit], 1):
        lines.append(f"  {i:>2}. [{t.score:>3}] {t.candidate.key} — {t.candidate.title}")
        lines.append(f"      {t.rationale}")
    return "\n".join(lines)


def render_markdown(res: QueueResult) -> str:
    out = ["# Triage queue", "", f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.", ""]
    out += ["| # | score | type | issue | etiquette |", "|---|---|---|---|---|"]
    for i, t in enumerate(res.ranked, 1):
        link = f"[{t.candidate.key}]({t.candidate.url})"
        out.append(f"| {i} | {t.score} | {t.donation_type} | {link} | {t.etiquette} |")
    out += ["", "## Rationales", ""]
    out += [f"- **{t.candidate.key}** — {t.rationale}" for t in res.ranked]
    if res.rejected:
        out += ["", "## Rejected by etiquette", ""]
        out += [f"- {s.target}: {s.reason}" for s in res.rejected]
    return "\n".join(out) + "\n"


def gate(
    etiquette: Etiquette, repo: str, number: int, clone_dir=None, test_fixtures_ok: bool = False
) -> str | None:
    """The last check before any agent touches a repo: a FRESH etiquette verdict (opt-out,
    anti-AI, CLA, injection, issue-thread secrets) plus a D4 secrets scan of the local clone.
    `test_fixtures_ok` (operator-listed repos, e.g. secret scanners): hits in test files are
    ignored; any hit elsewhere still rejects. Returns the rejection reason, or None."""
    from token_tithe.etiquette import scan_clone
    from token_tithe.secrets_scan import is_test_path

    v = etiquette.assess_issue(repo, number, fresh=True)
    if v.rejected:
        return "; ".join(v.reasons)
    hits = scan_clone(clone_dir) if clone_dir is not None else []
    if test_fixtures_ok:
        hits = [h for h in hits if not is_test_path(h.split(":", 1)[0])]
    if hits:
        kinds = sorted({h.rsplit("(", 1)[-1].rstrip(")") for h in hits})
        return f"leaked credentials in repo ({', '.join(kinds)}; {len(hits)} location(s)) (D4)"
    return None
