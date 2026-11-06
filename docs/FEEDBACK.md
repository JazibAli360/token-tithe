# Feedback — how outcomes change future behavior

```bash
.venv/bin/donate feedback        # run daily; idempotent and cheap
```

## Classification

For every `pr_opened` entry in the ledger, `feedback.py` fetches the PR and classifies it:

| Status | Rule |
|---|---|
| `merged` | `merged_at` is set |
| `closed` | closed without merging |
| `stale` | open, no activity for `feedback.stale_after_days` (default 30) |
| `open` | otherwise (not ledgered) |

Only **changes** are appended to the [ledger](LEDGER.md), with agent, model, donation type,
language and days open. Merged and closed are terminal and never re-fetched.
Per-repo counts are cached in `state/feedback_cache/repos.json`.

## The loop

1. **Triage history.** `Ledger.history()` gives each repo `(merged, closed)`. Triage adds
   `4 × merged − 5 × closed` points, clamped to ±10: repos that merged our work rank up,
   repos that closed it all rank down ([WORKFLOW.md](WORKFLOW.md)).
2. **Cost estimates.** Average real tokens per donation type replace the policy defaults in
   capacity sizing ([CAPACITY.md](CAPACITY.md)).
3. **Routing.** `donate optimize` computes tokens per *merged* PR per agent × model × language
   and proposes routing changes ([OPERATIONS.md](OPERATIONS.md)).
4. **Lessons.** `donate learn` turns merges, closures, incidents and abandonments into
   candidate lessons in the Brain ([LEARNING.md](LEARNING.md)).

## Closed PRs and incidents

A closed PR is information, not a fight. `learn` records "read the thread before trying that
repo again". If the closure came with a complaint, run the incident procedure in
[SAFETY.md](SAFETY.md): apology, offer to revert, 90-day blocklist, ledger, lesson.

## Etiquette verdict cache

Repo-level etiquette verdicts (anti-AI statements, CLA, responsiveness) are cached for 7 days
in `state/feedback_cache/etiquette/`. The opt-out file is **never** cached, and the gate
before each task always takes a fresh verdict.
