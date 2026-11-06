# Agent setup — from a repo link to a working install

**For AI coding agents** (Claude Code, Codex, Cursor, Gemini CLI, Copilot, Aider, Windsurf,
opencode, Amp, Jules…) whose human handed them this repo's link and said something like
*"set this up"* or *"donate my leftover tokens with this"*. Humans: you can follow the same
steps, or just run `make quickstart`.

The rules in [AGENTS.md](../AGENTS.md) apply from step 1. The short version for setup:
**ask before installing anything or changing config, never type a password or token, never
pass `--live` unless the human explicitly asks for one specific PR.**

## 1. Get the code

The repo is private: the human's GitHub account must have access (an owner adds them under
*Settings → Collaborators*).

```bash
gh repo clone JazibAli360/token-tithe && cd token-tithe
```

If that fails with "not found" or an auth error, stop and tell the human: they need to run
`gh auth login` themselves and/or accept the collaborator invite. Don't try other routes.

## 2. Ask once, then run the quickstart

Ask the human one question before running anything:

> "Setup may install missing tools (GitHub CLI, uv, an agent CLI) via brew/npm and writes
> your GitHub noreply address into `config/policy.yaml`. OK to go ahead?"

- **Yes** → `./scripts/quickstart.sh --yes`
- **No / unsure** → `./scripts/quickstart.sh` (installs and writes nothing; lists what it skipped)

Choose the task agent with `--agent`: use **yourself** if you're one of the shipped adapters.

| You are | Flag |
|---|---|
| Claude Code | `--agent claude` (default when `claude` is installed) |
| Codex CLI | `--agent codex` |
| Aider | `--agent aider` |
| anything else | omit it; then see [AGENT_INTEGRATION.md](AGENT_INTEGRATION.md) to add an adapter |

The run takes 1–3 minutes. It is safe to re-run; it never opens PRs or stores secrets.

## 3. Report back

The script ends with a **Summary** block. Relay it to the human:

- `self-test (simulate)` and `doctor` — both should say PASS.
- **NEEDS A HUMAN** — sign-ins (`gh auth login`, `claude auth login`, `codex login`) and the
  signing-key setup. These ask for passwords or browser logins: give the human the exact
  command and wait. Never do them yourself.
- **SKIPPED** — what wasn't done for lack of consent.

After they finish anything listed, re-run the quickstart (same flags) until it's clean.

## 4. First donation round (still nothing public)

```bash
export GITHUB_TOKEN=$(gh auth token)
.venv/bin/donate capacity
.venv/bin/donate discover --dry-run
.venv/bin/donate run --dry-run      # show the human the plan
.venv/bin/donate run                # agents work locally; proposals -> state/review/
.venv/bin/donate review             # show each proposal's diff and reasons
```

Show the human the dry-run plan before the real `run`, and every proposal's diff before
anything else. Only when they approve a specific proposal:

```bash
make sandbox-image                  # once; needs Docker running
.venv/bin/donate review --open N --live
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `BLOCKED: need Python ≥ 3.11 or uv` | ask the human to install `uv` (`brew install uv` or docs.astral.sh/uv) |
| doctor WARN `commit signing (R18)` | expected until the signing key is set up; blocks `--live` only |
| doctor WARN `docker` | expected until Docker Desktop is running; blocks `--live` only |
| `capacity` says the agent isn't authenticated | the human runs that agent's login command |
| A Mac `sudo` / Keychain prompt appears | stop; that's the human's to answer |

Next stop for a deeper understanding: [ONBOARDING.md](ONBOARDING.md) (each step explained)
and [OPERATIONS.md](OPERATIONS.md) (ongoing use).
