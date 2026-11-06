"""Phase 7: all docs exist, cross-links resolve, documented commands are real."""

import re

import pytest
import typer

from token_tithe import paths
from token_tithe.cli import app

DOCS = ["ARCHITECTURE", "WORKFLOW", "DONATION_POLICY", "AGENT_INTEGRATION", "SAFETY",
        "FEEDBACK", "LEDGER", "SIMULATE", "ONBOARDING", "CAPACITY", "HANDOVER",
        "OPERATIONS", "LEARNING"]  # fmt: skip
FILES = [paths.ROOT / "README.md", paths.ROOT / "AGENTS.md", paths.ROOT / "CLAUDE.md",
         *(paths.DOCS / f"{d}.md" for d in DOCS)]  # fmt: skip
LINK = re.compile(r"\]\(([^)#\s]+\.md)(#[\w-]+)?\)")


def _commands() -> set[str]:
    names = set()
    for c in typer.main.get_command(app).commands.values():
        names.add(c.name)
    return names


@pytest.mark.parametrize("name", DOCS)
def test_doc_exists_and_is_substantial(name):
    f = paths.DOCS / f"{name}.md"
    assert f.is_file() and len(f.read_text().splitlines()) >= 30, name


@pytest.mark.parametrize("f", FILES, ids=lambda f: f.name)
def test_links_resolve(f):
    for target, anchor in LINK.findall(f.read_text()):
        dest = (f.parent / target).resolve()
        assert dest.is_file(), f"{f.name} → {target}"
        if anchor:
            heads = {re.sub(r"[^\w\- ]", "", h.lower()).strip().replace(" ", "-")
                     for h in re.findall(r"^#+ (.+)$", dest.read_text(), re.M)}  # fmt: skip
            assert anchor[1:] in heads, f"{f.name} → {target}{anchor}"


@pytest.mark.parametrize("f", FILES, ids=lambda f: f.name)
def test_documented_commands_exist(f):
    known = _commands()
    for cmd in re.findall(r"donate (\w[\w-]*)", f.read_text()):
        if cmd in {"engine", "agent", "run", "the"} | known:
            continue
        pytest.fail(f"{f.name} documents `donate {cmd}`, which does not exist")


def test_readme_links_every_doc():
    readme = (paths.ROOT / "README.md").read_text()
    for d in DOCS:
        assert f"docs/{d}.md" in readme, d
