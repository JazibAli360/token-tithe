# Simulate

```bash
make simulate            # or: .venv/bin/donate simulate [--keep]
```

Runs the **entire** pipeline against the fixture repos in `eval/fixtures/` — no network, no
GitHub, no tokens — through the same code paths as a real run. Use it to validate every
prompt, adapter, policy or pipeline change before it touches the real world.

## What it does

| Stage | Checks |
|---|---|
| world | each fixture becomes a real git repo with one deterministic commit |
| github (fixture mode) | search and repo reads through `GitHubAPI` with the in-memory fake GitHub (R17 enforced) |
| baseline | each fixture's tests behave as its rubric says (e.g. flaky-ci fails before any fix) |
| discover → etiquette → triage | anti-ai and opt-out are rejected; the rest are ranked with rationales |
| clone gate | leaked-secret is rejected by the secrets scan; every other fixture lands as expected |
| capacity → execute → review queue | the real `donate run` with the zero-token `reference` agent, in an isolated `state/`; every runnable fix fixture must be queued |
| ledger + feedback + reports | schema_version on every line; feedback, report and journal render |
| learn → apply → inject | on a **copy** of the Brain: lessons are learned, promoted and injected into the next brief; the committed Brain is untouched |
| no GitHub writes | any write fails the simulation |

Output goes to `state/sim/<run-id>/report.txt`. `--keep` keeps the materialized repos.

## Fixtures

| Fixture | Language | Type | Expect |
|---|---|---|---|
| py-math-bug | Python | issue_fix | fix (off-by-one) |
| js-doc-gap | JavaScript | docs | fix (README API section) |
| py-untested | Python | tests | fix (tests must kill 2 mutants) |
| rs-clippy | Rust | ci_repro | fix (clippy) — skipped if cargo is absent |
| flaky-ci | Python | ci_repro | fix, never by skipping the test |
| anti-ai | Python | issue_fix | **reject** (anti-AI CONTRIBUTING) |
| opt-out | Python | issue_fix | **reject** (`.github/token-tithe-ignore`) |
| leaked-secret | Python | issue_fix | **reject** (AWS example key in tree) |

Each fixture is `eval/fixtures/<name>/repo/` (contents) + `github.yaml` (fake GitHub
metadata with relative dates, so fixtures never age). Hidden checks and reference solutions
live in `eval/expected/<name>/` and never reach the agent's worktree.

## Simulate vs eval

- **simulate** asks: does the *pipeline* behave? (one control agent, every stage)
- **eval** asks: how good is an *agent*? (every agent × every fixture, scored against rubrics)

Both are offline. Eval with a real agent (`make eval AGENTS=claude`) spends real tokens.
See [AGENT_INTEGRATION.md](AGENT_INTEGRATION.md).
