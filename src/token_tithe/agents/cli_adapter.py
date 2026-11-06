"""Adapter for any headless agent CLI described by config/agents/<name>.yaml."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from token_tithe.agents.base import SCRATCH, Adapter, AgentResult
from token_tithe.budget import Usage
from token_tithe.policy import AgentConfig
from token_tithe.sandbox import Runner

PROMPT = "Read the task brief at {path} and complete the task exactly as it describes."


class ForbiddenArgument(RuntimeError):
    """R13: the invocation would include a flag that spawns sub-agents or drops safety."""


def resolve_path(data: Any, path: str, model: str | None) -> Any:
    """Dotted path with two special steps: `@first_key` and `@arg_model`."""
    if path == "@arg_model":
        return model
    cur = data
    for step in path.split("."):
        if step == "@first_key":
            if not isinstance(cur, dict) or not cur:
                return None
            cur = next(iter(cur))
            continue
        if isinstance(cur, dict) and step in cur:
            cur = cur[step]
        else:
            return None
    return cur


class CliAdapter(Adapter):
    def __init__(self, config: AgentConfig, model: str | None = None):
        self.config = config
        self.name = config.name
        self.display_name = config.display_name
        chosen = model or config.models[0]
        self.model = None if chosen == "default" else chosen
        self._last_stdout = ""
        self._last_ok = False

    @property
    def accounted(self) -> bool:
        return self.config.accounted

    @property
    def capacity_aware(self) -> bool:
        return self.config.capacity_aware

    # ----------------------------------------------------------------- argv

    def build_argv(self, prompt: str, prompt_file: str) -> list[str]:
        out: list[str] = []
        for arg in self.config.invoke.args:
            if "{model}" in arg and self.model is None:
                if out and out[-1].startswith("-"):
                    out.pop()  # drop the --model flag together with its value
                continue
            out.append(
                arg.replace("{prompt}", prompt)
                .replace("{prompt_file}", prompt_file)
                .replace("{model}", self.model or "")
            )
        bad = [a for a in out if a in self.config.invoke.forbidden_args]
        if bad:
            raise ForbiddenArgument(f"{self.name}: forbidden argument(s) {bad} (R13)")
        return [self.config.binary, *out]

    def _env(self) -> dict[str, str]:
        return {k: os.environ[k] for k in self.config.invoke.env_passthrough if k in os.environ}

    def _run(self, prompt: str, prompt_file: str, worktree, runner, timeout) -> AgentResult:
        entry = self.config.invoke.prompt_entry
        argv = self.build_argv(prompt if entry == "argument" else "", prompt_file)
        stdin = prompt if entry == "stdin" else None
        r = runner.run(argv, worktree, "agent", timeout, env=self._env(), stdin=stdin)
        self._last_stdout = r.stdout
        self._last_ok = r.ok
        return AgentResult(
            exit_code=r.code,
            timed_out=r.timed_out,
            output_tail=r.output[-2000:],
            final_text=self._final_text(r.stdout),
            error=None if r.ok else f"exit {r.code}" + (" (timeout)" if r.timed_out else ""),
        )

    # ----------------------------------------------------------------- contract

    def invoke(self, worktree: Path, task_brief_path: str, runner: Runner, timeout: float):
        prompt = PROMPT.format(path=task_brief_path)
        return self._run(prompt, task_brief_path, worktree, runner, timeout)

    def ask(self, prompt: str, worktree: Path, runner: Runner, timeout: float) -> AgentResult:
        rel = f"{SCRATCH}/review_prompt.md"
        (worktree / SCRATCH).mkdir(exist_ok=True)
        (worktree / rel).write_text(prompt, encoding="utf-8")
        entry_prompt = prompt if self.config.invoke.prompt_entry != "file" else ""
        return self._run(entry_prompt or PROMPT.format(path=rel), rel, worktree, runner, timeout)

    def report_usage(self) -> Usage:
        spec = self.config.usage
        if spec.source == "none" or not self._last_stdout.strip():
            return Usage.unaccounted(self.model)
        events = self._events()
        if not events:
            return Usage.unaccounted(self.model)
        model = None
        inp = out = 0
        cost: float | None = None
        for ev in events:
            for p in spec.input_paths:
                inp += int(resolve_path(ev, p, self.model) or 0)
            for p in spec.output_paths:
                out += int(resolve_path(ev, p, self.model) or 0)
            if spec.cost_path:
                c = resolve_path(ev, spec.cost_path, self.model)
                if isinstance(c, int | float):
                    cost = (cost or 0.0) + float(c)
            if spec.model_path and model is None:
                model = resolve_path(ev, spec.model_path, self.model)
        if inp == 0 and out == 0:
            return Usage.unaccounted(model or self.model)
        return Usage(model=model or self.model, input_tokens=inp, output_tokens=out,
                     cost_estimate=cost, accounted=True)  # fmt: skip

    # ----------------------------------------------------------------- parsing

    def _events(self) -> list[dict]:
        spec = self.config.usage
        text = self._last_stdout.strip()
        if spec.source == "json_stdout":
            obj = _last_json_object(text)
            return [obj] if obj else []
        events = []
        for line in text.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and (not spec.event_type or ev.get("type") == spec.event_type):
                events.append(ev)
        return events

    def _final_text(self, stdout: str) -> str:
        path = self.config.invoke.result_path
        if not path:
            return stdout.strip()
        if self.config.usage.source == "json_stdout":
            obj = _last_json_object(stdout.strip())
            val = resolve_path(obj, path, self.model) if obj else None
            return str(val) if val is not None else ""
        found = ""
        for line in stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            val = resolve_path(ev, path, self.model) if isinstance(ev, dict) else None
            if isinstance(val, str):
                found = val
        return found


def _last_json_object(text: str) -> dict | None:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            continue
    return None
