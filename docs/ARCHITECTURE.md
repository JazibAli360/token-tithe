# Architecture

token-tithe is a single Python package (`src/token_tithe/`) driven by one CLI (`donate`).
Configuration lives in `config/`, user-facing text in `templates/`, durable knowledge in
`brain/`, test fixtures in `eval/`, and **all** mutable state in `state/` (gitignored).

## Data flow

```
                     config/policy.yaml · config/repos.yaml · config/agents/*.yaml
                                              │ (policy.py: strict, typed)
                                              ▼
 GitHub ──► discover.py ──► etiquette.py ──► triage.py ──► capacity.py ──► executor.py
 (github_api.py:            maintainer       0–100 score   sizing to       one task:
  real or fixture)          intent: reject   + rationale   real budget     clone → gate → brief →
                            / deprioritize                                 agent → verify → review
                                                                           → commit → queue|publish
                                                                                │
             ┌─────────────────────────────────────────────────────────────────┤
             ▼                                     ▼                            ▼
     state/review/ (review.py)          publish.py (race recheck,      ledger.py (every token,
     human: donate review --open        fork, push, PR/comment)        decision, outcome)
                                                                                │
                      feedback.py ◄── GitHub PR states ◄────────────────────────┤
                      (merged/closed/stale → triage history)                    │
                                                                                ▼
                         learn.py ──► brain.py (brain/BRAIN.md) ──► injected into the next
                         (rules over the ledger)   lessons, R19      task brief + review prompt
```

Every run is wrapped in a `HandoverSession` (`handover.py`) that rewrites
`state/HANDOVER.md` + `state/handover.json` at every step and on any exit (R14).

## Modules

| Module | Role | Spec |
|---|---|---|
| `paths.py` | every path, anchored to the repo root; `use_state()` for isolated runs | S2, S4 |
| `policy.py` | load + validate config into frozen dataclasses; R3/R4/R5 predicates | §2 |
| `doctor.py`, `secrets_scan.py` | primitives, self-containment, secret scan | S6, R6 |
| `github_api.py` | REST client: backoff, R5 write gate, R17 public-only; `FixtureTransport` fake GitHub | §3 |
| `discover.py` | issue search → candidates, every skip with a reason | §4 |
| `etiquette.py` | anti-AI / CLA / opt-out / injection / secrets; responsiveness | §4, R3, D4, D8 |
| `triage.py` | 0–100 score, donation-type inference, rationale | §4 |
| `pipeline.py` | discover→etiquette→triage; `gate()` = fresh verdict + clone scan; halt check | §4, R12 |
| `capacity.py` | agent auth/quota probes, cost estimates, sizing, donation window | A, D9 |
| `budget.py` | real-usage budget guard, min-viable check, live gating | R10, R15 |
| `agents/` | adapter contract, YAML-driven CLI adapter, control adapters | §6, R13 |
| `donation_types/` | issue_fix · tests · docs · ci_repro · triage_report | §5 |
| `toolchain.py` | detect install/test/lint commands per ecosystem | R7 |
| `sandbox.py` | Docker per command (`--network none` for tests) or local | R16, D1 |
| `executor.py` | one task end to end, snapshots for resume, model fallback | §4, D2, D3, A3 |
| `second_review.py` | cross-model review and confidence combination | D5, R11 |
| `publish.py` | race recheck, fork, push (askpass), PR, comment | D2, R1, R6 |
| `review.py` | human review queue | §4 review mode |
| `run.py` | `donate run` / `donate resume` orchestration | A, B |
| `handover.py` | handover files, stop flag, signal handling | B |
| `ledger.py` | append-only JSONL with `schema_version` | D11 |
| `feedback.py` | PR outcome tracking, incident procedure | §7 |
| `reports.py` | monthly report and journal | D11 |
| `adoption.py` | adopted-repo profiles | §3 |
| `address.py` | respond to review on our PRs | D6 |
| `optimizer.py` | cost per merged PR → routing | D10 |
| `brain.py`, `learn.py`, `brain_sync.py` | the Brain and its learning loop | E |
| `fixtures.py`, `evaluation.py`, `simulate.py` | fixture repos, eval harness, offline pipeline | §7 |

## The adapter layer

The engine never talks to a model directly. It talks to an **adapter** (`agents/base.py`)
with four methods: `invoke`, `report_usage`, `write_summary`, `cleanup` (plus `ask` for
review-only mode). `CliAdapter` implements them for any headless CLI from its YAML file:
argv template, prompt entry (argument/stdin/file), where real usage lives in the output, and
where the final text answer lives. Adding an agent is usually a YAML file, not code
(see [AGENT_INTEGRATION.md](AGENT_INTEGRATION.md)).

Two control adapters (`reference`, `null`) cost zero tokens and implement the same contract,
so [simulate](SIMULATE.md) and eval exercise the real executor path offline.

## Where the guarantees live

Guards are placed at the lowest layer that can enforce them, so no caller can skip them:

- **R5** writes: `GitHubAPI._write` raises `WriteNotAllowed` unless the client is `live`.
- **R17** public only: `GitHubAPI.ensure_public` runs before every repo-scoped call.
- **R6** secrets: `Ledger.append` refuses secret-looking lines; `publish.GitPusher` passes the
  token through askpass; doctor scans the repo; the executor scans diffs and reports.
- **R12/B** halt/stop: checked between every executor step and right before any write.
- **R13** one agent: the adapter argv disallows sub-agent tools; `forbidden_args` raise.

See [SAFETY.md](SAFETY.md) for the full threat model and [WORKFLOW.md](WORKFLOW.md) for the
run lifecycle.
