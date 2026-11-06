# Contributing to token-tithe

Thanks for helping. The engine's whole purpose is to be a *good citizen* of other people's
repos, so the bar for changes that affect what it publishes is high.

## Setup

```bash
make setup            # in-repo .venv, never the host python
make lint test        # must be green
make doctor simulate  # self-containment + offline end-to-end
make eval             # required for adapter / prompt / triage changes
```

## Rules for changes

- Put files in their folder (README "Repository map"); nothing new at the repo root.

- Keep the closed world: no absolute paths, no `~/`, no runtime fetches except live GitHub
  API data, all mutable state under `state/`. `donate doctor` enforces this.
- Never weaken a hard rule (R1–R19 in CLAUDE.md / AGENTS.md) without discussing it in an
  issue first. Changes that make the engine *more* conservative are welcome anytime.
- Any change to prompts, adapters, triage, or etiquette must pass `make simulate` and
  `make eval`, and the PR should include the relevant part of `state/eval_runs/report.md`.
- New agents: follow docs/AGENT_INTEGRATION.md. An agent is only added to default routing
  after it passes the eval suite.
- Pin new dependencies exactly in `pyproject.toml`.
- Commit style: imperative, scoped — `etiquette: detect "no LLM" phrasing in PR templates`.
