# Workflow — the life of a run

Every run, without exception, starts with a capacity probe and ends with a handover write.

```
halt check → capacity probe → min-viable check → discover → etiquette → triage → sizing
   → for each task: clone → gate → baseline → brief → agent → verify → second review
                    → confidence → commit → (review queue | race recheck → publish)
   → handover (completed | stopped | halted | budget_exhausted | insufficient_capacity | aborted | crashed)
```

## 1. Before anything (R12, A, R15)

- `state/HALT` present → nothing runs; the handover records `halted`.
- `capacity.probe()` checks each agent (installed, `auth status`, quota probe), the GitHub
  rate limit, ledger cost averages and the donation window. See [CAPACITY.md](CAPACITY.md).
- If the effective budget is below `budget.min_viable_tokens`, the run attempts nothing and
  says `insufficient capacity, nothing attempted`.

## 2. Queue

`pipeline.build_queue()` runs [discovery](ARCHITECTURE.md), etiquette on every candidate,
and triage. Inputs from the ledger: repos touched recently (excluded), issues already
attempted (excluded), and per-repo merge history (scores up or down — see
[FEEDBACK.md](FEEDBACK.md)). `capacity.select_tasks()` then takes the largest affordable
prefix, downgrading a fix to a `triage_report` when only that fits. Every sizing decision is
ledgered.

`donate run --dry-run` stops here and prints the plan (R5).

## 3. One task (executor.py)

| Step | What happens | Failure |
|---|---|---|
| clone | fixture materialize or `git clone --depth 50`; branch `donate/<n>-<slug>` | abandon |
| gate | **fresh** etiquette (opt-out, anti-AI, CLA, injection, issue secrets) + secrets scan of the clone | `rejected` |
| baseline | install deps (sandbox only), run detected tests + lint, record pass/fail | timeout |
| brief | `templates/task_brief.md`: type instructions, scope limits, conventions, **lessons**, issue as untrusted data | — |
| agent | one adapter invocation (R13); real usage recorded (R10); model fallback if the model is unavailable (A3) | abandon / `budget_exhausted` |
| verify | the engine re-runs tests and linter itself (R7), checks diff size (R8), donation-type scope, secrets in the diff | abandon |
| review | a different model reviews the diff (D5) | — |
| confidence | `(1−w)·agent + w·review`, capped at 40 on a reject verdict | — |
| commit | signed with the donation identity if configured (R18) | — |
| decide | queue if any reason applies; otherwise publish | — |

Between every step: halt flag, stop flag, task deadline (D3), budget checkpoint (R10).
Abandoned and rejected worktrees are deleted; timed-out and stopped ones are kept for resume.

## 4. Review mode (default)

Work goes to `state/review/<id>/` (proposal.json, body.md, diff.patch) when **any** of these
hold — each one is listed in the proposal:

- not `--live`, or `trust: false` in policy (the default)
- confidence below `confidence_threshold` (R11)
- no second reviewer configured (D5)
- commit not signed by the donation identity (R18)
- the agent is capacity-blind or unaccounted (A3, R10)
- the donation window forces review mode (D9)

```bash
donate review                    # list, with the reasons each one was queued
donate review --open 1           # dry-run: prints what it would open
donate review --open 1 --live    # signing check → race recheck → fork/push/PR (or comment)
donate review --discard 2        # ledgers it as abandoned and deletes the worktree
```

A human approval stands in for R11, D5 and A3 — that is the point of review mode. It never
bypasses R5, R12, R17, R18 or the race recheck. This hybrid is the intended first-month mode.

## 5. Publishing (publish.py)

Immediately before any write: stop-flag check → **race recheck (D2)** → stop-flag check →
write. The race recheck re-verifies: repo public and not archived, issue still open and
unassigned, no other PR now references it, etiquette verdict unchanged (opt-out!), rate
limits (R4). Any change → abandon with the reason. The proposal is saved as `pr_pending`
*before* the write, so a stop at that moment resumes at exactly that step
([HANDOVER.md](HANDOVER.md)).

## 6. After the run

- `donate feedback` — outcomes of every PR we opened ([FEEDBACK.md](FEEDBACK.md)).
- `donate address <pr>` — respond to review on our PR ([OPERATIONS.md](OPERATIONS.md)).
- `donate learn` / `donate learn --apply` — lessons into the Brain ([LEARNING.md](LEARNING.md)).
- `donate report` / `donate journal` — monthly views of the ledger ([LEDGER.md](LEDGER.md)).
