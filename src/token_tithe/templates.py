"""Render templates/*.md and prompt files. `{{name}}` placeholders, strictly checked."""

from __future__ import annotations

import re
from pathlib import Path

from token_tithe import paths

_VAR = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


class TemplateError(ValueError):
    pass


def placeholders(text: str) -> set[str]:
    return set(_VAR.findall(text))


def render_text(text: str, source: str = "<template>", **values: object) -> str:
    """Fill every placeholder. Missing or extra values are errors, so a renamed
    placeholder can never silently drop the AI disclosure or a scope limit."""
    needed = placeholders(text)
    missing = needed - values.keys()
    extra = values.keys() - needed
    if missing:
        raise TemplateError(f"{source}: missing values for {sorted(missing)}")
    if extra:
        raise TemplateError(f"{source}: unexpected values {sorted(extra)}")
    return _VAR.sub(lambda m: str(values[m.group(1)]), text)


def render(name: str, directory: Path = paths.TEMPLATES, **values: object) -> str:
    path = directory / name
    return render_text(path.read_text(encoding="utf-8"), paths.rel(path), **values)


def render_prompt(name: str, **values: object) -> str:
    return render(name, directory=paths.PROMPTS, **values)
