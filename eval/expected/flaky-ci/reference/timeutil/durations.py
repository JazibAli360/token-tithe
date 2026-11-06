"""Durations such as "1h30m" or "45s"."""

import re

_UNITS = {"h": 3600, "m": 60, "s": 1}
_PART = re.compile(r"(\d+)([hms])")


def parse_duration(text):
    """'1h30m' -> 5400. Unknown text yields 0."""
    return sum(int(n) * _UNITS[u] for n, u in _PART.findall(text.strip().lower()))


def format_duration(seconds):
    """5400 -> '1h30m'. Zero is rendered as '0s'."""
    if seconds < 0:
        raise ValueError("negative duration")
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s:
        parts.append(f"{s}s")
    return "".join(parts) or "0s"
