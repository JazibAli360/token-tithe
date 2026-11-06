"""Where untrusted code runs (R16, D1).

Running a repo's tests is arbitrary code execution (conftest.py, setup.py, build scripts),
and the agent reads attacker-controllable text. So in LIVE mode every agent invocation and
every install/test/lint run happens in a throwaway Docker container:

- fresh container per command, removed afterwards (`--rm`), only the worktree mounted
- tests / lint / build: `--network none`
- dependency install: network, through `sandbox.install_proxy` when configured
- agent: `sandbox.agent_network` (the agent must reach its model API; Docker cannot allowlist
  hosts by itself — route through an egress proxy for that, see docs/SAFETY.md)
- all capabilities dropped, no-new-privileges, CPU/memory/pids limits, non-root user

`LocalRunner` (no isolation) is allowed for simulate/eval (fixtures are trusted, in-repo),
for review mode when Docker is unavailable, and for live runs only when policy sets
`allow_unsandboxed: true` — which is logged loudly. Whatever runs on the host gets a scrubbed
environment (`host_env`): no tokens, keys or SSH agent, and no gh/git credential helpers, so a
target repo's test script cannot reach the operator's GitHub account or API keys.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from token_tithe import paths
from token_tithe.policy import Policy

Purpose = Literal["agent", "install", "test"]

VENV_DIR = ".tithe-venv"
# credentials an agent CLI can use inside a container (keychain logins cannot get there)
AGENT_AUTH_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
# Engine-owned paths inside a worktree; added to .git/info/exclude so they never reach a diff.
RUSTUP_DIR = ".tithe-rustup"  # a repo-pinned Rust toolchain, installed in the install step
SCRATCH_PATHS = (".tithe/", f"{VENV_DIR}/", ".tithe-cargo/", f"{RUSTUP_DIR}/", ".tithe-go/", ".tithe-npm/",
                 ".tithe-pnpm/", ".tithe-playwright/", ".tithe-corepack/", ".tithe-yarn/", "node_modules/", "target/")  # fmt: skip


def rust_pinned(worktree: Path) -> bool:
    return any((worktree / f).is_file() for f in ("rust-toolchain", "rust-toolchain.toml"))


class SandboxUnavailable(RuntimeError):
    """Live mode needs Docker (R16) and it is missing or not running."""


class UnsandboxedWarning(UserWarning):
    pass


@dataclass(frozen=True)
class RunResult:
    code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return (self.stdout + self.stderr)[-6000:]


class Runner(Protocol):
    name: str
    isolated: bool

    def run(
        self,
        argv: list[str],
        cwd: Path,
        purpose: Purpose,
        timeout: float,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> RunResult: ...


def _execute(argv, cwd, timeout, env, stdin) -> RunResult:
    try:
        p = subprocess.run(
            argv, cwd=cwd, input=stdin, capture_output=True, text=True,
            timeout=max(1.0, timeout), env=env,
        )  # fmt: skip
        return RunResult(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return RunResult(124, out, f"timed out after {timeout:.0f}s", timed_out=True)
    except FileNotFoundError as e:
        return RunResult(127, "", str(e))


# Env var names that carry credentials. Host runs drop them; the agent gets back only the
# keys its adapter lists in `env_passthrough` (passed explicitly as `env`).
SECRET_ENV_RE = re.compile(
    r"TOKEN|SECRET|PASSW|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIAL|SESSION|COOKIE|^SSH_AUTH_SOCK$"
    r"|^TITHE_|^AWS_|^AZURE_|^GOOGLE_APPLICATION|^NPM_CONFIG__AUTH|^NODE_AUTH",
    re.IGNORECASE,
)


def host_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for anything that runs on the host: secrets removed, gh/git logins hidden."""
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV_RE.search(k)}
    empty_gh = paths.STATE / "host-gh-config"  # an empty gh config: `gh` sees no login
    empty_gh.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "GH_CONFIG_DIR": str(empty_gh),
            "GIT_TERMINAL_PROMPT": "0",
            # an empty credential.helper resets the list: no keychain-stored git passwords
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "NO_COLOR": "1",
        }
    )
    env.update(extra or {})
    return env


class LocalRunner:
    """No isolation. `python` in argv[0] maps to the engine interpreter (fixtures need no deps)."""

    name = "local"
    isolated = False

    def run(self, argv, cwd, purpose, timeout, env=None, stdin=None) -> RunResult:
        if argv and argv[0] in ("python", "python3", "{python}"):
            argv = [sys.executable, *argv[1:]]
        return _execute(argv, cwd, timeout, host_env(env), stdin)


class DockerRunner:
    """One `docker run --rm` per command; only the worktree is mounted."""

    name = "docker"
    isolated = True

    def __init__(self, policy: Policy, docker: str = "docker"):
        self.policy = policy
        self.docker = docker

    def command(
        self, argv: list[str], cwd: Path, purpose: Purpose, env: dict[str, str] | None
    ) -> list[str]:
        sb = self.policy.sandbox
        network = {"test": sb.network, "install": "bridge", "agent": sb.agent_network}[purpose]
        cmd = [
            self.docker, "run", "--rm", "--init",
            "--network", network,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            # 4 CPUs: real-time browser tests (Tone.js onended timing) miss their windows on 2
            "--cpus", "4", "--memory", "4g", "--pids-limit", "512",
            # Chromium keeps shared memory in /dev/shm; Docker's 64 MB default crashes the browser
            # mid-suite ("browser disconnected" in Tonejs/Tone.js)
            "--shm-size", "1g",
            "--user", f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "1000:1000",
            "-e", "HOME=/tmp/home", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "NO_COLOR=1",
            # every container is fresh: keep dependency caches inside the mounted worktree
            "-e", "CARGO_HOME=/work/.tithe-cargo", "-e", "GOMODCACHE=/work/.tithe-go",
            "-e", "GOPATH=/work/.tithe-go", "-e", "GOCACHE=/work/.tithe-go/.build-cache",
            # git metadata is not usable in the container; `go build` would fail on VCS stamping
            "-e", "GOFLAGS=-buildvcs=false",
            "-e", "npm_config_cache=/work/.tithe-npm", "-e", "npm_config_store_dir=/work/.tithe-pnpm",
            # browsers fetched by the install step (toolchain.py); tests have no display, so
            # runners that read CI or HEADLESS (vitest browser mode, galacean/engine) go headless
            "-e", "PLAYWRIGHT_BROWSERS_PATH=/work/.tithe-playwright",
            "-e", "COREPACK_HOME=/work/.tithe-corepack", "-e", "COREPACK_ENABLE_DOWNLOAD_PROMPT=0",
            "-e", "YARN_CACHE_FOLDER=/work/.tithe-yarn",
            # the bind mount shows another owner, so git refuses /work as "dubious ownership";
            # repos whose tests run git (BabylonJS/Babylon.js lint tooling) need it trusted
            "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory",
            "-e", "GIT_CONFIG_VALUE_0=/work",
            "-e", "CI=true", "-e", "HEADLESS=true",
            # a pinned toolchain (rust-toolchain file) is installed into the worktree at install
            # time, since test runs have no network to fetch it (emilk/egui pins 1.95.0)
            *(["-e", f"RUSTUP_HOME=/work/{RUSTUP_DIR}"] if rust_pinned(cwd) else []),
            "-v", f"{cwd.resolve()}:/work", "-w", "/work",
        ]  # fmt: skip
        if purpose == "install" and sb.install_proxy:
            cmd += ["-e", f"HTTPS_PROXY={sb.install_proxy}", "-e", f"HTTP_PROXY={sb.install_proxy}"]
        for key in env or {}:
            cmd += ["-e", key]  # value passed via the docker process env, never on argv (R6)
        return [*cmd, sb.image, *argv]

    def run(self, argv, cwd, purpose, timeout, env=None, stdin=None) -> RunResult:
        # the venv's python is a symlink to a container path (/usr/bin/python) that may not
        # exist on the host, so test for the link itself, not its target
        if (
            argv
            and argv[0] in ("python", "python3")
            and os.path.lexists(cwd / VENV_DIR / "bin" / "python")
        ):
            argv = [f"/work/{VENV_DIR}/bin/python", *argv[1:]]
        # a tool the repo installed into its venv (`ruff check .`): the image's node entrypoint
        # would otherwise run an unknown program as `node ruff` (tBuLi/kingdon#152)
        elif argv and "/" not in argv[0] and os.path.lexists(cwd / VENV_DIR / "bin" / argv[0]):
            argv = [f"/work/{VENV_DIR}/bin/{argv[0]}", *argv[1:]]
        cmd = self.command(argv, cwd, purpose, env)
        proc_env = {**os.environ, **(env or {})}
        return _execute(cmd, cwd, timeout, proc_env, stdin)


class ReviewRunner:
    """Review mode with Docker: the agent runs on the host, where its CLI login lives (keychain
    logins do not reach containers), while install/test/lint run in the sandbox image, so the
    proposal is verified with the repo's real toolchain."""

    name = "docker (install/test) + host agent"
    isolated = True
    agent_on_host = True  # the executor reinstalls in the sandbox after the agent runs

    def __init__(self, policy: Policy):
        self.docker = DockerRunner(policy)
        self.host = LocalRunner()

    def run(self, argv, cwd, purpose, timeout, env=None, stdin=None) -> RunResult:
        runner = self.host if purpose == "agent" else self.docker
        return runner.run(argv, cwd, purpose, timeout, env=env, stdin=stdin)


def docker_available(docker: str = "docker") -> bool:
    if not shutil.which(docker):
        return False
    try:
        r = subprocess.run([docker, "info"], capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def select_runner(policy: Policy, live: bool, docker_ok: bool | None = None) -> Runner:
    """R16: live → Docker, or refuse unless policy explicitly allows unsandboxed (logged).
    Review mode prefers Docker too, so proposals are verified with the repo's toolchain; it
    falls back to the host only when Docker is unavailable."""
    if docker_ok is None:
        docker_ok = docker_available()
    if docker_ok:
        # an agent run on the host leaves host-built files (macOS native packages, generated
        # sources) that break verification in the sandbox, so the host agent is only the
        # fallback for when no agent credential can be passed into the container
        if live or any(os.environ.get(k) for k in AGENT_AUTH_ENV):
            return DockerRunner(policy)
        return ReviewRunner(policy)
    if not live:
        return LocalRunner()
    if policy.sandbox.allow_unsandboxed:
        warnings.warn(
            "LIVE RUN WITHOUT SANDBOX: untrusted repository code will execute directly on this "
            "machine (policy sandbox.allow_unsandboxed=true). This is logged in the ledger.",
            UnsandboxedWarning,
            stacklevel=2,
        )
        return LocalRunner()
    raise SandboxUnavailable(
        "live mode requires Docker for the sandbox (R16): install/start Docker and build the "
        "image with `make sandbox-image`, or (not recommended) set sandbox.allow_unsandboxed"
    )
