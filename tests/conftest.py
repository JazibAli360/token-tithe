import shutil
from pathlib import Path

import pytest

from token_tithe import paths
from token_tithe import policy as P


@pytest.fixture
def config_copy(tmp_path: Path) -> Path:
    """A writable copy of config/ for tests that mutate policy files."""
    dst = tmp_path / "config"
    shutil.copytree(paths.CONFIG, dst, ignore=shutil.ignore_patterns("*.local.yaml"))
    return dst


@pytest.fixture(autouse=True)
def shared_config_only(monkeypatch):
    """Tests see the shared config/ defaults, never the developer's *.local.yaml overlays."""
    monkeypatch.setenv("TITHE_NO_LOCAL_CONFIG", "1")
    monkeypatch.delenv("TITHE_SIGNING_KEY", raising=False)  # the operator's key must not leak in


@pytest.fixture(autouse=True)
def fixture_star_range(monkeypatch):
    """The real policy's star range picks real repos; fixture repos need FIXTURE_STARS.
    A policy loaded from an explicit test copy keeps whatever the test wrote."""
    real = P.load_policy

    def load(path=paths.POLICY_FILE):
        pol = real(path)
        return P.for_fixtures(pol) if path == paths.POLICY_FILE else pol

    monkeypatch.setattr(P, "load_policy", load)
