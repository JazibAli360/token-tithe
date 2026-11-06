"""Detect how a repository installs, tests and lints itself (R7).

The engine re-runs these independently of the agent: an agent saying "tests pass" is never
accepted on its own. Agent-reported commands are added only if their program is allowlisted.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_PROGRAMS = {
    "python", "python3", "pytest", "tox", "nox", "npm", "npx", "pnpm", "yarn", "node", "cargo",
    "go", "make", "ruff", "flake8", "mypy", "eslint", "tsc", "deno", "bun",
}  # fmt: skip
PYTEST = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
# Which toolchain a changed file needs to be verified (by suffix, or by exact file name).
_ECOSYSTEM_FILES = {
    "go": ((".go",), ("go.mod", "go.sum", "go.work")),
    "rust": ((".rs",), ("Cargo.toml", "Cargo.lock")),
    "python": ((".py",), ("pyproject.toml", "setup.py", "setup.cfg")),
    "node": ((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"), ("package.json", "package-lock.json")),
}
# How a runner reports that the program itself is absent (host exec, `docker run`, a shell).
MISSING_PROGRAM = re.compile(
    r"No such file or directory|executable file not found|command not found"
)


@dataclass
class Profile:
    ecosystems: list[str] = field(default_factory=list)
    install: list[list[list[str]]] = field(default_factory=list)  # alternatives per step
    test: list[list[str]] = field(default_factory=list)
    lint: list[list[str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.test


# a browser-test dependency: Playwright itself, or a runner that drives it
_PLAYWRIGHT = re.compile(
    r'"(?:@playwright/test|playwright|playwright-core|@vitest/browser-playwright)"\s*:'
)


NODE_TEST_SCRIPTS = ("test:unit", "test-unit", "test:node", "test-node")


def _browser_suite(w: Path, test_script: str) -> bool:
    """True when the default test script drives a real browser."""
    if re.search(r"\b(cypress|playwright test|wdio|karma|xvfb-run)\b", test_script):
        return True
    configs = [*w.glob("vitest.config.*"), *w.glob("vitest.workspace.*"), *w.glob("vite.config.*")]
    return any(re.search(r"@vitest/browser|browser:\s*\{", _read(c)) for c in configs)


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _pin(pyproject: dict, name: str) -> str:
    """The requirement for `name` as the repo declares it in a dependency group or optional
    dependency (`ruff>=0.17.3`), else the bare name."""
    pools = [*(pyproject.get("dependency-groups") or {}).values(),
             *((pyproject.get("project") or {}).get("optional-dependencies") or {}).values()]  # fmt: skip
    for req in (r for pool in pools for r in pool if isinstance(r, str)):
        if re.match(rf"{re.escape(name)}(?![\w.-])", req.strip(), re.I):
            return req.strip()
    return name


def detect(worktree: Path) -> Profile:
    prof = Profile()
    w = worktree

    has_py = any((w / f).exists() for f in ("pyproject.toml", "setup.py", "setup.cfg")) or any(
        w.glob("requirements*.txt")
    )
    if has_py or any(w.glob("tests/test_*.py")) or any(w.glob("test_*.py")):
        prof.ecosystems.append("python")
        pyproject = {}
        if (w / "pyproject.toml").is_file():
            try:
                pyproject = tomllib.loads(_read(w / "pyproject.toml"))
            except tomllib.TOMLDecodeError:
                pyproject = {}
        extras = (pyproject.get("project") or {}).get("optional-dependencies") or {}
        wanted = [e for e in ("test", "tests", "dev") if e in extras]
        steps = []
        if has_py and ((pyproject.get("project") or {}).get("dependencies") or extras):
            target = f".[{','.join(wanted)}]" if wanted else "."
            steps.append([["python", "-m", "pip", "install", "-q", "-e", target]])
        # PEP 735 dependency groups hold the test plugins a pytest config may require
        # (ManimCommunity/manim: pytest-cov, pytest-xdist); pip installs them from 25.1 on
        groups = [
            g for g in ("test", "tests", "dev") if g in (pyproject.get("dependency-groups") or {})
        ]
        if groups:
            steps.append([["python", "-m", "pip", "install", "-q", "-U", "pip>=25.1"]])
            steps.append([["python", "-m", "pip", "install", "-q", "--group", groups[0]]])
        for req in sorted(w.glob("requirements*dev*.txt")) + sorted(
            w.glob("requirements*test*.txt")
        ):
            steps.append([["python", "-m", "pip", "install", "-q", "-r", req.name]])
        # the engine always runs pytest, and repos rarely declare it as a runtime dependency
        steps.append([["python", "-m", "pip", "install", "-q", "pytest"]])
        prof.install += steps
        prof.test.append(PYTEST)
        tool = pyproject.get("tool") or {}
        if "ruff" in tool or (w / "ruff.toml").is_file() or (w / ".ruff.toml").is_file():
            prof.lint.append(["python", "-m", "ruff", "check", "."])
            # the engine runs the linter, so it installs it, at the repo's pin if it has one
            # (tBuLi/kingdon lists ruff only in its `dev` group, which is not installed)
            prof.install.append([["python", "-m", "pip", "install", "-q", _pin(pyproject, "ruff")]])
        elif (w / ".flake8").is_file() or "[flake8]" in _read(w / "setup.cfg"):
            prof.lint.append(["python", "-m", "flake8"])

    if (w / "package.json").is_file():
        prof.ecosystems.append("node")
        try:
            pkg = json.loads(_read(w / "package.json"))
        except ValueError:
            pkg = {}
        scripts = pkg.get("scripts") or {}
        deps = pkg.get("dependencies") or pkg.get("devDependencies")
        pnpm = (w / "pnpm-lock.yaml").is_file()
        yarn = not pnpm and (w / "yarn.lock").is_file()
        if deps:
            # a bun lockfile is the source of truth: a stale package-lock.json next to it
            # makes `npm ci` refuse (ChartGPU/ChartGPU), and installs never rewrite a lockfile
            # --ignore-scripts fallback: a repo whose `prepare` build is broken on main
            # (ecomfe/zrender) can still install and run its tests
            npm_ci = [["npm", "ci"], ["npm", "ci", "--ignore-scripts"]]
            if pnpm:
                # `workspace:` dependencies (galacean/engine) do not install with npm at all
                prof.install.append([["pnpm", "install", "--frozen-lockfile"],
                                     ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"]])  # fmt: skip
            elif yarn:
                # yarn 1 takes --frozen-lockfile, berry --immutable; corepack picks the version
                prof.install.append([["yarn", "install", "--frozen-lockfile"],
                                     ["yarn", "install", "--immutable"]])  # fmt: skip
            elif (w / "bun.lock").is_file() or (w / "bun.lockb").is_file():
                prof.install.append([["bun", "install", "--frozen-lockfile"], *npm_ci])
            elif (w / "package-lock.json").is_file():
                prof.install.append(npm_ci)
                # npm ci can skip this platform's native optional packages even when the lockfile
                # lists them (npm/cli#4828: @rollup/rollup-linux-arm64-gnu in Tonejs/Tone.js);
                # the image's helper installs exactly those, without touching the lockfile
                prof.install.append([["node", "/opt/tithe/fix-optional.js"]])
            else:
                prof.install.append([["npm", "install", "--no-audit", "--no-fund"]])
            if _PLAYWRIGHT.search(_read(w / "package.json")):
                # the browser build must match the repo's own playwright version, so it is
                # fetched in the networked install step into PLAYWRIGHT_BROWSERS_PATH (sandbox.py);
                # the image already has its system libraries, so no --with-deps (needs root)
                exec_ = ["pnpm", "exec"] if pnpm else ["yarn"] if yarn else ["npx", "--no-install"]
                prof.install.append([[*exec_, "playwright", "install", "chromium"]])
        runner = "pnpm" if pnpm else "yarn" if yarn else None
        test = scripts.get("test", "")
        node_only = next((n for n in NODE_TEST_SCRIPTS if n in scripts), None)
        if node_only and _browser_suite(w, test):
            # the default suite needs a real browser/display the sandbox lacks: e2e runners as
            # `test` (katspaugh/wavesurfer.js) or a vitest browser project (Vanilagy/mediabunny);
            # verify with the repo's own node-only script instead
            test = node_only
        # pnpm skips pre/post scripts: a `pretest` that runs `playwright install --with-deps`
        # needs root and network, which offline test runs do not have
        if test and not re.search(r"no test specified", test):
            if test in NODE_TEST_SCRIPTS:
                prof.test.append([runner or "npm", "run", test])
            else:
                prof.test.append([runner, "test"] if runner else ["npm", "test", "--silent"])
        if "lint" in scripts:
            prof.lint.append(
                [runner, "run", "lint"] if runner else ["npm", "run", "lint", "--silent"]
            )

    if (w / "Cargo.toml").is_file():
        prof.ecosystems.append("rust")
        if any((w / f).is_file() for f in ("rust-toolchain", "rust-toolchain.toml")):
            # installs the pinned channel, components and targets from the toolchain file
            prof.install.append(
                [["rustup", "toolchain", "install", "--profile", "minimal", "--no-self-update"]]
            )
        prof.install.append([["cargo", "fetch"]])
        prof.test.append(["cargo", "test", "-q"])
        prof.lint.append(["cargo", "clippy", "--all-targets", "-q", "--", "-D", "warnings"])

    if (w / "go.mod").is_file():
        prof.ecosystems.append("go")
        prof.install.append([["go", "mod", "download"]])
        prof.test.append(["go", "test", "./..."])
        prof.lint.append(["go", "vet", "./..."])

    if not prof.test and re.search(r"^test:", _read(w / "Makefile"), re.M):
        prof.test.append(["make", "test"])
    # Git LFS test assets (e.g. snapshot images) are pointer files after a plain clone; fetch
    # them in the networked install step so the offline tests compare against the real files.
    if prof.test and re.search(r"\bfilter=lfs\b", _read(w / ".gitattributes")):
        prof.install.insert(0, [["git", "lfs", "pull"]])
    return prof


def nested_go_modules(worktree: Path, files: list[str]) -> Profile:
    """Install/test/lint for the non-root Go modules the changed files live in.

    The root profile's `go test ./...` never descends into another module, so a change under
    e.g. assets/scripts/go.mod is only verified if that module is run on its own (`go -C`).
    """
    prof, dirs = Profile(), []
    for f in files:
        parent = Path(f).parent
        for d in (parent, *parent.parents):
            if d == Path(".") or {"testdata", "vendor"} & set(d.parts):
                break
            if (worktree / d / "go.mod").is_file():
                if d.as_posix() not in dirs:
                    dirs.append(d.as_posix())
                break
    for d in dirs:
        prof.install.append([["go", "-C", d, "mod", "download"]])
        prof.test.append(["go", "-C", d, "test", "./..."])
        prof.lint.append(["go", "-C", d, "vet", "./..."])
    if dirs:
        prof.ecosystems.append("go")
    return prof


def ecosystems_of(files: list[str]) -> set[str]:
    """Ecosystems whose toolchain a set of changed files needs (go.mod in a subdir counts)."""
    found = set()
    for f in files:
        name = Path(f).name
        for eco, (suffixes, names) in _ECOSYSTEM_FILES.items():
            if name.endswith(suffixes) or name in names:
                found.add(eco)
    return found


def missing_program(code: int, output: str) -> bool:
    """True when a command failed because its program is not installed, not because it ran."""
    if code == 127 and MISSING_PROGRAM.search(output):
        return True
    # the sandbox image's node entrypoint runs a program missing from PATH as `node <prog>`
    return code != 0 and bool(_NODE_FALLBACK.search(output))


_NODE_FALLBACK = re.compile(
    r"Cannot find module '/work/[^/'\s]+'\n(?:.*\n){0,12}?.*requireStack: \[\]"
)


_FILE_ARG = re.compile(r"^\./|\.(?:go|py|js|mjs|cjs|ts|tsx|rs)$")


def merge_agent_commands(
    prof: Profile, commands: list[list[str]], root: Path | None = None
) -> list[str]:
    """Add allowlisted agent-reported test commands; return the rejected ones. With `root`,
    a command naming a file that is not in the worktree (an agent's deleted scratch test)
    is rejected too: it cannot verify anything."""
    rejected = []
    for cmd in commands:
        prog = Path(cmd[0]).name
        missing = root is not None and any(
            _FILE_ARG.search(a) and not (root / a).exists()
            for a in cmd[1:]
            if a[:1] != "-" and "..." not in a  # `./...` is a Go package pattern
        )
        if prog not in ALLOWED_PROGRAMS:
            rejected.append(" ".join(cmd))
        elif missing:
            rejected.append(" ".join(cmd) + " (names a file not in the change)")
        elif cmd not in prof.test:
            prof.test.append(cmd)
    return rejected
