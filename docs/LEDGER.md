# Ledger

`state/ledger.jsonl` — append-only JSON Lines. Every token spent, every decision and every
outcome. It is the source for rate limits, triage history, cost estimates, reports, the
journal, the optimizer and the learning loop.

## Line format (schema v1)

Every line carries `schema_version`, `ts` (UTC, `YYYY-MM-DDTHH:MM:SSZ`) and `event`:

```json
{"schema_version": 1, "ts": "2026-10-08T15:20:53Z", "event": "queued_for_review",
 "run_id": "20261008T152051Z-37e5fe", "agent": "claude", "model": "claude-opus-5-5",
 "repo": "owner/name", "issue": 12, "donation_type": "issue_fix", "language": "Python",
 "mode": "dry-run", "confidence": 74, "tokens": 182340, "accounted": true,
 "diff_lines": 23, "proposal_id": "…", "reason": "review mode (…); no second reviewer (D5)",
 "review": "codex/default: approve, score 80"}
```

Common fields: `run_id`, `agent`, `model`, `repo`, `issue`, `url`, `donation_type`,
`language`, `mode` (`simulate` | `dry-run` | `live`), `tokens`, `accounted`, `confidence`,
`reason`.

## Events

| Event | Written by | Meaning |
|---|---|---|
| `pr_opened`, `comment_posted` | executor, review | published (counted by R4) |
| `queued_for_review` | executor | proposal written to `state/review/` |
| `rejected` | executor | etiquette/gate rejection or agent refusal |
| `abandoned` | executor, review | failed a check (reason says which rule) |
| `timeout`, `budget_exhausted` | executor | D3 / R10 |
| `unaccounted` | executor | the agent reported no real usage (R10) |
| `model_switch` | executor | eval-gated fallback (A3) |
| `sizing` | run | take / downgrade / skip per task, and "nothing attempted" (A4, R15) |
| `warning` | run | e.g. unsandboxed live run (R16) |
| `merged`, `closed`, `stale` | feedback | PR outcome changes |
| `incident` | feedback | apology + cooldown |
| `addressed` | address | a review-response round |

## Guarantees

- **No secrets** (R6): `Ledger.append` refuses any line that matches a secret pattern.
- **Tolerant readers**: malformed lines and unknown `schema_version`s are collected in
  `Ledger.problems` and reported (by `donate report`) instead of crashing a run.
- **Real usage only** (R10): unaccounted invocations are excluded from token totals and
  shown separately.

## Reading it

```bash
.venv/bin/donate report                 # this month: outcomes, tokens by agent/model, repos, efficiency
.venv/bin/donate report --month 2026-10
.venv/bin/donate journal --month 2026-10   # prose retrospective
jq -c 'select(.event=="pr_opened")' state/ledger.jsonl
```

Reports land in `state/reports/`. The ledger is JSONL so a dashboard is trivial — see the
README roadmap.
