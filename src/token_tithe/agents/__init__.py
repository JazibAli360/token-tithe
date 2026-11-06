"""Agent adapters (§6). `get_adapter(name)` returns a configured adapter."""

from __future__ import annotations

from token_tithe.agents.base import Adapter, AgentResult, SummaryError, read_summary
from token_tithe.agents.cli_adapter import CliAdapter
from token_tithe.agents.control import CONTROL_ADAPTERS, NullAdapter, ReferenceAdapter
from token_tithe.policy import load_agent

__all__ = [
    "Adapter", "AgentResult", "SummaryError", "read_summary", "CliAdapter", "NullAdapter",
    "ReferenceAdapter", "CONTROL_ADAPTERS", "get_adapter",
]  # fmt: skip


def get_adapter(name: str, model: str | None = None, **control_kwargs) -> Adapter:
    if name in CONTROL_ADAPTERS:
        return CONTROL_ADAPTERS[name](**control_kwargs)
    return CliAdapter(load_agent(name), model=model)
