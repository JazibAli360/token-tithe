"""`donate` command-line entrypoint. Commands are added phase by phase."""

from __future__ import annotations

import json
import os

import typer

from token_tithe import __version__

app = typer.Typer(
    name="donate",
    help="token-tithe: donate leftover AI-agent tokens to open source.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,  # locals can hold secrets (R6)
)


@app.callback()
def main() -> None:
    """token-tithe: donate leftover AI-agent tokens to open source."""


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(f"token-tithe {__version__}")


@app.command()
def doctor(
    offline: bool = typer.Option(False, "--offline", help="Skip the network probe."),
) -> None:
    """Verify primitives, config, self-containment (S6) and secret hygiene (R6)."""
    from token_tithe.doctor import FAIL, render, run_doctor

    checks = run_doctor(offline=offline)
    typer.echo(render(checks))
    raise typer.Exit(1 if any(c.status == FAIL for c in checks) else 0)


def _policy(fixtures: bool):
    from token_tithe.policy import for_fixtures, load_policy

    return for_fixtures(load_policy()) if fixtures else load_policy()


def _api(fixtures: bool):
    from token_tithe.github_api import GitHubAPI

    if fixtures:
        return GitHubAPI.fixture(live=False)
    if not os.environ.get("GITHUB_TOKEN"):
        typer.echo("warning: GITHUB_TOKEN unset — search is heavily rate-limited", err=True)
    return GitHubAPI.rest(live=False)


@app.command()
def discover(
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Discovery never writes (R5)."),
    fixtures: bool = typer.Option(False, "--fixtures", help="Offline: search eval fixtures."),
) -> None:
    """Search GitHub for candidate issues. Read-only."""
    from token_tithe.discover import discover as run_discover
    from token_tithe.pipeline import check_halt
    from token_tithe.policy import load_repos

    check_halt()
    res = run_discover(_api(fixtures), _policy(fixtures), load_repos())
    typer.echo(f"{'[dry-run] ' if dry_run else ''}{len(res.candidates)} candidates")
    for c in res.candidates:
        typer.echo(f"  {c.key:<40} ★{c.stars:<6} {c.language or '?':<11} {c.title}")
    if res.skipped:
        typer.echo(f"\nskipped ({len(res.skipped)}):")
        for sk in res.skipped:
            typer.echo(f"  {sk.target}: {sk.reason}")


@app.command(name="triage")
def triage_cmd(
    fixtures: bool = typer.Option(False, "--fixtures", help="Offline: use eval fixtures."),
    limit: int = typer.Option(20, help="How many queue entries to print."),
) -> None:
    """discover → etiquette → triage. Writes state/queue.json and a markdown report."""
    from datetime import UTC, datetime

    from token_tithe import paths
    from token_tithe.pipeline import build_queue, render_markdown, render_queue
    from token_tithe.policy import load_repos
    from token_tithe.triage import write_queue

    paths.ensure_state()
    res = build_queue(_api(fixtures), _policy(fixtures), load_repos())
    typer.echo(render_queue(res, limit))
    write_queue(res.ranked, paths.QUEUE)
    report = paths.REPORTS / f"triage-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.md"
    report.write_text(render_markdown(res), encoding="utf-8")
    typer.echo(f"\nqueue: {paths.rel(paths.QUEUE)}   report: {paths.rel(report)}")


# a list-typed option cannot be built in the signature (ruff B008)
_ONLY_OPTION = typer.Option(
    None, "--only", help="Work only on these issues (owner/repo#N), repeatable."
)


_FOLLOW_UP_OPTION = typer.Option(
    None,
    "--follow-up",
    help="Our closed PR (owner/repo#PR) whose maintainer asked for a different version; repeatable.",
)


@app.command()
def run(
    live: bool = typer.Option(False, "--live", help="Allow GitHub writes (R5). Default: dry-run."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Plan only: print exactly what would run."
    ),
    fixtures: bool = typer.Option(
        False, "--fixtures", help="Offline: fixture repos + control agent."
    ),
    agent: str = typer.Option("", help="Override routing with this agent."),
    max_tasks: int = typer.Option(0, help="Cap tasks this run (default: policy)."),
    only: list[str] | None = _ONLY_OPTION,
    follow_up: list[str] | None = _FOLLOW_UP_OPTION,
) -> None:
    """One donation run. Without --live (or with trust: false) work lands in state/review/."""
    from token_tithe.run import donate_run

    if live and dry_run:
        raise typer.BadParameter("--live and --dry-run are mutually exclusive")
    res = donate_run(live=live, fixtures=fixtures, plan_only=dry_run, agent=agent or None,
                     max_tasks=max_tasks or None, only=only, follow_ups=follow_up,
                     echo=typer.echo)  # fmt: skip
    raise typer.Exit(0 if res.status in ("completed", "planned", "insufficient_capacity") else 1)


@app.command()
def review(
    open_: str = typer.Option("", "--open", help="Approve proposal N (number or id)."),
    discard: str = typer.Option("", "--discard", help="Discard proposal N."),
    live: bool = typer.Option(False, "--live", help="Required to actually publish (R5)."),
    fixtures: bool = typer.Option(False, "--fixtures", help="Offline fixture GitHub."),
) -> None:
    """List the review queue, approve (--open N --live) or discard (--discard N) a proposal."""
    from token_tithe import review as rq
    from token_tithe.etiquette import Etiquette
    from token_tithe.github_api import GitHubAPI, WriteNotAllowed
    from token_tithe.ledger import Ledger
    from token_tithe.policy import load_repos
    from token_tithe.publish import GitPusher, RecordingPusher

    try:
        if discard:
            p = rq.discard(discard, Ledger())
            typer.echo(f"discarded {p.repo}#{p.issue}")
        elif open_:
            api = GitHubAPI.fixture(live=live) if fixtures else GitHubAPI.rest(live=live)
            pub = rq.open_proposal(
                open_, live=live, api=api, policy=_policy(fixtures), ledger=Ledger(),
                etiquette=Etiquette(api, cla_signed=load_repos().cla_signed, cache_dir=None),
                pusher=RecordingPusher() if fixtures else GitPusher(), fixture_mode=fixtures,
            )  # fmt: skip
            typer.echo(f"opened {pub.kind}: {pub.url}")
        else:
            typer.echo(rq.render_list(rq.load()))
    except (rq.ReviewError, WriteNotAllowed) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None


@app.command()
def capacity(fixtures: bool = typer.Option(False, "--fixtures")) -> None:
    """Live capacity table: agents, auth, quota, GitHub rate limit, what this run can afford."""
    from token_tithe import capacity as cap
    from token_tithe.ledger import Ledger
    from token_tithe.policy import load_agents, load_policy

    api = None if fixtures else _api(False)
    typer.echo(cap.probe(load_policy(), Ledger(), load_agents(), api).render())


@app.command()
def halt() -> None:
    """Kill switch ON (R12): every run refuses to start until `donate resume`."""
    from token_tithe import paths

    paths.ensure_state()
    paths.HALT_FLAG.write_text("halted\n")
    typer.echo("HALT set (state/HALT). `donate resume` clears it.")


@app.command()
def stop() -> None:
    """Immediate cooperative stop of a running run; it writes a handover and exits (B3)."""
    from token_tithe.handover import request_stop

    request_stop()
    typer.echo("STOP requested: the running run stops at its next checkpoint, before any write.")


@app.command()
def resume(
    live: bool = typer.Option(False, "--live"),
    fixtures: bool = typer.Option(False, "--fixtures"),
) -> None:
    """Clear HALT and continue the last run from its recorded next step."""
    from token_tithe.run import resume_run

    res = resume_run(live=live, fixtures=fixtures, echo=typer.echo)
    raise typer.Exit(0 if res.status in ("completed", "nothing_to_resume") else 1)


@app.command()
def handover() -> None:
    """Print state/HANDOVER.md."""
    from token_tithe import paths

    if paths.HANDOVER_MD.is_file():
        typer.echo(paths.HANDOVER_MD.read_text(encoding="utf-8"))
    else:
        typer.echo("no handover yet — nothing has run")


@app.command()
def feedback(fixtures: bool = typer.Option(False, "--fixtures")) -> None:
    """Check every PR we opened: merged / closed / open / stale. Feeds triage scoring."""
    from token_tithe.feedback import check_feedback, render_feedback
    from token_tithe.ledger import Ledger

    typer.echo(render_feedback(check_feedback(_api(fixtures), Ledger(), _policy(fixtures))))


@app.command()
def incident(
    pr_url: str,
    reason: str = typer.Option(..., help="What went wrong, in one plain sentence."),
    live: bool = typer.Option(False, "--live", help="Actually post the apology (R5)."),
) -> None:
    """Incident procedure: apology comment, 90-day blocklist, ledger `incident`. Never argues."""
    from token_tithe.feedback import incident as run_incident
    from token_tithe.ledger import Ledger
    from token_tithe.policy import load_policy

    api = _api(False)
    if live:
        from token_tithe.github_api import GitHubAPI

        api = GitHubAPI.rest(live=True)
    res = run_incident(api, Ledger(), load_policy(), pr_url, reason, agent="token-tithe operator",
                       live=live)  # fmt: skip
    typer.echo(res["body"] if not live else f"apology posted: {res['comment']}")
    typer.echo(f"{res['repo']} blocklisted until {res['until']} (config/repos.yaml)")


@app.command()
def report(month: str = typer.Option("", help="YYYY-MM (default: this month)")) -> None:
    """Monthly ledger report → state/reports/report-<month>.md."""
    from datetime import UTC, datetime

    from token_tithe import reports
    from token_tithe.ledger import Ledger

    m = month or datetime.now(UTC).strftime("%Y-%m")
    text = reports.report(Ledger(), m)
    f = reports.write(text, f"report-{m}.md")
    typer.echo(text + f"\nwritten: {f.relative_to(f.parents[2])}")


@app.command()
def journal(month: str = typer.Option("", help="YYYY-MM (default: this month)")) -> None:
    """Readable monthly donation retrospective → state/reports/journal-<month>.md."""
    from datetime import UTC, datetime

    from token_tithe import reports
    from token_tithe.ledger import Ledger

    m = month or datetime.now(UTC).strftime("%Y-%m")
    text = reports.journal(Ledger(), m)
    f = reports.write(text, f"journal-{m}.md")
    typer.echo(text + f"\nwritten: {f.relative_to(f.parents[2])}")


@app.command()
def adopt(repo: str, fixtures: bool = typer.Option(False, "--fixtures")) -> None:
    """Adopt a repo: profile it, add it to repos.yaml `adopted`, search it first."""
    from token_tithe.adoption import adopt as run_adopt
    from token_tithe.etiquette import Etiquette

    api = _api(fixtures)
    try:
        prof = run_adopt(api, repo, Etiquette(api, cache_dir=None))
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(json.dumps(prof, indent=2))


@app.command()
def address(
    pr_url: str,
    live: bool = typer.Option(False, "--live", help="Commit, push and comment (R5)."),
    agent: str = typer.Option("", help="Agent to use (default: routing default)."),
) -> None:
    """Address maintainer review on one of OUR PRs (ledger-verified, max 3 rounds, 14 days)."""
    from token_tithe.address import AddressRefused
    from token_tithe.address import address as run_address
    from token_tithe.agents import get_adapter
    from token_tithe.github_api import GitHubAPI
    from token_tithe.ledger import Ledger
    from token_tithe.policy import load_policy
    from token_tithe.publish import GitPusher
    from token_tithe.sandbox import select_runner

    policy = load_policy()
    try:
        res = run_address(pr_url, api=GitHubAPI.rest(live=live), ledger=Ledger(), policy=policy,
                          adapter=get_adapter(agent or policy.routing.default),
                          runner=select_runner(policy, live), pusher=GitPusher(), live=live)  # fmt: skip
    except AddressRefused as e:
        typer.echo(f"refused: {e}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"round {res.round}: {res.status} — {res.detail} {res.comment_url}".rstrip())


@app.command()
def optimize(
    apply: bool = typer.Option(False, "--apply", help="Rewrite routing in policy.yaml."),
) -> None:
    """Cost per merged PR per agent × model × language; propose (or --apply) routing."""
    from token_tithe import optimizer
    from token_tithe.ledger import Ledger
    from token_tithe.policy import load_policy

    proposal = optimizer.propose(load_policy(), Ledger())
    f = optimizer.write_proposal(proposal)
    typer.echo(optimizer.render(proposal) + f"\n\nproposal written: {f.name} in state/reports/")
    if apply:
        if proposal["changes"]:
            optimizer.apply(proposal)
            typer.echo("applied to config/policy.yaml routing.by_language")
        else:
            typer.echo("nothing to apply")


@app.command()
def learn(
    apply: bool = typer.Option(False, "--apply", help="Promote pending lessons (human approval)."),
    ids: str = typer.Option("", help="Comma-separated P-ids to promote (default: all open)."),
    reject: str = typer.Option("", help="Comma-separated P-ids to reject."),
) -> None:
    """Distill the ledger + latest eval into PENDING lessons; --apply promotes them (R19)."""
    from token_tithe.brain import Brain
    from token_tithe.learn import apply as apply_lessons
    from token_tithe.learn import learn as run_learn
    from token_tithe.ledger import Ledger
    from token_tithe.policy import load_policy

    brain = Brain.load(max_lines=load_policy().brain.max_lines)
    if reject:
        for pid in reject.split(","):
            brain.reject(pid.strip())
        typer.echo(f"rejected {reject}")
    elif apply:
        for pid, lid in apply_lessons(brain, [i.strip() for i in ids.split(",") if i.strip()]):
            typer.echo(f"promoted {pid} → {lid}")
    else:
        res = run_learn(Ledger(), brain)
        typer.echo(f"{res['candidates']} candidates, {len(res['added'])} new pending: "
                   f"{', '.join(res['added']) or '—'}")  # fmt: skip
        for p in brain.open_pending():
            typer.echo(f"  {p.id} → {p.section} [{p.origin}] {p.text[:110]}")
    brain.save()


brain_app = typer.Typer(help="The Brain: show, review, export/import/sync, supersede.")
app.add_typer(brain_app, name="brain")


@brain_app.command("show")
def brain_show() -> None:
    from token_tithe import paths

    typer.echo(paths.BRAIN.read_text(encoding="utf-8"))


@brain_app.command("review")
def brain_review() -> None:
    """Flag lessons not reinforced within brain.stale_after_days (marked, never removed)."""
    from token_tithe.brain import Brain
    from token_tithe.policy import load_policy

    p = load_policy()
    b = Brain.load(max_lines=p.brain.max_lines)
    flagged = b.review_stale(p.brain.stale_after_days)
    b.save()
    typer.echo(f"stale: {', '.join(flagged) or 'none'}")


@brain_app.command("export")
def brain_export(
    dest: str = typer.Option("state/reports", help="Directory for the export."),
) -> None:
    """One self-contained file: BRAIN.md + every knowledge file inlined."""
    from token_tithe import paths
    from token_tithe.brain import Brain

    f = Brain.load().export(paths.ROOT / dest)
    typer.echo(f"exported: {f}")


@brain_app.command("import")
def brain_import(file: str) -> None:
    """Merge an export; conflicts land in PENDING, never overwrite silently."""
    from pathlib import Path

    from token_tithe.brain import Brain

    b = Brain.load()
    stats = b.import_(Path(file))
    b.save()
    typer.echo(json.dumps(stats))


@brain_app.command("sync")
def brain_sync() -> None:
    """Optional: push/pull the Brain to `brain.sync_remote` (a separate git repo)."""
    from token_tithe.brain_sync import sync
    from token_tithe.policy import load_policy

    typer.echo(sync(load_policy().brain.sync_remote))


@brain_app.command("supersede")
def brain_supersede(
    lesson_id: str, text: str, prov: str = typer.Option(..., help="Evidence.")
) -> None:
    """Correct a lesson by appending a superseding one (R19: the original is never edited)."""
    from token_tithe.brain import Brain

    b = Brain.load()
    lid = b.supersede(lesson_id, text, prov)
    b.save()
    typer.echo(f"{lid} supersedes {lesson_id}")


@app.command()
def simulate(
    keep: bool = typer.Option(False, "--keep", help="Keep materialized repos under state/sim/."),
) -> None:
    """Run the pipeline against local fixture repos. No network, no GitHub writes."""
    from token_tithe.simulate import simulate as run_simulation

    report = run_simulation(keep=keep)
    typer.echo(report.render())
    raise typer.Exit(0 if report.ok else 1)


if __name__ == "__main__":  # pragma: no cover
    app()
