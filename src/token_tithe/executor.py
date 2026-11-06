"""Execute one triaged task end to end (§4 executor, R7, R8, R10, R11, R13, R16, D2, D3, D5).

    clone → gate → baseline → brief → invoke agent → verify → second review → confidence
          → commit → (review queue | race recheck → publish)

Between every step: stop flag, halt flag, wall-clock deadline (D3) and budget checkpoint (R10).
Any failure is a clean abandon with a ledger reason; nothing half-published, ever.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from token_tithe import paths
from token_tithe.agents import Adapter
from token_tithe.agents.base import BRIEF_FILE, SCRATCH, Summary
from token_tithe.budget import BudgetExhausted, BudgetGuard, UnaccountedAdapter, Usage
from token_tithe.donation_types import TEST_GLOBS, DonationType, get_type, matches
from token_tithe.donation_types.triage_report import REPORT_FILE
from token_tithe.etiquette import Etiquette
from token_tithe.fixtures import Fixture, diff_stat, materialize
from token_tithe.github_api import GitHubAPI
from token_tithe.handover import HandoverSession
from token_tithe.ledger import Ledger
from token_tithe.pipeline import check_halt, gate
from token_tithe.policy import Policy, review_mode
from token_tithe.pr_conventions import fill_template, find_template, issue_ref, title_marker
from token_tithe.publish import Pusher, assignment_gap, open_pr, post_comment, race_recheck
from token_tithe.sandbox import SCRATCH_PATHS, VENV_DIR, Runner
from token_tithe.second_review import ReviewVerdict, combine_confidence, review
from token_tithe.secrets_scan import secret_kind
from token_tithe.templates import render
from token_tithe.toolchain import (
    Profile,
    detect,
    ecosystems_of,
    merge_agent_commands,
    missing_program,
    nested_go_modules,
)
from token_tithe.triage import Triaged

NO_LESSONS = "None recorded yet."
EVIDENCE_LINES = 80
MODEL_UNAVAILABLE = re.compile(
    r"model\b[^\n]{0,80}\b(?:not found|unavailable|does not exist|not available|not supported)"
    r"|\boverloaded\b|quota exceeded|insufficient_quota|credit balance is too low",
    re.I,
)


class TaskTimeout(RuntimeError):
    """D3: the task exceeded task_timeout_minutes."""


def _tail(text: str, lines: int = EVIDENCE_LINES // 2) -> str:
    return "\n".join(text.splitlines()[-lines:])


class Abandon(RuntimeError):  # noqa: N818 - control flow, not an error condition
    def __init__(self, reason: str, outcome: str = "abandoned", output: str = ""):
        super().__init__(reason)
        self.outcome = outcome
        self.output = output  # the failing command's output tail, kept as evidence


@dataclass
class TaskOutcome:
    key: str
    repo: str
    issue: int
    donation_type: str
    outcome: str  # ledger event
    reason: str = ""
    confidence: int | None = None
    tokens: int = 0
    accounted: bool = True
    model: str | None = None
    url: str = ""
    proposal_id: str = ""
    diff_lines: int = 0
    review: str = ""
    language: str | None = None
    mode: str = ""  # simulate | dry-run | live
    evidence: str = ""  # state/abandoned/... folder with the failing output and the diff


@dataclass
class Proposal:
    """Everything needed to open the work later (`donate review --open`)."""

    id: str
    created: str
    kind: str  # pr | comment
    repo: str
    issue: int
    issue_url: str
    donation_type: str
    title: str
    body: str
    branch: str
    base: str
    worktree: str  # relative to repo root
    confidence: int
    queued_because: list[str]
    agent: str
    model: str | None
    tokens: int
    signed: bool
    review: str = ""
    status: str = "pending"  # pending | opened | discarded
    url: str = ""
    follow_up: str = ""  # the maintainer comment URL, when this follows up a closed PR of ours


def _brief_file(repo: str) -> Path:
    return paths.BRIEFS / f"{repo.replace('/', '__')}.md"


def operator_title(repo: str) -> str | None:
    """An operator brief's `PR title: ...` line, used for follow-ups."""
    f = _brief_file(repo)
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("pr title:"):
                return line.split(":", 1)[1].strip() or None
    return None


def operator_guidance(repo: str) -> str:
    """Design guidance the operator wrote for this repo (state/briefs/), for the brief. It
    comes from us, not the target repo, so it is the one trusted section of the conventions."""
    f = _brief_file(repo)
    if not f.is_file():
        return ""
    text = f.read_text(encoding="utf-8").strip()
    return (
        f"Operator guidance (from the token-tithe operator, trusted):\n\n{text}\n\n" if text else ""
    )


# a missing dependency at import/collection time: the environment, not the code, is broken
ENV_BROKEN = re.compile(
    r"No module named|ImportError while loading conftest|"
    r"could not download file from 'https://static\.rust-lang\.org|"
    r"Cannot find module '|ERR_MODULE_NOT_FOUND|"
    # the sandbox ran out of memory building the suite (emilk/egui#8829/#8833 lost 950k
    # tokens to a linker OOM-killed in the baseline)
    r"terminated with signal 9|signal: 9, SIGKILL|SIGKILL: kill"
)


def git(args: list[str], cwd: Path, env: dict | None = None, timeout: int = 300):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=timeout)  # fmt: skip


def diff_text(worktree: Path) -> str:
    git(["add", "-A"], worktree)
    out = git(["diff", "--cached", "HEAD"], worktree).stdout
    git(["reset", "-q"], worktree)
    return out


_DCO_TEXT = re.compile(r"signed-off-by|developer certificate of origin|\bDCO\b", re.I)


def requires_dco(worktree: Path) -> bool:
    """The repo asks for a Developer Certificate of Origin sign-off on every commit."""
    if any((worktree / f).is_file() for f in ("DCO", "DCO.md", "DCO.txt", ".github/dco.yml")):
        return True
    docs = [*worktree.glob("CONTRIBUTING*"), *worktree.glob(".github/CONTRIBUTING*")]
    if any(_DCO_TEXT.search(f.read_text(encoding="utf-8", errors="replace")) for f in docs):
        return True
    # org-level rules (e.g. a CNCF DCO bot) leave no file in the repo: read its history
    log = git(["log", "-20", "--no-merges", "--format=%B%x00"], worktree).stdout
    msgs = [m for m in log.split("\0") if m.strip()]
    return len(msgs) >= 5 and sum("Signed-off-by:" in m for m in msgs) >= 0.8 * len(msgs)


_CONVENTIONAL = re.compile(
    r"^\s*\[?((?:feat|fix|docs|test|tests|chore|refactor|perf|ci|build|style|revert)"
    r"(?:\([\w./-]+\))?!?)\]?:\s*",
    re.I,
)


# issue-template tags such as "[Bug]: ", "[BUG] ", "Bug: " say nothing in a PR title
_TEMPLATE_TAG = re.compile(r"^\s*(?:\[(?:bug|issue|report|problem)\]:?|(?:bug|issue)\s*:)\s*", re.I)


def pr_title(
    prefix: str, issue_title: str, number: int, limit: int = 72, marker: str | None = None
) -> str:
    """`prefix: title (#N)`. An issue title that already has a conventional-commit type
    (`feat: ...`) keeps it instead of getting a second one; long titles are cut at a word.
    A repo `marker` (cluster-api style emoji) replaces the conventional prefix: `🐛 title`."""
    text = _TEMPLATE_TAG.sub("", issue_title.strip())
    if m := _CONVENTIONAL.match(text):
        prefix, text = m.group(1).lower(), text[m.end() :]
    if marker:
        head = f"{marker} {text[:1].upper()}{text[1:]}"
    else:
        head = f"{prefix}: {text}" if prefix else text
    if len(head) > limit:
        cut = head[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-(")
        head = cut + "…"
    return f"{head} (#{number})"


def slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].strip("-") or "change"


@dataclass
class Executor:
    api: GitHubAPI
    policy: Policy
    ledger: Ledger
    adapter: Adapter
    runner: Runner
    etiquette: Etiquette
    pusher: Pusher
    session: HandoverSession
    budget: BudgetGuard
    run_id: str
    live: bool = False
    reviewer: Adapter | None = None
    fixtures: dict[str, Fixture] | None = None  # full_name → fixture (fixture mode)
    lessons: Callable[[Triaged], str] = lambda t: NO_LESSONS
    force_review: bool = False  # D9 expiring window, or a capacity-blind/unaccounted agent
    clock: Callable[[], float] = time.monotonic
    work_root: Path = field(default_factory=lambda: paths.WORKTREES)
    review_root: Path = field(default_factory=lambda: paths.REVIEW)

    # ----------------------------------------------------------------- guards

    def _guard(self, deadline: float) -> None:
        check_halt()
        self.session.checkpoint()
        if self.clock() > deadline:
            raise TaskTimeout(
                f"exceeded task_timeout_minutes={self.policy.limits.task_timeout_minutes}"
            )
        self.budget.checkpoint()

    def _remaining(self, deadline: float) -> float:
        return max(1.0, deadline - self.clock())

    # ----------------------------------------------------------------- snapshots (resume)

    @staticmethod
    def snapshot_path(worktree: Path) -> Path:
        return worktree.parent / f"{worktree.name}.task.json"

    def _snapshot(self, wt: Path, /, **data) -> None:
        f = self.snapshot_path(wt)
        current = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        current.update(data)
        f.write_text(json.dumps(current, indent=2, default=str), encoding="utf-8")

    # ----------------------------------------------------------------- task

    def run_task(self, t: Triaged, resume: dict | None = None) -> TaskOutcome:
        """Run a task. `resume` (a snapshot) re-enters at verify with the agent's work intact."""
        c = t.candidate
        dtype = get_type(t.donation_type)
        out = TaskOutcome(
            c.key,
            c.repo,
            c.number,
            dtype.name,
            "abandoned",
            model=self.adapter.model,
            language=c.language,
            mode="simulate" if self.fixtures is not None else "live" if self.live else "dry-run",
        )
        deadline = self.clock() + self.policy.limits.task_timeout_minutes * 60
        repo_env = {k.lower(): v for k, v in self.policy.sandbox.repo_env.items()}
        self._repo_env = repo_env.get(c.repo.lower(), {})
        extra = {k.lower(): v for k, v in self.policy.sandbox.repo_extra_deps.items()}
        self._repo_extra_deps = list(extra.get(c.repo.lower(), []))
        worktree = self.work_root / self.run_id / f"{c.repo.replace('/', '__')}-{c.number}"
        branch = f"donate/{c.number}-{slug(c.title)}"
        if resume:
            worktree = paths.ROOT / resume["worktree"]
            branch = resume["branch"]
        self.session.step("execute", key=c.key, repo=c.repo, issue=c.number,
                          donation_type=dtype.name, branch=branch,
                          worktree=paths.rel(worktree), next_step="clone")  # fmt: skip
        try:
            if resume:
                base, baseline = resume["base"], resume["baseline"]
                summary, report = Summary(**resume["summary"]), resume.get("report", "")
                out.tokens, out.model = resume.get("tokens", 0), resume.get("model")
                out.accounted = resume.get("accounted", True)
                profile = detect(worktree)
                self._guard(deadline)
            else:
                worktree.parent.mkdir(parents=True, exist_ok=True)
                self.snapshot_path(worktree).unlink(missing_ok=True)
                base = self._clone(c.repo, worktree, branch)
                self._snapshot(worktree, triaged=t.to_dict(), base=base, branch=branch,
                               worktree=paths.rel(worktree), run_id=self.run_id)  # fmt: skip
                self._mark(out, "clone", "gate", deadline)

                if reason := gate(self.etiquette, c.repo, c.number, worktree):
                    raise Abandon(reason, "rejected")
                self._mark(out, "gate", "baseline", deadline)

                profile = detect(worktree)
                self._install(profile, worktree, deadline)
                baseline = self._run_all(profile.test + profile.lint, worktree, deadline)
                # a suite that cannot even import its dependencies cannot verify any change
                # (R7): stop before the agent spends tokens (tBuLi/kingdon#152 lost 2.6M when
                # Docker was down and the host runner had no sympy)
                if broken := [k for k in map(" ".join, profile.test + profile.lint)
                              if baseline.get(k) is False
                              and ENV_BROKEN.search(self._baseline_output.get(k, ""))]:  # fmt: skip
                    raise Abandon(f"test environment is broken before any change: `{broken[0]}` "
                                  "cannot load its dependencies or build; not starting (R7)",
                                  output=self._baseline_output[broken[0]])  # fmt: skip
                # a test command that already fails would abandon the change at verify anyway
                # ("tests already failed before the change"): stop now, before agent tokens
                # (Nov 2026: kingdon#152 + egui#8829/#8833 burned ~10M tokens this way).
                # ci_repro exists to fix a failing suite, so it is the one type that may start.
                if dtype.output != "comment" and dtype.name != "ci_repro" and (failing := [
                    k for k in map(" ".join, profile.test)
                    if baseline.get(k) is False
                    and k not in self._baseline_missing
                ]):  # fmt: skip
                    raise Abandon(f"tests already fail before any change: `{failing[0]}`; "
                                  "the environment cannot verify a fix, not starting (R7)",
                                  output=_tail(self._baseline_output[failing[0]]))  # fmt: skip
                self._mark(out, "baseline", "invoke", deadline)

                # last check before spending agent tokens: someone may have opened a PR
                # since discovery (oras-project/oras-go#1589 lost 1.3M tokens to #1591)
                if dtype.output != "comment" and (
                    prs := self.api.linked_open_prs(c.repo, c.number)
                ):
                    raise Abandon(f"another PR already references the issue: {prs}; "
                                  "not starting (R9)", "rejected")  # fmt: skip
                self._write_brief(t, dtype, worktree, profile, baseline)
                summary, report = self._invoke(out, worktree, deadline)
                self._snapshot(worktree, baseline=baseline, summary=asdict(summary), report=report,
                               tokens=out.tokens, model=out.model, accounted=out.accounted)  # fmt: skip
                self.session.step(last_completed_step="invoke", next_step="verify")
                self.budget.record(self._usage)  # may raise BudgetExhausted (R10)
                self._mark(out, "invoke", "verify", deadline)

            if summary.refused:
                raise Abandon(
                    f"agent refused: {summary.refusal_reason or 'no reason given'}", "rejected"
                )
            if dtype.output == "comment":
                return self._finish_comment(t, dtype, out, worktree, summary, report, deadline)

            verification, penalty, notes = self._verify(
                dtype, out, worktree, profile, baseline, summary, deadline
            )
            self._mark(out, "verify", "review", deadline)

            diff = diff_text(worktree)
            verdict = self._second_review(t, dtype, worktree, diff, verification, deadline)
            agent_conf = max(0, summary.confidence - penalty)
            out.confidence = combine_confidence(
                agent_conf, verdict, self.policy.second_review.weight
            )
            out.review = verdict.label() if verdict else "none available"
            self._mark(out, "review", "decide", deadline)

            if dtype.name == "ci_repro" and out.confidence < self.policy.confidence_threshold:
                report = self._downgrade_report(summary, diff, verification)
                return self._finish_comment(t, get_type("triage_report"), out, worktree,
                                            summary, report, deadline, downgraded=True)  # fmt: skip

            template = find_template(worktree)
            config_only = not ecosystems_of(diff_stat(worktree).files)
            marker = title_marker(template, dtype.name, config_only)
            # a follow-up's issue title describes the closed PR's fix, not this one
            asked = operator_title(c.repo) if c.follow_up_pr is not None else None
            title = pr_title(dtype.title_prefix, asked or c.title, c.number, marker=marker)
            body = self._pr_body(t, summary, verification, notes, verdict, out, template)
            signed = self._commit(worktree, title)
            proposal = Proposal(
                id="", created="", kind="pr", repo=c.repo, issue=c.number, issue_url=c.url,
                donation_type=dtype.name, title=title, body=body, branch=branch, base=base,
                worktree=paths.rel(worktree), confidence=out.confidence, queued_because=[],
                agent=self.adapter.display_name, model=out.model, tokens=out.tokens,
                signed=signed, review=out.review, follow_up=c.follow_up_url,
            )  # fmt: skip
            return self._decide(t, out, proposal, verdict, worktree)
        except Abandon as e:
            out.outcome, out.reason = e.outcome, str(e)
            if e.output and e.outcome == "abandoned":
                out.evidence = self._keep_evidence(worktree, e.output)
        except TaskTimeout as e:
            out.outcome, out.reason = "timeout", str(e)
        except BudgetExhausted as e:
            out.outcome, out.reason = "budget_exhausted", str(e)
            self.session.h.tokens_spent = self.budget.spent  # the handover must show the overrun
            self._ledger(out)
            raise
        if out.outcome in ("abandoned", "rejected"):
            shutil.rmtree(worktree, ignore_errors=True)  # clean abandon; timeouts stay resumable
            self.snapshot_path(worktree).unlink(missing_ok=True)
        self._ledger(out)
        if out.outcome != "timeout":  # a timed-out task stays in the handover for `donate resume`
            self.session.finish_task(c.key)
        return out

    # ----------------------------------------------------------------- steps

    def _keep_evidence(self, worktree: Path, output: str) -> str:
        """Save the failing output tail and the agent's diff before the worktree is removed,
        so an R7 abandon can be diagnosed. Anything that looks like a secret is withheld (R6)."""
        folder = paths.ABANDONED / self.run_id / worktree.name
        folder.mkdir(parents=True, exist_ok=True)
        tail = "\n".join(output.splitlines()[-EVIDENCE_LINES:]) + "\n"
        diff = diff_text(worktree) if (worktree / ".git").exists() else ""
        for name, text in (("output.txt", tail), ("diff.patch", diff)):
            if kind := secret_kind(text):
                text = f"[withheld: looked like a {kind} (R6)]\n"
            (folder / name).write_text(text, encoding="utf-8")
        return paths.rel(folder)

    def _mark(self, out: TaskOutcome, done: str, nxt: str, deadline: float) -> None:
        self.session.h.tokens_spent = self.budget.spent
        self.session.step(last_completed_step=done, next_step=nxt)
        self._guard(deadline)

    def _clone(self, repo: str, worktree: Path, branch: str) -> str:
        if worktree.exists():
            shutil.rmtree(worktree)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if self.fixtures is not None:
            materialize(self.fixtures[repo.lower()], worktree)
            base = "main"
        else:
            r = git(["clone", "--quiet", "--depth", "50", "--no-tags", "--single-branch",
                     f"https://github.com/{repo}.git", str(worktree)], paths.ROOT)  # fmt: skip
            if r.returncode != 0:
                raise Abandon(f"clone failed: {r.stderr.strip()[-200:]}")
            base = git(["rev-parse", "--abbrev-ref", "HEAD"], worktree).stdout.strip() or "main"
        exclude = worktree / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(SCRATCH_PATHS) + "\n")
        git(["checkout", "-q", "-b", branch], worktree)
        return base

    def _install(self, profile: Profile, worktree: Path, deadline: float) -> None:
        if not self.runner.isolated:
            return  # never install untrusted dependencies into the host (fixtures need none)
        steps = list(profile.install)
        if "python" in profile.ecosystems:  # a venv inside the worktree survives between containers
            steps.insert(0, [["python", "-m", "venv", VENV_DIR]])
            if extra := getattr(self, "_repo_extra_deps", []):
                steps.append([["python", "-m", "pip", "install", "-q", *extra]])
        for alternatives in steps:
            outputs = []
            for cmd in alternatives:
                r = self.runner.run(
                    cmd,
                    worktree,
                    "install",
                    self._remaining(deadline),
                    env=getattr(self, "_repo_env", {}),
                )
                if r.ok:
                    break
                outputs.append(f"$ {' '.join(cmd)}\n{r.output}")
            else:  # every alternative failed: keep their output as evidence
                raise Abandon(f"dependency install failed: {' '.join(alternatives[0])}",
                              output="\n".join(outputs))  # fmt: skip

    def _run_all(self, cmds: list[list[str]], worktree: Path, deadline: float) -> dict[str, bool]:
        results = {}
        self._baseline_output: dict[str, str] = {}  # why a baseline command failed, as evidence
        self._baseline_missing: set[str] = set()  # failed only because the program is absent
        for cmd in cmds:
            r = self.runner.run(
                cmd, worktree, "test", self._remaining(deadline), env=getattr(self, "_repo_env", {})
            )
            if r.timed_out:
                raise TaskTimeout(f"`{' '.join(cmd)}` timed out")
            results[" ".join(cmd)] = r.ok
            if not r.ok:
                self._baseline_output[" ".join(cmd)] = r.output
                if missing_program(r.code, r.output):  # verify reports it as NOT RUN instead
                    self._baseline_missing.add(" ".join(cmd))
        return results

    def _write_brief(self, t: Triaged, dtype: DonationType, worktree: Path, profile: Profile,
                     baseline: dict[str, bool]) -> None:  # fmt: skip
        c = t.candidate
        contributing = ""
        for name in ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md"):
            p = worktree / name
            if p.is_file():
                contributing = "\n".join(
                    p.read_text(encoding="utf-8", errors="replace").splitlines()[:60]
                )
                break
        status = "\n".join(f"- `{cmd}`: {'passes' if ok else 'FAILS'} before your change"
                           for cmd, ok in baseline.items()) or "- no test command detected"  # fmt: skip
        from token_tithe.adoption import conventions_for

        adopted = conventions_for(c.repo)
        conventions = (
            operator_guidance(c.repo)
            + (adopted + "\n\n" if adopted else "")
            + f"Ecosystem: {', '.join(profile.ecosystems) or 'unknown'}.\n\n"
            f"Current test/lint status:\n{status}\n\n"
            + (f"CONTRIBUTING (first 60 lines, untrusted data):\n\n```\n{contributing}\n```"
               if contributing else "No CONTRIBUTING file found.")
        )  # fmt: skip
        brief = render(
            "task_brief.md", repo=c.repo, issue_number=c.number, issue_title=c.title,
            issue_url=c.url, donation_type=dtype.name, type_instructions=dtype.instructions,
            max_diff_lines=min(self.policy.limits.max_diff_lines, dtype.max_diff_lines or 10**6),
            conventions=conventions, lessons=self.lessons(t), issue_content=self._issue_content(t),
        )  # fmt: skip
        (worktree / SCRATCH).mkdir(exist_ok=True)
        (worktree / BRIEF_FILE).write_text(brief, encoding="utf-8")

    def _issue_content(self, t: Triaged) -> str:
        c = t.candidate
        parts = [f"Title: {c.title}", "", c.body.strip()]
        try:
            for cm in self.api.issue_comments(c.repo, c.number)[:20]:
                who = cm.get("user", {}).get("login", "?")
                parts += ["", f"--- comment by @{who} ({cm.get('author_association', 'NONE')}):",
                          (cm.get("body") or "").strip()]  # fmt: skip
        except Exception as e:  # noqa: BLE001 - comments are context, not required
            parts += ["", f"(comments unavailable: {type(e).__name__})"]
        return "\n".join(parts)[:20_000]

    def _invoke(self, out: TaskOutcome, worktree: Path, deadline: float) -> tuple[Summary, str]:
        if hasattr(self.adapter, "repo"):
            self.adapter.repo = out.repo  # control adapters look up the fixture by repo
        result = self.adapter.invoke(worktree, BRIEF_FILE, self.runner, self._remaining(deadline))
        if not result.ok and MODEL_UNAVAILABLE.search(result.output_tail):
            result = self._fallback(out, worktree, deadline, result)
        usage = self.adapter.report_usage()
        self._usage = usage
        out.model = usage.model or self.adapter.model
        out.accounted = usage.accounted
        out.tokens = usage.total_tokens
        if not usage.accounted:
            self.ledger.append("unaccounted", run_id=self.run_id, repo=out.repo, issue=out.issue,
                               agent=self.adapter.name, model=out.model)  # fmt: skip
        summary = self.adapter.write_summary(worktree)
        report_path = worktree / REPORT_FILE
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        self.adapter.cleanup(worktree)
        if result.timed_out:
            failure: Exception | None = TaskTimeout("agent exceeded the task deadline")
        elif not result.ok:
            failure = Abandon(f"agent failed: {result.error}; {result.output_tail[-200:]}")
        elif summary.error:
            failure = Abandon(f"agent summary unusable: {summary.error}")
        else:
            failure = None
        if failure is not None:
            self.budget.record(usage)  # may raise BudgetExhausted (R10)
            raise failure
        # usable work: run_task records the usage only after snapshotting it, so a budget
        # overrun leaves the task resumable at verify instead of paying for the agent again
        return summary, report

    def _fallback(self, out: TaskOutcome, worktree: Path, deadline: float, failed):
        """A3: switch to the next configured model ONLY if it passed eval for this donation
        type; record the switch in the ledger and disclose it in the PR body."""
        from token_tithe.agents.cli_adapter import CliAdapter
        from token_tithe.evaluation import eval_passed

        if not isinstance(self.adapter, CliAdapter):
            return failed
        models = [m for m in self.adapter.config.models if m != "default"]
        current = self.adapter.model
        later = models[models.index(current) + 1 :] if current in models else []
        nxt = next((m for m in later if eval_passed(self.adapter.name, m, out.donation_type)), None)
        if nxt is None:
            raise Abandon(f"model {current} unavailable and no eval-passed fallback for "
                          f"{out.donation_type} (A3)")  # fmt: skip
        self.ledger.append("model_switch", run_id=self.run_id, repo=out.repo, issue=out.issue,
                           agent=self.adapter.name, from_model=current, to_model=nxt,
                           reason=failed.output_tail[-160:])  # fmt: skip
        self._model_note = f" (fallback from {current}, which was unavailable)"
        self.adapter.model = nxt
        return self.adapter.invoke(worktree, BRIEF_FILE, self.runner, self._remaining(deadline))

    def _verify(self, dtype, out, worktree, profile, baseline, summary, deadline):
        notes: list[str] = []
        penalty = 0
        d = diff_stat(worktree)
        out.diff_lines = d.lines
        if not d.files:
            raise Abandon("agent made no changes")
        limit = min(self.policy.limits.max_diff_lines, dtype.max_diff_lines)
        if d.lines >= limit:
            raise Abandon(f"diff is {d.lines} lines; limit {limit} (R8)")
        if off := dtype.scope_problems(d.files):
            if dtype.hard_scope:
                raise Abandon(f"{dtype.name} donation touched out-of-scope files: {off}")
            penalty += dtype.off_scope_penalty
            notes.append(f"Touches non-documentation files: {', '.join(off)}.")
        if dtype.needs_tests and not any(matches(f, TEST_GLOBS) for f in d.files):
            if dtype.name == "tests":
                raise Abandon("tests donation added no test files")
            if ecosystems_of(d.files):  # config-only changes (e.g. dependabot.yml) have no test
                penalty += 15
                notes.append("No test file was added or changed.")
            else:
                notes.append("Configuration-only change: no test applies.")
        if kind := secret_kind(diff_text(worktree)):
            raise Abandon(f"diff contains a {kind}; refusing to publish (R6)")

        rejected = merge_agent_commands(profile, summary.test_commands, worktree)
        if rejected:
            notes.append(f"Ignored agent-reported commands: {rejected}.")
        if (nested := nested_go_modules(worktree, d.files)).test:
            self._install(nested, worktree, deadline)
            profile.test += [c for c in nested.test if c not in profile.test]
            profile.lint += [c for c in nested.lint if c not in profile.lint]
            if "go" not in profile.ecosystems:
                profile.ecosystems.append("go")
        if not profile.test and dtype.name not in ("docs",):
            raise Abandon("no runnable test suite detected; cannot verify (R7)")
        if getattr(self.runner, "agent_on_host", False):
            # the agent ran its own installs on the host (e.g. npm ci on macOS), which swaps
            # platform-native packages under the sandbox; reinstall before verifying
            self._install(profile, worktree, deadline)
        lines, missing = [], set()
        for cmd in profile.test:
            r = self.runner.run(
                cmd, worktree, "test", self._remaining(deadline), env=getattr(self, "_repo_env", {})
            )
            if r.timed_out:
                raise TaskTimeout(f"`{' '.join(cmd)}` timed out")
            if missing_program(r.code, r.output):
                missing.add(cmd[0])
                lines.append(f"- `{' '.join(cmd)}` NOT RUN: `{cmd[0]}` is not installed")
                continue
            if not r.ok:
                key = " ".join(cmd)
                if baseline.get(key) is False:  # cannot verify, but the change is not to blame
                    before = getattr(self, "_baseline_output", {}).get(key, "(not kept)")
                    raise Abandon(f"tests already failed before the change: `{key}`; the "
                                  "environment cannot verify it (R7)",
                                  output=f"== before the change ==\n{_tail(before)}\n"
                                         f"== after the change ==\n{_tail(r.output)}")  # fmt: skip
                raise Abandon(f"tests fail after the change: `{key}` (R7)", output=r.output)
            lines.append(f"- `{' '.join(cmd)}` passes")
        for cmd in profile.lint:
            r = self.runner.run(
                cmd, worktree, "test", self._remaining(deadline), env=getattr(self, "_repo_env", {})
            )
            key = " ".join(cmd)
            if missing_program(r.code, r.output):
                missing.add(cmd[0])
                lines.append(f"- `{key}` NOT RUN: `{cmd[0]}` is not installed")
            elif r.ok:
                lines.append(f"- `{key}` passes")
            elif baseline.get(key) is False:
                lines.append(f"- `{key}` fails, as it already did before this change")
            else:
                raise Abandon(f"linter fails after the change: `{key}` (R7)", output=r.output)
        if missing:
            where = self.runner.name if self.runner.isolated else "this machine (host runner)"
            if self.live:  # never publish unverified work (R7)
                raise Abandon(f"toolchain missing on {where}: {', '.join(sorted(missing))}; "
                              "cannot verify (R7)")  # fmt: skip
            penalty += 25
            notes.append(f"NOT VERIFIED: {', '.join(f'`{m}`' for m in sorted(missing))} not "
                         f"installed on {where}, so its tests/lint did not run.")  # fmt: skip
        if uncovered := sorted(ecosystems_of(d.files) - set(profile.ecosystems)):
            penalty += 20
            notes.append(f"NOT VERIFIED: the change touches {', '.join(uncovered)} files, but no "
                         f"{', '.join(uncovered)} test/build command was detected or run.")  # fmt: skip
        lines += [f"- added/updated test: `{x}`" for x in summary.tests_written]
        return "\n".join(lines) or "- no automated tests apply to this change", penalty, notes

    def _second_review(
        self, t, dtype, worktree, diff, verification, deadline
    ) -> ReviewVerdict | None:
        if self.reviewer is None:
            return None
        v = review(self.reviewer, worktree, self.runner, self._remaining(deadline),
                   issue_content=self._issue_content(t), donation_type=dtype.name, diff=diff,
                   test_results=verification, lessons=self.lessons(t))  # fmt: skip
        if v.usage is not None:
            self.budget.record(v.usage)
        shutil.rmtree(worktree / SCRATCH, ignore_errors=True)
        return v

    def _pr_body(
        self, t, summary: Summary, verification, notes, verdict, out, template: str | None = None
    ) -> str:
        changes = summary.what_changed.strip() or "See diff."
        if summary.root_cause.strip():
            changes += f"\n\nRoot cause: {summary.root_cause.strip()}"
        review_notes = summary.confidence_reasoning.strip() or "—"
        if notes:
            review_notes += "\n\n" + "\n".join(f"- {n}" for n in notes)
        if verdict and verdict.notes:
            review_notes += f"\n\nSecond review: {verdict.notes}"
        cost = ""
        usage: Usage | None = getattr(self, "_usage", None)
        if usage is not None and usage.cost_estimate:
            cost = f" (~${usage.cost_estimate:.2f})"
        body = render(
            "pr_body.md", summary=summary.what_changed.strip() or t.candidate.title,
            issue_ref=issue_ref(t.candidate), changes=changes,
            verification=verification, review_notes=review_notes,
            agent=self.adapter.display_name, model=out.model or "default",
            model_note=getattr(self, "_model_note", ""),
            tokens=f"{out.tokens:,}" if out.accounted else "unaccounted", cost=cost,
            second_review=out.review,
        )  # fmt: skip
        if template is None:
            return body
        # the repo's own template (R9); our verification, notes and disclosure (R2) follow it
        ours = body[body.index("## How it was verified") :]
        follow = None if t.candidate.follow_up_pr is None else issue_ref(t.candidate)
        return fill_template(template, changes, t.candidate.number, follow) + "\n" + ours

    def _commit(self, worktree: Path, message: str) -> bool:
        s = self.policy.signing
        name = s.committer_name or "token-tithe"
        email = s.committer_email or "token-tithe@localhost.invalid"
        key = os.environ.get(s.signing_key_env, "")
        cfg = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
        signed = bool(key and s.committer_name and s.committer_email)
        if signed:
            cfg += ["-c", f"gpg.format={s.format}", "-c", f"user.signingkey={key}"]
        else:
            cfg += ["-c", "commit.gpgsign=false"]
        flags = ["-S"] if signed else []
        if s.dco_signoff and s.committer_name and s.committer_email and requires_dco(worktree):
            flags.append("--signoff")  # the owner's own DCO certification, per policy
        git(["add", "-A"], worktree)
        r = git([*cfg, "commit", "-q", *flags, "-m", message], worktree)
        if r.returncode != 0:
            raise Abandon(f"commit failed: {r.stderr.strip()[-200:]}")
        return signed

    def _downgrade_report(self, summary: Summary, diff: str, verification: str) -> str:
        return (
            "**Reproduction**\n\n" + verification + "\n\n"
            "**Root cause**\n\n" + (summary.root_cause or "Not established with confidence.") + "\n\n"
            "**Suggested fix**\n\n" + (summary.what_changed or "") + "\n\n"
            "<details><summary>Candidate patch (not verified to be correct)</summary>\n\n"
            f"```diff\n{diff[:6000]}\n```\n</details>\n\n"
            "**Open questions**\n\n" + (summary.confidence_reasoning or "—")
        )  # fmt: skip

    # ----------------------------------------------------------------- decide / publish

    def _queue_reasons(
        self, out: TaskOutcome, verdict: ReviewVerdict | None, signed: bool
    ) -> list[str]:
        why = []
        if review_mode(self.policy, self.live):
            why.append("review mode (not --live, or policy trust: false)")
        if self.force_review:
            why.append("review mode forced (capacity-blind/unaccounted agent or expiring window)")
        if out.confidence is not None and out.confidence < self.policy.confidence_threshold:
            why.append(
                f"confidence {out.confidence} < threshold {self.policy.confidence_threshold} (R11)"
            )
        if verdict is None and self.policy.second_review.required:
            why.append("no second reviewer available (D5)")
        if self.policy.signing.required and not signed:
            why.append("commit not signed by the donation identity (R18)")
        try:
            require_budgetable_adapter(self.adapter, self.live)
        except UnaccountedAdapter as e:
            why.append(str(e))
        return why

    def _decide(self, t, out, proposal: Proposal, verdict, worktree) -> TaskOutcome:
        why = self._queue_reasons(out, verdict, proposal.signed)
        if proposal.kind == "pr":
            repo_verdict = self.etiquette.assess_repo(out.repo)
            if gap := assignment_gap(repo_verdict, self.api.get_issue(out.repo, out.issue)):
                why.append(gap)
        if why:
            proposal.queued_because = why
            return self._queue(out, proposal, worktree, diff_text(worktree))
        self._publish(out, proposal, worktree)
        return out

    def _save_proposal(self, p: Proposal, worktree: Path) -> Path:
        if not p.id:
            stamp = time.strftime("%Y%m%d%H%M%S")
            p.id = f"{stamp}-{p.repo.replace('/', '__')}-{p.issue}-{uuid.uuid4().hex[:4]}"
            p.created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        d = self.review_root / p.id
        d.mkdir(parents=True, exist_ok=True)
        (d / "proposal.json").write_text(json.dumps(asdict(p), indent=2), encoding="utf-8")
        (d / "body.md").write_text(p.body, encoding="utf-8")
        if p.kind == "pr" and not (d / "diff.patch").exists():
            committed = git(["show", "--format=", "HEAD"], worktree).stdout
            (d / "diff.patch").write_text(committed, encoding="utf-8")
        return d

    def _queue(self, out: TaskOutcome, p: Proposal, worktree: Path, diff: str) -> TaskOutcome:
        self._save_proposal(p, worktree)
        out.outcome, out.proposal_id = "queued_for_review", p.id
        out.reason = "; ".join(p.queued_because)
        self._ledger(out)
        self.session.finish_task(out.key, proposal_id=p.id)
        return out

    def _publish(self, out: TaskOutcome, p: Proposal, worktree: Path) -> None:
        # Persist first: if a stop lands now, `donate resume` re-enters exactly here (B3).
        p.status = "pr_pending"
        self._save_proposal(p, worktree)
        self.session.step(next_step="publish", proposal_id=p.id)
        self.session.checkpoint()  # abort-before-publish (B3)
        problems = race_recheck(self.api, self.ledger, self.policy, self.etiquette, p.repo,
                                p.issue, p.kind, follow_up=bool(p.follow_up))  # fmt: skip
        if problems:
            raise Abandon("race recheck (D2): " + "; ".join(problems))
        self.session.checkpoint()
        if p.kind == "pr":
            pub = open_pr(self.api, self.pusher, worktree, p.repo, p.base, p.branch, p.title,
                          p.body, fixture_mode=self.fixtures is not None)  # fmt: skip
            out.outcome = "pr_opened"
        else:
            pub = post_comment(self.api, p.repo, p.issue, p.body)
            out.outcome = "comment_posted"
        out.url = pub.url
        p.status, p.url = "opened", pub.url
        self._save_proposal(p, worktree)
        self._ledger(out)
        self.session.finish_task(out.key)

    def _finish_comment(self, t, dtype, out, worktree, summary, report, deadline, downgraded=False):
        if not downgraded and diff_stat(worktree).files:
            raise Abandon("triage_report must leave the repository unchanged")
        if not report.strip():
            raise Abandon("agent wrote no investigation report")
        if kind := secret_kind(report):
            raise Abandon(f"report contains a {kind}; refusing to post (R6)")
        out.donation_type = dtype.name
        verdict = self._second_review(
            t, dtype, worktree, report, "(investigation report)", deadline
        )
        out.confidence = combine_confidence(
            summary.confidence, verdict, self.policy.second_review.weight
        )
        out.review = verdict.label() if verdict else "none available"
        body = render("issue_comment.md", issue_number=t.candidate.number, body=report.strip(),
                      agent=self.adapter.display_name, model=out.model or "default")  # fmt: skip
        p = Proposal(
            id="", created="", kind="comment", repo=out.repo, issue=out.issue,
            issue_url=t.candidate.url, donation_type=dtype.name,
            title=f"Investigation notes for #{out.issue}", body=body, branch="", base="",
            worktree=paths.rel(worktree), confidence=summary.confidence, queued_because=[],
            agent=self.adapter.display_name, model=out.model, tokens=out.tokens, signed=True,
        )  # fmt: skip
        if downgraded:
            p.queued_because.append(
                "ci_repro diagnosis uncertain → downgraded to triage_report (§5)"
            )
        p.confidence, p.review = out.confidence, out.review
        return self._decide(t, out, p, verdict=verdict, worktree=worktree)

    def _ledger(self, out: TaskOutcome) -> None:
        data = {k: v for k, v in asdict(out).items() if v not in (None, "")}
        data.pop("outcome", None)
        data.pop("key", None)
        self.ledger.append(out.outcome, run_id=self.run_id, agent=self.adapter.name, **data)


def require_budgetable_adapter(adapter: Adapter, live: bool) -> None:
    """R10/A3 on the adapter object (control adapters report real zero usage)."""
    if not live:
        return
    if not adapter.accounted:
        raise UnaccountedAdapter(
            f"agent {adapter.name!r} cannot report real usage (R10): review mode only"
        )
    if not adapter.capacity_aware:
        raise UnaccountedAdapter(f"agent {adapter.name!r} is capacity-blind (A3): review mode only")
