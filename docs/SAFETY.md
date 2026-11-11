# Safety

## Guards at a glance

| Guard | Where | Default |
|---|---|---|
| Dry-run unless `--live` (R5) | `GitHubAPI._write` | on |
| Review queue unless `trust: true` | `executor._queue_reasons` | on |
| Confidence gate (R11) | executor | 70/100 |
| Rate limits (R4) | `publish.race_recheck` | 3/repo/30 days · 5/day · 1 comment/issue |
| Diff limit (R8) | executor verify | < 400 lines |
| Budget guard (R10) | `budget.BudgetGuard` | `max_tokens_per_run` |
| Task timeout (D3) | executor | 90 minutes |
| Kill switch (R12) | `pipeline.check_halt` | `donate halt` / `donate resume` |
| Immediate stop (B3) | `handover.HandoverSession` | `donate stop`, SIGINT, SIGTERM |
| Sandbox (R16) | `sandbox.select_runner` | Docker required for `--live`; preferred in review mode |
| Host env scrub | `sandbox.host_env` | anything run on the host gets no tokens, keys or gh/git logins |
| Claude Code guard | `.claude/settings.json` | `--live`, `gh pr create/comment`, `gh api -X` always ask first |
| Public repos only (R17) | `GitHubAPI.ensure_public` | always |
| Signed commits (R18) | executor / review | required |
| One agent per task (R13) | adapter argv | sub-agent tools disallowed |

## Stop, halt, resume

| Command | Effect | Leaves behind |
|---|---|---|
| `donate halt` | writes `state/HALT`; every run refuses to start, a running run stops at its next checkpoint | handover `halted` |
| `donate stop` | writes `state/STOP`; the running run stops at its next checkpoint, before any write | worktree + snapshot, handover `stopped` with `next_step` |
| Ctrl-C / SIGTERM | same as `stop` (signal handler) | same |
| `donate resume` | clears HALT, then continues the handover's task at its `next_step` | — |

HALT wins over everything: while it is set, nothing runs, including resume's continuation
(resume clears it first, deliberately). See [HANDOVER.md](HANDOVER.md).

## Threat model

**1. Hostile build and test code.** Running a repository's tests is arbitrary code execution:
`conftest.py`, `setup.py`, npm `postinstall`, `build.rs`. In live mode every install, test
and lint command runs in a fresh `docker run --rm` with only the worktree mounted, all
capabilities dropped, `no-new-privileges`, CPU/memory/pid limits, a non-root user, and
**`--network none`** for tests and lint. Dependency caches live inside the worktree so they
survive between containers. Review mode uses the same containers whenever Docker is running.

**Without Docker** (review mode only), install/test/lint run on your machine. They get a
scrubbed environment (`sandbox.host_env`): every variable that looks like a token, key,
password or session is dropped (`GITHUB_TOKEN`, `*_API_KEY`, `SSH_AUTH_SOCK`, …), `gh` sees an
empty config and git's credential helpers are switched off, so a hostile test script cannot
use your GitHub account or API keys. It can still read your files — **keep Docker running**
for anything beyond a first look. The host agent (review mode with a keychain login) gets
back only the variables its adapter lists in `env_passthrough`.

**2. Prompt injection.** Issue text, comments and repository files are attacker-controllable
and are read by the agent. Defences, in depth:
- the brief wraps issue content in an `UNTRUSTED DATA` block and says never to follow it;
- etiquette rejects threads with injection patterns before any agent runs;
- the agent runs with a fixed tool allowlist and cannot spawn sub-agents;
- the agent cannot publish — only the engine can, after independent verification;
- diffs and reports are scanned for secrets before anything leaves the machine;
- a second model reviews every diff for unrequested changes.

**3. The agent's network.** An agent must reach its model API, so agent containers use
`sandbox.agent_network` (default `bridge`). **Docker alone cannot restrict that to specific
hosts.** For strict egress control, run an allowlisting proxy (e.g. Squid or tinyproxy with
an allowlist of your model API host and the package registries in `install_allowlist`) and set
`sandbox.install_proxy`. Test and lint runs have no network, except for repos you list in
`sandbox.repo_test_network` because their suites cannot run offline. Those test containers get
`bridge` network but keep every other restriction, and each such task is logged in the ledger
as `sandbox_relaxed`. List only repos you trust.

**4. Secrets.** Tokens are read from the environment at call time and never written down
(R6). `git push` gets the token through a GIT_ASKPASS helper that reads `$GITHUB_TOKEN` — it
never appears on argv, in a URL, or on disk. The ledger refuses secret-looking lines. CLI
tracebacks don't print local variables. `donate doctor` scans the repo and `state/` for key
patterns and for the literal values of your secret env vars.

**GitHub token.** `gh auth token` is convenient but broad (it usually carries `repo`, which
reaches your private repos, plus `workflow` and `gist`). The engine needs only a **classic**
token with the single scope **`public_repo`**: fork, push to the fork and open PRs on public
repos (R17 keeps it public-only anyway). Create one at github.com/settings/tokens/new with
just that box ticked and `export GITHUB_TOKEN=…` in your shell profile. Fine-grained tokens
cannot open PRs on repositories you don't own, so they don't work here. `donate doctor`
warns when the token has more than it needs.

**5. Leaked secrets in target repos (D4).** The clone is scanned before any agent runs; a hit
skips the repo entirely and the secret is never quoted (only its kind is recorded). The one
exception is a repo you list in `sandbox.repo_secret_fixtures`, such as a secret scanner whose
tests carry fake keys on purpose. There, hits in test files are ignored, any other hit still skips
the repo, and each such task is logged as `sandbox_relaxed`.

**6. Our own mistakes.** Rate limits, review mode, the confidence gate, the second review and
the race recheck all exist so that a bad day produces queued proposals, not bad PRs.

## Incident handling

When a maintainer complains, or a PR lands badly:

1. **Apologise** on the PR — acknowledge, offer to revert, no excuses:
   ```bash
   donate incident https://github.com/o/r/pull/12 --reason "The PR ignored your style guide."          # preview
   donate incident https://github.com/o/r/pull/12 --reason "The PR ignored your style guide." --live   # post
   ```
   The text comes from `templates/apology_comment.md`.
2. **Do not close anything ourselves.** Revert only if asked (a normal reviewed PR).
3. **Blocklist** — the command appends the repo to `config/repos.yaml` with a 90-day cooldown
   (`feedback.incident_cooldown_days`), preserving the file's comments.
4. **Ledger** — an `incident` entry with the reason and cooldown date.
5. **Learn** — `donate learn` turns the incident into a REJECTIONS & INCIDENTS lesson.
6. **Never argue.** Not in the PR, not in the issue, not anywhere.

If the complaint is about AI contributions in general, also add the repo permanently
(`until: null`) — they have told us the answer.
