"""Phase 1: the scaffold is complete and the package is importable."""

from typer.testing import CliRunner

from token_tithe import __version__, paths
from token_tithe.cli import app

REQUIRED = [
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "docs/CONTRIBUTING.md",
    "LICENSE",
    "Makefile",
    "pyproject.toml",
    "container/Dockerfile",
    ".devcontainer/devcontainer.json",
    ".github/workflows/ci.yml",
    ".github/workflows/donate.yml",
]


def test_required_files_exist():
    missing = [f for f in REQUIRED if not (paths.ROOT / f).is_file()]
    assert not missing, missing


def test_root_anchor_is_repo_root():
    assert (paths.ROOT / "pyproject.toml").is_file()
    assert paths.STATE.parent == paths.ROOT


def test_state_is_gitignored():
    assert "state/" in (paths.ROOT / ".gitignore").read_text().splitlines()


def test_readme_has_roadmap_and_portability():
    text = (paths.ROOT / "README.md").read_text()
    assert "## Roadmap — not yet built" in text
    assert "## Portability guarantee" in text


def test_claude_md_has_all_rules_and_is_short():
    text = (paths.ROOT / "CLAUDE.md").read_text()
    assert len(text.splitlines()) < 150
    for n in range(1, 20):
        assert f"**R{n}**" in text, f"R{n} missing"


def test_cli_version():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output
