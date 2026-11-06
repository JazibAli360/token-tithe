"""Handover state (Addendum B): every run, however it ends, leaves a resumable record.

state/handover.json (machine) + state/HANDOVER.md (human, < 60 lines), rewritten atomically at
every step. SIGINT/SIGTERM become a cooperative stop that still writes the handover. `donate
stop` sets state/STOP, which is checked between every executor step — and immediately before
any GitHub write, so a stop never lands after a write has started (abort-before-publish).
"""

from __future__ import annotations

import json
import os
import signal
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from token_tithe import paths
from token_tithe.ledger import now_iso

FINAL_STATUSES = {
    "completed", "stopped", "halted", "crashed", "budget_exhausted", "insufficient_capacity",
    "aborted",
}  # fmt: skip


class StopRequested(RuntimeError):
    """Raised at a checkpoint when `donate stop` (or a signal) asked for an immediate stop."""


@dataclass
class TaskState:
    key: str = ""
    repo: str = ""
    issue: int = 0
    donation_type: str = ""
    branch: str = ""
    worktree: str = ""  # relative to the repo root
    last_completed_step: str = ""
    next_step: str = ""
    proposal_id: str = ""


@dataclass
class Handover:
    run_id: str
    status: str = "running"
    phase: str = "start"
    reason: str = ""
    live: bool = False
    tokens_spent: int = 0
    task: TaskState = field(default_factory=TaskState)
    completed_tasks: list[str] = field(default_factory=list)
    pending_review: list[str] = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    updated_at: str = ""

    @property
    def resumable(self) -> bool:
        return bool(self.task.next_step) and self.status != "completed"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def render_md(h: Handover) -> str:
    t = h.task
    lines = [
        "# HANDOVER — read this first, then brain/BRAIN.md",
        "",
        f"- run: `{h.run_id}` · status: **{h.status}** · phase: {h.phase} · live: {h.live}",
        f"- updated: {h.updated_at}",
        f"- tokens spent this run: {h.tokens_spent:,}",
    ]
    if h.reason:
        lines.append(f"- reason: {h.reason}")
    if t.key:
        lines += [
            "",
            "## Task in progress",
            f"- {t.key} ({t.donation_type})",
            f"- branch `{t.branch}` in `{t.worktree}`",
            f"- last completed step: {t.last_completed_step or '—'}",
            f"- **next step: {t.next_step or '—'}**",
        ]
        if t.proposal_id:
            lines.append(f"- proposal: state/review/{t.proposal_id}/")
    if h.completed_tasks:
        lines += ["", "## Finished this run"] + [f"- {k}" for k in h.completed_tasks[-15:]]
    if h.pending_review:
        lines += ["", "## Waiting in the review queue (`donate review`)"]
        lines += [f"- {p}" for p in h.pending_review[-15:]]
    lines += ["", "## How to continue", ""]
    if paths.OPERATOR_NOTES.is_file():  # survives every run's rewrite of this file
        lines.append(f"- **Read `{paths.rel(paths.OPERATOR_NOTES)}` next**: the operator's plan.")
    if h.flags.get("halt"):
        lines.append("- Kill switch is ON (state/HALT). `donate resume` clears it.")
    if h.resumable:
        lines.append(f"- `donate resume` continues {t.key} at **{t.next_step}**.")
    else:
        lines.append("- Nothing in flight. `donate run` starts a fresh run.")
    return "\n".join(lines[:59]) + "\n"


def write(h: Handover, json_path: Path | None = None, md_path: Path | None = None):
    json_path = json_path or paths.HANDOVER_JSON
    md_path = md_path or paths.HANDOVER_MD
    h.updated_at = now_iso()
    h.flags = {"halt": paths.HALT_FLAG.exists(), "stop": paths.STOP_FLAG.exists()}
    _atomic_write(json_path, json.dumps(asdict(h), indent=2))
    _atomic_write(md_path, render_md(h))


def read(json_path: Path | None = None) -> Handover | None:
    json_path = json_path or paths.HANDOVER_JSON
    if not json_path.is_file():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        data["task"] = TaskState(**data.get("task", {}))
        return Handover(**data)
    except (ValueError, TypeError):
        return None


def request_stop() -> None:
    paths.ensure_state()
    paths.STOP_FLAG.touch()


def stop_requested() -> bool:
    return paths.STOP_FLAG.exists()


class HandoverSession:
    """Context manager around one run: signal-safe, always writes a final handover (R14)."""

    def __init__(self, run_id: str, live: bool, install_signals: bool = True):
        self.h = Handover(run_id=run_id, live=live)
        self._install = install_signals
        self._old: dict = {}

    def __enter__(self) -> HandoverSession:
        paths.ensure_state()
        paths.STOP_FLAG.unlink(missing_ok=True)  # a stale stop must not kill a new run
        if self._install:
            for sig in (signal.SIGINT, signal.SIGTERM):
                self._old[sig] = signal.signal(sig, self._on_signal)
        write(self.h)
        return self

    def _on_signal(self, signum, frame):  # noqa: ARG002
        request_stop()
        raise StopRequested(f"received {signal.Signals(signum).name}")

    def checkpoint(self) -> None:
        if stop_requested():
            raise StopRequested("`donate stop` requested")

    def step(self, phase: str | None = None, **task_fields) -> None:
        if phase:
            self.h.phase = phase
        for k, v in task_fields.items():
            setattr(self.h.task, k, v)
        write(self.h)

    def finish_task(self, key: str, proposal_id: str = "") -> None:
        self.h.completed_tasks.append(key)
        if proposal_id:
            self.h.pending_review.append(proposal_id)
        self.h.task = TaskState()
        write(self.h)

    def finalize(self, status: str, reason: str = "") -> None:
        self.h.status = status
        self.h.reason = reason
        write(self.h)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._install:
            for sig, old in self._old.items():
                signal.signal(sig, old)
        if self.h.status == "running":  # not finalized explicitly → classify the exit
            if exc_type is None:
                self.finalize("completed")
            elif issubclass(exc_type, StopRequested):
                self.finalize("stopped", str(exc))
            else:
                self.finalize("crashed", f"{exc_type.__name__}: {exc}")
        paths.STOP_FLAG.unlink(missing_ok=True)
        return False
