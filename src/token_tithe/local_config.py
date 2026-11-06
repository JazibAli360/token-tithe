"""Per-user config overlays: `config/<name>.local.yaml` on top of the shared `config/<name>.yaml`.

The shared files hold neutral defaults and are committed; each person's identity, budget,
routing, blocklist and adopted repos live in the gitignored overlay, so `git pull` never
conflicts and nobody's settings get committed back by accident. Everything the engine itself
writes (adopt, incident cooldowns, optimize --apply, quickstart) goes to the overlay.

Merge rules: mappings merge key by key; in policy.yaml a list in the overlay replaces the
shared list; in repos.yaml lists are combined (shared blocklist + yours).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from token_tithe import paths

HEADER = (
    "# Your personal overrides for {base} — gitignored, never committed.\n"
    "# Same keys as {base}; only what you set here differs from the shared defaults.\n"
    "# Written by `make quickstart`, `donate adopt`, `donate incident`, `donate optimize --apply`;\n"
    "# edit freely.\n"
)


def local_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.local.yaml")


def overlay_enabled(path: Path) -> bool:
    # the test suite pins the real config/ to the shared defaults, whatever the developer's overlay
    return not (os.environ.get("TITHE_NO_LOCAL_CONFIG") and path.parent == paths.CONFIG)


def _read_raw(path: Path) -> dict:
    local = local_path(path)
    if not local.is_file():
        return {}
    data = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{paths.rel(local)}: top level must be a mapping")
    return data


def read_overlay(path: Path) -> dict:
    return _read_raw(path) if overlay_enabled(path) else {}


def merge(base: Any, over: Any, extend_lists: bool = False) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = merge(base[k], v, extend_lists) if k in base else v
        return out
    if extend_lists and isinstance(base, list) and isinstance(over, list):
        return base + [x for x in over if x not in base]
    return over


def _write(path: Path, data: dict) -> Path:
    local = local_path(path)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    local.write_text(HEADER.format(base=path.name) + "\n" + body, encoding="utf-8")
    return local


def set_value(path: Path, dotted: str, value: Any) -> Path:
    """Set one key (`signing.committer_name`) in the overlay of `path`."""
    data = _read_raw(path)
    node = data
    *parents, leaf = dotted.split(".")
    for p in parents:
        nxt = node.setdefault(p, {})
        if not isinstance(nxt, dict):
            raise ValueError(f"{dotted}: {p} is not a mapping in {local_path(path).name}")
        node = nxt
    node[leaf] = value
    return _write(path, data)


def append_item(path: Path, key: str, item: Any) -> Path:
    """Append to a top-level list in the overlay of `path` (no duplicates)."""
    data = _read_raw(path)
    items = data.get(key) or []
    if item not in items:
        items.append(item)
    data[key] = items
    return _write(path, data)
