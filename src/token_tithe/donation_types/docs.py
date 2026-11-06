"""docs — fill documented doc gaps. No opinion rewrites (§5.3)."""

from token_tithe.donation_types import DOC_GLOBS, DonationType

TYPE = DonationType(
    name="docs",
    output="pr",
    title_prefix="docs",
    allowed_globs=DOC_GLOBS,
    off_scope_penalty=15,  # docstrings live in source files: allowed, but reviewed harder
    instructions="""### Instructions: documentation

1. Fill exactly the gap the issue describes (a README section, missing docstrings for the
   public API, an outdated example). Document behavior as the code actually implements it —
   read the code; do not guess.
2. Keep the existing structure, tone and headings. Do not rewrite sections that are fine.
3. Do not change any behavior. Source files may change only in docstrings/comments.""",
)
