"""Run every agent against every fixture and write state/eval_runs/report.md (§7).

    .venv/bin/python eval/run_eval.py                 # all fixtures, control agents
    .venv/bin/python eval/run_eval.py --fast          # CI subset: python fixtures + reject fixtures
    .venv/bin/python eval/run_eval.py --fixtures py-math-bug,anti-ai --keep

Exit status is non-zero if the harness self-check fails (reference must pass every runnable
fixture, null must fail every fix fixture) or if any non-control agent fails a fixture.
"""

from __future__ import annotations

import argparse
import sys

from token_tithe import paths
from token_tithe.agents import get_adapter
from token_tithe.etiquette import Etiquette
from token_tithe.evaluation import (
    CONTROL_AGENTS,
    FAIL,
    AdapterAgent,
    record_passes,
    run_eval,
    write_report,
)
from token_tithe.fixtures import load_fixtures
from token_tithe.github_api import GitHubAPI
from token_tithe.pipeline import gate
from token_tithe.policy import PolicyError

FAST = {"py-math-bug", "py-untested", "flaky-ci", "anti-ai", "opt-out", "leaked-secret"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--agents", default="reference,null",
                    help="reference, null, or configured agents like claude or claude:<model>")  # fmt: skip
    ap.add_argument("--fixtures", default="", help="comma-separated fixture names (default: all)")
    ap.add_argument("--fast", action="store_true", help="CI subset")
    ap.add_argument("--keep", action="store_true", help="keep worktrees under state/eval_runs/")
    ap.add_argument("--no-screen", action="store_true", help="skip the engine etiquette gate")
    args = ap.parse_args(argv)

    agents = []
    for spec in filter(None, args.agents.split(",")):
        name, _, model = spec.partition(":")
        if name in CONTROL_AGENTS:
            agents.append(CONTROL_AGENTS[name]())
            continue
        try:
            agents.append(AdapterAgent(get_adapter(name, model=model or None)))
        except PolicyError as e:
            print(f"unknown agent {spec!r}: {e}", file=sys.stderr)
            return 2

    fixtures = load_fixtures()
    if args.fixtures:
        wanted = set(args.fixtures.split(","))
        fixtures = [f for f in fixtures if f.name in wanted]
    if args.fast:
        fixtures = [f for f in fixtures if f.name in FAST]

    paths.ensure_state()
    et = Etiquette(GitHubAPI.fixture(), cache_dir=None)

    def screen(f):
        return gate(et, f.full_name, f.rubric.issue, f.repo_dir)

    run = run_eval(agents, fixtures, screen=None if args.no_screen else screen, keep_work=args.keep)
    report = write_report(run, screen_available=not args.no_screen)
    record_passes(run)

    for s in run.scores:
        print(f"{s.outcome:<4}  {s.agent:<10} {s.fixture}")
    problems = run.control_problems()
    real_failures = [s for s in run.scores if s.agent not in CONTROL_AGENTS and s.outcome == FAIL]
    for p in problems:
        print(f"SELF-CHECK: {p}", file=sys.stderr)
    print(f"\nreport: {paths.rel(report)}")
    return 1 if problems or real_failures else 0


if __name__ == "__main__":
    sys.exit(main())
