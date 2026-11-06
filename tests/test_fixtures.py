"""Phase 3: fixtures are complete, self-contained and behave as their rubrics claim."""

import pytest

from token_tithe import paths
from token_tithe.doctor import scan_secrets
from token_tithe.fixtures import (
    diff_stat,
    load_fixtures,
    materialize,
    missing_toolchains,
    overlay,
    run_cmd,
)

FIXTURES = {f.name: f for f in load_fixtures()}
SPEC_SIX = {"py-math-bug", "js-doc-gap", "py-untested", "rs-clippy", "flaky-ci", "anti-ai"}


def test_spec_fixtures_plus_d4_d8_present():
    assert SPEC_SIX | {"opt-out", "leaked-secret"} <= set(FIXTURES)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_is_well_formed(name):
    f = FIXTURES[name]
    assert f.repo_dir.is_dir()
    assert f.full_name.startswith("tithe-fixtures/")
    assert f.github["repo"]["private"] is False
    if f.rubric.expect == "fix":
        assert any(f.reference_dir.rglob("*")), "fix fixtures ship a reference solution"


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_baseline_matches_rubric(name, tmp_path):
    f = FIXTURES[name]
    if missing_toolchains(f.rubric.requires):
        pytest.skip(f"toolchain missing for {name}")
    if not f.rubric.checks.repo_tests:
        return
    wt = materialize(f, tmp_path / name)
    r = run_cmd(f.rubric.checks.repo_tests, wt)
    assert ("pass" if r.ok else "fail") == f.rubric.baseline.repo_tests, r.output


def test_hidden_files_never_in_fixture_repo():
    for f in FIXTURES.values():
        names = {p.name for p in f.repo_dir.rglob("*")}
        assert not any("hidden" in n for n in names), f.name


def test_anti_ai_fixture_states_policy():
    text = (FIXTURES["anti-ai"].repo_dir / "CONTRIBUTING.md").read_text()
    assert "do not accept pull requests that were generated" in text


def test_opt_out_fixture_has_ignore_file():
    assert (FIXTURES["opt-out"].repo_dir / ".github" / "token-tithe-ignore").is_file()


def test_leaked_secret_fixture_is_detected_by_scanner(tmp_path):
    wt = materialize(FIXTURES["leaked-secret"], tmp_path / "ls")
    hits = scan_secrets(wt, env={})
    assert any("aws access key" in h for h in hits)
    assert not any("AKIA" in h for h in hits), "scanner must never echo the secret"


def test_repo_scanner_still_excludes_fixtures():
    assert scan_secrets(paths.ROOT, env={}) == []


def test_materialize_is_deterministic(tmp_path):
    f = FIXTURES["py-math-bug"]
    a, b = materialize(f, tmp_path / "a"), materialize(f, tmp_path / "b")
    head = ["git", "rev-parse", "HEAD"]
    assert run_cmd(head, a).output == run_cmd(head, b).output


def test_diff_stat_counts_untracked_and_modified(tmp_path):
    f = FIXTURES["py-math-bug"]
    wt = materialize(f, tmp_path / "w")
    overlay(f.reference_dir, wt)
    (wt / "NEW.md").write_text("one\ntwo\n")
    d = diff_stat(wt)
    assert set(d.files) == {"mathkit/stats.py", "tests/test_stats.py", "NEW.md"}
    assert d.lines > 2
