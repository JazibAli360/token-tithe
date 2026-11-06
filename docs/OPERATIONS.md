# Operations

Day-to-day commands beyond `donate run`.

## Responding to review — `donate address`

```bash
.venv/bin/donate address https://github.com/o/r/pull/34          # dry-run: shows what it would do
.venv/bin/donate address https://github.com/o/r/pull/34 --live   # commit, push, comment
```

Strict scope (D6):

- only PRs the ledger shows this engine opened;
- only explicit review comments (review bodies and inline comments) newer than our last round;
- at most `address.max_rounds` (3) rounds, within `address.window_days` (14) of opening;
- each round's diff under `address.max_diff_lines` (200), tests and linter re-run (R7);
- pushed to the **same** branch, never force-pushed; a short "addressing your feedback"
  comment (`templates/review_response.md`);
- every round ledgered (`addressed`).

If the agent is unsure or disagrees, it changes nothing for that request and explains — the
engine does not argue with maintainers.

## Adopting a repo — `donate adopt`

```bash
.venv/bin/donate adopt owner/name
```

For projects you want a longer relationship with. Profiles the repo (language, branch,
license, responsiveness, CONTRIBUTING/PR template presence, commit-title style from recent
merged PRs) into `state/adoption/`, and adds it to `adopted` in `config/repos.yaml`. Adopted
repos are searched first, and their conventions go into every task brief for that repo.
Etiquette rejections are refused.

## Routing economy — `donate optimize`

```bash
.venv/bin/donate optimize            # writes state/reports/optimizer-proposal.json
.venv/bin/donate optimize --apply    # rewrites routing.by_language in config/policy.yaml
```

Cost per merged PR = all real tokens an agent spent on a language (every attempt) ÷ its
merged PRs in that language. The cheapest agent with at least 2 merges wins the slot — but
only if it has passed eval. Simulate runs are ignored. `--apply` edits only the
`by_language` line and re-validates the policy.

## Monthly views

```bash
.venv/bin/donate report  --month 2026-10   # numbers: outcomes, tokens, repos, efficiency
.venv/bin/donate journal --month 2026-10   # prose retrospective, blog-ready
```

Both land in `state/reports/`. See [LEDGER.md](LEDGER.md).

## Expiring credits

Set `donation_window.cycle_end` and schedule a run a few days before it with your own
scheduler, for example cron:

```cron
0 9 25-31 * *  cd /path/to/token-tithe && .venv/bin/donate run >> state/cron.log 2>&1
```

Inside the final 48 hours runs are forced into review mode ([CAPACITY.md](CAPACITY.md)).

## Incidents

`donate incident <pr-url> --reason "…" [--live]` — see [SAFETY.md](SAFETY.md#incident-handling).

## Routine

| When | Command |
|---|---|
| each session | `donate handover`, then read `brain/BRAIN.md` |
| daily | `donate feedback` |
| after runs | `donate review` |
| weekly | `donate learn`, read PENDING, `donate learn --apply` |
| monthly | `donate report`, `donate journal`, `donate optimize`, `donate brain review` |
