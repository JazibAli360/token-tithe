# token-tithe

**Donate your leftover AI-agent tokens to open source, without wasting a maintainer's time.**

When your coding agent has tokens left over and nothing to do, token-tithe puts them to work:

1. **Discover**: search GitHub for permissively licensed, active repos with unclaimed
   `good first issue` / `help wanted` issues.
2. **Etiquette**: reject any repo that says no to AI or automation, requires a CLA you
   haven't signed, has opted out, or is private. Deprioritize unresponsive repos.
3. **Triage**: score each issue from 0 to 100 on clarity, size, test coverage, maintainer
   responsiveness and our past outcomes there.
4. **Execute**: fork, branch, brief one agent, then run the repo's own tests and linter
   inside a sandbox, get a second review, and gate on confidence.
5. **Publish or queue**: open a small, disclosed PR, or (by default) queue it for you to review.
6. **Track and account**: record every token, outcome and decision in an append-only ledger.
7. **Learn**: distill each run into lessons in a committed `brain/BRAIN.md`. Once you approve
   them, they're injected into future task briefs, and they travel to any clone or agent session.

It is **agent-agnostic**. Claude Code is the primary operator, but Codex, Cursor, Aider,
Windsurf, opencode and others drive it through [AGENTS.md](AGENTS.md) and a small YAML adapter.

## Philosophy

Maintainers' time is the scarcest resource in open source. **A donation that costs review
time is not a gift.** token-tithe optimizes for contributions that are small, reviewable and
high-confidence, never for volume. It discloses AI authorship on everything it writes. It
dry-runs by default, sends work to a human review queue until you explicitly trust it, and
backs off the moment a maintainer says no. See [docs/DONATION_POLICY.md](docs/DONATION_POLICY.md).

## Quickstart (one command)

You need: macOS or Linux, `git`, and a GitHub account. The script checks for everything
else (Python ≥ 3.11 or `uv`, the GitHub CLI, Claude Code CLI, Docker) and offers to install
what's missing — it asks before every install.

```bash
gh repo clone JazibAli360/token-tithe && cd token-tithe   # or: git clone https://github.com/JazibAli360/token-tithe.git
make quickstart
```

`make quickstart` (same as `./scripts/quickstart.sh`) will:

1. check/install prerequisites,
2. install token-tithe into `./.venv` (your system Python is never touched),
3. sign you in to GitHub via `gh auth login` if needed,
4. set the commit identity in `config/policy.yaml` to *your* GitHub noreply address,
5. run the full pipeline offline against fixture repos (`make simulate`) and `donate doctor`,
6. print exactly what to run next.

It is safe to re-run, opens nothing on GitHub, and stores no tokens. Then:

```bash
export GITHUB_TOKEN=$(gh auth token)
.venv/bin/donate discover --dry-run        # what's out there (read-only)
.venv/bin/donate run                       # review mode: proposals go to state/review/, nothing is opened
.venv/bin/donate review                    # read the proposals
.venv/bin/donate review --open 1 --live    # approve one: opens exactly one disclosed PR
```

Using Claude Code? Open the folder and say *"run a donation round"* — `CLAUDE.md` is its
operating manual, and it will dry-run and show you the plan first.

Want to try it without touching GitHub at all? Run `make simulate`. It runs the whole
pipeline against fixture repos that ship in `eval/fixtures/`.

Full first-run walkthrough: [docs/ONBOARDING.md](docs/ONBOARDING.md).

## Safety in one table

| Guard | Default |
|---|---|
| Dry-run unless `--live` | on |
| Review queue unless `trust: true` in `config/policy.yaml` | on |
| Confidence gate | 70/100 |
| Rate limits | 3 PRs/repo/30 days · 5/day · 1 comment per issue ever |
| Sandbox for live agent and test runs | Docker, no network by default |
| Kill switch | `donate halt` · immediate stop: `donate stop` |
| Maintainer opt-out | commit `.github/token-tithe-ignore` (any content) |

More: [docs/SAFETY.md](docs/SAFETY.md).

## Portability guarantee

token-tithe is a closed world. Everything it needs lives in this directory: config,
templates, prompts, fixtures, eval suites and docs. Everything it changes lives in `state/`.
It depends only on documented primitives (Python ≥ 3.11 or `uv`, `git`, `gh`, network,
and Docker for live sandboxing). Secrets are read from environment variables at runtime
and never written down.

**You can `mv` or `rsync` this directory to any path or machine and it keeps working
after `make setup`.** `donate doctor` checks this: it confirms the venv is in-repo, that no
absolute paths leaked into code or config, and that every primitive is present.
Delete `state/` to reset completely.

## Repository map

Every file has one home. The root holds only files tools require there; everything else
lives in a self-contained folder. `donate doctor` fails if anything strays onto the root.

| Folder | Holds | Committed? |
|---|---|---|
| `src/token_tithe/` | the engine (Python package, CLI `donate`) | ✅ |
| `config/` | `policy.yaml` (every limit), `repos.yaml` (favorites/blocklist), `agents/*.yaml` + `agents/prompts/` | ✅ |
| `templates/` | PR body, comments, task brief | ✅ |
| `eval/` | fixture repos, expected outcomes, eval runner | ✅ |
| `tests/` | pytest suite | ✅ |
| `brain/` | `BRAIN.md` + `knowledge/`: durable learned lessons (Addendum E) | ✅ |
| `docs/` | all documentation, `CONTRIBUTING.md`, the build spec in `docs/spec/` | ✅ |
| `container/` | `Dockerfile` (+ its `.dockerignore`) | ✅ |
| `scripts/` | `quickstart.sh`: one-command first-time setup | ✅ |
| `.github/ .devcontainer/ .claude/ .cursor/ .gemini/` | tool-convention config | ✅ |
| `state/` | **all** mutable run state: ledger, review queue, handover, reports, worktrees. Delete to reset | ❌ |
| `.venv/` | in-repo virtualenv from `make setup` | ❌ |

## Documentation

| Doc | |
|---|---|
| [ONBOARDING](docs/ONBOARDING.md) | first run, end to end |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | modules, data flow, adapter layer |
| [WORKFLOW](docs/WORKFLOW.md) | run lifecycle, review mode, feedback loop |
| [DONATION_POLICY](docs/DONATION_POLICY.md) | ethics, disclosure, opt-out |
| [AGENT_INTEGRATION](docs/AGENT_INTEGRATION.md) | adapter contract, adding an agent, eval gating |
| [SAFETY](docs/SAFETY.md) | limits, budget, kill switch, sandbox, incidents |
| [CAPACITY](docs/CAPACITY.md) | quota probing and sizing |
| [HANDOVER](docs/HANDOVER.md) | stop and resume semantics |
| [LEARNING](docs/LEARNING.md) | the Brain: cross-repo lessons, export/import |
| [FEEDBACK](docs/FEEDBACK.md) · [LEDGER](docs/LEDGER.md) · [SIMULATE](docs/SIMULATE.md) · [OPERATIONS](docs/OPERATIONS.md) | |

## Roadmap — not yet built

- Dashboard over the ledger (it's JSONL — trivially chartable)
- Public opt-in donation-report static site (transparency signal for maintainers)
- Bounty mode: prioritize `funding.yml` bounties, auto-route bounty back into budget
- Multi-user/team mode: shared ledger, per-person budgets
- Local-model fallback tier for low-difficulty tasks

## License

MIT. See [LICENSE](LICENSE).
