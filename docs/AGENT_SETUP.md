# Agent setup — from a repo link to a working install

**For AI coding agents** (Claude Code, Codex, Cursor, Gemini CLI, Copilot, Aider, Windsurf,
opencode, Amp, Jules…) whose human handed them this repo's link and said something like
*"set this up"* or *"donate my leftover tokens with this"*. Humans: you can follow the same
steps, or just run `make quickstart`.

The rules in [AGENTS.md](../AGENTS.md) apply from step 1. The short version for setup:
**ask before installing anything or changing config, never type a password or token, never
pass `--live` unless the human explicitly asks for one specific PR.**

## 0. Access (the human, once)

The repo is private. Before an agent can even read this file, the human needs:

1. their GitHub account added as a collaborator (the owner does this; accept the email invite);
2. the GitHub CLI installed and signed in: `brew install gh` (or https://cli.github.com),
   then `gh auth login`.

If `gh repo clone` below fails with "not found" or an auth error, stop and tell the human
which of these is missing. Don't try other routes.

## 1. Get the code

```bash
gh repo clone JazibAli360/token-tithe && cd token-tithe
```

## 2. Ask once, then run the quickstart

Ask the human one question before running anything:

> "Setup may install missing tools (uv, an agent CLI) via brew/npm/pipx, create a commit
> signing key in ~/.ssh and register it on your GitHub account, build a Docker sandbox image
> (~1–2 GB), and save your settings in config/policy.local.yaml. OK to go ahead?"

- **Yes** → `./scripts/quickstart.sh --yes --agent <you>`
- **No / unsure** → `./scripts/quickstart.sh --agent <you>` (installs and writes nothing; lists what it skipped)

| You are | `--agent` |
|---|---|
| Claude Code | `claude` |
| Codex CLI | `codex` |
| Aider | `aider` |
| anything else | omit it; to add your own adapter see [AGENT_INTEGRATION.md](AGENT_INTEGRATION.md) |

It takes 1–3 minutes (plus the sandbox image build if accepted). It is safe to re-run, never
opens PRs, and never writes into the shared config files.

## 3. Report back

The script ends with a **Summary** block. Relay it to the human:

- `self-test (simulate)` and `doctor` — both should say PASS.
- **NEEDS A HUMAN** — browser sign-ins (`claude auth login`, `codex login`, `gh auth refresh`),
  starting Docker, and one line for their shell profile (`TITHE_SIGNING_KEY`). Give them the
  exact command and wait. Never do sign-ins yourself and never edit their shell profile unasked.
- **SKIPPED** — what wasn't done for lack of consent.

When they've done those, re-run the quickstart with the same flags until it's clean.

## 4. First donation round (still nothing public)

```bash
export GITHUB_TOKEN=$(gh auth token)   # or the narrower classic `public_repo` token (docs/SAFETY.md)
.venv/bin/donate capacity
.venv/bin/donate discover --dry-run
.venv/bin/donate run --dry-run      # show the human the plan
.venv/bin/donate run                # agents work locally; proposals -> state/review/
.venv/bin/donate review             # show each proposal's diff and reasons
```

Show the human the dry-run plan before the real `run`, and every proposal's diff before
anything else. Only when they approve one specific proposal:

```bash
.venv/bin/donate review --open N --live
```

## Settings and updates

- Personal settings live in `config/policy.local.yaml` and `config/repos.local.yaml`
  (gitignored, same keys as the shared files, merged on top). **Never put personal values in
  `config/policy.yaml` or `config/repos.yaml`** — those are shared and committed.
- `make update` pulls the latest version, reinstalls and re-runs doctor. Local settings survive.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `BLOCKED: need Python ≥ 3.11 or uv` | the human installs `uv` (`brew install uv` or docs.astral.sh/uv) |
| `BLOCKED: gh … too old` | install gh from https://cli.github.com (distro packages lag) |
| doctor WARN `commit signing (R18)` | expected until `TITHE_SIGNING_KEY` is in the shell profile; blocks `--live` only |
| doctor WARN `docker` | the human starts Docker Desktop; blocks `--live` only |
| doctor WARN `GITHUB_TOKEN scope` | works, but a classic `public_repo`-only token is safer (docs/SAFETY.md) |
| `capacity` says the agent isn't authenticated | the human runs that agent's login command |
| A `sudo`, Keychain or browser prompt appears | stop; that's the human's to answer |
| `make update` can't fast-forward | shared files were edited locally: move the change into `config/*.local.yaml`, then `git checkout -- <file>` |

Deeper: [ONBOARDING.md](ONBOARDING.md) (each step explained) · [OPERATIONS.md](OPERATIONS.md)
(ongoing use) · [SAFETY.md](SAFETY.md).
