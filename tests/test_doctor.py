"""Phase 2: doctor detects self-containment violations and leaks (S2, S6, R6)."""

from pathlib import Path

from typer.testing import CliRunner

from token_tithe import paths
from token_tithe.cli import app
from token_tithe.doctor import (
    FAIL,
    check_abs_paths,
    check_layout,
    check_secrets,
    scan_absolute_paths,
    scan_secrets,
    stray_root_entries,
)

# Assembled at runtime so this test file never contains the patterns it plants.
HOME_PATH = "/" + "Users/alice/project"
GH_TOKEN = "gh" + "p_" + "A" * 36


def _tree(tmp: Path) -> Path:
    (tmp / "src").mkdir()
    (tmp / "config").mkdir()
    (tmp / "src" / "ok.py").write_text("ROOT = Path(__file__).resolve().parent\n")
    return tmp


def test_repo_itself_is_clean():
    assert scan_absolute_paths() == []
    assert check_layout().status != FAIL
    assert stray_root_entries() == []


def test_planted_absolute_path_is_detected(tmp_path):
    root = _tree(tmp_path)
    (root / "config" / "bad.yaml").write_text(f"workdir: {HOME_PATH}\n")
    (root / "src" / "home.py").write_text("p = '~" + "/.cache'\n")
    hits = scan_absolute_paths(root)
    assert any("config/bad.yaml:1" in h for h in hits)
    assert any("src/home.py:1" in h for h in hits)
    assert check_abs_paths(root).status == FAIL


def test_planted_secret_detected_without_leaking_value(tmp_path):
    root = _tree(tmp_path)
    (root / "state").mkdir()
    (root / "state" / "log.txt").write_text(f"token={GH_TOKEN}\n")
    hits = scan_secrets(root, env={})
    assert hits == ["state/log.txt:1 (github token)"]
    check = check_secrets(root, env={})
    assert check.status == FAIL and GH_TOKEN not in check.detail


def test_live_env_secret_value_is_detected(tmp_path):
    root = _tree(tmp_path)
    (root / "src" / "leak.py").write_text("X = 'not-a-pattern-but-real-123'\n")
    hits = scan_secrets(root, env={"GITHUB_TOKEN": "not-a-pattern-but-real-123"})
    assert hits == ["src/leak.py:1 (value of $GITHUB_TOKEN)"]


def test_fixtures_excluded_from_secret_scan(tmp_path):
    root = _tree(tmp_path)
    fx = root / "eval" / "fixtures" / "leaky"
    fx.mkdir(parents=True)
    (fx / "config.py").write_text(f"KEY = '{GH_TOKEN}'\n")
    assert scan_secrets(root, env={}) == []


def test_nested_checkout_skipped_by_secret_scan(tmp_path):
    root = _tree(tmp_path)
    wt = root / ".claude" / "worktrees" / "other"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n")  # a git worktree is a .git file
    (wt / "config.py").write_text(f"KEY = '{GH_TOKEN}'\n")
    assert scan_secrets(root, env={}) == []


def test_stray_root_file_fails_layout(tmp_path):
    (tmp_path / "README.md").write_text("x")
    (tmp_path / "notes-final-v2.txt").write_text("x")
    assert stray_root_entries(tmp_path) == ["notes-final-v2.txt"]
    assert check_layout(tmp_path).status == FAIL


def test_doctor_cli_passes_offline():
    result = CliRunner().invoke(app, ["doctor", "--offline"])
    assert "doctor:" in result.output
    assert result.exit_code == 0, result.output


def test_state_dir_inside_repo():
    assert paths.STATE.parent == paths.ROOT
