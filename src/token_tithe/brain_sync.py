"""Optional shared Brain across clones (Addendum E4): `brain.sync_remote` is a tiny git repo
holding one exported brain file. Off by default; the in-repo Brain always works alone."""

from __future__ import annotations

import shutil
import subprocess

from token_tithe import paths
from token_tithe.brain import Brain

EXPORT_NAME = "tithe-brain.md"


def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def sync(remote: str) -> str:
    if not remote:
        return "brain.sync_remote is empty in config/policy.yaml — sync is off (the in-repo Brain is used)"
    work = paths.STATE / "brain-sync"
    if not (work / ".git").is_dir():
        shutil.rmtree(work, ignore_errors=True)
        r = _git(["clone", "-q", remote, str(work)], paths.ROOT)
        if r.returncode != 0:
            return f"clone failed: {r.stderr.strip()[-200:]}"
    else:
        _git(["pull", "-q", "--ff-only"], work)
    brain = Brain.load()
    pulled = work / EXPORT_NAME
    stats = brain.import_(pulled) if pulled.is_file() else {"note": "remote empty"}
    brain.save()
    exported = brain.export(paths.STATE / "tmp")
    shutil.copy(exported, pulled)
    _git(["add", EXPORT_NAME], work)
    if _git(["diff", "--cached", "--quiet"], work).returncode != 0:
        _git(["-c", "user.name=token-tithe", "-c", "user.email=token-tithe@localhost.invalid",
              "-c", "commit.gpgsign=false", "commit", "-qm", "brain: sync"], work)  # fmt: skip
        r = _git(["push", "-q"], work)
        if r.returncode != 0:
            return f"pulled {stats}; push failed: {r.stderr.strip()[-200:]}"
    return f"synced with {remote}: {stats}"
