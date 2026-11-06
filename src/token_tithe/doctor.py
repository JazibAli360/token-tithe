"""`donate doctor`: verify primitives and self-containment (S6), and scan for leaks (R6).

Each check returns PASS / WARN / FAIL / INFO. Any FAIL makes doctor exit non-zero.
WARN means "fine for dry-run/simulate, blocks live mode".
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from token_tithe import paths
from token_tithe.policy import PolicyError, load_agents, load_policy, load_repos
from token_tithe.secrets_scan import iter_text_files, scan_lines, scan_tree

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


# --------------------------------------------------------------------------- layout

# Everything allowed at the repo root. Anything else is a stray that will get lost:
# it belongs in one of the self-contained folders (see README "Repository map").
ROOT_ALLOWED = frozenset(
    {
        # entry files tools require at the root
        "README.md", "CLAUDE.md", "AGENTS.md", "LICENSE", "Makefile", "pyproject.toml",
        ".gitignore",
        # self-contained folders
        "src", "tests", "eval", "config", "templates", "docs", "brain", "container",
        "scripts",
        # tool-convention folders
        ".github", ".devcontainer", ".claude", ".cursor", ".gemini",
        # generated / local-only (gitignored)
        ".git", ".venv", "state", ".pytest_cache", ".ruff_cache", ".DS_Store",
    }
)  # fmt: skip


def stray_root_entries(root: Path = paths.ROOT) -> list[str]:
    return sorted(
        p.name
        for p in root.iterdir()
        if p.name not in ROOT_ALLOWED and not p.name.endswith(".egg-info")
    )


# --------------------------------------------------------------------------- scanners

# Code/config that must stay free of absolute paths (S2). container/, .devcontainer/ and
# .github/ describe the container's own filesystem, so absolute paths there are legitimate.
ABS_SCAN_TARGETS = ("src", "config", "templates", "eval", "tests", "Makefile", "pyproject.toml")
# Built from fragments so this file does not flag itself.
_S = "/"
ABS_PATH_RE = re.compile(
    r"(?<![\w.$}])("
    + "|".join(
        re.escape(x)
        for x in (
            _S + "Users" + _S,
            _S + "home" + _S,
            _S + "root" + _S,
            _S + "private" + _S + "var",
            _S + "var" + _S + "folders",
            "C:" + "\\",
            "~" + _S,
        )
    )
    + r")"
)


def scan_absolute_paths(root: Path = paths.ROOT) -> list[str]:
    """S2: return `file:line` for every absolute or home-relative path in code/config."""

    def match(line: str) -> str | None:
        m = ABS_PATH_RE.search(line)
        return f"absolute path {m.group(1)!r}" if m else None

    return scan_lines(iter_text_files(root, ABS_SCAN_TARGETS), root, match)


def scan_secrets(root: Path = paths.ROOT, env: Mapping[str, str] | None = None) -> list[str]:
    """R6: return `file:line (kind)` for anything that looks like a secret. Never the value.

    Scans the whole repo including state/. eval/fixtures is excluded: it deliberately
    contains fake planted keys for the D4 secrets-scan tests.
    """
    env = os.environ if env is None else env
    # Third-party clones are not our secrets (they often ship fake test keys); R6 is about ours.
    third_party = (root / "eval" / "fixtures", root / "state" / "worktrees", root / "state" / "sim")
    return scan_tree(root, env=env, exclude=third_party)


# --------------------------------------------------------------------------- checks


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 15) -> tuple[int, str]:
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return out.returncode, (out.stdout + out.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def check_python() -> Check:
    v = sys.version_info
    ok = v >= (3, 11)
    return Check("python >= 3.11", PASS if ok else FAIL, f"{v.major}.{v.minor}.{v.micro}")


def check_venv(root: Path = paths.ROOT) -> Check:
    venv = (root / ".venv").resolve()
    prefix = Path(sys.prefix).resolve()
    if prefix == venv:
        return Check("in-repo venv (S3)", PASS, ".venv")
    return Check(
        "in-repo venv (S3)",
        FAIL,
        "not running from .venv — run `make setup`, then use .venv/bin/donate",
    )


def check_package_location(root: Path = paths.ROOT) -> Check:
    import token_tithe

    pkg = Path(token_tithe.__file__).resolve()
    if pkg.is_relative_to((root / "src").resolve()):
        return Check("package from repo src/", PASS, "src/token_tithe")
    return Check("package from repo src/", FAIL, "token_tithe imported from outside the repo")


def check_primitive(binary: str, version_args: list[str], required: bool = True) -> Check:
    path = shutil.which(binary)
    if not path:
        return Check(f"{binary} installed", FAIL if required else WARN, "not found on PATH")
    _, out = _run([binary, *version_args])
    first = out.splitlines()[0] if out else "found"
    return Check(f"{binary} installed", PASS, first[:70])


def check_docker(allow_unsandboxed: bool) -> Check:
    if not shutil.which("docker"):
        status = INFO if allow_unsandboxed else WARN
        return Check("docker (sandbox, R16)", status, "not installed — required for --live runs")
    code, _ = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
    if code != 0:
        return Check("docker (sandbox, R16)", WARN, "daemon not running — required for --live runs")
    return Check("docker (sandbox, R16)", PASS, "daemon reachable")


def check_github_token(env: Mapping[str, str]) -> Check:
    if env.get("GITHUB_TOKEN"):
        return Check("GITHUB_TOKEN (env only, R6)", PASS, "set (value never printed or stored)")
    return Check("GITHUB_TOKEN (env only, R6)", WARN, "unset — needed for discovery and --live")


def check_network() -> Check:
    try:
        import httpx

        r = httpx.get("https://api.github.com/zen", timeout=5)
        if r.status_code < 500:
            return Check("network: api.github.com", PASS, f"HTTP {r.status_code}")
        return Check("network: api.github.com", WARN, f"HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001 - any failure is just "offline"
        return Check(
            "network: api.github.com",
            WARN,
            f"unreachable ({type(e).__name__}) — simulate still works",
        )


def check_config() -> tuple[list[Check], object | None]:
    checks: list[Check] = []
    policy = None
    try:
        policy = load_policy()
        checks.append(Check("config/policy.yaml", PASS, "valid"))
    except PolicyError as e:
        checks.append(Check("config/policy.yaml", FAIL, str(e)))
    try:
        repos = load_repos()
        checks.append(Check("config/repos.yaml", PASS, f"{len(repos.blocklist)} blocklisted"))
    except PolicyError as e:
        checks.append(Check("config/repos.yaml", FAIL, str(e)))
    try:
        agents = load_agents()
        checks.append(Check("config/agents/*.yaml", PASS, ", ".join(agents)))
        if policy is not None:
            routed = {
                policy.routing.default,
                *policy.routing.by_language.values(),
                *policy.routing.by_donation_type.values(),
            }
            missing = sorted(a for a in routed if a not in agents or a == "generic")
            if missing:
                checks.append(
                    Check(
                        "routing targets", FAIL, f"routed to unknown/template agent(s): {missing}"
                    )
                )
            else:
                checks.append(Check("routing targets", PASS, ", ".join(sorted(routed))))
        for name, a in agents.items():
            if name == "generic":
                continue
            installed = "installed" if shutil.which(a.binary) else "not installed"
            acct = "real usage" if a.accounted else "UNACCOUNTED (review mode only)"
            cap = "quota probe" if a.capacity_aware else "capacity-blind (review mode only)"
            checks.append(Check(f"agent: {name}", INFO, f"{installed}; {acct}; {cap}"))
    except PolicyError as e:
        checks.append(Check("config/agents/*.yaml", FAIL, str(e)))
    return checks, policy


def check_signing(policy, env: Mapping[str, str]) -> Check:
    """R18/D7: actually sign a throwaway commit with the configured identity."""
    name = "commit signing (R18)"
    s = policy.signing
    if not s.required:
        return Check(name, INFO, "signing.required is false")
    missing = [k for k in ("committer_name", "committer_email") if not getattr(s, k)]
    key = env.get(s.signing_key_env, "")
    if missing or not key:
        what = [f"signing.{m}" for m in missing] + ([f"${s.signing_key_env}"] if not key else [])
        return Check(name, WARN, f"not configured ({', '.join(what)}) — required for --live")
    paths.ensure_state()
    with tempfile.TemporaryDirectory(dir=paths.STATE, prefix="doctor-sign-") as tmp:
        gitc = ["git"]
        for opt in (f"user.name={s.committer_name}", f"user.email={s.committer_email}",
                    f"gpg.format={s.format}", f"user.signingkey={key}"):  # fmt: skip
            gitc += ["-c", opt]
        code, _ = _run(["git", "init", "-q"], cwd=Path(tmp))
        if code == 0:
            code, out = _run(
                [*gitc, "commit", "-q", "--allow-empty", "-S", "-m", "doctor"], cwd=Path(tmp)
            )
        if code != 0:
            return Check(name, WARN, "signing failed — check the key in $" + s.signing_key_env)
    return Check(name, PASS, f"{s.format} signature as {s.committer_email}")


def check_layout(root: Path = paths.ROOT) -> Check:
    strays = stray_root_entries(root)
    if strays:
        return Check("root layout", FAIL, f"unexpected at repo root: {strays} — move into a folder")
    return Check("root layout", PASS, "only entry files + self-contained folders")


def check_gitignore(root: Path = paths.ROOT) -> Check:
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.is_file() else []
    missing = [x for x in ("state/", ".venv/") if x not in lines]
    if missing:
        return Check("state/ + .venv/ gitignored (S4)", FAIL, f"missing from .gitignore: {missing}")
    return Check("state/ + .venv/ gitignored (S4)", PASS, "yes")


def check_abs_paths(root: Path = paths.ROOT) -> Check:
    hits = scan_absolute_paths(root)
    if hits:
        more = f" (+{len(hits) - 5} more)" if len(hits) > 5 else ""
        return Check("no absolute paths (S2)", FAIL, "; ".join(hits[:5]) + more)
    return Check("no absolute paths (S2)", PASS, "code + config clean")


def check_secrets(root: Path = paths.ROOT, env: Mapping[str, str] | None = None) -> Check:
    hits = scan_secrets(root, env)
    if hits:
        more = f" (+{len(hits) - 5} more)" if len(hits) > 5 else ""
        return Check("no secrets on disk (R6)", FAIL, "; ".join(hits[:5]) + more)
    return Check("no secrets on disk (R6)", PASS, "repo + state/ clean")


def run_doctor(offline: bool = False, env: Mapping[str, str] | None = None) -> list[Check]:
    env = os.environ if env is None else env
    checks = [
        check_python(),
        check_venv(),
        check_package_location(),
        check_primitive("git", ["--version"]),
        check_primitive("gh", ["--version"]),
    ]
    config_checks, policy = check_config()
    checks += config_checks
    checks.append(check_docker(policy.sandbox.allow_unsandboxed if policy else False))
    checks.append(check_github_token(env))
    if not offline:
        checks.append(check_network())
    if policy is not None:
        checks.append(check_signing(policy, env))
    checks += [check_layout(), check_gitignore(), check_abs_paths(), check_secrets(env=env)]
    return checks


def render(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    icon = {PASS: "✓", WARN: "!", FAIL: "✗", INFO: "·"}
    lines = [f" {icon[c.status]} {c.status:<4}  {c.name:<{width}}  {c.detail}" for c in checks]
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    verdict = "FAIL" if fails else "PASS"
    lines.append("")
    lines.append(f" doctor: {verdict} — {fails} fail, {warns} warn (warnings block --live only)")
    return "\n".join(lines)
