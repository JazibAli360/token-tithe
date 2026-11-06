---
description: token-tithe status — PRs opened/merged, tokens spent, queue, handover
---
Give me a short token-tithe status report.

1. Run `.venv/bin/donate feedback` (refreshes outcomes of PRs we opened; read-only on GitHub),
   then `.venv/bin/donate report`.
2. Report: PRs opened / merged / closed / open, merge rate, tokens spent this month, proposals
   waiting in `.venv/bin/donate review`, whether `state/HALT` is set, and the latest handover
   status (`.venv/bin/donate handover`).
3. Point out any open PR with maintainer feedback waiting, and suggest `donate address <url>`
   for it. Keep it under 15 lines.

$ARGUMENTS
