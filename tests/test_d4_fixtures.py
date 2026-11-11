"""D4 exception for repos whose tests carry fake credentials on purpose."""

from types import SimpleNamespace

from token_tithe.pipeline import gate
from token_tithe.secrets_scan import is_test_path

FAKE = "AKIA" + "ABCDEFGHIJKLMNOP"  # built at runtime so this file trips no scanner


class Clean:
    def assess_issue(self, repo, number, fresh=False):
        return SimpleNamespace(rejected=False, reasons=[])


def test_test_paths():
    for p in ("veles/secrets/slack/detector_test.go", "pkg/testdata/a.json", "tests/x.py",
              "src/__tests__/a.ts", "a/test_keys.py", "web/a.spec.tsx", "fixtures/k.txt"):  # fmt: skip
        assert is_test_path(p), p
    for p in ("main.go", "src/config.py", "contest/x.go", "latest/a.ts"):
        assert not is_test_path(p), p


def test_fixture_exception_only_covers_test_files(tmp_path):
    (tmp_path / "scanner").mkdir()
    (tmp_path / "scanner" / "aws_test.go").write_text(f'const k = "{FAKE}"\n')
    assert "(D4)" in gate(Clean(), "o/r", 1, tmp_path)
    assert gate(Clean(), "o/r", 1, tmp_path, test_fixtures_ok=True) is None
    (tmp_path / "config.go").write_text(f'const k = "{FAKE}"\n')  # a real leak outside tests
    assert "(D4)" in gate(Clean(), "o/r", 1, tmp_path, test_fixtures_ok=True)
