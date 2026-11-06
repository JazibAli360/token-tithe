# Onboarding — your first runs

Read order for any session, human or agent: `state/HANDOVER.md` (if it exists) → this repo's
`brain/BRAIN.md`. Then follow the steps below in order. Each step is safe on its own.

> **Shortcut:** `make quickstart` does steps 0–4 for you (asks before installing anything).
> Read on for what each step does, or to do them by hand.

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

Use a dedicated GitHub account (bio: "token-tithe donation account operated by @you").
Add an SSH signing key to it, then in `config/policy.yaml`:

```yaml
signing:
  committer_name: "token-tithe (for @you)"
  committer_email: "12345+tithe-bot@users.noreply.github.com"
```

and `export TITHE_SIGNING_KEY=~/.ssh/tithe_signing.pub`. `donate doctor` signs a throwaway
commit to prove it works.

## 8. First live PR — one, by hand

```bash
open -a Docker && make sandbox-image    # R16: live runs refuse without the sandbox
.venv/bin/donate review --open 1 --live
```

Watch your notifications. Respond to the maintainer yourself or with
`donate address <pr-url>` ([OPERATIONS.md](OPERATIONS.md)).

## 9. Keep going

Weekly: `donate feedback`, `donate learn` (+ `--apply` after reading), `donate report`.
Monthly: `donate journal`, `donate optimize`. Only after several weeks of reviewed runs with
a good merge rate consider `trust: true` and a configured `second_review.agent`.

## Moving the engine

The directory is self-contained: `mv`/`rsync` it anywhere, run `make setup`, done. Delete
`state/` to reset. The Brain is committed, so it travels with the repo.
