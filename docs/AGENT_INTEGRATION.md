# Agent integration

Any coding agent can drive token-tithe (as **operator**, through the `donate` CLI and
[AGENTS.md](../AGENTS.md)) or be driven by it (as **task agent**, through an adapter).
This page covers the second.

## The adapter contract (`src/token_tithe/agents/base.py`)

| Method | Must do |
|---|---|
| `invoke(worktree, task_brief_path, runner, timeout)` | Run the agent headless inside the worktree, with relative paths only, through the given runner (sandbox in live mode). Exactly one agent process (R13). |
| `report_usage()` | **Actual** usage from the agent's own accounting: model, input/output tokens, cost if known. No real numbers → `Usage.unaccounted()` (R10). |
| `write_summary(worktree)` | Return the structured summary the agent wrote to `.tithe/summary.json` (what changed, files, tests, test commands, confidence 0–100 + reasoning). |
| `cleanup(worktree)` | Remove `.tithe/` scratch so the worktree holds only the real change. |
| `ask(prompt, …)` *(optional)* | Review-only mode for [second review](WORKFLOW.md) (D5). |

The task agent never commits, pushes or comments. The engine publishes, after verifying
independently.

## Adding an agent — usually just YAML

1. `cp config/agents/generic.yaml config/agents/<name>.yaml` and fill it in:

```yaml
name: myagent                  # must equal the file name
display_name: My Agent         # appears in the PR disclosure (R2)
binary: myagent
invoke:
  args: [run, --headless, --prompt-file, "{prompt_file}", --model, "{model}"]
  prompt_entry: file           # argument ({prompt}) | stdin | file ({prompt_file})
  forbidden_args: [--spawn-subagents, --yolo]   # R13: rejected if they ever appear
  env_passthrough: [MYAGENT_API_KEY]
  result_path: result          # where the final text answer is (for review mode)
usage:
  source: json_stdout          # json_stdout | jsonl_stdout | none
  event_type: null             # jsonl only: which event type carries usage (summed)
  model_path: "model"          # dotted path; "@first_key" and "@arg_model" are special steps
  input_paths: [usage.input_tokens]
  output_paths: [usage.output_tokens]
  cost_path: usage.cost_usd
capacity_probe:
  auth: [auth, status]         # exit 0 (or {"loggedIn": true}) = authenticated
  models: null
  quota: null                  # a command printing remaining tokens; null = capacity-blind
models: [model-a, model-b]     # ordered fallback; "default" = don't pass --model
```

2. `.venv/bin/donate doctor` validates it (unknown keys and wrong types are errors).
3. Score it: `make eval AGENTS=reference,null,myagent` (or `myagent:model-b`).
4. Only after it passes, route to it in `config/policy.yaml`:
   `routing: {by_language: {rust: myagent}}` or `by_donation_type: {docs: myagent}`.

If an agent needs logic YAML can't express, subclass `Adapter` in `src/token_tithe/agents/`
and register it in `get_adapter`.

## Shipped adapters

| Agent | Usage | Capacity | Notes |
|---|---|---|---|
| `claude` (Claude Code) | real (`--output-format json`) | blind | `--allowedTools` lets it run tests; `--disallowedTools Task Agent` (R13) |
| `codex` (Codex CLI) | real (`turn.completed` events) | blind | `workspace-write` sandbox; prompt on stdin |
| `aider` | **unaccounted** (prints rounded numbers only) | blind | review mode only |
| `generic` | — | — | template; never routed |

## What "accounted" and "capacity-aware" mean

- **Accounted** (R10): the adapter reports real token usage. Unaccounted agents may run, but
  their work is excluded from budget math with a warning and they can never be used in
  budgeted live runs — everything goes to the review queue.
- **Capacity-aware** (A3): `capacity_probe.quota` returns real remaining quota. No CLI
  exposes this today, so every shipped agent is capacity-blind: the effective budget is the
  configured cap, and work is queued for human review rather than auto-published.
  See [CAPACITY.md](CAPACITY.md).

## Model fallback (A3)

If an invocation fails because the model is unavailable (not found, overloaded, out of
quota), the executor tries the next entry in `models` **only if** that agent:model has passed
eval for the task's donation type (`state/eval_runs/passes.json`, written by `make eval`).
The switch is ledgered (`model_switch`) and disclosed in the PR body. No eval-passed
fallback → the task is abandoned; quality is never silently downgraded.

## Eval gating

`make eval` runs every requested agent on every fixture in `eval/fixtures/` and scores it
against `eval/expected/<fixture>/rubric.yaml`: hidden acceptance tests, repo tests, mutants
(for tests donations), diff size, scope, and real usage. Two control agents keep the harness
honest: `reference` must pass everything runnable, `null` must fail every fix fixture.
Results: `state/eval_runs/report.md` (agent × fixture and agent × language matrices).
