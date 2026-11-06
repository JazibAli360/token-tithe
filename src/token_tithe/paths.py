"""Every path the engine uses, anchored to the repo root via __file__ (S2).

Nothing else in the codebase may build a path from cwd, $HOME, or a literal absolute path.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CONFIG = ROOT / "config"
POLICY_FILE = CONFIG / "policy.yaml"
REPOS_FILE = CONFIG / "repos.yaml"
AGENTS_CONFIG = CONFIG / "agents"

TEMPLATES = ROOT / "templates"
EVAL = ROOT / "eval"
FIXTURES = EVAL / "fixtures"
EXPECTED = EVAL / "expected"
PROMPTS = AGENTS_CONFIG / "prompts"

BRAIN_DIR = ROOT / "brain"
BRAIN = BRAIN_DIR / "BRAIN.md"
KNOWLEDGE = BRAIN_DIR / "knowledge"

DOCS = ROOT / "docs"
CONTAINER = ROOT / "container"

STATE = ROOT / "state"
LEDGER = STATE / "ledger.jsonl"
HALT_FLAG = STATE / "HALT"
STOP_FLAG = STATE / "STOP"
HANDOVER_JSON = STATE / "handover.json"
HANDOVER_MD = STATE / "HANDOVER.md"
REPORTS = STATE / "reports"
REVIEW = STATE / "review"
FEEDBACK_CACHE = STATE / "feedback_cache"
ADOPTION = STATE / "adoption"
BRIEFS = STATE / "briefs"  # operator guidance per repo: <owner>__<name>.md
EVAL_RUNS = STATE / "eval_runs"
WORKTREES = STATE / "worktrees"
ABANDONED = STATE / "abandoned"  # why a task was abandoned: failing output + diff
OPERATOR_NOTES = (
    STATE / "OPERATOR_NOTES.md"
)  # the operator's carry-over notes; runs never rewrite it
QUEUE = STATE / "queue.json"

VENV = ROOT / ".venv"


def ensure_state() -> Path:
    """Create the state tree on demand. Deleting state/ is a full reset (S4)."""
    for d in (STATE, REPORTS, REVIEW, FEEDBACK_CACHE, ADOPTION, EVAL_RUNS, WORKTREES):
        d.mkdir(parents=True, exist_ok=True)
    return STATE


def rel(path: Path) -> str:
    """Render a path relative to the repo root for logs, ledger and reports."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


_STATE_NAMES = {
    "STATE": "", "LEDGER": "ledger.jsonl", "HALT_FLAG": "HALT", "STOP_FLAG": "STOP",
    "HANDOVER_JSON": "handover.json", "HANDOVER_MD": "HANDOVER.md", "REPORTS": "reports",
    "REVIEW": "review", "FEEDBACK_CACHE": "feedback_cache", "ADOPTION": "adoption",
    "EVAL_RUNS": "eval_runs", "WORKTREES": "worktrees", "QUEUE": "queue.json",
    "ABANDONED": "abandoned", "OPERATOR_NOTES": "OPERATOR_NOTES.md",
}  # fmt: skip


@contextmanager
def use_state(directory: Path) -> Iterator[Path]:
    """Point every state path at `directory` (simulate, tests). Restores on exit."""
    g = globals()
    saved = {k: g[k] for k in _STATE_NAMES}
    try:
        for name, rel_name in _STATE_NAMES.items():
            g[name] = directory / rel_name if rel_name else directory
        ensure_state()
        yield directory
    finally:
        g.update(saved)
