"""Phase 6: the Brain (R19), learning loop, portability (Addendum E acceptance)."""

import shutil
from datetime import date, timedelta

import pytest

from token_tithe import paths
from token_tithe.brain import Brain
from token_tithe.learn import apply, from_entry, learn
from token_tithe.ledger import Ledger


@pytest.fixture
def brain(tmp_path):
    f = tmp_path / "brain" / "BRAIN.md"
    f.parent.mkdir()
    shutil.copy(paths.BRAIN, f)
    return Brain.load(f)


def approved_lines(b: Brain) -> list[str]:
    return [le.line for le in b.lessons()]


def test_committed_brain_parses_and_has_seed_lessons():
    b = Brain.load()
    assert len(b.lessons()) >= 7 and not b.open_pending()
    assert b.relevant_text({"python", "issue_fix", "general"}) != "None recorded yet."


def test_r19_no_operation_edits_or_deletes_approved_lessons(brain):
    before = approved_lines(brain)
    pid = brain.add_pending(
        "PROMPT PATTERNS", "Ask the agent to name the failing test first.", ["general"], "test"
    )
    pid2 = brain.add_pending(
        "AGENT QUIRKS", "Some agent quirk that is fairly unique.", ["x"], "test"
    )
    lid = brain.promote(pid)
    brain.reject(pid2, "not useful")
    brain.add_pending("AGENT QUIRKS", brain.lessons()[0].text, ["claude"], "again")  # reinforces
    brain.supersede("L-0001", "Updated guidance on allowed tools.", "test")
    brain.review_stale(1, today=date.today() + timedelta(days=400))
    brain.save()
    reloaded = Brain.load(brain.path)
    after = approved_lines(reloaded)
    assert after[: len(before)] == before or set(before) <= set(after)
    assert all(line in after for line in before), "an approved lesson changed or vanished"
    assert lid in {le.id for le in reloaded.lessons()}
    assert "L-0001" in reloaded.superseded()
    assert "L-0001" not in {le.id for le in reloaded.active()}
    log = " ".join(w for _, w in reloaded.log())
    assert "rejected" in log and "reinforced L-0001" in log and "stale:" in log


def test_templated_lessons_about_different_repos_are_kept(brain):
    a = brain.add_pending(
        "REJECTIONS & INCIDENTS", "a/b rejects AI-generated contributions.", ["a/b"], "x"
    )
    b = brain.add_pending(
        "REJECTIONS & INCIDENTS", "c/d rejects AI-generated contributions.", ["c/d"], "y"
    )
    assert a and b and a != b


def test_duplicates_reinforce_instead_of_duplicating(brain):
    text = brain.lessons()[0].text
    assert brain.add_pending("AGENT QUIRKS", text, ["claude"], "p1") is None
    assert (
        brain.add_pending("AGENT QUIRKS", text.replace("silently", "quietly"), ["claude"], "p2")
        is None
    )
    assert brain.reinforcements("L-0001") == 2
    assert brain.effective_confidence(brain.lessons()[0]) == "confirmed"


def test_cap_overflow_goes_to_knowledge(brain):
    brain.max_lines = len(brain.render().splitlines()) + 6
    words = ["cargo", "npm", "poetry", "gradle", "maven", "bundler", "pnpm", "yarn", "tox", "nox",
             "bazel", "meson"]  # fmt: skip
    for i, w in enumerate(words):
        pid = brain.add_pending("ECOSYSTEM PLAYBOOKS", f"{w} projects need {w}-specific caching rule {i * 97}",
                                [w], f"t{i}")  # fmt: skip
        brain.promote(pid)
    long_pid = brain.add_pending(
        "PROMPT PATTERNS", "A very long lesson. " * 30, ["general"], "long"
    )
    brain.promote(long_pid)
    brain.save()
    text = brain.path.read_text()
    assert "more in knowledge/ecosystem-playbooks.md" in text
    assert "more in knowledge/prompt-patterns.md" in text
    kfile = brain.knowledge_dir / "prompt-patterns.md"
    assert kfile.is_file() and "A very long lesson." in kfile.read_text()
    assert len(text.splitlines()) <= brain.max_lines
    reloaded = Brain.load(brain.path, brain.max_lines)
    assert len(reloaded.lessons()) == 7 + 13, "overflow lessons are still lessons"
    assert "(L-0010" in reloaded.relevant_text({"poetry"}, 5), "overflow lessons are injectable"


def test_export_import_into_fresh_clone_is_identical(brain, tmp_path):
    for t in ("Lesson alpha about python venvs", "Lesson beta about npm ci and lockfiles"):
        brain.promote(brain.add_pending("ECOSYSTEM PLAYBOOKS", t, ["python"], "run x"))
    brain.promote(brain.add_pending("PROMPT PATTERNS", "Z" * 400, ["general"], "long"))
    brain.save()
    export = brain.export(tmp_path / "out")
    fresh = tmp_path / "fresh" / "brain" / "BRAIN.md"
    fresh.parent.mkdir(parents=True)
    shutil.copy(paths.BRAIN, fresh)  # a fresh clone has only the committed seed
    b2 = Brain.load(fresh)
    stats = b2.import_(export)
    b2.save()
    assert stats["fast_forward"]
    assert fresh.read_text() == brain.path.read_text()
    assert (fresh.parent / "knowledge" / "prompt-patterns.md").read_text() == \
        (brain.knowledge_dir / "prompt-patterns.md").read_text()  # fmt: skip


def test_import_never_overwrites_silently(brain, tmp_path):
    other = tmp_path / "other" / "BRAIN.md"
    other.parent.mkdir()
    shutil.copy(paths.BRAIN, other)
    theirs = Brain.load(other)
    theirs.promote(
        theirs.add_pending("AGENT QUIRKS", "Their lesson about codex sandboxes.", ["codex"], "them")
    )
    theirs.save()
    mine_before = approved_lines(brain)
    brain.promote(
        brain.add_pending("AGENT QUIRKS", "My own different lesson about aider.", ["aider"], "me")
    )
    stats = brain.import_(theirs.export(tmp_path / "x"))
    assert not stats["fast_forward"] and stats["pending"] == 1
    assert any("codex sandboxes" in p.text for p in brain.open_pending())
    assert all(line in approved_lines(brain) for line in mine_before)


def test_learn_rules():
    c = from_entry(
        {
            "event": "rejected",
            "repo": "a/b",
            "reason": 'anti-AI/automation statement in CONTRIBUTING.md: "We do not accept AI PRs." (R3)',
            "mode": "live",
            "run_id": "r",
        }
    )
    assert c.section == "REJECTIONS & INCIDENTS" and "a/b" in c.text and "a/b" in c.tags
    c = from_entry({"event": "abandoned", "agent": "claude", "language": "Python", "donation_type": "issue_fix",
                    "reason": "tests fail after the change: `python -m pytest` (R7)", "mode": "simulate"})  # fmt: skip
    assert c.section == "PROMPT PATTERNS" and c.origin == "simulate" and "python" in c.tags
    assert from_entry({"event": "merged", "donation_type": "docs", "language": "Go", "repo": "x/y",
                       "url": "https://github.com/x/y/pull/1", "days_open": 2}).origin == "feedback"  # fmt: skip
    assert from_entry({"event": "pr_opened"}) is None


def test_learn_is_incremental(brain, tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append("timeout", agent="claude", language="Python", donation_type="tests", mode="live")
    first = learn(led, brain, eval_report=tmp_path / "none.md")
    second = learn(led, brain, eval_report=tmp_path / "none.md")
    assert len(first["added"]) == 1 and second["candidates"] == 0


def test_loop_changes_the_next_task_brief(tmp_path, monkeypatch):
    """E6 integration: run → learn → apply → the NEXT task brief contains the lesson."""
    from tests.test_executor import MATH, make, triaged
    from token_tithe.agents.control import ReferenceAdapter
    from token_tithe.fixtures import load_fixtures
    from token_tithe.github_api import GitHubAPI
    from token_tithe.policy import load_policy
    from token_tithe.run import lessons_provider

    fx = {f.full_name.lower(): f for f in load_fixtures()}
    with paths.use_state(tmp_path / "state"):
        bfile = tmp_path / "brain" / "BRAIN.md"
        bfile.parent.mkdir()
        shutil.copy(paths.BRAIN, bfile)
        monkeypatch.setattr(paths, "BRAIN", bfile)
        led = Ledger()
        led.append("abandoned", agent="reference", language="Python", donation_type="issue_fix",
                   repo=MATH, reason="tests fail after the change: `python -m pytest` (R7)", mode="live")  # fmt: skip
        b = Brain.load(bfile)
        learn(led, b, eval_report=tmp_path / "none.md")
        [(pid, lid)] = apply(b)
        b.save()

        seen = {}

        class Spy(ReferenceAdapter):
            def invoke(self, worktree, brief, runner, timeout):
                seen["brief"] = (worktree / brief).read_text()
                return super().invoke(worktree, brief, runner, timeout)

        api = GitHubAPI.fixture()
        ex, _ = make(api, load_policy(), adapter=Spy(fx))
        ex.lessons = lessons_provider(load_policy())
        ex.run_task(triaged(api, MATH))
        assert lid in seen["brief"] and "full suite" in seen["brief"]
