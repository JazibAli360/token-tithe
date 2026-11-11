"""Secret detection shared by `donate doctor` (R6) and the target-repo scan (D4).

Findings are reported as `file:line (kind)` and NEVER include the matched value.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules",
    "target", "dist", "build", ".tox",
}  # fmt: skip
TEXT_SUFFIXES = {
    ".py", ".yaml", ".yml", ".toml", ".json", ".md", ".txt", ".cfg", ".ini", ".sh", ".js",
    ".ts", ".rs", ".go", ".env", "", ".lock", ".mdc", ".properties", ".conf", ".tf", ".rb",
    ".java", ".kt", ".php", ".cs", ".swift", ".jsx", ".tsx", ".mjs", ".cjs", ".xml",
}  # fmt: skip
MAX_FILE_BYTES = 1_000_000

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "github fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    "anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "private key": re.compile("-----BEGIN [A-Z ]*" + "PRIVATE KEY-----"),
}
SECRET_ENV_VARS = (
    "GITHUB_TOKEN", "GH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)  # fmt: skip


def secret_kind(text: str) -> str | None:
    """The kind of the first secret-looking token in `text`, or None. Never the value."""
    for kind, rx in SECRET_PATTERNS.items():
        if rx.search(text):
            return kind
    return None


def iter_text_files(root: Path, targets: tuple[str, ...] | None = None) -> Iterator[Path]:
    starts = [root / t for t in targets] if targets else [root]
    for start in starts:
        if start.is_file():
            yield start
            continue
        if not start.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(start):
            # a nested checkout (e.g. a git worktree of this repo under .claude/) is its own tree
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not (Path(dirpath) / d / ".git").exists()]  # fmt: skip
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.suffix in TEXT_SUFFIXES or fn in ("Makefile", "Dockerfile"):
                    yield p


def scan_lines(files: Iterator[Path], root: Path, match: Callable[[str], str | None]) -> list[str]:
    hits = []
    for f in files:
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            what = match(line)
            if what:
                rel = f.relative_to(root) if f.is_relative_to(root) else f
                hits.append(f"{rel}:{n} ({what})")
    return hits


_TEST_PATH = re.compile(
    r"(?:^|/)(?:testdata|tests?|__tests__|fixtures?)/|_test\.go$|(?:^|/)test_[^/]*\.py$|"
    r"_test\.py$|\.(?:test|spec)\.[cm]?[jt]sx?$"
)


def is_test_path(rel: str) -> bool:
    """True for test code and test data (`x_test.go`, `testdata/`, `tests/`, `a.test.ts`, ...)."""
    return bool(_TEST_PATH.search(rel.replace("\\", "/")))


def scan_tree(
    root: Path, env: Mapping[str, str] | None = None, exclude: tuple[Path, ...] = ()
) -> list[str]:
    """Scan every text file under `root` for secret patterns and for the literal values of
    any secret env vars in `env`. Returns `file:line (kind)` only."""
    env = env or {}
    live_values = {k: v for k in SECRET_ENV_VARS if len(v := env.get(k, "")) >= 8}

    def files() -> Iterator[Path]:
        for f in iter_text_files(root):
            if not any(f.is_relative_to(x) for x in exclude):
                yield f

    def match(line: str) -> str | None:
        for name, value in live_values.items():
            if value in line:
                return f"value of ${name}"
        return secret_kind(line)

    return scan_lines(files(), root, match)
