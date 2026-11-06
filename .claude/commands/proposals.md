---
description: Review queued token-tithe proposals with their diffs; open one only on request
---
Show me the token-tithe review queue so I can decide what to publish.

1. Run `.venv/bin/donate review`.
2. For each proposal: repo and issue link, donation type, confidence, the queue reasons, and
   the diff from `state/review/<id>/diff.patch` (summarize long diffs, show the key hunks).
   Say plainly whether you'd merge it in five minutes as the maintainer, and why.
3. If I name one to open (e.g. "open 2"), repeat its repo and issue back, then run
   `.venv/bin/donate review --open <N> --live` — Claude Code will ask me to approve that
   command. If I say "discard N", run `.venv/bin/donate review --discard <N>`.
Never open or discard anything I didn't name.

$ARGUMENTS
