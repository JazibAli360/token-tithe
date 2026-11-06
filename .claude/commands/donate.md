---
description: Run one token-tithe donation round (dry-run first, real run on your OK)
---
Run one donation round with the token-tithe engine in this repo. Follow CLAUDE.md's hard rules.

1. If `.venv/bin/donate` is missing, stop and offer to run setup per docs/AGENT_SETUP.md.
2. If `state/HANDOVER.md` exists, read it and say whether a previous run is unfinished.
3. Run `.venv/bin/donate capacity` and `.venv/bin/donate run --dry-run`. Make sure
   `GITHUB_TOKEN` is set for these commands (`export GITHUB_TOKEN=$(gh auth token)` in the same
   shell if it isn't), but never print or save the token.
4. Show me the plan as a short table: repo, issue, donation type, agent, estimated tokens.
   Flag anything that looks trivial, already has an open PR, or seems risky.
5. Ask me whether to proceed. Only on a clear yes, run `.venv/bin/donate run` (review mode —
   never add `--live`).
6. Summarize `.venv/bin/donate review`: one line per proposal with confidence and reasons.
   Offer `/proposals` to read the diffs. Do not open any PR.

$ARGUMENTS
