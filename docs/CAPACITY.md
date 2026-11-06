# Capacity

The engine never assumes resources. Every run starts with a probe, and every downstream
decision is sized to what is really available.

```bash
.venv/bin/donate capacity
```

## What is probed

| Item | How | Notes |
|---|---|---|
| Installed | binary on PATH | |
| Authenticated | `capacity_probe.auth` (e.g. `claude auth status --json` → `loggedIn`) | costs no tokens |
| Models | `capacity_probe.models`, else the YAML `models` list | |
| Remaining quota | `capacity_probe.quota` | **no shipped CLI exposes this** → capacity-blind |
| GitHub rate limit | `GET /rate_limit` | core + search |
| Cost per task | ledger averages of real tokens per donation type, else `budget.default_task_tokens` | |
| Donation window | `donation_window.cycle_end` | D9 |

"Reachable" is inferred from the auth probe; a real ping would spend tokens.

## Budget

`effective budget = min(budget.max_tokens_per_run, real remaining quota)` — or just the cap
when quota is unknown. The budget guard (`budget.py`):

- refuses to start if the effective budget is below `budget.min_viable_tokens` — the run
  attempts nothing and says so (R15);
- records only **real** usage; unaccounted invocations are excluded with a warning (R10);
- raises `BudgetExhausted` the moment spending reaches the budget, even mid-task; the task is
  abandoned cleanly and the handover records `budget_exhausted`.

## Sizing (A4)

Triage ranks the queue; `capacity.select_tasks()` takes the largest affordable prefix up to
`limits.max_tasks_per_run`. When a fix no longer fits but a `triage_report` would, the task
is **downgraded** instead of skipped. Every take / downgrade / skip is ledgered (`sizing`).

## Blind adapters

An agent without a quota probe is capacity-blind; one without real usage reporting is
unaccounted. Both may run, but only in review mode — their work is always queued for a human,
never auto-published, and they are refused in budgeted live runs (A3, R10).

## Model fallback

See [AGENT_INTEGRATION.md](AGENT_INTEGRATION.md#model-fallback-a3): fallback only to a model
that passed eval for the donation type; always ledgered and disclosed.

## Expiring credits (D9)

If credits are use-it-or-lose-it, set the billing-cycle end:

```yaml
donation_window:
  cycle_end: 2026-10-31
  expiring_days: 7             # capacity shows EXPIRING in the final week
  review_mode_hours_before: 48 # runs inside the final 48h force review mode
```

Review mode in the last 48 hours means approvals can happen while tokens still exist.
Scheduling the pre-expiry run is up to your own cron or scheduler; the engine only reads the
window. See [OPERATIONS.md](OPERATIONS.md).
