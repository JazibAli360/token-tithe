"""Hidden acceptance check: README documents the whole public API."""

import re
import sys
from pathlib import Path

text = Path("README.md").read_text(encoding="utf-8")
problems = []
if not re.search(r"^##\s+API\b", text, re.M):
    problems.append("no '## API' section")
for needle in ("slugify", "isSlug", "separator", "lowercase", "maxLength"):
    api = text.split("## API", 1)[-1]
    if needle not in api:
        problems.append(f"API section does not mention {needle}")
if "## Install" not in text or "## License" not in text:
    problems.append("existing sections were removed")
print("\n".join(problems) or "ok")
sys.exit(1 if problems else 0)
