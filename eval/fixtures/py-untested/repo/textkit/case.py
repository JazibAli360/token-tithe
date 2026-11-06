"""Case conversion."""

import re


def snake_case(text):
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[\s\-]+", "_", text).lower()
