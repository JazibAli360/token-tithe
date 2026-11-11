"""Phase 5: adapter contract, usage parsing (R10), R13 guards, sandbox (R16), templates, ledger."""

import json
import re
import warnings
from pathlib import Path

import pytest

from token_tithe import paths
from token_tithe import policy as P
from token_tithe.agents import get_adapter, read_summary
from token_tithe.agents.cli_adapter import CliAdapter, ForbiddenArgument
from token_tithe.ledger import Ledger, LedgerError
from token_tithe.sandbox import (
    VENV_DIR,
    DockerRunner,
    LocalRunner,
    ReviewRunner,
    RunResult,
    SandboxUnavailable,
    UnsandboxedWarning,
    select_runner,
)
from token_tithe.templates import TemplateError, render, render_text


class FakeRunner:
    name, isolated = "fake", False

    def __init__(self, stdout="", code=0):
        self.stdout, self.code, self.calls = stdout, code, []

    def run(self, argv, cwd, purpose, timeout, env=None, stdin=None):
        self.calls.append((argv, purpose, stdin))
        return RunResult(self.code, self.stdout, "")


CLAUDE_OUT = json.dumps({
    "type": "result", "result": "done", "total_cost_usd": 0.42,
    "usage": {"input_tokens": 100, "cache_creation_input_tokens": 20,
              "cache_read_input_tokens": 30, "output_tokens": 50},
    "modelUsage": {"claude-opus-5-5": {"inputTokens": 150}},
})  # fmt: skip
CODEX_OUT = "\n".join([
    json.dumps({"type": "thread.started"}),
    json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}}),
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 70, "output_tokens": 7}}),
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 30, "output_tokens": 3}}),
])  # fmt: skip


def test_claude_usage_is_real_and_summed(tmp_path):
    a = get_adapter("claude")
    r = a.invoke(tmp_path, ".tithe/task_brief.md", FakeRunner(CLAUDE_OUT), 10)
    u = a.report_usage()
    assert u.accounted and u.input_tokens == 150 and u.output_tokens == 50
    assert u.cost_estimate == 0.42 and u.model == "claude-opus-5-5" and r.final_text == "done"


def test_codex_jsonl_usage_and_stdin_prompt(tmp_path):
    a = get_adapter("codex")
    runner = FakeRunner(CODEX_OUT)
    r = a.invoke(tmp_path, ".tithe/task_brief.md", runner, 10)
    u = a.report_usage()
    assert (u.input_tokens, u.output_tokens) == (100, 10) and r.final_text == "hello"
    argv, purpose, stdin = runner.calls[0]
    assert purpose == "agent" and ".tithe/task_brief.md" in stdin and "--model" not in argv


def test_aider_is_unaccounted(tmp_path):
    a = get_adapter("aider")
    a.invoke(tmp_path, ".tithe/task_brief.md", FakeRunner("Tokens: 2.3k sent"), 10)
    assert not a.report_usage().accounted and not a.accounted


def test_garbage_output_is_unaccounted_not_invented(tmp_path):
    a = get_adapter("claude")
    a.invoke(tmp_path, ".tithe/task_brief.md", FakeRunner("not json at all"), 10)
    assert not a.report_usage().accounted


def test_r13_no_subagent_tools_and_forbidden_args():
    argv = get_adapter("claude").build_argv("p", "f")
    i = argv.index("--disallowedTools")
    assert {"Task", "Agent"} <= set(argv[i + 1 :])
    cfg = P.load_agent("claude")
    bad = CliAdapter(
        type(cfg)(
            **{
                **cfg.__dict__,
                "invoke": type(cfg.invoke)(
                    **{
                        **cfg.invoke.__dict__,
                        "args": [*cfg.invoke.args, "--dangerously-skip-permissions"],
                    }
                ),
            }
        )
    )
    with pytest.raises(ForbiddenArgument, match="R13"):
        bad.build_argv("p", "f")


def test_summary_validation(tmp_path):
    assert "no .tithe/summary.json" in read_summary(tmp_path).error
    (tmp_path / ".tithe").mkdir()
    (tmp_path / ".tithe/summary.json").write_text('{"confidence": 150}')
    assert "confidence" in read_summary(tmp_path).error
    (tmp_path / ".tithe/summary.json").write_text(
        '{"confidence": 80, "test_commands": [["python", "-m", "pytest"]], "refused": false}'
    )
    s = read_summary(tmp_path)
    assert s.confidence == 80 and not s.error


# ---------------------------------------------------------------- sandbox (R16)


def test_live_without_docker_refuses():
    with pytest.raises(SandboxUnavailable, match="R16"):
        select_runner(P.load_policy(), live=True, docker_ok=False)
    assert isinstance(select_runner(P.load_policy(), live=False, docker_ok=False), LocalRunner)
    assert isinstance(select_runner(P.load_policy(), live=True, docker_ok=True), DockerRunner)


def test_review_mode_runs_the_agent_in_the_sandbox_when_it_can_authenticate(monkeypatch):
    from token_tithe.sandbox import AGENT_AUTH_ENV

    for k in AGENT_AUTH_ENV:
        monkeypatch.delenv(k, raising=False)
    review = select_runner(P.load_policy(), live=False, docker_ok=True)
    assert isinstance(review, ReviewRunner) and review.isolated  # host agent: only fallback
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    assert isinstance(select_runner(P.load_policy(), live=False, docker_ok=True), DockerRunner)


def test_review_runner_keeps_agent_on_host_and_tests_in_docker(monkeypatch):
    calls = []
    monkeypatch.setattr(
        LocalRunner, "run", lambda self, argv, *a, **k: calls.append(("host", argv))
    )
    monkeypatch.setattr(
        DockerRunner, "run", lambda self, argv, *a, **k: calls.append(("docker", argv))
    )
    r = ReviewRunner(P.load_policy())
    r.run(["claude", "-p", "x"], Path("."), "agent", 10)
    r.run(["go", "test", "./..."], Path("."), "test", 10)
    r.run(["go", "mod", "download"], Path("."), "install", 10)
    assert [c[0] for c in calls] == ["host", "docker", "docker"]


def test_unsandboxed_override_is_loud(config_copy):
    import yaml

    f = config_copy / "policy.yaml"
    d = yaml.safe_load(f.read_text())
    d["sandbox"]["allow_unsandboxed"] = True
    f.write_text(yaml.safe_dump(d))
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert isinstance(select_runner(P.load_policy(f), live=True, docker_ok=False), LocalRunner)
    assert any(issubclass(x.category, UnsandboxedWarning) for x in w)


def test_docker_command_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-" + "ant-should-not-appear-on-argv-000000")
    d = DockerRunner(P.load_policy())
    test_cmd = d.command(["python", "-m", "pytest"], tmp_path, "test", None)
    agent_cmd = d.command(["claude", "-p", "x"], tmp_path, "agent", {"ANTHROPIC_API_KEY": "v"})
    assert test_cmd[test_cmd.index("--network") + 1] == "none"
    assert agent_cmd[agent_cmd.index("--network") + 1] == "bridge"
    for c in (test_cmd, agent_cmd):
        assert "--rm" in c and "ALL" in c and "no-new-privileges" in c
        mounts = [c[i + 1] for i, x in enumerate(c) if x == "-v"]
        assert mounts == [f"{tmp_path.resolve()}:/work"]
    assert "ANTHROPIC_API_KEY" in agent_cmd and not any("sk-ant" in x for x in agent_cmd)
    for cache in ("GOMODCACHE", "GOPATH", "GOCACHE", "CARGO_HOME", "npm_config_cache"):
        assert any(x.startswith(f"{cache}=/work/") for x in test_cmd), cache  # L-0007
    d.test_network = "bridge"  # an operator-listed repo (sandbox.repo_test_network)
    test_cmd = d.command(["python", "-m", "pytest"], tmp_path, "test", None)
    assert test_cmd[test_cmd.index("--network") + 1] == "bridge"
    assert "ALL" in test_cmd and "no-new-privileges" in test_cmd, "still isolated otherwise"


# ---------------------------------------------------------------- templates + ledger


def test_templates_are_strict():
    with pytest.raises(TemplateError, match="missing"):
        render_text("{{a}} {{b}}", a=1)
    with pytest.raises(TemplateError, match="unexpected"):
        render_text("{{a}}", a=1, b=2)


def test_pr_body_carries_r2_disclosure():
    body = render("pr_body.md", summary="s", issue_ref="Fixes #3 (u)", changes="c",
                  verification="v", review_notes="r", agent="Claude Code", model="m",
                  model_note="", tokens="1,000", cost=" (~$0.10)", second_review="x")  # fmt: skip
    assert "Generated with Claude Code by the token-tithe engine" in body
    assert "Fixes #3" in body and "tokens: 1,000" in body and "token-tithe-ignore" in body


def test_ledger_schema_secrets_and_malformed(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append("pr_opened", repo="o/r", issue=1)
    with pytest.raises(LedgerError, match="R6"):
        led.append("abandoned", reason="key AKIA" + "ABCDEFGHIJKLMNOP")
    with pytest.raises(LedgerError):
        led.append("made_up_event")
    with (tmp_path / "ledger.jsonl").open("a") as fh:
        fh.write("{not json\n")
        fh.write(
            json.dumps({"schema_version": 99, "ts": "2026-01-01T00:00:00Z", "event": "x"}) + "\n"
        )
    entries = list(led.entries())
    assert entries[0]["schema_version"] == 1 and len(entries) == 2
    assert any("malformed" in p for p in led.problems) and any("99" in p for p in led.problems)
    assert led.prs_to_repo("O/R") == 1 and led.prs_today() == 1


def test_ecosystems_of_and_missing_program():
    from token_tithe.toolchain import ecosystems_of, missing_program

    assert ecosystems_of(["assets/scripts/go.mod", "a/b.rs", "README.md"]) == {"go", "rust"}
    assert ecosystems_of(["docs/index.md"]) == set()
    assert missing_program(127, "[Errno 2] No such file or directory: 'go'")
    assert missing_program(127, 'exec: "go": executable file not found in $PATH')
    assert not missing_program(1, "No such file or directory")
    assert not missing_program(127, "some test failed")


def test_nested_go_modules_only_those_the_diff_touches(tmp_path):
    from token_tithe.toolchain import nested_go_modules

    for d in ("assets/scripts", "assets/artifact-hub-pkg", "tools", "x/testdata/m"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "go.mod").write_text("module m\n")
    (tmp_path / "go.mod").write_text("module root\n")  # covered by detect(), not here
    (tmp_path / "assets/scripts/cmd").mkdir()
    prof = nested_go_modules(tmp_path, [
        "assets/scripts/cmd/main.go", "assets/scripts/go.sum", "assets/scripts/go.mod",
        "main.go", "README.md", "x/testdata/m/a.go", "assets/gone/deleted.go",
    ])  # fmt: skip
    assert prof.ecosystems == ["go"]
    assert prof.install == [[["go", "-C", "assets/scripts", "mod", "download"]]]
    assert prof.test == [["go", "-C", "assets/scripts", "test", "./..."]]
    assert prof.lint == [["go", "-C", "assets/scripts", "vet", "./..."]]
    assert nested_go_modules(tmp_path, ["main.go", "docs/a.md"]).test == []


def test_sandbox_image_pins_checksummed_go():
    text = (paths.ROOT / "container" / "sandbox.Dockerfile").read_text()
    assert not re.search(r"install.*golang-go", text) and "GOTOOLCHAIN=auto" in text
    assert re.search(r"ARG GO_VERSION=1\.\d+\.\d+", text)
    assert len(re.findall(r"ARG GO_SHA256_(AMD64|ARM64)=[0-9a-f]{64}", text)) == 2
    assert "sha256sum -c" in text


def test_docker_runner_uses_container_venv_even_if_link_dangles_on_host(tmp_path, monkeypatch):
    from token_tithe import sandbox

    (tmp_path / VENV_DIR / "bin").mkdir(parents=True)
    (tmp_path / VENV_DIR / "bin" / "python").symlink_to("/nonexistent/container/python")
    seen = []
    monkeypatch.setattr(
        sandbox, "_execute", lambda cmd, *a: seen.append(cmd) or RunResult(0, "", "")
    )
    DockerRunner(P.load_policy()).run(
        ["python", "-m", "pip", "install", "."], tmp_path, "install", 10
    )
    assert f"/work/{VENV_DIR}/bin/python" in seen[0]
    (tmp_path / VENV_DIR / "bin" / "ruff").write_text("")
    DockerRunner(P.load_policy()).run(["ruff", "check", "."], tmp_path, "test", 10)
    assert f"/work/{VENV_DIR}/bin/ruff" in seen[1]
    DockerRunner(P.load_policy()).run(["cargo", "test"], tmp_path, "test", 10)
    assert seen[2][-2:] == ["cargo", "test"]  # not in the venv: left to PATH


def test_node_entrypoint_fallback_counts_as_missing_program():
    from token_tithe.toolchain import missing_program

    out = (
        "node:internal/modules/cjs/loader:1433\n  throw err;\n  ^\n\n"
        "Error: Cannot find module '/work/ruff'\n    at Function._resolveFilename (x)\n"
        "    at y {\n  code: 'MODULE_NOT_FOUND',\n  requireStack: []\n}\n"
    )
    assert missing_program(1, out)
    assert not missing_program(
        1, "Error: Cannot find module 'vitest'\nrequireStack: [ '/work/a.js' ]"
    )


def test_bun_lockfile_installs_with_bun(tmp_path):
    import json as _json

    from token_tithe.toolchain import detect

    (tmp_path / "package.json").write_text(
        _json.dumps({"scripts": {"test": "vitest"}, "devDependencies": {"vitest": "4"}})
    )
    (tmp_path / "package-lock.json").write_text("{}")  # stale npm lock next to bun.lock
    npm_ci = [["npm", "ci"], ["npm", "ci", "--ignore-scripts"]]
    reconcile = [["node", "/opt/tithe/fix-optional.js"]]
    assert detect(tmp_path).install == [npm_ci, reconcile]
    (tmp_path / "bun.lock").write_text("{}")
    assert detect(tmp_path).install == [[["bun", "install", "--frozen-lockfile"], *npm_ci]]
    assert ["npm", "test", "--silent"] in detect(tmp_path).test


def test_pnpm_lockfile_installs_and_tests_with_pnpm(tmp_path):
    import json as _json

    from token_tithe.toolchain import detect

    (tmp_path / "package.json").write_text(_json.dumps({
        "scripts": {"pretest": "playwright install --with-deps chromium", "test": "vitest",
                    "lint": "eslint ."},
        "devDependencies": {"@playwright/test": "^1.53.1", "vitest": "2"},
    }))  # fmt: skip
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    prof = detect(tmp_path)
    assert prof.install == [
        [["pnpm", "install", "--frozen-lockfile"],
         ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"]],
        [["pnpm", "exec", "playwright", "install", "chromium"]],
    ]  # fmt: skip
    assert prof.test == [["pnpm", "test"]] and prof.lint == [["pnpm", "run", "lint"]]


def test_playwright_browser_installs_only_for_browser_test_repos(tmp_path):
    import json as _json

    from token_tithe.toolchain import detect

    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / "package.json").write_text(
        _json.dumps({"scripts": {"test": "jest"}, "devDependencies": {"jest": "30"}})
    )
    assert len(detect(tmp_path).install) == 2  # npm ci, then the native-optional fix
    (tmp_path / "package.json").write_text(
        _json.dumps({"scripts": {"test": "vitest"}, "devDependencies": {"playwright": "1.64"}})
    )
    assert detect(tmp_path).install[-1] == [["npx", "--no-install", "playwright", "install",
                                             "chromium"]]  # fmt: skip


def test_docker_runner_keeps_browsers_and_pnpm_store_in_worktree(tmp_path):
    cmd = DockerRunner(P.load_policy()).command(["pnpm", "test"], tmp_path, "test", None)
    for env in ("PLAYWRIGHT_BROWSERS_PATH=/work/.tithe-playwright",
                "npm_config_store_dir=/work/.tithe-pnpm", "CI=true", "HEADLESS=true",
                "COREPACK_HOME=/work/.tithe-corepack"):  # fmt: skip
        assert env in cmd
    text = (paths.ROOT / "container" / "sandbox.Dockerfile").read_text()
    assert "install-deps chromium" in text and "corepack enable pnpm yarn" in text


def test_dependency_group_installs_test_plugins(tmp_path):
    from token_tithe.toolchain import detect

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "m"\ndependencies = ["numpy"]\n'
        '[dependency-groups]\ndev = ["pytest-cov", "pytest-xdist"]\n'
    )
    install = detect(tmp_path).install
    assert [["python", "-m", "pip", "install", "-q", "--group", "dev"]] in install
    assert install.index([["python", "-m", "pip", "install", "-q", "-U", "pip>=25.1"]]) < (
        install.index([["python", "-m", "pip", "install", "-q", "--group", "dev"]])
    )


def test_yarn_lockfile_installs_and_tests_with_yarn(tmp_path):
    import json as _json

    from token_tithe.toolchain import detect

    (tmp_path / "package.json").write_text(_json.dumps(
        {"scripts": {"test": "jest", "lint": "eslint ."}, "devDependencies": {"jest": "29"}}
    ))  # fmt: skip
    (tmp_path / "yarn.lock").write_text("")
    prof = detect(tmp_path)
    assert prof.install == [[["yarn", "install", "--frozen-lockfile"],
                             ["yarn", "install", "--immutable"]]]  # fmt: skip
    assert prof.test == [["yarn", "test"]] and prof.lint == [["yarn", "run", "lint"]]


def test_e2e_test_script_defers_to_unit_tests(tmp_path):
    import json as _json

    from token_tithe.toolchain import detect

    (tmp_path / "package.json").write_text(_json.dumps({
        "scripts": {"test": "cypress run --browser chromium", "test:unit": "vitest run"},
        "devDependencies": {"cypress": "13"},
    }))  # fmt: skip
    (tmp_path / "yarn.lock").write_text("")
    assert detect(tmp_path).test == [["yarn", "run", "test:unit"]]


def test_vitest_browser_project_defers_to_node_tests(tmp_path):
    import json as _json

    from token_tithe.toolchain import detect

    (tmp_path / "package.json").write_text(_json.dumps({
        "scripts": {"test": "npx vitest --run", "test-node": "npx vitest --run node/"},
        "devDependencies": {"vitest": "3"},
    }))  # fmt: skip
    (tmp_path / "package-lock.json").write_text("{}")
    assert detect(tmp_path).test == [["npm", "test", "--silent"]]  # no browser project
    (tmp_path / "vitest.config.ts").write_text(
        '/// <reference types="@vitest/browser/providers/webdriverio" />\n'
        "export default { test: { projects: [{ test: { browser: { enabled: true } } }] } }\n"
    )
    assert detect(tmp_path).test == [["npm", "run", "test-node"]]


def test_git_lfs_assets_are_fetched_before_tests(tmp_path):
    from token_tithe.toolchain import detect

    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
    assert detect(tmp_path).install == [[["cargo", "fetch"]]]
    (tmp_path / ".gitattributes").write_text(
        "snapshots/*.png filter=lfs diff=lfs merge=lfs -text\n"
    )
    assert detect(tmp_path).install == [[["git", "lfs", "pull"]], [["cargo", "fetch"]]]


def test_ruff_lint_installs_ruff_at_the_repo_pin(tmp_path):
    from token_tithe.toolchain import detect

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["numpy"]\n'
        '[dependency-groups]\ntest = ["pytest"]\ndev = ["ruff>=0.17.3", "ruffle"]\n'
        "[tool.ruff.lint]\nignore = []\n"
    )
    prof = detect(tmp_path)
    assert ["python", "-m", "ruff", "check", "."] in prof.lint
    assert [["python", "-m", "pip", "install", "-q", "ruff>=0.17.3"]] in prof.install
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n[tool.ruff]\n')
    assert [["python", "-m", "pip", "install", "-q", "ruff"]] in detect(tmp_path).install


def test_pinned_rust_toolchain_is_installed_into_the_worktree(tmp_path, monkeypatch):
    from token_tithe import sandbox
    from token_tithe.toolchain import detect

    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
    assert not any("rustup" in s[0] for s in detect(tmp_path).install)
    (tmp_path / "rust-toolchain").write_text('[toolchain]\nchannel = "1.95.0"\n')
    assert detect(tmp_path).install[0][0][:3] == ["rustup", "toolchain", "install"]
    seen = []
    monkeypatch.setattr(
        sandbox, "_execute", lambda cmd, *a: seen.append(cmd) or RunResult(0, "", "")
    )
    DockerRunner(P.load_policy()).run(["cargo", "test"], tmp_path, "test", 10)
    assert f"RUSTUP_HOME=/work/{sandbox.RUSTUP_DIR}" in seen[0]


def test_playwright_core_alone_uses_its_own_binary(tmp_path):
    import json as _json

    from token_tithe.toolchain import detect

    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "package.json").write_text(
        _json.dumps({"scripts": {"test": "jest"}, "devDependencies": {"playwright-core": "1.17"}})
    )
    assert [["pnpm", "exec", "playwright-core", "install", "chromium"]] in detect(tmp_path).install


def test_tests_on_uncommitted_build_output_build_first(tmp_path):
    import json as _json

    from token_tithe.toolchain import detect

    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "package.json").write_text(_json.dumps({
        "main": "out/index.js", "devDependencies": {"jest": "29"},
        "scripts": {"build": "tsc", "test": "jest --verbose"},
    }))  # fmt: skip
    assert detect(tmp_path).test == [["pnpm", "run", "build"], ["pnpm", "test"]]
    (tmp_path / "out").mkdir()  # committed build output: nothing to build
    assert detect(tmp_path).test == [["pnpm", "test"]]


def test_host_runs_get_no_secrets(monkeypatch, tmp_path):
    """A target repo's test script on the host must not see the operator's tokens or logins."""
    import json as _json
    import sys as _sys

    for k, v in {"GITHUB_TOKEN": "g", "GH_TOKEN": "g", "ANTHROPIC_API_KEY": "a",
                 "OPENAI_API_KEY": "o", "SSH_AUTH_SOCK": "/s", "TITHE_SIGNING_KEY": "k",
                 "AWS_SECRET_ACCESS_KEY": "w", "MY_HARMLESS": "keep"}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    dump = "import json, os; print(json.dumps(dict(os.environ)))"
    r = LocalRunner().run([_sys.executable, "-c", dump], tmp_path, "test", 30)
    env = _json.loads(r.stdout)
    leaked = {"GITHUB_TOKEN", "GH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SSH_AUTH_SOCK",
              "TITHE_SIGNING_KEY", "AWS_SECRET_ACCESS_KEY"} & env.keys()  # fmt: skip
    assert not leaked
    assert env["MY_HARMLESS"] == "keep" and env["PATH"]
    assert env["GIT_CONFIG_KEY_0"] == "credential.helper" and env["GIT_CONFIG_VALUE_0"] == ""
    # the agent gets back exactly what its adapter passes through, nothing more
    r = LocalRunner().run([_sys.executable, "-c", dump], tmp_path, "agent", 30,
                          env={"ANTHROPIC_API_KEY": "a"})  # fmt: skip
    env = _json.loads(r.stdout)
    assert env["ANTHROPIC_API_KEY"] == "a" and "GITHUB_TOKEN" not in env
