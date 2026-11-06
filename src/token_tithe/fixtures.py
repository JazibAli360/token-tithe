"""Fixture repos: load metadata + rubrics, materialize them as real git repos, run commands.

Layout (all committed, self-contained — S1):

    eval/fixtures/<name>/github.yaml   fake GitHub metadata served by FixtureTransport
    eval/fixtures/<name>/repo/         the repository contents
    eval/expected/<name>/rubric.yaml   expected outcome + scoring checks
    eval/expected/<name>/reference/    known-good solution (overlay), used by the reference agent
    eval/expected/<name>/acceptance/   hidden checks, overlaid only into a scoring copy
    eval/expected/<name>/mutants/      broken implementations the agent's tests must catch

Nested `.git` directories cannot be committed, so `materialize()` turns `repo/` into a git
repo with one deterministic commit under state/.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from token_tithe import paths
from token_tithe.policy import DONATION_TYPES, PolicyError, parse_typed

FIXTURE_AUTHOR = ("tithe-fixtures", "fixtures@token-tithe.invalid")
FIXTURE_DATE = "2026-01-01T00:00:00+00:00"


@dataclass(frozen=True)
class Baseline:
    repo_tests: Literal["pass", "fail"] = "pass"


@dataclass(frozen=True)
class FileText:
    file: str
    pattern: str


@dataclass(frozen=True)
class Mutant:
    target: str
    replacement: str


@dataclass(frozen=True)
class Checks:
    repo_tests: list[str] | None = None
    lint: list[list[str]] = field(default_factory=list)
    acceptance: list[list[str]] = field(default_factory=list)
    tests_added: list[str] = field(default_factory=list)
    max_diff_lines: int = 400
    unchanged: list[str] = field(default_factory=list)
    mutants: list[Mutant] = field(default_factory=list)
    must_contain: list[FileText] = field(default_factory=list)
    must_not_contain: list[FileText] = field(default_factory=list)


@dataclass(frozen=True)
class Rubric:
    fixture: str
    language: str
    donation_type: str
    issue: int
    expect: Literal["fix", "reject"]
    requires: list[str]
    baseline: Baseline
    checks: Checks
    reject_reason: str | None = None


@dataclass(frozen=True)
class Fixture:
    name: str
    root: Path
    expected: Path
    github: dict
    rubric: Rubric

    @property
    def repo_dir(self) -> Path:
        return self.root / "repo"

    @property
    def full_name(self) -> str:
        return self.github["repo"]["full_name"]

    @property
    def issue(self) -> dict:
        return next(i for i in self.github["issues"] if i["number"] == self.rubric.issue)

    @property
    def reference_dir(self) -> Path:
        return self.expected / "reference"

    @property
    def acceptance_dir(self) -> Path:
        return self.expected / "acceptance"


def load_fixture(
    name: str, fixtures_dir: Path = paths.FIXTURES, expected_dir: Path = paths.EXPECTED
) -> Fixture:
    root = fixtures_dir / name
    exp = expected_dir / name
    gh_file, rubric_file = root / "github.yaml", exp / "rubric.yaml"
    for f in (gh_file, rubric_file, root / "repo"):
        if not f.exists():
            raise PolicyError(f"fixture {name}: missing {paths.rel(f)}")
    github = yaml.safe_load(gh_file.read_text(encoding="utf-8"))
    rubric: Rubric = parse_typed(
        Rubric, yaml.safe_load(rubric_file.read_text(encoding="utf-8")), paths.rel(rubric_file)
    )
    if rubric.fixture != name:
        raise PolicyError(f"{paths.rel(rubric_file)}: fixture {rubric.fixture!r} != {name!r}")
    if rubric.donation_type not in DONATION_TYPES:
        raise PolicyError(f"{paths.rel(rubric_file)}: unknown donation_type")
    if rubric.issue not in {i["number"] for i in github.get("issues", [])}:
        raise PolicyError(f"{paths.rel(rubric_file)}: issue #{rubric.issue} not in github.yaml")
    if rubric.expect == "reject" and not rubric.reject_reason:
        raise PolicyError(f"{paths.rel(rubric_file)}: reject fixtures need reject_reason")
    if rubric.expect == "fix" and not (exp / "reference").is_dir():
        raise PolicyError(f"{paths.rel(rubric_file)}: fix fixtures need a reference/ solution")
    return Fixture(name, root, exp, github, rubric)


def load_fixtures(
    fixtures_dir: Path = paths.FIXTURES, expected_dir: Path = paths.EXPECTED
) -> list[Fixture]:
    names = sorted(p.parent.name for p in fixtures_dir.glob("*/github.yaml"))
    return [load_fixture(n, fixtures_dir, expected_dir) for n in names]


# --------------------------------------------------------------------------- toolchains

TOOLCHAIN_BINARIES = {"python": None, "node": "node", "cargo": "cargo"}


def missing_toolchains(requires: list[str]) -> list[str]:
    """`python` is always the venv interpreter; others must be on PATH."""
    missing = []
    for tool in requires:
        binary = TOOLCHAIN_BINARIES.get(tool, tool)
        if binary and not shutil.which(binary):
            missing.append(tool)
    return missing


# --------------------------------------------------------------------------- git + commands


@dataclass(frozen=True)
class CmdResult:
    code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def run_cmd(cmd: list[str], cwd: Path, timeout: int = 300) -> CmdResult:
    """Run a fixture/rubric command. `{python}` means the engine's own interpreter."""
    argv = [sys.executable if a == "{python}" else a for a in cmd]
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": "",
        "CARGO_TARGET_DIR": str(cwd / "target"),
        "NO_COLOR": "1",
    }
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return CmdResult(p.returncode, (p.stdout + p.stderr)[-4000:])
    except FileNotFoundError as e:
        return CmdResult(127, str(e))
    except subprocess.TimeoutExpired:
        return CmdResult(124, f"timed out after {timeout}s")


def git(args: list[str], cwd: Path) -> CmdResult:
    name, email = FIXTURE_AUTHOR
    env_cfg = [
        "-c", f"user.name={name}", "-c", f"user.email={email}",
        "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", "-c", "core.autocrlf=false",
    ]  # fmt: skip
    env = {**os.environ, "GIT_AUTHOR_DATE": FIXTURE_DATE, "GIT_COMMITTER_DATE": FIXTURE_DATE}
    p = subprocess.run(["git", *env_cfg, *args], cwd=cwd, capture_output=True, text=True, env=env)
    return CmdResult(p.returncode, (p.stdout + p.stderr).strip())


def materialize(fixture: Fixture, dest: Path) -> Path:
    """Copy fixture repo/ to `dest` and make it a git repo with one deterministic commit."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(fixture.repo_dir, dest)
    for step in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "fixture baseline"]):
        r = git(step, dest)
        if not r.ok:
            raise RuntimeError(f"materialize {fixture.name}: git {step[0]} failed: {r.output}")
    return dest


def overlay(src: Path, dest: Path) -> list[str]:
    """Copy every file under `src` onto `dest`, returning the relative paths written."""
    written = []
    for f in sorted(src.rglob("*")):
        if f.is_file():
            rel = f.relative_to(src)
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest / rel)
            written.append(str(rel))
    return written


@dataclass(frozen=True)
class DiffStat:
    files: list[str]
    lines: int


def diff_stat(worktree: Path) -> DiffStat:
    """Everything changed since the baseline commit, including untracked files."""
    git(["add", "-A"], worktree)
    r = git(["diff", "--cached", "--numstat", "HEAD"], worktree)
    files, lines = [], 0
    for row in r.output.splitlines():
        added, deleted, name = row.split("\t", 2)
        files.append(name)
        lines += (int(added) if added.isdigit() else 0) + (int(deleted) if deleted.isdigit() else 0)
    git(["reset", "-q"], worktree)
    return DiffStat(files, lines)
