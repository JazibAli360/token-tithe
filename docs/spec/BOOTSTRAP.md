# PROJECT BOOTSTRAP: Token Tithe — Self-Contained Token Donation Engine for Open Source

You are bootstrapping a new repository in the current directory called `token-tithe`.
Read this entire brief before writing anything. Build phase by phase, stop after each
phase with a short summary. Do not skip files, do not stub anything out.

## 0. SELF-CONTAINMENT MANDATE (read first — it shapes every decision)

The repo must be a closed world. It runs on any machine — laptop, server, container,
fresh clone — with NO dependence on anything outside its own directory except
documented, checked primitives (python 3.11+, git, gh CLI, network). Rules:

- S1 Everything lives in the repo: fixtures, templates, agent prompts, config, docs,
  evaluation suites. No file may be fetched at runtime except live GitHub API data.
- S2 All paths relative to the repo root, resolved via `Path(__file__)` anchors. Zero
  absolute paths, zero `~/` assumptions, zero reliance on host env beyond env-var
  secrets (GITHUB_TOKEN) read at runtime and never persisted.
- S3 `make setup` creates its own `.venv/` inside the repo and installs all deps into
  it. Never install into the host python. Never assume host packages exist.
- S4 All mutable state (ledger, reports, halt flag, feedback cache, adoption profiles)
  lives under `state/` (gitignored) INSIDE the repo. Deleting `state/` resets it fully.
- S5 Ship a `Dockerfile` and `.devcontainer/devcontainer.json` so the whole engine runs
  containerized with the host contributing only secrets. CI runs in a container too.
- S6 `donate doctor` must verify self-containment: checks the venv, that no absolute
  paths leaked into config/code, that primitives exist, and prints a pass/fail table.
- S7 The repo must survive `mv` / `rsync` to another machine and continue working with
  only `make setup` — document this as the portability guarantee in README.

## 1. What this project is

A self-hosted tool that donates leftover AI-agent tokens to open source. When I have
tokens left over and no task for the agent, the engine: DISCOVERS suitable repos on
GitHub, TRIAGES and scores candidate work, EXECUTES scoped fixes/PRs via any coding
agent, TRACKS outcomes (merged? closed? ignored?), and ACCOUNTS for every token in an
auditable ledger. Agent-agnostic: Claude Code is the primary operator, but any agent
(Codex, Cursor, Aider, Windsurf, opencode) drives it through AGENTS.md + adapters.

Philosophy, stated in README: maintainers' time is the scarcest resource. A donation
that costs review time is not a gift. We optimize for reviewable, high-confidence,
small contributions — never volume.

## 2. Hard rules — encode verbatim in CLAUDE.md as numbered non-negotiables

- R1 PRs only. Never push to main/master, never merge, never force-push another's
  branch, never close or edit issues. Triage-report comments on issues are allowed only
  via the donation types in §5 and must follow that section's limits.
- R2 Every PR and comment discloses AI authorship ("Generated with <agent> by the
  token-tithe engine") and links the source issue.
- R3 Permissive licenses only by default: MIT, Apache-2.0, BSD-2/3, ISC, MPL-2.0. GPL
  family opt-in via config. Skip: archived repos, repos with anti-AI or no-automation
  statements, repos requiring an unsigned CLA, repos whose CONTRIBUTING forbids bots.
- R4 Rate limits (config/policy.yaml): max 3 open PRs per repo per 30 days, 5 per day
  globally, max 1 comment per issue ever, never exceed.
- R5 DRY-RUN by default. Anything mutating the outside world requires `--live`.
  Dry-run prints exactly what it would have done.
- R6 No secrets on disk, ever. Env vars only, read at runtime. Never write tokens to
  logs, ledger, reports, PR text, or anywhere else. `donate doctor` scans for leaks.
- R7 Every change must pass the target repo's own tests and linter, or the PR is
  abandoned with a documented ledger reason.
- R8 One PR = one small focused change. Diff < 400 lines. No sweeping refactors, no
  drive-by formatting, no dependency bumps unless the issue asks.
- R9 Etiquette first: read CONTRIBUTING, PR templates, and issue conventions; match
  their style; claim issues per their process.
- R10 Budget guard: hard-stop when `max_tokens_per_run` is exhausted, even mid-task.
  Token accounting must be REAL (see adapter contract §6) — estimated usage is not
  accepted; adapters report actual usage or the run is marked `unaccounted` and skipped
  from budget math with a warning.
- R11 Confidence gate: no PR opens below the confidence threshold in policy.yaml
  (default 70/100). Below threshold, work goes to the review queue instead.
- R12 Kill switch: `donate halt` sets `state/HALT`. Every run checks it before doing
  anything. `donate resume` clears it.

## 3. Repository layout

```
token-tithe/
├── README.md                   # what/why, 60-second quickstart, philosophy, Roadmap (§8)
├── CLAUDE.md                   # Claude Code operating manual (<150 lines)
├── AGENTS.md                   # universal agent manual; identical rules; adapter contract
├── CONTRIBUTING.md
├── LICENSE                     # MIT
├── Makefile                    # setup, doctor, lint, test, eval, discover, donate,
│                               # report, adopt, halt, resume, simulate, container targets
├── pyproject.toml              # ruff + pytest, deps pinned
├── Dockerfile                  # full self-contained runtime image
├── .devcontainer/devcontainer.json
├── .github/workflows/
│   ├── ci.yml                  # lint + tests + eval-suite in container, every push
│   └── donate.yml              # scheduled/manual dispatch, dry-run only in CI
├── config/
│   ├── policy.yaml             # budgets, rate limits, license allowlist, confidence
│   │                           # threshold, language filter, agent skill routing
│   ├── repos.yaml              # adopted repos, favorites, blocklist
│   └── agents/
│       ├── claude.yaml         # headless invocation + usage-extraction command
│       ├── codex.yaml
│       ├── aider.yaml
│       └── generic.yaml        # template for adding any agent
├── src/token_tithe/
│   ├── cli.py                  # typer entrypoint: discover|triage|run|review|report|
│   │                           # adopt|feedback|halt|resume|doctor|simulate
│   ├── discover.py             # GitHub search → candidates
│   ├── etiquette.py            # maintainer-intent detection (anti-AI statements,
│   │                           # CLA checks, responsiveness scoring, opt-out respect)
│   ├── triage.py               # score issues → ranked queue w/ rationales
│   ├── executor.py             # worktrees, agent invocation, tests, PR creation,
│   │                           # confidence gate, review-queue mode
│   ├── donation_types/         # issue_fix.py, tests.py, docs.py, ci_repro.py,
│   │                           # triage_report.py  (see §5)
│   ├── agents/                 # base.py contract + per-agent adapters + prompts/
│   ├── feedback.py             # PR outcome tracking + triage-score learning
│   ├── adoption.py             # `adopt` mode: per-repo profiles in state/adoption/
│   ├── ledger.py               # append-only JSONL + monthly report
│   ├── policy.py               # load + enforce everything in config/
│   ├── simulate.py             # full pipeline against local fixture repos, no network
│   ├── budget.py               # real-time token budget guard
│   └── github_api.py           # thin authenticated REST wrapper, backoff, fixture mode
├── tests/                      # pytest: policy, triage, etiquette, ledger, adapters
├── eval/                       # DETERMINISTIC AGENT EVALUATION SUITE (§7)
│   ├── fixtures/               # 6 self-contained fake repos with known issues
│   │   ├── py-math-bug/        #   a small python repo, off-by-one bug, tests present
│   │   ├── js-doc-gap/         #   a js repo, missing README section, documented API
│   │   ├── py-untested/        #   a python module with 0% coverage on one file
│   │   ├── rs-clippy/          #   a rust crate failing clippy (if toolchain present)
│   │   ├── flaky-ci/           #   repo with a reproducible failing test
│   │   └── anti-ai/            #   repo with "no AI contributions" — MUST be rejected
│   ├── expected/               # per-fixture expected outcomes + scoring rubric
│   └── run_eval.py             # runs each adapter against each fixture, scores, emits
│                               # eval/report.md (agent × language × outcome matrix)
├── templates/
│   ├── pr_body.md              # AI disclosure, issue link, tests run, token cost
│   ├── issue_comment.md        # claim / triage-report comment
│   ├── apology_comment.md      # incident revert-with-apology (§7 incidents)
│   └── task_brief.md           # the brief written into each worktree for the agent
├── state/                      # gitignored: ledger.jsonl, HALT, reports/, review/,
│                               # feedback_cache/, adoption/, eval_runs/
└── docs/
    ├── ARCHITECTURE.md         # modules, data flow, adapter layer (ascii diagram)
    ├── WORKFLOW.md             # full run lifecycle incl. review mode + feedback loop
    ├── DONATION_POLICY.md      # ethics: disclosure, small PRs, maintainer opt-out,
    │                           # why dead/anti-AI repos are skipped, opt-out method
    ├── AGENT_INTEGRATION.md    # adapter contract, how to add any agent, eval gating
    ├── SAFETY.md               # rate limits, budget guard, kill switch, INCIDENT
    │                           # HANDLING procedure (complaint → revert w/ apology
    │                           # comment → blocklist → cooldown → ledger)
    ├── FEEDBACK.md             # how PR outcome tracking adjusts triage scoring
    ├── LEDGER.md               # format, fields, how to read reports
    ├── SIMULATE.md             # fixture-repo simulation mode usage
    └── ONBOARDING.md           # first run: gh auth, env vars, doctor, first dry-run,
                                # first --review run, first --live run, container use
```

## 4. Core pipeline

**discover.py** — GitHub search API: permissive license, pushed < 60 days, not archived,
50–5000 stars, language from policy, unclaimed `good first issue`/`help wanted` with no
linked PR. Favorites first; blocklist and ledger-30-days repos excluded.

**etiquette.py** — BEFORE any repo enters the queue: scan README/CONTRIBUTING/.github
and issue threads for anti-AI / no-automation statements → hard reject + cache verdict
in `state/feedback_cache/`. Score maintainer responsiveness (issue age, recent commits,
median PR review time if cheaply available). Unresponsive repos are deprioritized, not
rejected. Maintainers can opt out by any method in DONATION_POLICY.md.

**triage.py** — score 0–100: problem clarity, repro steps, estimated size, test coverage
of the area, maintainer responsiveness, AND the repo's donation-outcome history from
feedback.py (repos that merged our PRs rank up; repos that closed them all rank down).
Emit ranked queue with one-paragraph rationale per item.

**executor.py** — per task: fork → branch `donate/<issue>-<slug>` → write task brief
(templates/task_brief.md: issue text, repo conventions, scope limits, donation type)
→ invoke agent adapter in the worktree → run repo's tests + lint → compute confidence
score → if ≥ threshold and `--live`: open PR via templates/pr_body.md → ledger append.
Failure at any step = clean abandon + ledger reason, no broken branches left.

**Review mode (default until `--trust` is set in policy.yaml)** — `donate run` ends by
writing the proposed PRs to `state/review/` (diff, rationale, confidence, token cost)
instead of opening them. `donate review` lists them; `donate review --open N` approves
and opens. This hybrid mode is the intended first-month behavior.

## 5. Donation types (each a module with its own limits + templates)

1. `issue_fix` — the core: fix a scoped issue, PR, tests included. R8 limits apply.
2. `tests` — add tests for an untested module. Lowest-risk donation; no behavior change;
   still a PR, still disclosed.
3. `docs` — fill documented doc gaps (README section, missing docstrings for public API,
   outdated examples). No opinion rewrites.
4. `ci_repro` — reproduce a failing/flaky CI run, diagnose, propose fix. If diagnosis is
   uncertain, downgrade to a triage_report instead of a PR.
5. `triage_report` — investigate a bug and post ONE well-structured comment: repro steps,
   root-cause analysis, suggested fix direction. Not a PR. R4's one-comment-per-issue
   limit applies. Sometimes worth more than code.

policy.yaml maps each donation type to allowed repos/languages and default agent.

## 6. Agent adapter contract (base.py — every adapter MUST implement)

1. `invoke(worktree, task_brief_path) -> result` — run the agent headless, repo-locally,
   with only relative paths.
2. `report_usage() -> {model, input_tokens, output_tokens, cost_estimate}` — ACTUAL
   usage from the agent's own accounting (Claude Code: `--output-format json` usage
   fields / `/cost` json; codex/aider: their equivalents). No fabricated numbers.
3. `write_summary() -> structured JSON` — what changed, files touched, tests written,
   self-reported confidence + reasoning.
4. `cleanup()` — remove scratch files, leave worktree clean.

config/agents/*.yaml carries: invocation command, prompt-entry method, usage-extraction
command, notes. policy.yaml may route tasks: `rust: codex.yaml`, `docs: aider.yaml`,
default: claude.yaml. An adapter that cannot report real usage runs but its output is
marked `unaccounted` (R10) and it cannot be used in budgeted live runs.

## 7. Feedback, incidents, evaluation

**feedback.py** — `donate feedback` checks every PR we ever opened (GitHub API): merged /
closed-unmerged / open / stale. Appends outcomes to the ledger and updates per-repo
scores in `state/feedback_cache/`. Triage reads these. FEEDBACK.md documents the loop.

**Incident procedure (SAFETY.md + templates/apology_comment.md)** — if a maintainer
complains or a PR lands badly: post the apology comment on the PR (acknowledge, offer
revert, no excuses), close nothing ourselves but revert if asked, blocklist the repo,
start a 90-day cooldown, log to ledger with outcome `incident`. Never argue.

**Eval suite (eval/)** — the six fixtures are real miniature repos committed in-tree
(self-contained: S1). `make eval` runs every configured adapter against every fixture
in SIMULATE mode (no network, no GitHub) and scores against eval/expected/ rubrics:
did it fix the bug, did it add tests, did it refuse the anti-AI repo, diff size, usage
accuracy. Output: `state/eval_runs/report.md` — the agent × language × outcome matrix.
CI runs a fast subset. AGENT_INTEGRATION.md requires a passing eval before a new agent
is added to default routing.

**simulate.py** — run the entire pipeline (discover→triage→execute→review) against
local fixture repos with github_api.py in fixture mode. `make simulate`. This is how
every adapter/prompt change is validated before touching the real world.

## 8. Roadmap section (README gets this verbatim as "Roadmap — not yet built")

- Dashboard over the ledger (it's JSONL — trivially chartable)
- Public opt-in donation-report static site (transparency signal for maintainers)
- Bounty mode: prioritize `funding.yml` bounties, auto-route bounty back into budget
- Multi-user/team mode: shared ledger, per-person budgets
- Local-model fallback tier for low-difficulty tasks

## 9. Build phases

Phase 1 — Scaffold: full tree, README (incl. Roadmap + portability guarantee), LICENSE,
Makefile, pyproject, Dockerfile, devcontainer, CI.
Phase 2 — Policy + config + doctor: policy.yaml, repos.yaml, agent yamls, policy.py,
budget.py, self-containment checks. `donate doctor` works.
Phase 3 — Fixtures + eval harness: all six fixtures committed, expected/ rubrics,
run_eval.py, simulate.py, github_api fixture mode.
Phase 4 — Discovery + etiquette + triage with tests (fixtures only, offline).
Phase 5 — Executor + adapter layer + all templates + review mode + donation types.
Phase 6 — Ledger + feedback + adoption + reports.
Phase 7 — Docs: all nine docs complete, cross-referenced, INCIDENT procedure written
properly, DONATION_POLICY.md thoughtful not filler.
Phase 8 — Live wiring: gh auth check, real calls behind `--live`, donate.yml (dry-run).
Phase 9 — Self-review against this brief, `make lint test eval doctor simulate` all
green, write docs/ACCEPTANCE.md from the checklist, end with a compact handover
section appended to CLAUDE.md.

## 10. Acceptance checklist

- [ ] `make setup && make test` green from a clean clone on a fresh machine/container
- [ ] Repo runs after `mv` to another path/machine with only `make setup` (S7 verified)
- [ ] `donate doctor` passes and detects a planted absolute-path violation
- [ ] `donate discover --dry-run` works offline from fixtures
- [ ] `donate run` (review mode) proposes PRs to state/review/ without opening any
- [ ] `donate review --open 1` opens exactly one PR in live mode with disclosure + cost
- [ ] Confidence gate blocks a below-threshold task (test proves it)
- [ ] anti-ai fixture is REJECTED by etiquette in both simulate and eval
- [ ] `make eval` produces the agent × outcome matrix; claude adapter passes all fixtures
- [ ] An adapter with no real usage reporting is refused budgeted live runs (R10 test)
- [ ] `donate feedback` correctly classifies a merged/closed fixture PR via mock API
- [ ] `donate halt` stops subsequent runs; `donate resume` clears
- [ ] No secret can reach any file/log/PR (grep-based test)
- [ ] Any agent can complete a fixture task using only AGENTS.md + its yaml
- [ ] All nine docs exist, consistent, cross-linked

# ADDENDUM A — CAPACITY-AWARE OPERATION

## A1. What it means

The engine never assumes resources. Before ANY run it probes what it actually has:
credit/quota per provider, which agents/models are authenticated and reachable, each
model's donation budget for this session, and remaining GitHub rate limit. Every
decision downstream is sized to what is really available.

## A2. New module: src/token_tithe/capacity.py

`donate capacity` — prints the live capacity table and exits:
- per configured agent: authenticated? (auth probe), reachable? (cheap ping), model(s)
  available, quota/credit remaining (via the agent's own reporting — see A3),
  tokens donated to budget this session.
- GitHub API rate limit remaining (REST /rate_limit).
- computed: "this run can afford ~N tasks of size M" using the running average token
  cost per donation type from the ledger (fallback: policy.yaml defaults).

Every run begins with the same probe. If capacity < `min_viable_tokens` (policy.yaml,
default: enough for one small PR), the run exits gracefully with a written reason:
"insufficient capacity, nothing attempted" — never a half-done task.

## A3. Capacity probing is part of the adapter contract (amends §6)

Each config/agents/*.yaml gains:
- `capacity_probe:` command that reports the agent's real remaining quota/credit and
  available models (Claude Code: usage/`/cost` json + model list; others: their
  equivalents). An adapter with no probe is capacity-blind: it may run only in
  review mode and never in budgeted live runs (same treatment as R10 unaccounted).
- `models:` ordered fallback list. If the primary model is unavailable or its quota
  is exhausted, the engine picks the next model ONLY if that model has already passed
  the eval suite for the task's donation type; otherwise it ends the run. Never
  silently downgrade quality on work destined for a live PR — any model switch is
  recorded in the ledger and disclosed in the PR body.

## A4. Sizing decisions to capacity

- triage ranks the queue, capacity.py then selects the largest prefix of tasks the
  budget can afford, cheapest-donation-type-first when capacity is tight (e.g. a
  triage_report instead of an issue_fix when only one small task fits).
- budget.py is amended: it reads the REAL remaining quota from capacity.py, not just
  the configured per-run cap; the effective budget is min(configured cap, real quota).
- every sizing/downgrade/skip decision is appended to the ledger with its reason.

# ADDENDUM B — HANDOVER STATE (immediate stop, always resumable)

## B1. The invariant

Every run — completed, stopped, halted, or crashed — ends by leaving the repo in a
resumable state. There is no such thing as an ambiguous stop.

## B2. New module: src/token_tithe/handover.py + state/HANDOVER.md + state/handover.json

- `state/handover.json` (machine-readable): run id, phase, task in progress, repo,
  branch, worktree path, tokens spent so far, last completed step, exact next step,
  pending review-queue items, halt/stop flags.
- `state/HANDOVER.md` (human-readable, < 60 lines): same story in prose, regenerated
  at every stop. Appended to CLAUDE.md conventions: any session (human or agent)
  reads HANDOVER.md first and resumes from it — never restarts from scratch.

## B3. Stop semantics

- `donate stop` — IMMEDIATE cooperative stop: checks the stop flag at every phase
  boundary AND between every executor step; on seeing it, abandons the in-flight task
  cleanly (delete scratch, keep the local branch + diff summary for resume), writes
  the handover, exits. Nothing is opened, pushed, or commented after the flag.
- automatic handover triggers: budget exhausted (R10/A4), `donate halt`, SIGINT/SIGTERM
  (signal handler — crash-safe), unhandled exception, end of every run.
- abort-before-publish rule: if a PR is mid-creation when a stop arrives, the engine
  aborts BEFORE creating it and records "pr_pending" in the handover; resume re-enters
  at that exact step. We never fire a GitHub write and then die.
- `donate resume` reads handover.json, validates the worktree still exists and is
  intact, and continues from the recorded next step. If state was wiped, it says so
  plainly and starts a fresh run instead of guessing.

## B4. Rules to add

- R13 Sub-agents forbidden by default: the engine invokes exactly ONE agent process
  per task. No parallel child agents, no multi-agent orchestration, no adapter flags
  that spawn sub-sessions. policy.yaml gate: `allow_subagents: false` default; when
  the user flips it true, concurrency is still capped at policy.yaml
  `max_subagents_per_task` and every spawned agent is logged in the ledger.
- R14 Every run ends with a written handover, whatever the exit reason.
- R15 A run below minimum viable capacity attempts nothing and says so.

# ADDENDUM C — LAYOUT, COMMANDS, CHECKLIST AMENDMENTS (for Addenda A + B)

## C1. Layout additions

```
├── src/token_tithe/
│   ├── capacity.py             # NEW: live quota/model/auth probe + sizing math
│   └── handover.py             # NEW: handover write/resume, signal-safe stops
├── config/policy.yaml          # += min_viable_tokens, allow_subagents: false,
│                               #    max_subagents_per_task, model_fallback rules
├── config/agents/*.yaml        # += capacity_probe, models (ordered fallback list)
├── state/                      # += HANDOVER.md, handover.json
└── docs/
    ├── CAPACITY.md             # NEW: probing, sizing, downgrade/skip rules, blind
    │                           # adapters, model fallback policy
    └── HANDOVER.md             # NEW: stop semantics, resume, crash safety, format
```

## C2. Makefile / CLI additions

`make capacity`, `make stop`, `make resume`, `make handover` (prints current
handover). CLI: `donate capacity | stop | resume | handover`.

## C3. Docs amendments

- ONBOARDING.md: first-run section now reads HANDOVER.md, runs `donate capacity`,
  then `donate doctor`.
- SAFETY.md: stop/halt/resume matrix (which flag wins, what each leaves behind).
- WORKFLOW.md: lifecycle now starts with the capacity probe and ends with handover
  write, every run, no exceptions.

## C4. Acceptance checklist additions

- [ ] `donate capacity` shows real per-agent quota/models and an afford estimate;
      a blind adapter is restricted to review mode (test proves it)
- [ ] A run with capacity below min_viable_tokens attempts nothing and exits cleanly
- [ ] `donate stop` mid-task leaves the worktree intact, writes HANDOVER.md +
      handover.json, and opens/pushes/comments NOTHING after the flag
- [ ] `donate resume` continues from the recorded next step, not from scratch;
      a killed process (SIGTERM) still leaves a valid handover
- [ ] An adapter whose primary model is unavailable falls back only to an
      eval-passed model, and the switch appears in ledger + PR body
- [ ] With `allow_subagents: false` (default), no code path spawns a second agent
      process; flipping it true is logged and capped
- [ ] Every run, of any exit reason, ends with a fresh handover (property test)

# ADDENDUM D — SECURITY, QUALITY, IDENTITY, ECONOMICS (final layer)

## D1. Sandboxed execution (R16) — src/token_tithe/sandbox.py

Running an untrusted repo's test suite is arbitrary code execution: pytest runs
conftest.py, setup.py runs install hooks, build scripts run post-install commands.
Therefore:

- Every agent invocation and every test/lint run in LIVE mode executes inside a
  throwaway Docker container: fresh filesystem per task, no network by default
  (allowlisted proxy ONLY for the target repo's own package manager during
  dependency install), host mounts limited to the worktree, container removed
  after every task.
- `donate doctor` verifies docker availability. In live mode with the sandbox
  disabled, the run refuses unless policy.yaml `allow_unsandboxed: true` AND the
  run logs a prominent warning. Default: sandboxed.
- SIMULATE/eval mode may run unsandboxed (fixtures are trusted and in-repo).
- SAFETY.md documents the threat model: hostile build scripts, post-install hooks,
  prompt injection arriving via repo file contents into the agent's context.

## D2. Race recheck before publish (executor.py)

Between triage and PR creation, minutes or hours can pass. Immediately before
opening ANY PR or comment, re-verify: issue still open, still unassigned, still no
linked PR; repo not archived/deleted/made private; our rate counters (R4) still
under cap; etiquette verdict not invalidated (new anti-AI statement). Any change
→ abandon with a ledger reason. A stale write is a wasted donation; a wrong write
is an incident.

## D3. Per-task wall-clock timeout (policy.yaml: `task_timeout_minutes`, default 45)

R10 caps tokens but not time; agents can loop forever. On expiry: the same
clean-abandon path as budget exhaustion — worktree preserved, diff summary kept,
handover written, ledger outcome `timeout`. The stop flag is checked on the same
schedule.

## D4. Secrets scan of target repos

If a candidate repo contains leaked credentials (hardcoded keys/tokens in tests,
configs, or secrets surfaced inside the issue text), the engine: skips the repo
entirely, records the reason in the ledger, NEVER quotes the secret anywhere, and
(optionally, policy-gated) posts once via the repo's private security advisory
channel per its SECURITY.md. No code change ever touches a live secret.

## D5. Cross-model second review (quality bought with leftover tokens)

Before a PR opens, a DIFFERENT configured model (or the same agent in a
review-only prompt mode) reviews the diff against the issue: does it actually fix
it? is it minimal? any behavior change not asked for? are the tests real or
theater? The verdict feeds the confidence score (R11). If no second model is
available, the PR is held in the review queue instead of auto-opening. The review
prompt lives in `agents/prompts/second_review.md`.

## D6. `donate address <pr>` — review-response mode (src/token_tithe/address.py)

Maintainer feedback is where one-shot PRs die. `donate address` reads the review
thread on OUR PR, makes the minimal requested change, and pushes to the same
branch. Strict scope: only PRs opened by the engine (ledger-verified), only
explicit review comments, diff < 200 lines, max 3 addressing rounds per PR,
time-limited (14 days from PR open), passes the R7 gates again, every round
appended to the ledger. templates/pr_body.md gains an "addressing review
feedback" note convention.

## D7. Signed commits + dedicated identity (R18)

All bot commits are GPG/SSH-signed. Committer: the dedicated donation GitHub
account with a noreply email. Signing config lives in policy.yaml; `donate doctor`
verifies signing works. Signed provenance is a trust signal maintainers can check
before choosing to review a bot PR.

## D8. One-file maintainer opt-out

Any repo may opt out by committing `.github/token-tithe-ignore` (any content;
presence = opt-out). etiquette.py checks for it on every discovery pass AND
immediately before every run against that repo. The convention is documented in
DONATION_POLICY.md. Opt-out must cost the maintainer one file and zero
conversation.

## D9. Expiry-aware donation windows

If provider credits are use-it-or-lose-it, value comes from timing. policy.yaml
gains `donation_window:` (billing-cycle-end date). The engine flags capacity as
"expiring" within the final 7 days, and the scheduled donate.yml run switches to
review mode 48h before expiry so approvals happen while tokens still exist.
(Scheduling setup happens in the user's own scheduler/cron — the engine only
reads the window.)

## D10. Cost-efficiency optimizer (src/token_tithe/optimizer.py)

`donate optimize` recomputes cost-per-merged-PR per agent × model × language from
ledger + feedback outcomes and REWRITES the skill-routing table in policy.yaml —
writing the proposed change to `state/` first, applied only on `--apply`.
Routing becomes an economy: whichever agent actually gets work merged cheapest
wins the slot.

## D11. Tier E additions

- Donation journal: `donate journal --month 2026-10` renders a readable monthly
  retrospective from the ledger (PRs opened, merges, tokens spent, repos helped)
  to `state/reports/journal-<month>.md` — blog-ready, and a personal retro tool.
- Private-repo hygiene is a HARD rule (R17), enforced in github_api.py on every
  call, not just discovery.
- Ledger schema_version field on every line (schema v1); readers tolerate and
  report unknown versions instead of crashing.

## D12. New rules

- R16 Live-mode agent invocations and test runs MUST run in the sandbox (D1).
  Unsandboxed live execution requires explicit policy override and is logged.
- R17 The engine only ever touches PUBLIC repos. Enforced at every GitHub call;
  a private-repo response aborts the run with a handover note.
- R18 All bot commits are cryptographically signed from the dedicated identity (D7).

## D13. Layout additions

```
├── src/token_tithe/
│   ├── sandbox.py              # NEW: container execution wrapper
│   ├── address.py              # NEW: review-response mode
│   └── optimizer.py            # NEW: cost-per-merged-PR routing optimizer
├── config/policy.yaml          # += task_timeout_minutes, allow_unsandboxed: false,
│                               #    signing config, donation_window, address limits
├── config/agents/prompts/
│   └── second_review.md        # NEW: cross-model review prompt
├── templates/review_response.md  # NEW: "addressing your feedback" PR comment
└── docs/
    └── OPERATIONS.md           # NEW: optimizer, journal, expiry windows, address mode
```
(SAFETY.md += sandbox threat model; DONATION_POLICY.md += opt-out file convention;
WORKFLOW.md += race recheck + addressing loop; LEDGER.md += schema_version.)

## D14. Phase amendments

- Phase 5 gains sandbox.py + race recheck + timeout wiring.
- Phase 6 gains address.py, optimizer.py, journal.
- Phase 7 docs gain OPERATIONS.md and the amendments above.

## D15. Acceptance checklist additions

- [ ] Live-mode test run of a fixture executes inside a container (test proves
      isolation); with docker absent, a live run refuses with clear guidance
- [ ] Race recheck test: fixture issue closed between triage and publish →
      abandoned with ledger reason, nothing published
- [ ] A task exceeding task_timeout_minutes is cleanly abandoned with a handover
- [ ] Secrets-scan test: fixture repo with a planted fake key is skipped entirely
- [ ] Cross-model review verdict appears in confidence math; with no second model
      available, the PR is held in the review queue (test proves it)
- [ ] `donate address` refuses a PR not in the ledger (test proves it)
- [ ] `donate doctor` verifies commit signing works
- [ ] Opt-out fixture (repo containing .github/token-tithe-ignore) is respected in
      discovery AND at run time
- [ ] `donate optimize` proposes a routing change and applies it only on --apply
- [ ] `donate journal` renders a monthly retrospective from the ledger
- [ ] Every ledger line carries schema_version; a malformed line is reported, not a crash
- [ ] github_api refuses any private-repo response (R17 test)

# ADDENDUM E — CROSS-REPO LEARNING LAYER (the Brain)

## E1. Concept: two memory layers, one portable asset

- **Run state** (already specced): `state/` — gitignored, per-repo, disposable.
- **The Brain** (NEW): durable, committed, versioned knowledge that travels across
  repos, machines, and fresh agent sessions. One distilled file at the repo root
  plus a small `knowledge/` directory for depth. The Brain is THE transferable
  asset of this project: any new token-tithe clone — or any fresh Claude/agent
  session, in this repo or any other — can be handed the Brain file and inherit
  everything learned so far.

Convention added to CLAUDE.md + AGENTS.md: every session reads `state/HANDOVER.md`
first (where am I?) and `BRAIN.md` second (what do we know?). Handover is
per-run volatile state; the Brain is durable knowledge. They never mix.

## E2. Brain format (self-contained by construction)

`BRAIN.md` (committed, < 500 lines, hard cap) with sections:

- **AGENT QUIRKS** — per agent/model: invocation flags that worked, failure modes,
  usage-reporting quirks, prompt fragments that got better results.
- **WHAT GETS MERGED** — evidence-based patterns: which donation types, languages,
  diff sizes, PR-body styles correlate with merges (sourced from ledger+feedback).
- **REJECTIONS & INCIDENTS** — why maintainers closed our PRs, etiquette misses,
  anti-patterns with links. This section prevents repeating expensive mistakes.
- **PROMPT PATTERNS** — task-brief fragments and templates that produced accepted
  work, with the run/PR that proves them.
- **ECOSYSTEM PLAYBOOKS** — per language/toolchain: how rust repos run tests, python
  venv conventions, js package-manager pitfalls, CI quirks worth remembering.
- **PENDING LESSONS** — auto-appended candidates awaiting human approval (see E3).

Every lesson line carries: date, provenance (ledger run id / PR URL), and a
confidence tag (observed-once / repeated / confirmed). Deep material that would
burst the cap moves to `knowledge/<topic>.md`; BRAIN.md keeps a one-line pointer.

## E3. The learning loop — src/token_tithe/learn.py + brain.py

- `donate learn` — post-run retrospective: distills the run's ledger entries,
  handover, incidents, review outcomes, and eval results into candidate lessons;
  dedupes against existing Brain content (fuzzy match on topic+repo); appends to
  **PENDING LESSONS** with provenance. Simulate/eval/dry-run lessons are marked
  as such (cheap experiments still teach).
- `donate learn --apply` — human approves pending lessons; they are promoted into
  their section with provenance intact. **R19: the engine never edits approved
  lessons and never deletes anything from the Brain. Corrections are appended as
  superseding entries.**
- Quarterly `donate brain --review` — flags lessons not reinforced in 6+ months
  as stale (marked, not removed).
- Triage/executor/adapter layers READ the Brain: relevant lessons are injected
  into task briefs (templates/task_brief.md gains a "lessons that apply here"
  section) and into second-review prompts. Learning must change behavior, not
  just accumulate.

## E4. Portability across repos and machines

- `donate brain export` — emits ONE self-contained file
  `tithe-brain-<date>.md`: BRAIN.md plus the inlined contents of every
  knowledge/*.md file it references. This single file is the entire learning
  state; it can be committed anywhere, attached to a fresh agent chat in any
  project, or dropped into another machine.
- `donate brain import <file>` — merges an exported brain: matching lessons are
  deduped with provenance union; conflicts NEVER overwrite silently — the
  incoming version lands in PENDING for human approval.
- Default storage: the Brain lives committed inside the repo (S1 holds — no
  external dependency). OPTIONAL advanced setup documented in LEARNING.md: keep
  the Brain in its own tiny git repo (or dotfiles) added as a git remote and
  synced with `donate brain sync` — for users running several token-tithe clones
  who want a single shared brain. Off by default; the in-repo copy always works
  alone.
- New-clone cold start: ONBOARDING.md gains a step — "if you have a
  tithe-brain export from another machine/repo, import it now; otherwise the
  engine starts learning from zero."

## E5. Layout additions

```
├── BRAIN.md                    # NEW: committed distilled knowledge (<500 lines)
├── knowledge/                  # NEW: deep-dive topic files referenced by BRAIN.md
├── src/token_tithe/
│   ├── learn.py                # NEW: run retrospective → candidate lessons
│   └── brain.py                # NEW: read/merge/export/import/dedupe/decay
├── templates/task_brief.md     # += "lessons that apply here" injection section
├── config/policy.yaml          # += brain: {max_lines, stale_after_days, auto_inject: true}
└── docs/
    └── LEARNING.md             # NEW: the loop, format, provenance rules, export/
                                # import, sync option, what belongs vs doesn't
```

## E6. Phase amendments

- Phase 6 gains learn.py + brain.py + BRAIN.md scaffold with seeded starter
  lessons drawn from the eval suite's own first run.
- Phase 7 docs gain LEARNING.md; CLAUDE.md/AGENTS.md gain the read-order
  convention (HANDOVER → BRAIN).
- Phase 9 self-review verifies a full loop: run → learn → apply → next task
  brief contains the injected lesson (integration test).

## E7. Acceptance checklist additions

- [ ] `donate learn` after a simulate run appends provenance-tagged pending lessons
- [ ] `donate learn --apply` promotes lessons; the NEXT task brief contains the
      injected lesson (integration test proves the loop changes behavior)
- [ ] `donate brain export` produces one file that, imported into a FRESH clone
      of the repo, restores identical Brain state (portability test)
- [ ] Import never silently overwrites: conflicting lessons land in PENDING (test)
- [ ] BRAIN.md stays under its cap; overflow is promoted to knowledge/*.md with
      pointers (test with a deliberately oversized lesson set)
- [ ] No engine code path deletes or rewrites an approved lesson (R19 test)
- [ ] A fresh agent session given only AGENTS.md + BRAIN.md + HANDOVER.md can
      state the project's top 5 learned lessons without other context

