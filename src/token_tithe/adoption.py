"""`donate adopt owner/repo`: a long-term relationship with one repo (§3).

Builds a profile in state/adoption/<owner>__<repo>.json — etiquette verdict, toolchain,
conventions (CONTRIBUTING/PR template presence, commit-title style of recent merged PRs) — and
adds the repo to `adopted` in config/repos.yaml, which discovery searches first. The executor
puts the profile's conventions into every task brief for that repo.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from token_tithe import local_config, paths
from token_tithe.etiquette import Etiquette
from token_tithe.github_api import GitHubAPI
from token_tithe.policy import load_repos

CONVENTIONAL = re.compile(
    r"^(feat|fix|docs|test|tests|chore|refactor|perf|ci|build|style)(\(.+\))?!?:"
)


def profile_path(repo: str) -> Path:
    return paths.ADOPTION / (repo.replace("/", "__").lower() + ".json")


def commit_style(titles: list[str]) -> str:
    if not titles:
        return "unknown"
    conv = sum(bool(CONVENTIONAL.match(t)) for t in titles)
    if conv / len(titles) >= 0.6:
        return "conventional commits (type(scope): summary)"
    lower = sum(t[:1].islower() for t in titles)
    return "plain sentence titles, " + (
        "lowercase start" if lower > len(titles) / 2 else "capitalized"
    )


def adopt(api: GitHubAPI, repo: str, etiquette: Etiquette, repos_file: Path | None = None) -> dict:
    verdict = etiquette.assess_repo(repo, fresh=True)
    if verdict.rejected:
        raise ValueError(f"cannot adopt {repo}: " + "; ".join(verdict.reasons))
    meta = api.get_repo(repo)
    root = set(api.list_dir(repo, "")) | set(api.list_dir(repo, ".github"))
    merged = api.recent_merged_pulls(repo, 30)
    titles = [p.get("title", "") for p in merged if p.get("title")]
    labels = Counter(
        lb for p in merged for lb in [x.get("name") for x in p.get("labels", [])] if lb
    )
    prof = {
        "repo": meta["full_name"],
        "adopted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "language": meta.get("language"),
        "default_branch": meta.get("default_branch"),
        "license": (meta.get("license") or {}).get("spdx_id"),
        "responsiveness": verdict.responsiveness,
        "has_contributing": any("CONTRIBUTING" in p for p in root),
        "has_pr_template": any("pull_request_template" in p.lower() for p in root),
        "commit_style": commit_style(titles),
        "recent_titles": titles[:8],
        "common_labels": [lb for lb, _ in labels.most_common(5)],
    }
    paths.ADOPTION.mkdir(parents=True, exist_ok=True)
    profile_path(repo).write_text(json.dumps(prof, indent=2), encoding="utf-8")
    _add_adopted(meta["full_name"], repos_file or paths.REPOS_FILE)
    return prof


def _add_adopted(repo: str, repos_file: Path) -> None:
    """Register in your repos.local.yaml overlay; a repo already adopted anywhere is left alone."""
    if any(r.lower() == repo.lower() for r in load_repos(repos_file).adopted):
        return
    local_config.append_item(repos_file, "adopted", repo)


def conventions_for(repo: str) -> str:
    """Extra conventions for the task brief, if the repo is adopted."""
    f = profile_path(repo)
    if not f.is_file():
        return ""
    p = json.loads(f.read_text(encoding="utf-8"))
    lines = [f"Adopted repo profile: commit/PR titles use {p['commit_style']}."]
    if p.get("recent_titles"):
        lines.append("Recent merged PR titles: " + "; ".join(p["recent_titles"][:5]) + ".")
    if p.get("has_pr_template"):
        lines.append("The repo has a PR template — the engine fills it in the PR body.")
    return "\n".join(lines)
