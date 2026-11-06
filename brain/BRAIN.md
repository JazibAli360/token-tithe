# BRAIN — token-tithe's durable knowledge

> Read this second, after `state/HANDOVER.md`. Handover = where am I (volatile); Brain = what
> do we know (durable, committed, portable). Append-only (R19): approved lessons are never
> edited or deleted — corrections are new lessons that supersede old ones. Managed with
> `donate learn` (candidates → PENDING, `--apply` promotes) and `donate brain`.
> Confidence: observed-once → repeated → confirmed. Provenance: ledger run id, PR URL or commit.

## AGENT QUIRKS

- **L-0001** · 2026-10-08 · confirmed · tags: claude, general · prov: commit bbd057b — Claude Code in headless -p mode silently denies Bash unless it is listed in --allowedTools; the task agent must be allowed to run the repo's tests, while Task/Agent stay disallowed (R13).
- **L-0002** · 2026-10-08 · confirmed · tags: claude, sandbox · prov: commit bbd057b — The claude CLI keeps its own login, separate from the desktop app: check `claude auth status`; containers need ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN because keychain logins do not reach them.

## WHAT GETS MERGED

- **L-0005** · 2026-10-08 · confirmed · tags: tests, general · prov: eval fixture py-untested, commit 9f85aae — Tests-only donations must assert real behavior: tests that merely import the module pass the suite but are caught by mutants (py-untested eval).

## REJECTIONS & INCIDENTS

- **L-0003** · 2026-10-08 · confirmed · tags: etiquette, general · prov: commit 961a606 — Anti-AI detection must be negation-aware: 'AI contributions are not welcome' contains 'welcome'. Ban = an AI term AND a prohibition in the same sentence, with no permission we satisfy (e.g. 'welcome if disclosed').

## PROMPT PATTERNS

- **L-0004** · 2026-10-08 · confirmed · tags: ci_repro, general · prov: eval fixture flaky-ci, commit 9f85aae — Fixing CI by skipping, xfail-ing or deleting the failing test is not a fix: the flaky-ci rubric fails it and the ci_repro brief forbids it.

## ECOSYSTEM PLAYBOOKS

- **L-0006** · 2026-10-08 · confirmed · tags: discovery, general · prov: commit 961a606 — GitHub issue search does not support repository qualifiers (stars:, pushed:, license:); filter those per repo after fetching it, or results silently skew.
- **L-0007** · 2026-10-08 · confirmed · tags: sandbox, python, rust, go, javascript · prov: commit bbd057b — Every sandbox container is fresh: dependency caches (.tithe-venv, cargo, go, npm) must live inside the mounted worktree or installs vanish before the test step.

## PENDING LESSONS

_(none yet)_

## REVIEW LOG

- 2026-10-08 · seeded 7 lessons from the Phase 3–5 build and eval runs
