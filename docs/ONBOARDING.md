# Onboarding — your first runs

Read order for any session, human or agent: `state/HANDOVER.md` (if it exists) → this repo's
`brain/BRAIN.md`. Then follow the steps below in order. Each step is safe on its own.

> **Shortcut:** `make quickstart` does steps 0–4 and 7 for you (asks before installing anything).
> Read on for what each step does, or to do them by hand. AI agents: [AGENT_SETUP.md](AGENT_SETUP.md).

## 0. Prerequisites

| Primitive | Check | Install |
|---|---|---|
| Python ≥ 3.11 **or** `uv` | `python3 --version` / `uv --version` | `brew install uv` |
| git, GitHub CLI | `git --version`, `gh --version` | `brew install gh` |
| Claude Code CLI (default agent) | `claude --version` | `npm i -g @anthropic-ai/claude-code` |
| Docker (live runs only) | `docker info` | Docker Desktop: open it once after installing |

## 1. Set up

```bash
make setup                       # creates .venv/ inside the repo; nothing touches host Python
```

If you have a Brain export from another machine or clone, import it now; otherwise the
engine starts from the seeded lessons in `brain/BRAIN.md`:

```bash
.venv/bin/donate brain import path/to/tithe-brain-YYYY-MM-DD.md
```

## 2. Read the handover, check capacity, run doctor

```bash
.venv/bin/donate handover        # "nothing has run" on a fresh clone
.venv/bin/donate capacity        # which agents are installed/authenticated, budget, window
.venv/bin/donate doctor          # primitives, config, self-containment, secret scan
```

Doctor WARNs (Docker, `GITHUB_TOKEN`, signing) only block `--live`. Fix FAILs first.

## 3. Authenticate

```bash
export GITHUB_TOKEN=$(gh auth token)   # read at runtime, never stored (R6)
claude auth login                      # the CLI's own login — separate from the desktop app
```

For sandboxed live runs the container cannot see your keychain login. Create a token with
`claude setup-token` and `export CLAUDE_CODE_OAUTH_TOKEN=...` (or use `ANTHROPIC_API_KEY`).

## 4. Prove it offline

```bash
make simulate                    # whole pipeline on fixture repos: no network, no writes
make eval                        # control agents vs fixtures (harness self-check)
make eval AGENTS=reference,null,claude   # score Claude Code itself — spends real tokens
```

An agent joins routing only after it passes eval ([AGENT_INTEGRATION.md](AGENT_INTEGRATION.md)).

## 5. First real dry-run

```bash
.venv/bin/donate discover --dry-run     # what's out there (read-only)
.venv/bin/donate triage                 # ranked queue with rationales → state/queue.json
.venv/bin/donate run --dry-run          # the plan: which tasks, which agent, nothing executed
```

## 6. First review-mode run

```bash
.venv/bin/donate run                    # agents work locally; proposals → state/review/
.venv/bin/donate review                 # read each proposal: body.md, diff.patch, reasons
```

Read every diff as if you were the maintainer. Would you merge it in five minutes?

## 7. Set up the donation identity (R18)

`make quickstart` does this: it saves your GitHub noreply identity to
`config/policy.local.yaml`, creates `~/.ssh/token_tithe_signing`, and registers it on GitHub
as a signing key (if gh lacks the permission it tells you to run
`gh auth refresh -h github.com -s admin:ssh_signing_key` first). You add one line to your
shell profile: `export TITHE_SIGNING_KEY=$HOME/.ssh/token_tithe_signing.pub`.

By hand, or for a dedicated bot account (bio: "token-tithe donation account operated by
@you" — the cleanest option), put this in `config/policy.local.yaml`:

```yaml
signing:
  committer_name: "token-tithe (for @you)"
  committer_email: "12345+tithe-bot@users.noreply.github.com"
```

Never edit `config/policy.yaml` for personal values — it's shared. `donate doctor` signs a
throwaway commit to prove the key works.

## 8. First live PR — one, by hand

```bash
open -a Docker && make sandbox-image    # R16 (quickstart offers this); live runs refuse without it
.venv/bin/donate review --open 1 --live
```

Watch your notifications. Respond to the maintainer yourself or with
`donate address <pr-url>` ([OPERATIONS.md](OPERATIONS.md)).

## 9. Keep going

Weekly: `donate feedback`, `donate learn` (+ `--apply` after reading), `donate report`.
Monthly: `donate journal`, `donate optimize`. Only after several weeks of reviewed runs with
a good merge rate consider `trust: true` and a configured `second_review.agent`.

## Updating

`make update` pulls the latest version, reinstalls and runs doctor. Your `config/*.local.yaml`
and `state/` are never touched.

## Moving the engine

The directory is self-contained: `mv`/`rsync` it anywhere, run `make setup`, done. Delete
`state/` to reset. The Brain is committed, so it travels with the repo.
