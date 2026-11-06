# Handover — immediate stop, always resumable

**Invariant:** every run — completed, stopped, halted, crashed, out of budget — leaves the
repo in a resumable state with a written handover (R14). There is no ambiguous stop.

## Files

- `state/handover.json` — machine-readable: run id, status, phase, reason, tokens spent, the
  task in flight (repo, issue, branch, worktree, last completed step, **next step**, proposal
  id), finished tasks, pending review items, halt/stop flags.
- `state/HANDOVER.md` — the same story in prose, under 60 lines.

Both are rewritten atomically (temp file + rename) at every executor step. Any session —
human or agent — reads `HANDOVER.md` first, then `brain/BRAIN.md`, and resumes rather than
restarting.

```bash
.venv/bin/donate handover
```

## Stop semantics

| Trigger | Behavior |
|---|---|
| `donate stop` | sets `state/STOP`; the run stops at the next checkpoint |
| SIGINT / SIGTERM | signal handler sets STOP and raises a cooperative stop |
| `donate halt` | sets `state/HALT`; stops at the next checkpoint and blocks new runs |
| budget exhausted | `budget_exhausted` |
| unhandled exception | `crashed`, with the exception in `reason` |
| private repo seen | `aborted` (R17) |

Checkpoints sit between every executor step **and immediately before every GitHub write**.
After a stop nothing is opened, pushed or commented.

### Abort before publish

Before publishing, the executor saves the proposal with status `pr_pending` and records
`next_step: publish` + the proposal id. Only then does it check the stop flag and do the race
recheck. A stop at that moment therefore never leaves a half-done GitHub write: resume
re-enters at exactly the publish step.

## Resume

```bash
.venv/bin/donate resume [--live]
```

1. Clears `state/HALT` (R12).
2. Reads `handover.json`. If missing (state was wiped) it says so plainly and starts a fresh
   run instead of guessing.
3. `next_step: publish` → re-runs the race recheck and publishes the saved proposal.
4. `next_step: verify | review | decide` → loads the task snapshot
   (`state/worktrees/<run>/<task>.task.json`: triaged item, base branch, baseline results,
   the agent's summary) and continues at verification with the agent's work intact — the
   agent is **not** invoked again.
5. Earlier steps → the task is re-run from the clone (the gate must be fresh anyway).
6. Resumed tasks always go to the review queue.

## Sub-agents

The engine runs exactly one agent process per task (R13). The second review runs after the
task agent has finished — sequentially, never concurrently. `allow_subagents: false` is the
default and no code path spawns a second concurrent agent; the Claude adapter disallows its
sub-agent tools.
