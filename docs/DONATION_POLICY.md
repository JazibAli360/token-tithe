# Donation policy

## Why this exists

Open-source maintainers are mostly volunteers, and review time is their scarcest resource.
Since 2024 they have been flooded with low-effort, AI-generated pull requests; many projects
now ban them outright. A pull request that takes an hour to review and gets closed is not a
gift — it is a cost we imposed on someone who never asked for it.

token-tithe exists to make leftover agent capacity *useful* to open source, which means
behaving like the contributor maintainers wish they had: small, correct, tested, honest,
responsive — and gone the moment they say no.

## What we promise maintainers

1. **We disclose.** Every PR and comment says it was "Generated with <agent> by the
   token-tithe engine" and links the source issue (R2). We never pass AI work off as human.
2. **We respect "no".** Any statement in your README, CONTRIBUTING, code of conduct, PR
   template, AI policy or the issue thread that rejects AI or automated contributions means
   we never touch your repo (R3). So does an unsigned CLA, a private repo, or an archive.
3. **We only take wanted work.** Open, unassigned issues labelled `good first issue` or
   `help wanted`, with no PR already linked. We re-check right before publishing, and walk
   away if anything changed (D2).
4. **We keep it small.** One focused change per PR, under 400 changed lines (R8), no drive-by
   refactors, formatting churn or dependency bumps.
5. **We test.** Your own test suite and linter must pass, re-run by the engine itself — never
   on the agent's word (R7). Fixes come with tests. Tests-only donations must catch real bugs.
6. **We don't flood.** At most 3 PRs per repo per 30 days, 5 per day across all of GitHub, and
   one comment per issue, ever (R4).
7. **We follow up — politely, briefly.** We address explicit review comments (at most three
   rounds, within 14 days). We never argue; if you close the PR, that is the answer.
8. **We clean up our mistakes.** If something we sent was a burden, we apologise without
   excuses, offer to revert, and stop contributing to your repo ([SAFETY.md](SAFETY.md)).

## Opting out — one file, zero conversation

Commit a file named **`.github/token-tithe-ignore`** (any content, even empty) to your
repository. That's it.

The engine checks for it on every discovery pass **and** again immediately before any work
starts and before anything is published. The check is never cached. Every PR and comment we
post includes this instruction in its footer.

You can also opt out by saying so in an issue, in your CONTRIBUTING file ("we do not accept
AI-generated contributions"), or by closing our PR and asking us to stop — all of which the
engine detects or the operator acts on.

## Why we skip dead and anti-AI repos

- **Anti-AI repos**: their maintainers have told us the answer. Contributing anyway would be
  disrespectful and would burn goodwill for everyone using AI responsibly.
- **Unresponsive repos** are not rejected but ranked low: a PR to a repo nobody reviews costs
  the donor tokens and adds to the maintainer's backlog without helping anyone.
- **Repos with leaked credentials in their tree** are skipped entirely (D4) and the secret is
  never quoted anywhere. Optionally (policy-gated) we report it once via the repo's private
  security channel.

## Who we are on GitHub

Commits come from a **dedicated donation account** with a noreply address and are
cryptographically signed (R18), so you can verify where a contribution came from before
deciding to review it. The operator of that account is responsible for everything it sends.

## What counts as a donation

| Type | Output | Notes |
|---|---|---|
| `issue_fix` | PR | Reproducing test first, smallest correct fix |
| `tests` | PR | Test files only; must catch mutants of the code under test |
| `docs` | PR | Fill a documented gap; behavior described as implemented |
| `ci_repro` | PR, or a report | Never "fix" CI by skipping or loosening a test |
| `triage_report` | one issue comment | Reproduction, root cause, fix direction — sometimes worth more than code |
